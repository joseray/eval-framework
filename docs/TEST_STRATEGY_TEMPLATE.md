# Test Strategy

> Fill in this template (or rewrite). Target 1–2 pages. Reviewers will read this looking for evidence that you've thought about the *system*, not just the test code.

## Layers

For each layer, describe what gets tested, where it runs, who owns it, what it costs, and what failures at that layer block.

| Layer | What it tests | Runs where | Owned by | Cost / SLA | What it blocks |
| --- | --- | --- | --- | --- | --- |
| Unit | | | | | |
| Schema / contract | | | | | |
| Integration | | | | | |
| Eval (offline, against golden dataset) | | | | | |
| Canary (online, sample of prod traffic) | | | | | |
| Production monitoring | | | | | |

## What blocks a deploy

What's the CI gate? Be specific:

- Metric: ...
- Threshold: ...
- Doc-type subset (or all docs): ...
- Sample size requirement: ...
- What if eval is non-deterministic and the metric is right at the threshold?

## What does *not* block a deploy

What is best-effort? What goes into a dashboard but never gates? Why?

## Cost model

You have $200/day for eval compute and 50 ground-truth labels/quarter. How does your strategy stay within that?

- Cost per eval run: ...
- Frequency: ...
- Sampling strategy (run all docs every time, or rotate?): ...
- What gets pre-computed and cached?

## Model-version comparison

When `v2` is proposed for production, what do you run before approving it?

- Comparison protocol: ...
- Sample size / statistical significance: ...
- Decision criteria (when do you ship despite a regression on metric X?): ...
- Rollout plan with kill switch: ...

## Trade-offs you made

What did you deliberately *not* do? Why?

- ...

## Open questions for the team

What would you want to know that you don't know yet?

- ...
