# extracteval

A backend testing framework for LLM-backed document extraction APIs. Calls a `/extract` endpoint, validates JSON responses against ground truth and business rules, runs cross-field invariant checks across N seeds, and produces CI-ready pass/fail reports.

---

## Project structure

```
extracteval/
├── framework/                        # Reusable framework (not tied to any service)
│   ├── api_client.py                 # HTTP client with retry and extract_n_times()
│   ├── assertions.py                 # pytest helpers (assert_field_accuracy, etc.)
│   ├── config.py                     # EvalSettings loaded from YAML + env vars
│   ├── ground_truth.py               # Loads JSON label files (primary + alt/disputed)
│   ├── invariants.py                 # Cross-field consistency checks, registry
│   ├── normalizers.py                # Date/amount/string normalization, registry
│   ├── reporter.py                   # DocumentTestReport: summary, JSON, CI gate
│   └── validators.py                 # Field-by-field comparison with tolerances
│
├── tests/                            # Framework unit tests
│   └── unit/
│       ├── test_invariants.py
│       ├── test_normalizers.py
│       └── test_validators.py
│
└── projects/
    └── docextract/                   # Project: docextract-eval service
        ├── config.yaml               # Service URL, thresholds, field specs per doc type
        ├── conftest.py               # pytest fixtures: api_client, ground_truth, settings
        ├── ground_truth/             # Expected extractions (10 labeled documents)
        ├── ground_truth_alt/         # Disputed labels (sov_keystone_reit)
        ├── custom_rules/
        │   ├── carrier_names.py      # Carrier name canonicalization
        │   └── insurance_invariants.py  # 10 domain-specific invariants
        └── tests/
            ├── test_api_health.py
            ├── test_classification.py
            ├── test_edge_cases.py
            ├── test_extraction_accuracy.py
            ├── test_invariants.py
            ├── test_model_comparison.py
            └── test_reseed_resilience.py
```

---

## Requirements

- Python 3.11+
- The docextract service running locally (Docker container on port 8000)

```bash
pip install pytest httpx pyyaml
```

---

## Usage

### 1. Start the service

```bash
docker run -p 8000:8000 docextract-eval:latest
```

### 2. Run the framework unit tests

These do not require the service to be running.

```bash
pytest tests/ -v
```

### 3. Run the full docextract test suite

```bash
pytest projects/docextract/tests/ -v --tb=short
```

### 4. Run both together

```bash
pytest tests/ projects/docextract/tests/ -v --tb=short
```

### Configuration

The service URL and all thresholds are in [`projects/docextract/config.yaml`](projects/docextract/config.yaml). Any value can be overridden with an environment variable:

| Env var | Default | Description |
|---|---|---|
| `EXTRACTEVAL_BASE_URL` | `http://localhost:8000` | Service endpoint |
| `EXTRACTEVAL_N_RUNS` | `20` | Extractions per document |
| `EXTRACTEVAL_SEED_BASE` | `42000` | First seed (sequential) |
| `EXTRACTEVAL_MIN_FIELD_ACCURACY` | `0.60` | CI gate threshold |
| `EXTRACTEVAL_MAX_REGRESSION_PP` | `10.0` | Max allowed v1→v2 regression |

Example — run against a staging environment with fewer seeds:

```bash
EXTRACTEVAL_BASE_URL=http://staging:8000 EXTRACTEVAL_N_RUNS=5 \
  pytest projects/docextract/tests/test_extraction_accuracy.py -v
```

---

## How it works

Each test calls `api_client.extract_n_times(document_id, model, n, seed_base)`, which fires N deterministic extractions using sequential seeds. Results are validated field-by-field using `validate_extraction()` and aggregated into per-field accuracy rates.

```
seed 42000 → extract → validate → FieldResult[]
seed 42001 → extract → validate → FieldResult[]
...
seed 42019 → extract → validate → FieldResult[]

per-field accuracy = hits / N
CI gate: accuracy ≥ 60% for every field
```

---

## Test suite overview

| Test file | What it checks |
|---|---|
| `test_api_health.py` | Service alive, response schema, 404 on unknown doc |
| `test_extraction_accuracy.py` | Per-field accuracy ≥ 60% over N runs, all 10 labeled docs |
| `test_model_comparison.py` | v2 does not regress > 10pp vs v1 on any field |
| `test_invariants.py` | Cross-field consistency (no ground truth needed), all 11 docs |
| `test_classification.py` | Doc type classified correctly, high-confidence misroutes flagged |
| `test_edge_cases.py` | Partial truth (skip_if), disputed labels (either label OK), unlabeled doc |
| `test_reseed_resilience.py` | Framework survives bug rotation — not overfit to specific doc IDs |

---

## Development with Claude Code

This framework was designed and built entirely through a conversation-driven workflow using [Claude Code](https://claude.ai/code) — Anthropic's CLI for Claude. The goal was not only to produce working code, but to demonstrate a repeatable pattern for building eval infrastructure with an AI assistant.

### How the workflow operated

The session started with a single architectural spec in `CLAUDE.md` that described the full intended design: module responsibilities, public APIs, class signatures, and the test structure for the docextract project. From there, each development phase was driven by a prompt that stated the goal, the constraints, and the expected outcome — no step-by-step instructions, just intent.

```
Prompt → Claude Code reads context → implements → verifies → reports result
```

Claude Code ran all verification steps inline: executed `pytest`, read compiler errors, fixed them, and confirmed tests passed before moving to the next module. The session covered:

1. **Architecture confirmation** — Claude Code read `CLAUDE.md` and returned the implementation order with reasoning before writing a single line of code.
2. **Framework build (4 phases)** — `config.py` → normalizers → validators → invariants → api_client → ground_truth → assertions → reporter → framework unit tests.
3. **Project wiring** — `config.yaml`, `conftest.py`, `custom_rules/`, 7 test files for the docextract suite.
4. **Bug detection verification** — ran the suite, confirmed the 4 controlled failures were detected (TIV bias, construction_type omission, phantom coverage, unit drift). Diagnosed a hallucination gap in the validator (extra nested items not visible to GT-driven iteration) and fixed it.
5. **Namespace collision fix** — two `tests/` packages caused `ModuleNotFoundError` when run together; resolved with `--import-mode=importlib` in `pytest.ini`.
6. **Documentation** — `EVAL_RUBRIC.md`, `TEST_STRATEGY.md`, this README.

### Prompts as a development log

The [`prompts/`](prompts/) directory stores the prompts used for each major deliverable. Each file records the context passed to Claude Code, the constraints, and the expected outcome. This makes the development history reproducible: anyone can re-run the same prompts against a clean repo and arrive at the same framework.

```
prompts/
├── 01_architecture_confirmation.md
├── 02_build_framework.md
└── 05_test_strategy.md
```

### What worked well

- **Spec-first development**: having `CLAUDE.md` as a single authoritative reference meant Claude Code never drifted from the intended design. Corrections were rare.
- **Inline verification**: asking Claude Code to run `pytest` after each phase caught integration issues immediately (the namespace collision, the hallucination gap) rather than at the end.
- **Constraint-driven prompts**: giving hard constraints ($200/day budget, 8-minute CI gate, N=20) forced the implementation to be practical rather than theoretically correct.
- **Iterative doc generation**: `EVAL_RUBRIC.md` and `TEST_STRATEGY.md` were written with full access to the codebase, so every number in those docs (violation rates, cost estimates, CI thresholds) traces back to actual code.

---

## Adding a new project

```bash
# 1. Create project directory
mkdir -p projects/my_service/custom_rules
mkdir -p projects/my_service/tests
mkdir -p projects/my_service/ground_truth

# 2. Create config.yaml pointing at your service
# 3. Add ground truth JSONs
# 4. (Optional) Register custom normalizers and invariants in custom_rules/
# 5. Wire fixtures in conftest.py
# 6. Write tests using framework helpers
pytest projects/my_service/tests/ -v
```

---

## Example test

See the [full example below](#example-test-implementation).

---

## Example test implementation

The following is a real test from the suite. It demonstrates the core pattern:
**run N times → validate each run → aggregate accuracy → assert CI gate**.

```python
# projects/docextract/tests/test_extraction_accuracy.py

import pytest
from framework.validators import validate_extraction, MatchLevel

DOCS_WITH_GT = [
    "sov_acme_properties",
    "sov_pacific_realty",
    # ... 8 more documents
]

PASSING = {MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH}


@pytest.mark.parametrize("document_id", DOCS_WITH_GT)
def test_field_accuracy(api_client, ground_truth, settings, document_id):
    """Every field must match ground truth in at least 60% of N runs."""
    gt = ground_truth.get(document_id)
    field_specs = settings.get_field_specs(gt.doc_type)
    nested_specs = settings.get_nested_specs(gt.doc_type)

    # Fire 20 extractions with seeds 42000–42019
    runs, failures = api_client.extract_n_times(
        document_id,
        model="v1",
        n=settings.n_runs,        # 20
        seed_base=settings.seed_base,  # 42000
    )

    # Aggregate per-field accuracy across all runs
    field_hits: dict[str, list[bool]] = {}
    for run in runs:
        results = validate_extraction(
            run["extraction"],
            gt.extraction,
            field_specs,
            nested_specs,
        )
        for r in results:
            if r.match == MatchLevel.SKIPPED:
                continue
            field_hits.setdefault(r.field_path, []).append(r.match in PASSING)

    accuracy = {fp: sum(h) / len(h) for fp, h in field_hits.items()}
    failing = {fp: acc for fp, acc in accuracy.items() if acc < settings.min_field_accuracy}

    # Print diagnostic output to CI logs
    print(f"\n{document_id} | {len(runs)} runs | {failures} failures")
    for fp, acc in sorted(failing.items(), key=lambda x: x[1]):
        print(f"  FAIL {fp}: {acc:.0%}")

    assert not failing, (
        f"{document_id}: fields below {settings.min_field_accuracy:.0%}:\n"
        + "\n".join(f"  {fp}: {acc:.0%}" for fp, acc in sorted(failing.items()))
    )
```

### What a failure looks like

When a bug is present, pytest prints exactly which fields are failing and by how much:

```
FAILED test_extraction_accuracy.py::test_field_accuracy[sov_pacific_realty]

AssertionError: sov_pacific_realty: fields below 60%:
  total_tiv: 0%
  properties.0.building_value: 0%
  properties.0.total_insured_value: 0%
  ...
```

This output identified a **TIV calibration bias** in the service — all numeric property values for `sov_pacific_realty` were consistently off by a factor of 0.895 across all 20 seeds.

### Field spec that drives the comparison

The test above is data-driven. No hardcoded field names in test code — everything comes from `config.yaml`:

```yaml
field_specs:
  sov:
    fields:
      insured_name:  { match: fuzzy }
      policy_number: { match: exact }
      carrier:       { match: carrier }
      effective_date: { match: date }
      total_tiv:     { match: amount, tolerance: 0.05 }
    nested:
      properties:
        match_by: index
        fields:
          building_value:      { match: amount }
          total_insured_value: { match: amount, tolerance: 0.05 }
          construction_type:   { match: exact }
          square_footage:      { match: amount, skip_if: unknown }
```

Adding a new field to validate requires only a one-line change to `config.yaml`.

---

## Improving extraction quality with DeepEval

[DeepEval](https://github.com/confident-ai/deepeval) is an open-source LLM evaluation framework that provides ready-made metrics for output quality: hallucination detection, faithfulness, answer relevancy, and GEval (a customizable LLM-as-judge metric). It integrates with pytest and produces CI-compatible pass/fail results.

The current extracteval framework detects structural and numeric problems — wrong values, missing fields, invariant violations. DeepEval adds a complementary layer: **semantic plausibility**. It can answer questions like "does this COI extraction make sense as a real certificate of insurance?" even when there is no ground truth to compare against.

### Where DeepEval fills the gaps

| Gap in current framework | DeepEval metric that fills it |
|---|---|
| `coi_unlabeled_mystery` has no GT — invariants run but field values go unchecked | `GEval` plausibility check: does the extraction look like a valid COI? |
| Hallucination detection flags extra fields but can't assess whether extracted text is grounded in the source | `HallucinationMetric`: is each extracted value supported by the source document? |
| `coi_zurich_legacy` phantom `cyber` coverage is caught structurally, but the *text* of the coverage fields isn't verified | `FaithfulnessMetric`: are the coverage descriptions faithful to the raw document text? |
| Model comparison measures accuracy delta but not output coherence (v2 could be 100% accurate but produce malformed prose in notes fields) | `AnswerRelevancyMetric`: are free-text fields relevant to the extraction context? |

### Installation

```bash
pip install deepeval
```

### GEval — semantic plausibility for unlabeled documents

GEval lets you define a custom rubric in natural language. The model grades the extraction against that rubric and returns a score between 0 and 1.

```python
# projects/docextract/tests/test_deepeval_plausibility.py

import pytest
from deepeval import assert_test
from deepeval.metrics import GEvalMetric
from deepeval.test_case import LLMTestCase

COI_RUBRIC = """
You are an insurance document reviewer. Score the following COI extraction on a scale of 0 to 1.

Criteria:
- All coverage types must be real insurance lines (GL, Auto, Workers Comp, Umbrella, etc.)
- Policy numbers must follow recognizable formats (alphanumeric, 6–20 chars)
- Effective dates must precede expiration dates
- Limits must be positive dollar amounts in a plausible range for commercial insurance ($100K–$100M)
- Carrier names must be real insurance companies

Score 1.0 if all criteria are met. Deduct 0.2 for each violated criterion.
"""

coi_plausibility = GEvalMetric(
    name="coi_plausibility",
    criteria=COI_RUBRIC,
    threshold=0.6,
)


def test_unlabeled_mystery_plausibility(api_client):
    """coi_unlabeled_mystery: no GT available — use LLM-as-judge for plausibility."""
    runs, _ = api_client.extract_n_times("coi_unlabeled_mystery", model="v1", n=5, seed_base=42000)

    for run in runs:
        extraction = run["extraction"]
        test_case = LLMTestCase(
            input="Extract structured data from this Certificate of Insurance.",
            actual_output=str(extraction),
        )
        assert_test(test_case, [coi_plausibility])
```

### HallucinationMetric — grounding extracted values in source text

When the raw document text is available alongside the extraction, `HallucinationMetric` verifies that each extracted value is actually present in or inferable from the source.

```python
from deepeval.metrics import HallucinationMetric
from deepeval.test_case import LLMTestCase

hallucination_metric = HallucinationMetric(threshold=0.1)  # allow 10% max hallucination score


def test_extraction_grounded_in_source(api_client, raw_documents):
    """Extracted fields must be grounded in the source document text."""
    response = api_client.extract("coi_zurich_legacy", model="v1", seed=42000)
    extraction = response.json()["extraction"]

    # raw_documents["coi_zurich_legacy"] is the plain-text content of the PDF
    source_text = raw_documents["coi_zurich_legacy"]

    test_case = LLMTestCase(
        input="Extract fields from this Certificate of Insurance.",
        actual_output=str(extraction),
        context=[source_text],  # DeepEval checks actual_output against context
    )
    assert_test(test_case, [hallucination_metric])
```

### Integrating with the existing invariant layer

DeepEval runs as regular pytest tests and can be combined with the existing invariant checks. The recommended pattern is to run invariant checks first (cheap, no LLM cost) and only run DeepEval metrics when invariants pass — avoiding spending on semantically checking extractions that are already structurally broken.

```python
@pytest.mark.parametrize("document_id", UNLABELED_DOCS)
def test_unlabeled_quality_gate(api_client, settings, document_id):
    doc_type = settings.get_document_config(document_id)["doc_type"]
    invariant_names = settings.get_invariants(doc_type)

    runs, _ = api_client.extract_n_times(document_id, "v1", n=5, seed_base=42000)

    for run in runs:
        # Step 1: structural invariants (free)
        inv_results = run_invariants(run["extraction"], invariant_names)
        errors = [r for r in inv_results if r.severity == Severity.ERROR]
        if errors:
            pytest.fail(f"Invariant errors before DeepEval: {errors}")

        # Step 2: semantic plausibility (LLM cost ~$0.01/call)
        test_case = LLMTestCase(
            input=f"Extract structured data from this {doc_type} document.",
            actual_output=str(run["extraction"]),
        )
        assert_test(test_case, [coi_plausibility])
```

### Cost and routing considerations

Each DeepEval GEval call makes 1–2 LLM calls (depending on the metric). At GPT-4o pricing (~$0.01–0.03/call), running 5 seeds on 5 unlabeled documents costs roughly **$0.25–$0.75 per nightly run** — well within the $200/day budget.

For production canary use (100 docs/day at 5% traffic), running GEval on every extraction would cost ~$1–3/day. Sampling the 5 lowest-confidence extractions per day instead brings this to ~$0.10/day.

The recommended integration path:

1. **Phase 1 — nightly only:** run DeepEval on all unlabeled documents as part of the nightly eval (not CI). Gate is informational — log scores, don't block deploy.
2. **Phase 2 — gate on systematic failure:** if plausibility score drops below threshold in >50% of nightly runs for any document, trigger a Slack alert and require QA sign-off before the next deploy.
3. **Phase 3 — canary sampling:** route the 5 lowest-confidence extractions per day through DeepEval as a production quality signal.

```bash
# Install and configure DeepEval (one-time)
pip install deepeval
deepeval login  # connects to Confident AI dashboard for run tracking

# Run only DeepEval tests
pytest projects/docextract/tests/test_deepeval_plausibility.py -v

# Run full suite including DeepEval (nightly profile)
EXTRACTEVAL_N_RUNS=20 pytest tests/ projects/docextract/tests/ -v --tb=short
```
