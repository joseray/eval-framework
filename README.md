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
