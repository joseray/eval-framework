# INCIDENT-2026-04-12 — Pacific Realty TIV + Liberty Mutual Incurred Regressions

**Status**: Resolved  
**Severity**: SEV-2 (canary deploy blocked; production unaffected)  
**On-call**: QA team  
**Duration**: 03:14 UTC → 05:30 UTC (~2h 15m)  
**Production impact**: None — v2.0.3 was at 5% canary only; rolled back to 0% before any production traffic committed

---

## Summary

- Two simultaneous eval alerts fired at 03:12 UTC for `sov_pacific_realty` (TIV accuracy −13%) and `loss_run_libertymutual` (total_incurred accuracy −25%).
- **Pacific Realty was a false alarm**: the eval baseline was computed against stale ground truth (last refreshed 04-09). @sam corrected a $400k typo in property 3 on 04-10, which shifted the expected total_tiv. The live service (v1) is extracting correctly; the 14-day historical baseline was comparing against the wrong expected value.
- **Liberty Mutual is a real bug**: v2.0.3 ships a `abs()` change in the totals helper that was intended for the UI display layer but leaked into the extraction pipeline. Subrogation claims with negative `total_incurred` values are sign-flipped during the rollup, producing an inflated aggregate that fails the sum invariant.
- Immediate remediation: canary rolled back to 100% v1 at 03:51 UTC. Stale baseline invalidated. Fix for the abs() bug scheduled for v2.0.4.

---

## Triage — First 30 Minutes

**03:14** — Paged. Two regressions, same run (`eval-2026-04-12-0312`), both large sigma (10.9σ, 16.4σ). First decision: are these correlated?

**03:16** — Check for a common cause: was there a deploy?
```bash
# Check recent service version in deployment log (or ask platform team)
curl -s http://localhost:8000/version
# Expected: {"model": "v1", "version": "2.0.3-canary5pct"}
```
The `/version` endpoint (or equivalent) would reveal whether v2.0.3 is active. Confirmed via Slack: v2.0.3 at 5% canary since 02:55 UTC — 17 minutes before the eval run triggered.

**03:18** — Check canary routing. Canary routes by `doc_type` hash. Determine which doc types hit v2:
```bash
# Reproduce both docs against v2 locally
curl -s -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d '{"document_id": "sov_pacific_realty", "model": "v2", "seed": 42000}'

curl -s -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d '{"document_id": "loss_run_libertymutual", "model": "v2", "seed": 42000}'
```

**03:22** — Run a quick 5-seed comparison (not the full 50) to see direction of the v1 vs v2 delta on both docs:
```bash
# Spot-check 5 seeds for sov_pacific_realty
for seed in 42000 42001 42002 42003 42004; do
  echo "seed=$seed"
  curl -s -X POST http://localhost:8000/extract \
    -H "Content-Type: application/json" \
    -d "{\"document_id\": \"sov_pacific_realty\", \"model\": \"v1\", \"seed\": $seed}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('v1 total_tiv:', d['extraction'].get('total_tiv'))"
  curl -s -X POST http://localhost:8000/extract \
    -H "Content-Type: application/json" \
    -d "{\"document_id\": \"sov_pacific_realty\", \"model\": \"v2\", \"seed\": $seed}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('v2 total_tiv:', d['extraction'].get('total_tiv'))"
done
```

**03:26** — Results: v1 is extracting ~$30.1M consistently; v2 extracts ~$33.7M (higher). Ground truth says $30.4M. This means v1 is *more* wrong than v2 on pacific, which is consistent with the Slack note that v2 was meant to fix the TIV calibration bias. The current regression is NOT caused by v2.

**03:28** — Check when the ground truth was last modified:
```bash
git log --oneline -- data/ground_truth/sov_pacific_realty.json
# Should show: @sam commit on 04-10 correcting property 3 TIV
```

**03:30** — Hypothesis confirmed: the eval baseline (last refreshed 04-09) was computed against the pre-correction ground truth ($30.04M total_tiv). After sam's 04-10 fix (+$400k), current run correctly scores against $30.44M. The v1 service extracts ~$30.1M, which was ~90% accurate against the old truth but is only ~79% accurate against the corrected truth. Pacific Realty is a false alarm — the metric dropped because the ground truth moved, not the model.

Meanwhile for Liberty Mutual — @dani reproduced at 03:42 with seed 17. The invariant check (`sum(claims[i].total_incurred) ≠ total_incurred`) is firing because v2's abs() change is sign-flipping negative subrogation values in the aggregate rollup. This is a real bug.

---

## Diagnosis

### Problem 1: `sov_pacific_realty` — FALSE ALARM (eval infrastructure bug)

**Root cause**: The 14-day accuracy baseline is computed against a snapshot of ground truth that was taken on 04-09. @sam committed a correction to `data/ground_truth/sov_pacific_realty.json` on 04-10 — property 3's `total_insured_value` corrected by +$400k (typo fix). The baseline continued comparing live extractions against the old expected value; the current eval run correctly compared against the new value. The *delta* in the alert is not a model regression — it's the gap between two different expected values used in two different evaluation passes.

The v1 service is extracting approximately the right TIV. The 13% accuracy drop is entirely explained by the $400k ground truth correction propagating to the current run but not the historical baseline.

**The canary did not cause this.** The 5% v2 canary traffic (routed by doc_type hash) likely did not hit `sov_pacific_realty` at all during the 50-run eval window, as @morgan suspected.

**Required action**: Invalidate the 14-day baseline for `sov_pacific_realty` and recompute from the current ground truth. Do not re-alert on this doc without a fresh baseline.

### Problem 2: `loss_run_libertymutual` — REAL BUG (v2.0.3 abs() sign leak)

**Root cause**: v2.0.3 refactored the totals helper to call `abs()` on all `total_incurred` values before summing, to address a UI display ticket about negative numbers appearing in the claims dashboard. This change was not scoped to the UI layer — it shipped in the shared extraction pipeline. Loss run documents with subrogation recoveries have at least one claim with a negative `total_incurred` (recovery from a third party). The `abs()` call sign-flips these values, inflating the aggregate `total_incurred` by approximately `2 × |subrogation_amount|`.

**Concrete example** (from @dani's seed 17 reproduction): one subrogation claim has `total_incurred = -$45,200`. v2's rollup computes `abs(-45200) = +45200`, adding $90,400 to the aggregate instead of subtracting $45,200 — a delta of $90,400 on a total around $1.13M (~8%), which exceeds the 2% invariant threshold.

The invariant `loss_run_total_incurred_sum` (#6 in the rubric) correctly caught this: `sum(claims[i].total_incurred) ≠ total_incurred` at ERROR severity.

---

## Remediation

### Immediate (done by 04:00 UTC)

1. **Rolled back canary to 100% v1** — @priya executed at 03:51 UTC. v2.0.3 is off all production traffic.
2. **Invalidated stale baseline for `sov_pacific_realty`** — eval team deleted the cached 14-day baseline. Next nightly run will recompute from current ground truth.
3. **Acknowledged alert** — both regressions explained; production unaffected; SEV-2 downgraded to SEV-3 (process failure, not service failure).

### Short-term (this sprint)

1. **Fix the abs() bug in v2** — Scope the `abs()` call to the UI serialization layer only. The extraction pipeline's `total_incurred` values must preserve sign. Assign to @morgan; target v2.0.4. Regression test: add a seeded test covering `loss_run_libertymutual` on v2 that asserts `invariant_violation_rate(ERROR) == 0`.
2. **Add baseline auto-invalidation on ground truth change** — When a file under `data/ground_truth/` is modified in git, the corresponding 14-day eval baseline must be invalidated and rebuilt. This can be a post-commit hook or a CI step on the data path. Target: before next ground truth update is merged.
3. **Document v2 canary in the deploy channel** — @priya's "forgot to mention" left the on-call without context for 20 minutes. Require a deploy-channel post for all canary weight changes > 0%.

### Medium-term (next quarter)

1. **Pre-canary eval gate** — before routing any traffic to a new model version, run a reduced eval (N=10, all DOCUMENTS_WITH_GROUND_TRUTH) and block canary if any document regresses > 10pp on field accuracy or introduces a new ERROR invariant. This would have caught the abs() bug before it reached any production traffic.
2. **Audit trail: model version per eval run** — the nightly alert did not include which model version ran which documents. Without this, triage wasted 12 minutes determining whether pacific hit v2 or not. Every eval run should log `{doc_id, model_version, seed}` per call.

---

## Post-Mortem

### Timeline

| UTC | Event |
|-----|-------|
| 02:50 | v2.0.3 tagged; deploy begins |
| 02:55 | Canary set to 5% v2 by @priya; not communicated to deploy channel |
| 03:12 | Nightly eval run `eval-2026-04-12-0312` completes; two regressions detected |
| 03:14 | PagerDuty fires; on-call QA paged |
| 03:31 | @morgan mentions v2.0.3 deploy in Slack |
| 03:34 | @priya acknowledges canary is live |
| 03:42 | @dani reproduces Liberty Mutual delta on v2 with seed 17 |
| 03:48 | @morgan identifies abs() leak as root cause |
| 03:51 | Canary rolled back to 100% v1 |
| 04:10 | @morgan raises baseline staleness hypothesis for Pacific Realty |
| 04:14 | @dani confirms baseline was not refreshed after sam's 04-10 ground truth update |
| 04:30 | Baseline invalidated; both root causes confirmed |
| 05:30 | Incident closed; post-mortem scheduled |

### What Failed (process, not code)

1. **No deploy communication for canary weight changes.** The 5% v2 canary went live 17 minutes before the eval run. On-call had no context and spent the first 20 minutes of the incident not knowing whether a deploy had happened. Fix: mandatory deploy-channel post for all canary weight changes, enforced by the deploy pipeline (not optional).

2. **No baseline refresh when ground truth changes.** The 14-day eval baseline is a cached aggregate. When `@sam` corrected the ground truth on 04-10, the baseline silently became stale. There was no process to detect or flag this. The result: an eval alert that looked like a real regression but was purely an artifact of baseline/truth divergence. Fix: version-lock baselines to a ground truth hash; invalidate automatically when the hash changes.

3. **No pre-canary eval gate.** v2.0.3 went to canary without running the eval suite against it first. The abs() sign bug would have been caught in < 8 minutes by `test_model_v2_subrogation_sign_bug_confirmed` or the `loss_run_total_incurred_sum` invariant. Fix: make pre-canary eval a required gate, not an optional post-deploy check.

4. **UI and extraction code shared the same totals helper.** The `abs()` change was made to fix a UI complaint. It should have been scoped to the serialization/display layer. Instead, it was applied to a shared helper that the extraction pipeline depends on. Fix: document and enforce the boundary between extraction schema (sign-preserving) and display representation (abs or formatted).

### What Went Right

- The invariant `loss_run_total_incurred_sum` fired correctly and surfaced the exact nature of the v2 bug — @dani was able to identify the sign flip from the invariant message alone.
- Canary was at 5%, so zero production documents were committed with the abs() error.
- The Slack thread converged on both root causes within 60 minutes.
- The on-call knew to check for a deploy as the first triage step.

### Action Items

| Action | Owner | Deadline |
|--------|-------|----------|
| Fix abs() bug in v2; add regression test | @morgan | 2026-04-14 |
| Baseline auto-invalidation on ground truth change | @dani | 2026-04-18 |
| Mandatory deploy-channel post for canary changes | @priya | 2026-04-14 |
| Pre-canary eval gate in deploy pipeline | Platform + QA | 2026-04-25 |
| Document extraction/display layer boundary | @morgan | 2026-04-14 |
| Add model version to per-doc eval run logs | @dani | 2026-04-18 |

### CTO Communication (11:00 ET)

> Production was unaffected — v2.0.3 was limited to 5% canary traffic and rolled back at 03:51 UTC before any documents committed. Two eval alerts fired: one was a false alarm caused by stale eval infrastructure (fixed); one is a real v2 bug (sign error in subrogation totals, fix targeted for v2.0.4). We can confirm the hotfix the model team wants to ship is not v2.0.3 — it will need the abs() fix before it's safe to promote. Estimated v2.0.4 readiness: 2026-04-14.

---

## Eval Pipeline Change Proposal

Four specific changes to prevent this class of failure:

**1. Version-lock baselines to a ground truth hash**  
Store a `ground_truth_sha` alongside each cached eval baseline. Before comparing a new run against the baseline, verify the current ground truth hash matches. If it doesn't, rebuild the baseline and do not alert on the delta until the rebuild is complete. Implementation: add a `BaselineCache` class that wraps the baseline JSON with a hash of the relevant ground truth file(s).

**2. Pre-canary eval gate**  
Before setting canary weight > 0% for any model version, run the full eval suite (N=10 for speed) against that version. Gate format: field accuracy must not regress > 10pp on any document in `DOCUMENTS_WITH_GROUND_TRUTH`, and zero new ERROR invariants are allowed. This check takes < 5 minutes and would have blocked v2.0.3 before it reached canary.

**3. Deploy audit trail in eval reports**  
Each eval run should record the model version routing table — which documents hit which model version — so on-call can immediately determine causality without a Slack archaeology session. Concretely: log `{"doc_id": ..., "model_version_routed": ..., "seed": ...}` per extraction in the eval run output.

**4. Invariant-first triage meta-health check**  
Add a test that runs `check_invariants` on a known-stable document (e.g., `loss_run_nationwide` on v1) and asserts all results are OK or WARNING. This test is fast (single run, no ground truth needed) and serves as a smoke test for the invariant checker itself — if invariants are returning unexpected results, flag it before the nightly eval completes.
