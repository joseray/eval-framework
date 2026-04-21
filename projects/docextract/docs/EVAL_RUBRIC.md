# Eval Rubric — DocExtract Pipeline

**Audience:** Reviewers deciding whether a given extraction is safe to auto-commit, requires human review, or must be rejected.  
**Scope:** All five document types processed by the pipeline: SOV, COI, Loss Run, Endorsement, Binder.

---

## 1. Field categories and match rules

| Field type | Example fields | Match rule | Tolerance / notes |
|---|---|---|---|
| **Identifiers** | `policy_number`, `claim_number`, `binder_number`, `endorsement_number` | Exact match, case-insensitive, whitespace-stripped | 0% tolerance. Any character difference is a mismatch. |
| **Party names** | `insured_name`, `certificate_holder`, `claimant`, `producer` | Fuzzy: case-insensitive, collapse internal whitespace, strip | "Acme Corp." == "acme corp" == "ACME CORP". Punctuation differences are ignored via whitespace normalization. |
| **Carrier names** | `carrier` (all doc types) | Canonical lookup, then fuzzy match on result | Applied before comparison. Variants map to a single canonical form — see §6. |
| **Dates** | `effective_date`, `expiration_date`, `policy_effective_date`, `valuation_date`, `binder_*_date`, `endorsement_effective_date` | Parse to ISO `YYYY-MM-DD`, then exact match | Accepts ISO, US `MM/DD/YYYY`, EU `DD/MM/YYYY`. Ambiguous dates (first segment ≤ 12) resolved as US format per pipeline config. `03/04/2025` → `2025-03-04` (March 4, not April 3). |
| **Dollar amounts — per item** | `building_value`, `contents_value`, `business_income_value`, `paid_amount`, `reserved_amount`, `each_occurrence_limit`, `general_aggregate_limit`, `premium_delta` | Relative tolerance ±3% | `\|actual − expected\| / expected ≤ 0.03`. Values within ±3% are `within_tolerance` (pass). Explicit 100x unit-drift check applied first — see §2. |
| **Dollar amounts — aggregates** | `total_tiv`, `total_insured_value`, `total_paid`, `total_incurred`, `total_recoveries`, `products_completed_ops` | Relative tolerance ±5% AND cross-field invariant must hold | Aggregate tolerance is wider (±5%) because these values are derived from components that each carry ±3% noise. Invariant check is independent of tolerance — a value can be within tolerance but still violate an invariant. |
| **Structural / enum** | `doc_type`, `construction_type`, `coverage_type`, `status`, `change_type`, `claim_type` | Exact match, case-insensitive | `"fire-resistive"` ≠ `"fire resistive"`. `"closed"` ≠ `"Closed"` after lowercasing fails. No partial credit. |
| **Optional / nullable** | `producer`, `year_built`, `square_footage`, `loss_ratio`, `claimant`, `old_value`, `anticipated_policy_number` | Both null → pass (`missing_both`). Extraction null, GT present → `missing_expected` (omission). Extraction present, GT null → `hallucinated`. | Omission rate tracked per field per document. Flag if omission rate exceeds 15% across runs for any required field. For optional fields, omission is acceptable but must be logged. |

---

## 2. Cross-field invariants

Invariants run independently of ground truth and apply to every document of the relevant type. A single ERROR violation is sufficient to block auto-commit.

| Invariant | Applies to | Formula | Threshold | Severity |
|---|---|---|---|---|
| **component_sum** | SOV, per property | `building_value + contents_value + business_income_value == total_insured_value` | Drift > 5% → ERROR | ERROR |
| **total_tiv_sum** | SOV | `sum(properties[i].total_insured_value) == total_tiv` | Drift > 5% → ERROR | ERROR |
| **incurred_sum** | Loss Run, per claim | `paid_amount + reserved_amount == total_incurred` | Drift > 3% → ERROR | ERROR |
| **total_paid_sum** | Loss Run | `sum(claims[i].paid_amount) == total_paid` | Drift > 5% → ERROR | ERROR |
| **total_incurred_sum** | Loss Run | `sum(claims[i].total_incurred) == total_incurred` | Drift > 5% → ERROR | ERROR |
| **closed_reserves_zero** | Loss Run | `status == "closed"` → `reserved_amount == 0` | Any nonzero reserved on a closed claim → ERROR | ERROR |
| **unit_drift_detection** | Loss Run | `paid_amount / median(paid_amounts)` — ratio > 50x or < 0.02x signals cents-vs-dollars bug | Any outlier above threshold → ERROR | ERROR |
| **duplicate_coverage** | COI | No two coverage entries share the same `coverage_type` | Any duplicate → ERROR | ERROR |
| **phantom_coverage** | COI | All `coverage_type` values must belong to the known ACORD 25 set | Unknown type → ERROR | ERROR |
| **date_ordering** | COI, Binder | `effective_date < expiration_date` for every date pair and every coverage entry | `effective >= expiration` → ERROR | ERROR |
| **binder_duration** | Binder | `expiration - effective` in calendar days | ≤ 0 days → ERROR; > 180 days → WARNING | ERROR / WARNING |

The `unit_drift_detection` invariant computes the median of all non-zero `paid_amount` values within a loss run. Any single claim whose paid amount is more than 50× or less than 1/50th of that median is flagged. This detects cents-vs-dollars post-processor bugs that produce values such as $12,345,678 when the correct figure is $123,456.

---

## 3. Classification calibration

The pipeline classifies every document before extracting it. A misclassified document will be validated against the wrong field schema, making all downstream field results meaningless.

| Confidence | Correct classification | Decision |
|---|---|---|
| ≥ 85% | Yes | Healthy — proceed to field validation |
| < 70% | Yes | Model is uncertain — route to human review regardless of field accuracy |
| 70–85% | Yes | Acceptable — include field accuracy in routing decision |
| Any | No, confidence < 70% | Likely noise — route to human review |
| ≥ 90% | **No** | **MOST DANGEROUS** — high-confidence misclassification. Blocks auto-commit and triggers SEV-2 alert. |

**Known case:** `coi_travelers_umbrella` is systematically classified as `"policy"` with confidence 95–99% due to a template-matching artifact in the upstream classifier. This is the exact failure pattern that bypasses safety gates — a high-confidence wrong answer causes the extraction to be validated against the wrong schema and potentially auto-committed as incorrect document type.

A healthy classifier shows positive correlation between confidence and correctness. Confidence inversion — high confidence on wrong answers — is a SEV-2 signal that must be investigated before the next deploy.

**Current service risk:** `AUTO_COMMIT_THRESHOLD = 5` in the pipeline config is dangerously low. The service will auto-commit extractions with confidence as low as 5/100. Recommended minimum: **85**. Any deployment with a threshold below 70 should be treated as a production incident.

---

## 4. Pass / Review / Reject thresholds

| Tier | Criteria | Action |
|---|---|---|
| **Auto-commit** | ≥ 90% of scored fields match (exact or within tolerance) AND zero ERROR invariant violations AND classification correct AND confidence ≥ 85% | Write to production database |
| **Human review** | Field accuracy 70–90% OR any WARNING invariant (e.g., binder duration > 180 days) OR confidence < 85% OR any hallucinated field OR classification correct but low-confidence | Queue for operator review console |
| **Reject** | Field accuracy < 70% OR any ERROR invariant violation OR misclassification with confidence ≥ 90% OR unit-drift detected in any amount field | Discard extraction, re-queue document, alert on-call |

Field accuracy is computed over scored fields only. Skipped fields (ground truth marked `"unknown"`) and optional fields where both sides are null (`missing_both`) do not count against the score.

---

## 5. Per-doc-type considerations

**SOV (Statement of Values):** The most field-dense document type, with nested property schedules that require position-matched validation (properties are matched by index, not by address). The primary failure modes are: (1) a TIV calibration bias observed on `sov_pacific_realty` where all numeric values are systematically scaled by approximately 0.895 — this is a bias, not noise, and will show 0% accuracy across all amount fields; (2) `construction_type` omission on `sov_keystone_reit` at ~40% of extractions, which is above the 15% acceptable omission rate; and (3) `component_sum` drift caused by independent rounding of building, contents, and BI components. Because SOVs feed blanket schedule endorsements, a wrong `total_tiv` can cascade into incorrect premium calculations.

**COI (Certificate of Insurance):** Coverage lines are matched by `coverage_type` key, not position. Extra coverage lines in the extraction that have no corresponding entry in ground truth are flagged as hallucinated — every field of such a line scores 0%. `coi_zurich_legacy` uses a 2010/05 legacy ACORD 25 layout with older limit field labels, which stresses the carrier and limit extraction. `coi_zurich_legacy` is also the known source of phantom `cyber` coverage injection at 20% rate. Carrier names are highly variable across COIs — "The Hartford," "Hartford," and "Hartford Financial Services" must all resolve to the same canonical form before comparison.

**Loss Run:** The most arithmetically constrained document type, with three independent sum invariants that must all hold simultaneously. The pipeline has a known unit-drift bug on `loss_run_libertymutual` affecting ~15% of `paid_amount` values (cents emitted instead of dollars, 100× off). Additionally, `loss_run_libertymutual` contains subrogation recoveries represented as negative `paid_amount` values — these are valid and must not be rejected. Closed claims with nonzero `reserved_amount` indicate a post-processor logic error and trigger an ERROR invariant. The v2 model has a sign-handling bug that corrupts `total_incurred` on this document.

**Endorsement:** Short documents with few fields. The highest-stakes field is `premium_delta` (a signed dollar amount: positive for increases, negative for decreases). Sign errors are not caught by tolerance checks — a delta of +$5,000 vs −$5,000 would be a 200% relative error, far outside any reasonable tolerance, but the validator must not mistake sign flips for unit drift. `endorsement_number` must be exact — it is the primary key used to apply the endorsement to the policy record.

**Binder:** A temporary coverage document with a strict expected duration of 30–90 calendar days. The primary failure mode is a date swap — `binder_effective_date` and `binder_expiration_date` are transposed, which produces a negative `binder_duration` and triggers an ERROR invariant. This is a known pipeline failure mode with an observed swap rate. A binder duration warning (> 180 days) requires manual review before committing, as it typically indicates a parse error rather than a legitimately long binder period.

---

## 6. Normalization rules

All normalizations are applied before comparison. They do not fix incorrect values — they only collapse equivalent representations into a canonical form.

**Date normalization** (`framework/normalizers.normalize_date`):
- `YYYY-MM-DD` → pass through unchanged
- `MM/DD/YYYY` → `YYYY-MM-DD` (e.g., `03/01/2025` → `2025-03-01`)
- `DD/MM/YYYY` → `YYYY-MM-DD` (e.g., `25/06/2025` → `2025-06-25`)
- Ambiguous slash-separated dates where the first segment is ≤ 12: treated as `MM/DD/YYYY` (US bias). `06/07/2025` → `2025-06-07` (June 7, not July 6).
- Whitespace stripped before parsing.

**Amount normalization** (`framework/normalizers.normalize_amount`):
- All values rounded to 2 decimal places before comparison.
- `$1,234,567.891` → `1234567.89`. This prevents floating-point representation differences from causing false mismatches.

**String normalization** (`framework/normalizers.normalize_string`):
- Collapse consecutive whitespace to a single space.
- Strip leading and trailing whitespace.
- Applied before case-folding in fuzzy comparison.

**Carrier name normalization** (`custom_rules/carrier_names.normalize_carrier`):
- Lookup is case-insensitive. Result is the canonical legal entity name.

| Input variant | Canonical form |
|---|---|
| "The Hartford", "Hartford" | `Hartford Financial Services` |
| "Travelers", "Travelers Insurance" | `The Travelers Indemnity Company` |
| "Zurich", "Zurich North America" | `Zurich Insurance` |
| "Nationwide", "Nationwide Mutual" | `Nationwide Insurance` |
| "Liberty Mutual" | `Liberty Mutual Fire Insurance Company` |
| "Chubb" | `Chubb Insurance` |

Unknown carrier names (not in the lookup table) pass through unchanged and are compared as fuzzy strings.

---

## 7. Handling non-determinism

The pipeline is non-deterministic at a given temperature. A single extraction result is not sufficient to determine whether a bug is systematic or a one-off. The framework runs **20 extractions per document** using sequential seeds (42000–42019) and computes per-field accuracy as the fraction of runs in which that field matched.

| Accuracy across 20 runs | Classification | Recommended action |
|---|---|---|
| > 90% | Healthy | No action required |
| 60–90% | Intermittent issue | Investigate; do not block deploy unilaterally |
| < 60% | **Systematic bug** | Block deploy; file ticket |

The 60% threshold is a hard CI gate. A field that fails in more than 8 of 20 runs is considered unreliable.

**Variance vs. bias:** A field with high variance shows mismatches that are randomly distributed around the correct value (some runs over, some under, no consistent direction). A field with bias shows mismatches that are consistently offset in one direction across all 20 seeds — for example, every extraction of `sov_pacific_realty` produces amounts approximately 10.5% below ground truth. Variance is noise; bias is a systematic model or post-processor bug. Distinguishing them requires looking at the distribution of actual values across seeds, not just the pass rate.

---

## 8. Partial, disputed, and missing ground truth

**Partial — `sov_acme_properties`, `square_footage` of property at 120 River Road:**  
The source SOV states "see attached schedule" for this property's square footage and the attachment was not provided. Ground truth is set to `"unknown"`. The framework skips this field entirely via `skip_if: unknown` — it is neither scored nor counted toward accuracy. The model's output for this field is still logged for observability purposes. If the pipeline returns a value, treat it as unverified and route for human confirmation before use.

**Disputed — `sov_keystone_reit`, `total_tiv`:**  
Two valid interpretations exist: $92.4M (primary label, excluding a Toronto branch denominated in CAD) and $96.6M (alt label, including the Toronto branch at face value in USD). Both labels live in the framework — primary in `ground_truth/`, alternate in `ground_truth_alt/`. An extraction is considered correct if it falls within ±5% of **either** label. The evaluation report must document which label was matched for each run. When both labels are within tolerance, the primary label takes precedence for reporting. This case demonstrates that "correct" is sometimes a policy decision, not a factual one.

**Missing — `coi_unlabeled_mystery`:**  
No ground truth is available for this document. Field accuracy cannot be computed. Evaluation relies exclusively on invariant checks (`duplicate_coverage`, `date_ordering`) and output stability across seeds (how often the same coverage structure is returned). Optionally, an LLM-as-judge plausibility check (e.g., DeepEval GEval) can assess whether the extracted values are internally coherent as a COI document. Stability below 80% (coverage structure changes in more than 4 of 20 runs) should be flagged as a confidence concern even without ground truth.

---

## 9. Precision vs. recall

For this pipeline, **precision failures are more dangerous than recall failures.**

A **hallucinated field** (precision failure) means the extraction contains data that does not exist in the source document. The most dangerous example is a phantom coverage line — a `cyber` liability coverage that is not in the original certificate. If this extraction auto-commits, the insured's policy record will show coverage they do not have. This can result in incorrect premium charges or, worse, an uncovered claim being paid out based on a phantom policy entry.

A **missing field** (recall failure) means a real value from the source document was not extracted. For required fields this triggers human review — a human looks at the document and supplies the value. For optional fields it is logged but does not block the pipeline. In both cases the outcome is a delay, not incorrect data being written to production.

The routing logic reflects this asymmetry: any hallucinated field, regardless of confidence or overall accuracy, routes the extraction to human review. A missing required field also routes to review, but the severity is lower because no incorrect data has been produced. The auto-commit path requires zero hallucinations — it is not sufficient to have high overall accuracy if even one field is fabricated.
