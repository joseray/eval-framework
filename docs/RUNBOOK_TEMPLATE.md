# DocExtract Eval — On-Call Runbook

> Fill in this template (or rewrite). The reader is the on-call engineer at 3am with a page in their hand. They have NOT read your eval framework. Be specific. Include commands.

## When you get paged

| Alert | What it likely means | First check |
| --- | --- | --- |
| `eval-regression: total_tiv accuracy below baseline` | | |
| `eval-regression: hallucinated coverage rate spiked` | | |
| `eval-flake: nightly run timed out` | | |
| `canary: 5xx rate > 5%` | | |
| `canary: latency p99 > 5s` | | |
| `production: confidence calibration drifted` | | |

## First 15 minutes

1. ...
2. ...
3. ...

(What do you check, what do you communicate, who do you tag.)

## Diagnosis flowchart

For an "extraction quality regressed" page, walk the on-call through deciding:

- Is it a model change? (How do you know?)
- Is it a ground-truth change? (How do you know?)
- Is it an eval-pipeline bug? (How do you know?)
- Is it real production-traffic drift? (How do you know?)

## Rollback criteria

When do you roll back the model? When do you wait for a fix-forward? Who has authority to make the call at 3am?

## Common false-positive patterns

What looks like a regression but isn't?

- Baseline staleness after a ground-truth refresh: ...
- Seed unluck on small sample size: ...
- Flake from operational noise (rate-limit hit during the eval run): ...

## Comms templates

Status page:
> ...

Customer-facing if production-impacting:
> ...

Internal Slack update:
> ...

## After the incident

Post-mortem owner, timeline, what artifacts to attach, what action items typically come out of these.

## Useful commands

```bash
# Pull recent eval runs
...

# Diff two eval runs
...

# Rerun against a single doc with a fixed seed for repro
curl -X POST http://localhost:8000/extract \
  -H 'Content-Type: application/json' \
  -d '{"document_id":"...","seed":...,"model":"v1"}'

# Rotate the bug registry to test framework generality
curl -X POST 'http://localhost:8000/admin/reseed-bugs?seed=42'
```
