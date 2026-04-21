# Prompt 05 — Create TEST_STRATEGY.md

## Request
Create `projects/docextract/docs/TEST_STRATEGY.md` — test strategy for the docextract assessment.
Audience: reviewer looking for evidence of system-level thinking, not just test code.

Read for context: `config.yaml`, all 7 test files in `projects/docextract/tests/`,
`insurance_invariants.py`, `framework/api_client.py`, `framework/assertions.py`.

**Production constraints to respect:**
- 2,000 docs/day, 15 doc types
- $200/day eval compute budget
- 8-minute CI gate
- 50 GT labels/quarter
- Operated by on-call at 3am

**Structure (7 sections):**
1. Test layers — 6-row table (Unit / Schema+Contract / Integration / Eval / Canary / Production monitoring)
   with: what it tests, runs where, owned by, cost/SLA, what it blocks
2. What blocks a deploy — 3 distinct gates (CI, pre-canary, canary promotion) with specific thresholds
3. What does NOT block a deploy — explicit list with rationale
4. Cost model — line-item breakdown against $200/day budget + labeling budget for 50 labels/quarter
5. Model-version comparison protocol — seeds, delta threshold, rollout %, kill switch
6. Trade-offs made — 4 explicit decisions with reasoning (noise OFF, N=20 not N=50, invariants logged not hard-failed, reseed resilience)
7. Open questions — 4 genuine questions: alert false-positive rate, LLM-as-judge cost, GT ownership gap, canary routing hash vs random

Target: 1.5-2 pages. Tables for structured data. Concrete numbers throughout. No placeholder text.

## Outcome
Created `projects/docextract/docs/TEST_STRATEGY.md` with all 7 sections complete.
Key specifics: ~$30/day total cost, $170 reserve budget identified, N=20 vs N=50 justified
statistically, all 4 trade-offs documented, 4 open questions grounded in actual codebase.
