# Test Strategy — DocExtract Pipeline

**Audience:** Reviewer evaluating whether the test design covers the system, not just the code.  
**Constraints:** 2,000 docs/day across 15 doc types · $200/day eval compute · 8-minute CI gate · 50 GT labels/quarter · operated by on-call at 3am.

---

## 1. Test layers

| Layer | What it tests | Runs where | Owned by | Cost / SLA | What it blocks |
|---|---|---|---|---|---|
| **Unit** | Normalizers (date/amount/string), comparators (validate_exact, validate_amount, 100x drift), invariant logic in isolation | CI on every PR | QA eng | ~$0 / <30s | Merge |
| **Schema / contract** | Pydantic response shape (`classification`, `extraction`, `metadata` all present), `/config` returns expected keys, `/health` returns `{"status": "ok"}`, 404 on unknown `document_id` | CI on every PR | QA eng | ~$0 / <30s | Merge |
| **Integration** | `/extract` returns 200 for all 11 documents, operational noise toggles work (latency / 5xx / rate-limit ON and OFF), retry logic handles 429 and 503 correctly | CI on every PR | QA eng | ~$0 / <2min | Merge |
| **Eval (offline, golden dataset)** | Field accuracy per document over N=20 runs, invariant violation rates, hallucination rate, model comparison v1 vs v2 (assert no regression >10pp), reseed resilience | Nightly cron + pre-canary gate | QA eng | ~$15–30/run / <8min | Deploy to canary |
| **Canary (online, prod sample)** | Same eval metrics on 5–10% of live traffic; latency p99; 5xx rate; confidence distribution vs baseline | Continuous on canary slice | Platform eng + QA | ~$20/day | Promote canary to 100% |
| **Production monitoring** | Confidence distribution drift, auto-commit rate, human-review-override rate, per-field anomaly detection (3σ from 30-day baseline) | Continuous dashboards + pager | Platform eng | ~$10/day | Nothing — triggers pager on regression |

For implementation of the offline eval layer, see `framework/` and `projects/docextract/tests/`.

---

## 2. What blocks a deploy

**Every PR — CI gate (<2 min):**  
All unit, schema, and integration tests must pass at 100%. This covers `tests/unit/`, `test_api_health.py`, and the smoke-level assertions in `test_edge_cases.py`. Any failure blocks the merge. No exceptions.

**Model version change — pre-canary gate (<8 min):**  
Full eval suite runs against all 10 labeled documents with N=20 seeds for both models. Requirements: no field regresses >10pp (`assert_no_regression`, threshold from `config.yaml`), zero new ERROR-level invariant violations, classification accuracy ≥80% on all documents with ground truth, hallucination rate <10%. Implemented in `test_extraction_accuracy.py`, `test_model_comparison.py`, `test_invariants.py`, and `test_classification.py`.

**Canary promotion (24h observation window):**  
After 24 hours at 5% traffic: no eval metric regresses >5pp vs the pre-canary baseline, latency p99 <5s, 5xx rate <2%. Canary is gated on real traffic, not seeds — this catches distribution shift that the golden dataset does not.

**Statistical note:** With N=20, the framework detects a 10pp accuracy difference at ~95% confidence. Detecting 5pp differences reliably requires N=50. Nightly runs use N=50 for finer signal; CI runs use N=20 to stay within the 8-minute gate.

---

## 3. What does NOT block a deploy

The following go to dashboards and weekly review, never to a deploy gate:

- WARNING-level invariant violations (e.g., binder duration >180 days) — logged, not fatal
- Omission of optional fields (`producer`, `year_built`, `loss_ratio`) below 15% rate
- Classification confidence fluctuation within ±5 points of the 30-day baseline
- Per-document regressions <5pp on a single field — within the noise of N=20 seeds
- `coi_travelers_umbrella` classifying as `"policy"` — documented known misroute, tracked separately, does not gate other documents

These are reviewed weekly by the QA team. Elevating them to a deploy gate would produce alert fatigue and cause teams to suppress the monitoring rather than fix the underlying issues.

---

## 4. Cost model

**Nightly eval (N=20, both models):**

| Item | Calculation | Cost |
|---|---|---|
| 11 docs × 20 runs × 2 models | 440 `/extract` calls (in-process, no LLM) | ~$0 today |
| 440 calls against real GPT-4o endpoint | 440 × ~$0.03/call | ~$13/night |
| Nightly CI compute (runner time) | ~25 min wall clock | ~$2/night |

**CI runs (per PR):**  
~50 PRs/week × 11 docs × 5 runs (smoke only) = 2,750 calls/week → ~$12/week (~$1.70/day).

**Canary monitoring:**  
5% of 2,000 docs/day = 100 docs/day at real LLM cost → ~$3/day.

**Total: ~$30/day.** The remaining $170/day is reserved for: ad-hoc model comparisons during model evaluation sprints, scaling from 5 to 15 doc types (estimated 3× cost increase), and quarterly golden dataset refresh runs (N=50 per document).

**Labeling budget — 50 labels/quarter:**  
Prioritization order: (1) new doc types with zero ground truth (blocking new coverage entirely), (2) document types where model accuracy is lowest (highest ROI per label), (3) highest production volume documents (most impact on auto-commit rate). At 50 labels/quarter, full coverage of 15 doc types at 3–4 labels each takes approximately 3 quarters. Each quarter, rotate which existing documents receive fresh labels to detect ground truth drift (source documents evolve — label staleness is a real failure mode, and was the root cause of the Pacific Realty SEV-2).

---

## 5. Model-version comparison protocol

1. Run both models with identical seeds (`seed_base=42000`, N=20) via `api_client.extract_n_times()` — see `test_model_comparison.py`.
2. Compute per-field accuracy delta. Any field dropping >10pp fails `assert_no_regression` and blocks promotion.
3. **Acceptable trade-off:** if v2 drops 5pp on field X but improves 15pp on field Y, net improvement may justify shipping. This requires explicit sign-off from QA lead and model engineer, documented in the deploy ticket. The framework logs the full delta table for every field to CI output.
4. **Rollout plan:** 5% canary → 24h hold → 25% → 24h hold → 50% → 24h hold → 100%.
5. **Kill switch:** any eval metric regression >5pp during the rollout triggers automatic rollback to the previous model version. Rollback is a one-line config change, not a code deploy.

---

## 6. Trade-offs made

**Operational noise disabled in CI:** Latency simulation, 5xx injection, and rate limiting are set to `off` via env vars in `conftest.py`. This gives deterministic, fast results in the CI gate. A separate test (not in this suite) should verify that retry logic handles 429 and 503 correctly under controlled injection. That test belongs in the integration layer, not the eval quality gate — mixing them would make the eval flaky.

**N=20, not N=50 in CI:** The 8-minute gate limits sample size. N=20 reliably detects 10pp differences. For finer 5pp detection, nightly runs use N=50. Increasing CI to N=50 would push runtime to ~20 minutes and exceed the gate budget.

**Invariant violations logged, not hard-failed:** `assert_invariants_pass` prints violations but does not call `pytest.fail`. This is intentional — invariant violation *rate* matters, not any single occurrence. The framework tracks violation rates across N runs; a rate >30% is the signal. A single violation in one seed is noise.

**Framework not overfit to document IDs:** `test_reseed_resilience.py` verifies this explicitly by calling `/admin/reseed-bugs`, redistributing behavioral patterns across documents, and confirming the framework still produces valid reports. An eval framework that hardcodes which document carries which bug is brittle and will miss the same bug when it appears in a different document in production.

---

## 7. Open questions for the team

1. **Acceptable false-positive rate for nightly eval alert?** Currently >3σ from the 30-day rolling baseline fires a page. With 15 doc types and ~60 tracked fields, we expect ~3 false positives/night by chance alone (at p=0.05). Consider raising to 5σ or switching to a Bonferroni-corrected threshold to reduce alert fatigue without missing real regressions.

2. **LLM-as-judge for unlabeled documents?** `coi_unlabeled_mystery` currently runs invariant checks only. A DeepEval GEval pass would add semantic plausibility scoring (does this extraction make sense as a COI?) without requiring a hand label. Estimated cost: ~$1/doc. With 100 canary docs/day, that is $100/day — over budget unless sampled. Worth piloting on the 5 lowest-confidence documents per day.

3. **Who owns the ground truth update process?** The current gap: anyone with repo write access can update a ground truth JSON. There is no validation that the new label is consistent with the previous version, no changelog, and no mechanism to invalidate cached eval baselines when a label changes. This gap was the root cause of the Pacific Realty SEV-2. Ground truth updates should go through a lightweight review process (PR + second approver) and automatically trigger a full eval run before merging.

4. **Canary routing: doc-type hash or random?** Routing 5% of traffic by doc-type hash means the same documents always go to canary, creating systematic blind spots for the other 95% of documents in each type. Random routing is more representative but makes individual-extraction debugging harder (you cannot reliably reproduce a canary result). Recommendation: random routing in production, with a debug mode that pins a specific `document_id` to the canary model for investigation.
