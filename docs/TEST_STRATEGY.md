# Test Strategy

This document describes the testing layers for the DocExtract extraction pipeline, what each layer costs, what it blocks, and how the strategy fits within the $200/day compute budget and 50 ground-truth labels/quarter.

---

## Layers

| Layer | What it tests | Runs where | Owned by | Cost / SLA | What it blocks |
| --- | --- | --- | --- | --- | --- |
| **Unit** | Normalizers (carrier canonical map, date parsing, amount rounding), comparators (MatchLevel logic, tolerance math, Levenshtein), invariant formulas in isolation | CI on every push | QA | < 30 s; free (no API calls) | Merge to main |
| **Schema / contract** | FastAPI request/response shapes, Pydantic validation, required vs. optional fields, enum values, HTTP status codes (200, 400, 422, 503) | CI on every push | QA | < 30 s; free | Merge to main |
| **Integration** | End-to-end `/extract` call through the live TestClient; noise OFF (latency, failures, rate-limiting disabled); verifies that extraction returns parseable JSON with correct top-level structure for each supported doc type | CI on every push | QA | < 2 min; free | Merge to main |
| **Eval (offline, golden dataset)** | Field-level accuracy and cross-field invariants for all 10 labeled documents × 20 seeded runs; model v1 vs. v2 delta; hallucination rate; omission rate; classification accuracy | Nightly cron + pre-canary gate | QA | < 8 min; ~$13/night (200 API calls × $0.065) | Canary deploy (pre-canary gate); nightly failures page on-call |
| **Canary (online, prod traffic sample)** | Real documents from production, routed 5% to new model version; compares live extraction outputs against invariants and classification confidence in real time | Prod cluster, continuous during canary window | Platform + QA | ~$3/day (canary traffic overhead) | Promotion from canary to 100% prod |
| **Production monitoring** | Invariant violation rates, classification confidence distribution, p99 latency, 5xx rate — aggregated over all 2,000 docs/day across 15 doc types | Prod cluster, continuous | Platform | Infra cost only | Triggers PagerDuty; does not gate deploys, gates rollback |

---

## What Blocks a Deploy

**CI gate (merge to main)**  
- All unit, schema/contract, and integration tests must pass: 100% pass rate, < 2 min total.
- No new HALLUCINATED or MISSING results allowed on any of the 10 golden documents in the integration pass (single-seed sanity check, not full eval).

**Pre-canary gate (before canary weight > 0%)**  
- Run full offline eval: all 10 documents in `DOCUMENTS_WITH_GROUND_TRUTH`, N=10 runs per doc (half the nightly N for speed), same seed base.
- Block if: any document's mean field accuracy regresses > 10 percentage points vs. the current production baseline.
- Block if: any new ERROR-level invariant violation appears in any run (was 0 before, > 0 now).
- Must complete in < 8 min.
- Baseline version-locked to a hash of the ground truth files — if ground truth was updated since the baseline was computed, rebuild the baseline first (do not compare against stale baseline).

**Canary promotion (5% → 100%)**  
- Minimum 24-hour canary window.
- Block if: mean field accuracy on canary docs regresses > 5pp vs. v1 over the canary window.
- Block if: p99 extraction latency exceeds 5 seconds (noise-enabled in canary, unlike CI).
- Block if: 5xx error rate exceeds 2% over any 30-minute window.

**Threshold non-determinism**  
If the metric is within 2pp of a gate threshold (e.g., mean accuracy = 79% against an 80% gate), require a second eval run with a different seed base before making a gate decision. Two runs within 2pp of the threshold both failing is sufficient to block; one passing and one failing means human review, not auto-block.

---

## What Does NOT Block a Deploy

The following are logged, dashboarded, and reviewed weekly — but do not stop a deploy:

- **WARNING-level invariant violations** (e.g., component sum discrepancy within 5%, binder duration > 180 days, duplicate coverage types). These indicate data quality issues in the source document, not model failures.
- **Optional field omission rate < 15%** for nullable fields (`producer`, `year_built`, `square_footage`, etc.). High omission on optional fields is noise from ambiguous source documents; it becomes a blocker only if it exceeds 15% per the rubric threshold.
- **Classification confidence ± 5pp** relative to the historical mean. Normal run-to-run variance in LLM confidence scores.
- **Individual document accuracy drops < 5pp** on any single document, if the fleet average holds. One document's variance doesn't represent a systemic regression.
- **`coi_unlabeled_mystery` results** — no ground truth means no accuracy signal. Invariant violations on this doc are dashboarded but not gated.

All of the above appear in the nightly eval report and trigger a Slack notification to `#docextract-eval`. Any item sustained for > 3 consecutive nightly runs gets escalated to the sprint backlog.

---

## Cost Model

**Budget**: $200/day compute; 50 ground-truth labels/quarter.

**Nightly eval run**  
- 10 documents × 20 seeds × 2 models (v1 + v2 comparison) = 400 API calls  
- Plus 40 additional calls for `evaluate_all` single-model sweep = 440 total  
- Cost: 440 × ~$0.03/call = **~$13/night**

**CI (integration + eval smoke)**  
- 10 documents × 2 seeds × 1 model = 20 API calls per push  
- 5 pushes/day × 20 = 100 calls/day → **~$3/day**

**Canary monitoring**  
- 5% of 2,000 docs/day = 100 docs/day  
- Cost: 100 × ~$0.03 = **~$3/day**

**Total**: ~$19/day against a $200/day budget. Leaves significant headroom for ad-hoc investigations, pre-canary gates on release days, and ground-truth labeling runs.

**Ground truth labeling (50/quarter)**  
Prioritization order:
1. New document types not yet in `DOCUMENTS_WITH_GROUND_TRUTH` — unlocks field-level accuracy for documents currently on invariant-only eval.
2. Documents with the lowest current field accuracy (highest return on labeling investment).
3. Highest production volume document types (most exposure if accuracy is wrong).

Labels are not regenerated on existing documents unless a ground truth correction is needed (as with sam's 04-10 fix). When a label is corrected, the corresponding eval baseline is rebuilt before the next nightly run.

**Caching**: the ground truth files are static between label updates. Comparison results (FieldResult lists) for deterministic seeds can be cached keyed by `(doc_id, model, seed, ground_truth_hash)`. Cache hit rate in steady state is high; cache is invalidated when ground truth changes or the service version changes.

---

## Model-Version Comparison

**When v2 is proposed for production:**

**Comparison protocol**  
Same seed base (`seed_base=42000`), same N=20 runs per document. Run both models against all 10 labeled documents. Compare at the document level (mean field accuracy per doc) and the field level (field_accuracy_by_name for each field). Report: v1_accuracy, v2_accuracy, delta, v1_hallucination_rate, v2_hallucination_rate per document.

**Statistical significance**  
N=20 is sufficient to detect a ±10pp accuracy difference at 80% power for a binomial outcome (pass/fail per run). Differences < 5pp on a single document are not statistically distinguishable from noise at N=20 — flag them but do not block on them. For aggregate fleet accuracy, N=20 × 10 documents = 200 outcomes; ±3pp delta is significant at this scale.

**Decision criteria**  
- v2 ships if: no document regresses > 10pp, no new ERROR invariants, and fleet mean accuracy is neutral or positive.
- v2 ships despite a regression on metric X if: the regression is on a known-buggy document where v2 is fixing a different bug, AND a QA lead + model-eng jointly sign off, AND the regressing doc's baseline is rebuilt before promotion.
- v2 is blocked if: any hallucination_rate increases > 2pp on a coverage limit or TIV field. Hallucination on high-stakes numeric fields is a hard block regardless of overall accuracy.

**Rollout plan**  
5% canary → 24h hold → 25% → 24h hold → 50% → 24h hold → 100%. Each step requires no gate failures during the preceding 24h window. A > 5pp accuracy regression at any step triggers automatic rollback to the previous weight. The kill switch is a single config change to canary routing weights — rollback SLA is < 5 minutes.

---

## Trade-offs

**Omitted: dedicated runbook file**  
An operational runbook would repeat ~80% of `docs/INCIDENT_RESPONSE.md`. The incident response doc already covers triage steps, commands, escalation path, and the baseline staleness gotcha. Maintaining two documents with the same information creates drift. If a dedicated runbook becomes necessary (e.g., for an ops team that doesn't read incident post-mortems), it should be auto-generated from the incident response structure, not hand-maintained.

**Omitted: standalone golden dataset strategy document**  
The labeling cost model, prioritization logic, and label-correction protocol are fully covered in the Cost Model section above. A separate doc would duplicate that content. The key policy decisions (50/quarter, prioritization order, invalidation on correction) are in this document and cross-referenced from `EVAL_RUBRIC.md`.

**Noise OFF in CI (latency, failures, rate-limiting disabled)**  
The operational noise toggles are disabled in CI via `conftest.py` (set before app import). This makes tests fast (< 8 min) and deterministic. The trade-off is that CI does not test retry logic, backoff behavior, or rate limit handling. Those behaviors are tested in separate reliability tests (outside this eval harness scope) and are exercised in the canary window, which runs with noise enabled.

**N=20, not N=50**  
N=50 would detect ±6pp differences instead of ±10pp, but takes 2.5× longer and costs 2.5× more per run. The < 8 min CI budget at the current API call rate caps useful N at ~22 for the full document set. N=20 was chosen as a round number that fits comfortably in budget. For the nightly run there is no hard time constraint, but the cost model shows N=20 is sufficient for the bug classes this pipeline exhibits (all known bugs produce > 20pp accuracy deltas, well above the N=20 detection threshold).

---

## Open Questions

- **False-positive rate for nightly alerts**: the alert threshold (10.9σ for Pacific Realty) suggests the 14-day baseline standard deviation is very small. With N=50 seeded runs, a 3σ threshold may fire on normal run-to-run LLM variance. Is 3σ the right threshold, or should it be 5σ to reduce alert fatigue? Needs empirical data from 30+ nightly runs.

- **LLM-as-judge for unlabeled documents**: using a stronger model (e.g., claude-opus-4-7) to generate pseudo-ground-truth for `coi_unlabeled_mystery` would cost approximately $1/doc and replace invariant-only evaluation with field-level scoring for unlabeled documents. Is this acceptable cost given the 50-label/quarter budget constraint, or should unlabeled docs remain invariant-only?

- **Ground truth update process ownership**: sam updated the Pacific Realty ground truth on 04-10 without triggering a baseline rebuild — a process failure, not a technical one. Who is the designated owner for ground truth corrections, and what is the merge-gate protocol? (Suggestion: ground truth changes require a QA sign-off that triggers a baseline rebuild in the same PR pipeline run.)

- **Canary routing strategy**: current routing is by `doc_type` hash, which means all documents of a given type hit the same model version. This means a single bad doc type (e.g., `loss_run`) can get 100% of its canary traffic routed to v2 or 0%, depending on the hash — not a true 5% sample per type. Random per-document routing would give better statistical coverage but complicate reproducibility. Which is the right trade-off for this pipeline?
