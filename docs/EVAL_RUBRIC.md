# Eval Rubric

This rubric governs how extracted fields are compared against ground truth, when a result auto-commits vs. requires human review, and how the system handles structural inconsistencies, non-determinism, and label uncertainty.

---

## Field Categories and Match Rules

| Field type | Example fields | Match rule | Tolerance / notes |
| --- | --- | --- | --- |
| Identifiers | `policy_number`, `claim_number`, `binder_number`, `endorsement_number` | Case-insensitive exact match after whitespace normalization | 0% tolerance; any divergence is a MISMATCH |
| Party names | `insured_name`, `certificate_holder`, `claimant`, `producer` | Exact after whitespace normalization; fuzzy fallback with Levenshtein ≤ 2 | Handles OCR noise, legal entity suffix variation; dist > 2 is MISMATCH |
| Carrier names | `carrier` | Canonical-map lookup (both sides normalized), then exact compare | Map covers short-forms → legal entity (e.g. "Zurich" → "Zurich Insurance"); unknown names pass through as-is |
| Dates | `effective_date`, `expiration_date`, `date_of_loss`, `policy_effective_date`, `valuation_date`, `binder_effective_date`, `binder_expiration_date`, `endorsement_effective_date` | Normalize both sides to ISO 8601 (YYYY-MM-DD), then exact compare | US bias for ambiguous MM/DD vs DD/MM when both tokens ≤ 12; unparseable → MISMATCH |
| Dollar amounts | `building_value`, `contents_value`, `paid_amount`, `reserved_amount`, `each_occurrence_limit`, `general_aggregate_limit`, `premium_delta` | Relative tolerance | ±3% per-item; zero expected: EXACT if \|extracted\| ≤ 1.0, else MISMATCH |
| Structural / enum | `doc_type`, `construction_type`, `coverage_type`, `status`, `change_type`, `currency`, `occupancy` | Case-insensitive exact match | No tolerance; "frame" ≠ "Frame" after lowercasing is a config error, not acceptable variance |
| Derived aggregates | `total_tiv`, `total_paid`, `total_recoveries`, `total_incurred`, `loss_ratio` | Relative tolerance (looser — rounding accumulates over N items) | ±5%; additionally checked by cross-field invariants (see below) |
| Optional / nullable | `producer`, `year_built`, `square_footage`, `business_income_value`, `old_value`, `binding_authority_reference` | Null/null → EXACT; extracted value where expected null → HALLUCINATED; null where expected non-null → MISSING | Omission rate tracked separately; flag doc for review if MISSING rate > 15% over N runs |

---

## Cross-Field Invariants

These checks run on every extraction regardless of whether ground truth exists. Violations are classified as WARNING (logged, dashboarded) or ERROR (blocks auto-commit, SEV-2 signal if sustained).

| # | Rule | Formula | Level |
|---|------|---------|-------|
| 1 | SOV TIV sum | `\|sum(properties[i].total_insured_value) − total_tiv\| / sum(…) ≤ 0.01` | ERROR if > 0.05; WARNING if 0.01–0.05 |
| 2 | SOV component sum | `\|building_value + contents_value + business_income_value − total_insured_value\| / total_insured_value ≤ 0.05` | WARNING per property |
| 3 | Loss run per-claim incurred | `\|paid_amount + reserved_amount − total_incurred\| / \|total_incurred\| ≤ 0.02` | ERROR; catches v2 abs() sign bug on subrogation claims |
| 4 | Loss run closed reserves | `status == "closed" → reserved_amount == 0` | WARNING; non-zero reserved on closed claim indicates pipeline retained stale state |
| 5 | Loss run total paid sum | `\|sum(claims[i].paid_amount) − total_paid\| / \|total_paid\| ≤ 0.02` | ERROR |
| 6 | Loss run total incurred sum | `\|sum(claims[i].total_incurred) − total_incurred\| / \|total_incurred\| ≤ 0.02` | ERROR; catches abs() bug where aggregate is recomputed with sign-flipped subrogation |
| 7 | Coverage / binder date ordering | `effective_date < expiration_date` for each coverage and the binder itself | ERROR |
| 8 | Binder duration | `(expiration_date − effective_date).days ≤ 180` | WARNING; binders > 180 days likely have a date swap |
| 9 | Unit drift detection | Any `paid_amount` > 50 × median(paid_amounts) | ERROR; flags cents-vs-dollars confusion |

---

## Classification Calibration

The pipeline returns a `doc_type` label plus a confidence score. Mis-classification is a hard failure upstream of field extraction — all downstream metrics are meaningless if the document type is wrong.

| Scenario | Expected confidence | Action |
|----------|-------------------|--------|
| Correct classification | 75–99% depending on doc clarity | Auto-proceed to extraction |
| Correct classification, low confidence (< 70%) | < 70% | Route to human review; do not auto-commit |
| Wrong classification, low confidence | < 70% | Human review; extraction still attempted but flagged |
| **Wrong classification, high confidence (> 90%)** | **Should never happen** | **Most dangerous case — SEV-2 signal; extraction result discarded and quarantined** |

Known live instance: `coi_travelers_umbrella` is routed as `"policy"` at 95–99% confidence. This is a systematic misroute, not noise — the pipeline's classification head has a feature overlap between umbrella COIs and policy documents. Until fixed, all `coi_travelers_umbrella` extractions are evaluated against the `coi` schema (the correct type) and the wrong-but-confident classification counts as a classification ERROR in every run.

The confidence score is not an accuracy proxy. A model can be simultaneously high-confidence and wrong. Alert on: `(predicted_type != correct_type) AND (confidence > 0.90)` — this pattern indicates a systematic calibration failure, not random noise.

---

## Pass / Review / Reject Thresholds

Field accuracy is computed as `(EXACT + APPROX + SKIPPED) / total_scorable_fields`, averaged over N=20 runs per document.

| Tier | Field accuracy | Invariant errors | Action |
|------|---------------|-----------------|--------|
| **Auto-commit** | ≥ 90% | Zero ERRORs in run | Commit to production database; flag WARNING invariants for async review |
| **Human review** | 70–89% | Any WARNING, or ≤ 1 ERROR | Route to QA queue within 4 hours; do not discard |
| **Reject** | < 70% | Any ERROR | Discard extraction; trigger re-extraction with fallback model; page if sustained |

> **Config bug**: `AUTO_COMMIT_THRESHOLD = 5` in `app/config.py` is set far below the 85–90 point where auto-commit is safe. This allows near-random extractions to auto-commit. Fix: raise to 85 (conservative) or 90 (production-safe). Tracked as P0 bug.

The 90% threshold applies to mean accuracy over N=20 runs. A single run at 95% and 19 runs at 30% does not pass. The threshold is on the mean, not the maximum.

---

## Per-Doc-Type Considerations

**SOV**  
Largest field count (≥ 10 fields × number of properties). Total accuracy is dominated by property-level fields. Pay special attention to `construction_type` — it is the highest-omission field in production (observed 38% drop rate in `sov_keystone_reit`). Cross-property TIV reconciliation (Invariant #1) is mandatory; an SOV that sums correctly but has wrong per-property values is more dangerous than one that misses optional fields. Foreign-currency properties (e.g., a Toronto footnote denominated in CAD) must be excluded from USD `total_tiv` or converted; rolling them in at 1:1 is incorrect.

**COI**  
Field count is moderate but hallucination risk is high. A spurious coverage (e.g., phantom "cyber" coverage injected by `coi_zurich_legacy`) is classified as HALLUCINATED with full penalty. An umbrella COI routed as "policy" produces an unusable extraction — downstream systems will attempt to parse coverage fields that do not exist in the policy schema. Coverage date ordering (Invariant #7) is mandatory. Duplicate coverage types within a single COI are flagged as WARNING.

**Loss Run**  
Numeric consistency is the primary quality signal. All three sum invariants (#3, #5, #6) must pass. The v2 `abs()` sign bug manifests here: subrogation recoveries have a negative `total_incurred` by convention; v2 applies `abs()` to the aggregate, breaking the sum check while per-claim values remain correct. Unit drift (cents vs. dollars, Invariant #9) is common in older carriers' export formats. Closed-claim reserved amounts (Invariant #4) are frequently non-zero due to late-close data entry lag.

**Endorsement**  
Smallest field count; primary risk is date parsing (`endorsement_effective_date` appears in many formats including non-ISO). A parseable but semantically wrong date (transposed month/day) scores as MISMATCH; an unparseable date is flagged by Invariant via WARNING. `premium_delta` uses the ±3% per-item tolerance; sign matters (credits are negative).

**Binder**  
Duration is the key invariant: binders are typically 30–60 days; > 180 days is almost always a date swap between effective and expiration (Invariant #8, WARNING). Policy number is often provisional at binder stage and may differ from the final policy number — avoid comparing binder policy_number against a subsequently-issued policy as ground truth.

---

## Normalization Rules

**Carrier names**  
Canonical map lookup before comparison. Both extracted and ground-truth values are normalized. Known mappings include: "Hartford" / "Hartford General" → "Hartford Fire Insurance Company"; "Travelers" / "Travelers Ins" → "Travelers Casualty and Surety Company"; "Zurich" / "Zurich NA" / "Zurich North America" → "Zurich Insurance"; "Nationwide" → "Nationwide Mutual Insurance Company"; "Liberty" / "Liberty Mutual" / "LM" → "Liberty Mutual Insurance"; "Chubb" / "Chubb Group" → "Chubb Limited". Unknown strings pass through unchanged — an unknown carrier does not auto-fail, but will not benefit from canonical matching.

**Dates**  
Priority order: ISO 8601 (YYYY-MM-DD or YYYY/MM/DD) → US format (MM/DD/YYYY or MM-DD-YYYY) → EU format (DD/MM/YYYY). For ambiguous dates where both tokens are ≤ 12 (e.g., "03/04/2025"), US bias is applied (March 4th, not April 3rd). This matches production traffic distribution (~70% US-format documents).

**Dollar amounts**  
`round(float(value), 2)` before comparison. Commas, currency symbols ("$", "USD"), and thousands-separators are stripped. Negative values are valid (subrogation recoveries, premium credits). Cross-currency values (e.g., CAD amounts on a USD SOV) are not auto-converted — the pipeline is expected to exclude them or flag them explicitly.

**Whitespace and string normalization**  
Collapse internal whitespace to single spaces; strip leading/trailing whitespace. Do not case-fold identifiers (policy numbers may be case-sensitive) — only fold for party names and structural enums.

**Legacy ACORD layouts**  
ACORD 25 (COI) vs. ACORD 27 (property) vs. modern fillable-PDF ACORD 28 produce different field positions and labels. The extractor must normalize these to the canonical schema. Eval treats the schema field as the source of truth regardless of which ACORD layout produced it — a "general aggregate" from ACORD 25 is the same field as `general_aggregate_limit` in the schema.

---

## Handling Non-Determinism

The pipeline uses a seeded LLM mock with configurable variance. For deterministic CI, noise is disabled (latency, failures, rate-limiting all set to "off"). For production monitoring, variance is present.

**Eval protocol**: Run N=20 extractions per document per model version using seeds `{seed_base + i : i in [0, N)}` where `seed_base = 42000`. Compute per-field accuracy as the fraction of runs where that field passed comparison. Aggregate mean field accuracy as the mean of per-run scores.

**Interpreting variance**:
- A field at 100% accuracy across all runs is deterministically correct — no action needed.
- A field at 60–90% accuracy is **noisy** — review the raw_text for ambiguity; consider adding normalization.
- A field at < 60% accuracy **in the same direction** (always low, never random) indicates a **systematic bug** — triggers a failing test, not just a low metric.
- A field at 0% accuracy is a hard regression — treat as P1 bug.

**Thresholds**: Field-level accuracy < 60% sustained across 20 runs flags a systematic extraction failure. Overall document accuracy < 70% flags a doc-level regression.

**Variance vs. bias**: High variance (sometimes right, sometimes wrong) usually indicates prompting or normalization issues. Zero accuracy (always wrong in the same way) indicates a code bug — a miscalibration constant, a sign error, or a dropped field.

---

## Partial, Disputed, and Missing Ground Truth

**Explicitly unknown ground truth** (e.g., `sov_acme_properties.square_footage = "unknown"`):  
Mark as `SKIPPED` — excluded from accuracy numerator and denominator alike. The field is still extracted and logged for qualitative review; it simply does not contribute to the pass/fail score. This prevents known-uncertain fields from dragging down otherwise strong extractions.

**Two ground-truth labelings that disagree** (e.g., `sov_keystone_reit` total_tiv: primary = $92.4M, alt = $96.6M):  
Report accuracy against both labels. A result passes if it is within tolerance of either label. Log both results — `accuracy_primary` and `accuracy_alt` — and surface the discrepancy in the eval report. Do not silently pick one. The appropriate long-term action is to resolve the labeling disagreement with the source document owner; until then, the alt-label pass is treated as informational, not as a blocker override. For `sov_keystone_reit`, the primary label ($92.4M) is correct per the placement memo (Toronto property excluded); the alt label ($96.6M) reflects a reasonable-but-wrong FX assumption.

**No ground truth at all** (e.g., `coi_unlabeled_mystery`):  
Invariant-only evaluation. Cross-field invariants (#1–#9) can still detect structural inconsistencies — date inversions, sum mismatches, unit drift — without knowing what the correct values are. Classification confidence and `doc_type` are logged. Field accuracy is reported as N/A. This is the evaluation mode for newly ingested document types before labeling is complete.

---

## Precision vs. Recall

For this pipeline, **precision is more important than recall**. A hallucinated field (the model returns a value that is not in the source document) is more dangerous than a missing field (the model returns null where a value exists).

**Why**: Downstream consumers — policy administration systems, claims databases, reinsurance treaties — act on extracted values. A hallucinated coverage limit of $5M that the policy does not actually grant creates legal and financial exposure. A missing `producer` field is an operational inconvenience — a human can look it up — but it does not cause incorrect downstream decisions.

**Scoring implications**:
- HALLUCINATED fields are counted separately (`hallucination_rate`) and surfaced prominently in eval reports.
- An extraction with `hallucination_rate > 0` on a coverage limit or TIV field should not auto-commit regardless of overall field accuracy.
- Missing optional fields (`omission_rate`) are tracked and alerted at > 15% but do not block auto-commit on their own.
- A zero omission rate combined with zero hallucination rate is the gold standard.

**Trade-off acknowledgment**: Optimizing for precision means the model may legitimately leave nullable fields empty when uncertain. This is the correct behavior. Penalizing null outputs too aggressively (requiring non-null for optional fields) will cause the model to hallucinate values to avoid MISSING penalties — the wrong optimization target.

---

## Out of Scope / Known Gaps

- **Multi-page cross-reference validation**: fields that span multiple pages (e.g., an endorsement referencing a base policy number that appears on page 1) are not currently cross-checked. Would require a document graph, not just per-page extraction.
- **Image-only PDFs (OCR layer)**: `ocr_confidence` is present in `SCANNED_CONFIDENCE_WEIGHTS` but eval does not currently model OCR error distributions. Normalization catches common OCR substitutions (0 vs O, 1 vs l) but structured OCR error models are out of scope.
- **FX conversion for multi-currency SOVs**: currently out of scope; foreign-currency rows are expected to be excluded by the extractor. An automated FX lookup at extraction time would allow meaningful comparison.
- **LLM-as-judge for unlabeled documents**: using a stronger model to generate pseudo-ground-truth for `coi_unlabeled_mystery` and similar documents (estimated ~$1/doc) would replace invariant-only evaluation with field-level scoring. Cost and latency make this a nightly-only option.
