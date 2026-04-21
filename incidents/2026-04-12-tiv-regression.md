# INCIDENT-2026-04-12 — Pacific Realty TIV regression in nightly eval

> **Status**: investigating — assigned to on-call QA
> **Severity**: SEV-2 (eval gate would block this morning's deploy if held)
> **Reported by**: nightly-eval cron (run `eval-2026-04-12-0312`)

You were paged at **03:14 UTC** on 2026-04-12 with the alert below. The model team wants to ship a hotfix to production today. The CTO wants to know by 11:00 ET whether they can.

Treat this as a real incident. Walk through your investigation in `docs/INCIDENT_RESPONSE.md` (create it). What you'd look at, in what order, what you'd ship as a remediation, and what the post-mortem would say.

---

## The PagerDuty alert

```
[SEV-2] DocExtract nightly eval regression
Suite: production-canary  Run: eval-2026-04-12-0312
Doc: sov_pacific_realty
Metric: total_tiv accuracy (mean over 50 seeded runs)
  baseline (last 14 days):  0.92  ±  0.012
  current run:               0.79
  delta:                    -0.13   (10.9 sigma)

Doc: loss_run_libertymutual
Metric: total_incurred accuracy
  baseline:  0.96
  current:   0.71
  delta:    -0.25   (16.4 sigma)

Action: production deploy gated. ack within 15 min or escalate to model-eng on-call.
```

## Slack thread (#docextract-eval, redacted)

> **@morgan (model-eng)** 03:31 — fyi we cut v2 of the extractor at 02:50 UTC. tagged release `v2.0.3`. shouldn't be in canary yet but check the route weights
>
> **@priya (platform)** 03:34 — canary is at 5% v2 since 02:55. forgot to mention in the deploy channel, sorry
>
> **@morgan** 03:36 — ok well that's it then. v2 has a fix for the pacific TIV thing — i wouldn't expect it to *worsen* the metric. the libertymutual one is news to me
>
> **@dani (qa)** 03:42 — i can repro the libertymutual delta locally on v2 with seed 17. total_incurred jumps from $1.13M to $1.22M and i can't tell why looking at the diff. it's like the negative subrogation amounts are flipping sign during the rollup
>
> **@morgan** 03:48 — oh god. yeah we refactored the totals helper to use abs() because there was a UI complaint about negative numbers showing up. that should not have shipped to extraction
>
> **@priya** 03:51 — should i roll canary back to 100% v1?
>
> **@morgan** 03:53 — yes. but the pacific TIV regression — eval says v2 is *better* on that one. did the canary actually run v2 against pacific?
>
> **@dani** 03:56 — checking. canary routes by doc_type hash. pacific might be hitting v1 still
>
> **@morgan** 04:01 — if pacific is still on v1 then the TIV regression is something else. could be a baseline drift — when did we last refresh the 14-day baseline?
>
> **@dani** 04:05 — last refresh was 04-09. the schedule fixture for pacific got updated 04-10 by @sam but that's the *ground truth* not the service
>
> **@sam (data-ops)** 04:08 — yeah i corrected a $400k typo in property 3's TIV. that's a 1.3% increase to the schedule total. doesn't explain a 13% accuracy drop on its own
>
> **@morgan** 04:11 — wait, did the eval baseline get updated when sam updated the truth? the historical baseline is computed against the *old* truth, the current run against the *new* truth
>
> **@dani** 04:14 — i don't think it does. let me check the eval pipeline
>
> --- thread goes quiet ---

## What's in front of you

- The repo (this codebase). Service is on v1 by default; you can hit v2 via `{"model": "v2"}` on `POST /extract`.
- The nightly eval run output is **not** in this repo. You have the alert above and the Slack thread.
- Two ground-truth labels for `sov_keystone_reit` exist (`data/ground_truth/` and `data/ground_truth_alt/`) — that's a separate disagreement, mentioned for context.
- You can call `POST /admin/reseed-bugs` to redistribute the bug pattern across documents (testing whether eval is overfit to specific doc_ids is part of your job).
- Production handles ~2,000 docs/day across 15 doc types. Eval runs nightly, ~$200/day budget for compute.

## What we want from you

In `docs/INCIDENT_RESPONSE.md`, write up:

1. **Triage in the first 30 minutes** — what do you check, in what order, with what commands or queries.
2. **Diagnosis** — what's actually wrong here? (There may be more than one thing.)
3. **Remediation** — what ships today, what waits.
4. **Post-mortem (1 page)** — what failed in the process, not the code. What would you change so this incident is less likely or less painful next time.
5. **Eval pipeline change proposal** — anything you'd add to your test framework to catch this class of failure earlier.

You don't need to write code to fix the bugs. You're being evaluated on judgment, sequencing, and communication.
