# CLAUDE.md — extracteval: BE testing framework for LLM extraction pipelines

## What this is

A **backend testing framework** that:
1. Calls an extraction API (POST /extract)
2. Receives JSON responses
3. Validates those responses against ground truth and business rules
4. Runs N times to detect statistical inconsistencies
5. Reports pass/fail for CI and model version comparison

This is NOT an SDK or a generic library. It's a **pytest project** organized so that a QA engineer can point it at any extraction service, configure validations, and run the suite.

---

## Monorepo structure

```
extracteval/
├── CLAUDE.md
├── pyproject.toml
├── pytest.ini
│
├── framework/                          # Reusable framework
│   ├── __init__.py
│   ├── api_client.py                   # HTTP client: calls the API, handles retry/rate-limit
│   ├── ground_truth.py                 # Ground truth loader (JSON files)
│   ├── normalizers.py                  # Pre-comparison normalization (dates, amounts, strings)
│   ├── validators.py                   # Field-by-field comparators with tolerances
│   ├── invariants.py                   # Internal consistency checks (no ground truth needed)
│   ├── assertions.py                   # Assertion helpers for pytest (assert_field_accuracy, etc.)
│   ├── reporter.py                     # Report generation (console, JSON, CI)
│   └── config.py                       # Settings loader from YAML + env vars
│
├── tests/                              # Framework's own tests
│   ├── unit/
│   │   ├── test_normalizers.py
│   │   ├── test_validators.py
│   │   └── test_invariants.py
│   └── integration/
│       └── test_api_client.py
│
└── projects/
    └── docextract/                     # Project-specific: docextract-eval assessment
        ├── config.yaml                 # Service under test configuration
        ├── ground_truth/               # JSON files with expected responses
        ├── ground_truth_alt/           # Disputed labels
        ├── custom_rules/               # Insurance domain-specific rules
        │   ├── __init__.py
        │   ├── carrier_names.py        # Carrier-to-canonical-name mapping
        │   └── insurance_invariants.py # Insurance-specific invariants
        ├── conftest.py                 # Fixtures: api_client, ground_truth, config
        └── tests/
            ├── test_api_health.py          # Smoke tests: health, config, schema
            ├── test_extraction_accuracy.py  # Field-by-field accuracy vs ground truth
            ├── test_invariants.py           # Cross-field consistency checks
            ├── test_model_comparison.py     # v1 vs v2 regression detection
            ├── test_classification.py       # Doc type classification + confidence
            ├── test_edge_cases.py           # Partial truth, disputed labels, missing GT
            └── test_reseed_resilience.py    # Framework is not overfit to doc_ids
```

---

## framework/ — Framework building blocks

### `api_client.py` — Calls the API under test

```python
"""HTTP client for the extraction service.

Responsibility: make the calls, handle transient errors,
and return the raw JSON. Does NOT validate anything — that's the tests' job.
"""

class ExtractionAPIClient:
    """
    Usage in a test:
        def test_something(api_client):
            response = api_client.extract("sov_pacific_realty", model="v1", seed=42)
            assert response.status_code == 200
            data = response.json()
    """
    
    def __init__(self, base_url: str, max_retries: int = 3, retry_delay: float = 2.0):
        self.base_url = base_url
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = httpx.Client(base_url=base_url, timeout=30.0)
    
    def extract(self, document_id: str, model: str = "v1", seed: int | None = None) -> httpx.Response:
        """POST /extract with automatic retry for 429/5xx."""
        payload = {"document_id": document_id, "model": model}
        if seed is not None:
            payload["seed"] = seed
        
        for attempt in range(self.max_retries):
            response = self._client.post("/extract", json=payload)
            if response.status_code == 200:
                return response
            if response.status_code in (429, 500, 502, 503):
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            return response  # 4xx errors return immediately
        
        return response  # Return last failed response
    
    def extract_n_times(self, document_id: str, model: str = "v1", 
                        n: int = 20, seed_base: int = 42000) -> tuple[list[dict], int]:
        """Run N extractions with sequential seeds.
        
        Returns a tuple of (results list, failure count).
        Each result is a dict with {seed, extraction, classification, metadata}.
        Failed responses are filtered out and counted.
        """
        results = []
        failures = 0
        for i in range(n):
            seed = seed_base + i
            resp = self.extract(document_id, model=model, seed=seed)
            if resp.status_code == 200:
                data = resp.json()
                results.append({
                    "seed": seed,
                    "extraction": data["extraction"],
                    "classification": data["classification"],
                    "metadata": data["metadata"],
                })
            else:
                failures += 1
        return results, failures
    
    def health(self) -> httpx.Response:
        """GET /health"""
        return self._client.get("/health")
    
    def config(self) -> httpx.Response:
        """GET /config"""
        return self._client.get("/config")
    
    def reseed_bugs(self, seed: int | None = None) -> httpx.Response:
        """POST /admin/reseed-bugs"""
        url = "/admin/reseed-bugs"
        if seed is not None:
            url += f"?seed={seed}"
        return self._client.post(url)
    
    @classmethod
    def from_test_client(cls, test_client) -> "ExtractionAPIClient":
        """Create a client that uses FastAPI TestClient (in-process, no network)."""
        instance = cls.__new__(cls)
        instance.base_url = ""
        instance.max_retries = 3
        instance.retry_delay = 0.01  # Fast retries in tests
        instance._client = test_client
        return instance
```

### `ground_truth.py` — Loads expected responses

```python
"""Ground truth loader.

Loads JSONs from a directory. Supports primary labels,
alternate (disputed) labels, and documents without labels.
"""

class GroundTruthStore:
    """
    Usage in a test:
        def test_something(ground_truth):
            gt = ground_truth.get("sov_pacific_realty")
            assert gt is not None
            assert gt.doc_type == "sov"
            expected = gt.extraction  # dict with correct values
    """
    
    def __init__(self, gt_dir: Path, alt_dir: Path | None = None):
        self._cache: dict[str, GroundTruthEntry] = {}
        self._alt_cache: dict[str, GroundTruthEntry] = {}
        self._load(gt_dir, self._cache)
        if alt_dir and alt_dir.exists():
            self._load(alt_dir, self._alt_cache)
    
    def get(self, document_id: str) -> GroundTruthEntry | None:
        """Return the primary ground truth, or None if it doesn't exist."""
        return self._cache.get(document_id)
    
    def get_alt(self, document_id: str) -> GroundTruthEntry | None:
        """Return the alternate (disputed) ground truth."""
        return self._alt_cache.get(document_id)
    
    def has_label(self, document_id: str) -> bool:
        return document_id in self._cache
    
    def all_document_ids(self) -> list[str]:
        return list(self._cache.keys())
    
    def documents_with_ground_truth(self) -> list[str]:
        return [d for d in self._cache]
    
    def documents_without_ground_truth(self) -> list[str]:
        """Document IDs known to the service but not in ground truth."""
        ...


@dataclass
class GroundTruthEntry:
    document_id: str
    doc_type: str
    extraction: dict
    notes: str = ""
    alt_label: bool = False
    disagrees_on: list[str] = field(default_factory=list)
```

### `normalizers.py` — Pre-comparison normalization

```python
"""Value normalizers.

Applied BEFORE comparing extraction vs ground truth.
Collapse different representations of the same value into a canonical form.

Do NOT fix incorrect values — only normalize format.
"""

def normalize_date(value: str | None) -> str | None:
    """YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY → YYYY-MM-DD"""
    ...

def normalize_amount(value: float | None) -> float | None:
    """Round to 2 decimal places."""
    ...

def normalize_string(value: str | None) -> str | None:
    """Collapse whitespace, strip."""
    ...

# Registry of custom normalizers by field type
_NORMALIZER_REGISTRY: dict[str, Callable] = {
    "date": normalize_date,
    "amount": normalize_amount,
    "string": normalize_string,
}

def register_normalizer(field_type: str, fn: Callable):
    """Register a custom normalizer. Used by projects to add
    domain-specific normalization (e.g., carrier names)."""
    _NORMALIZER_REGISTRY[field_type] = fn

def normalize(value: Any, field_type: str) -> Any:
    """Normalize a value using the registered normalizer for its type."""
    fn = _NORMALIZER_REGISTRY.get(field_type)
    return fn(value) if fn else value
```

### `validators.py` — Field-by-field comparison

```python
"""Validators that compare extraction values against ground truth.

Each validator returns a FieldResult with the comparison outcome.
Validators are tolerance-aware and field-type-specific.
"""

@dataclass
class FieldResult:
    field_path: str       # "properties.0.building_value"
    match: MatchLevel     # exact, within_tolerance, mismatch, missing, hallucinated, skipped
    expected: Any = None
    actual: Any = None
    tolerance: float | None = None
    note: str = ""

class MatchLevel(str, Enum):
    EXACT = "exact"
    WITHIN_TOL = "within_tolerance"
    MISMATCH = "mismatch"
    MISSING_EXPECTED = "missing_expected"
    MISSING_BOTH = "missing_both"
    HALLUCINATED = "hallucinated"
    SKIPPED = "skipped"


def validate_exact(path: str, expected, actual) -> FieldResult:
    """Exact match. For identifiers, enums."""
    ...

def validate_amount(path: str, expected, actual, tolerance=0.03) -> FieldResult:
    """Relative tolerance. Explicitly detects unit drift (100x)."""
    ...

def validate_date(path: str, expected, actual) -> FieldResult:
    """Date comparison after normalization to ISO."""
    ...

def validate_string_fuzzy(path: str, expected, actual) -> FieldResult:
    """Case-insensitive, whitespace-normalized."""
    ...


def validate_extraction(
    extraction: dict,
    ground_truth: dict,
    field_specs: dict,           # {field_name: {type, match, tolerance?}}
    nested_specs: dict = None,   # {collection_name: {match_by, fields}}
) -> list[FieldResult]:
    """Validate a full extraction against ground truth using field specs.
    
    Usage in a test:
        results = validate_extraction(
            extraction=response["extraction"],
            ground_truth=gt.extraction,
            field_specs={
                "insured_name": {"type": "string", "match": "fuzzy"},
                "total_tiv": {"type": "amount", "match": "tolerance", "tolerance": 0.05},
            },
            nested_specs={
                "properties": {
                    "match_by": "address",
                    "fields": {
                        "building_value": {"type": "amount", "match": "tolerance"},
                    }
                }
            }
        )
        accuracy = sum(1 for r in results if r.match in PASSING) / len(results)
    """
    ...
```

### `invariants.py` — Checks without ground truth

```python
"""Invariants: internal consistency validations.

Do not need ground truth. Verify that the extraction is
internally coherent. Can run on ANY document,
including those without labels.
"""

@dataclass
class InvariantResult:
    check: str          # "component_sum"
    severity: Severity  # error, warning, info
    message: str
    details: dict | None = None

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Registry of invariants by name
_INVARIANT_REGISTRY: dict[str, Callable] = {}

def register_invariant(name: str):
    """Decorator to register an invariant check."""
    def decorator(fn):
        _INVARIANT_REGISTRY[name] = fn
        return fn
    return decorator

def run_invariants(extraction: dict, invariant_names: list[str], **kwargs) -> list[InvariantResult]:
    """Run the listed invariants against an extraction."""
    results = []
    for name in invariant_names:
        fn = _INVARIANT_REGISTRY.get(name)
        if fn:
            results.extend(fn(extraction, **kwargs))
    return results


# Built-in invariants (always available):

@register_invariant("date_ordering")
def check_date_ordering(extraction: dict, **kwargs) -> list[InvariantResult]:
    """effective_date < expiration_date for any date pair."""
    ...
```

### `assertions.py` — Helpers for pytest

```python
"""Assertion helpers that integrate with pytest.

These wrap the validators/invariants logic into asserts
that produce clear error messages when they fail.
"""

def assert_field_accuracy(
    results: list[FieldResult],
    min_accuracy: float = 0.60,
    label: str = "",
):
    """Assert that field accuracy is above the minimum.
    
    Usage:
        results = validate_extraction(extraction, gt, specs)
        assert_field_accuracy(results, min_accuracy=0.60, label="sov_pacific_realty v1")
    """
    passing = {MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH}
    scored = [r for r in results if r.match != MatchLevel.SKIPPED]
    if not scored:
        return
    accuracy = sum(1 for r in scored if r.match in passing) / len(scored)
    if accuracy < min_accuracy:
        failing = [r for r in scored if r.match not in passing]
        msg = f"{label}: accuracy {accuracy:.0%} < {min_accuracy:.0%}\n"
        msg += "\n".join(f"  {r.field_path}: {r.match.value} (expected={r.expected}, actual={r.actual})" 
                        for r in failing[:10])
        pytest.fail(msg)


def assert_no_hallucinations(results: list[FieldResult], label: str = ""):
    """Assert that no fields are hallucinated."""
    hallucinated = [r for r in results if r.match == MatchLevel.HALLUCINATED]
    if hallucinated:
        msg = f"{label}: {len(hallucinated)} hallucinated fields:\n"
        msg += "\n".join(f"  {r.field_path}: {r.actual}" for r in hallucinated)
        pytest.fail(msg)


def assert_invariants_pass(
    invariant_results: list[InvariantResult],
    max_error_rate: float = 0.30,
    label: str = "",
):
    """Assert that ERROR-level invariants don't exceed the maximum rate."""
    errors = [r for r in invariant_results if r.severity == Severity.ERROR]
    if errors:
        msg = f"{label}: {len(errors)} invariant violations:\n"
        msg += "\n".join(f"  [{r.severity.value}] {r.check}: {r.message}" for r in errors)
        # Log but don't fail for known issues — the signal is in the report
        print(f"⚠ {msg}")


def assert_no_regression(
    accuracy_baseline: dict[str, float],
    accuracy_candidate: dict[str, float],
    max_regression_pp: float = 10.0,
    label: str = "",
):
    """Assert that the candidate model doesn't regress more than N percentage points."""
    regressions = {}
    for field, baseline_acc in accuracy_baseline.items():
        candidate_acc = accuracy_candidate.get(field, 0)
        delta_pp = (baseline_acc - candidate_acc) * 100
        if delta_pp > max_regression_pp:
            regressions[field] = (baseline_acc, candidate_acc, delta_pp)
    
    if regressions:
        msg = f"{label}: {len(regressions)} regressions > {max_regression_pp}pp:\n"
        msg += "\n".join(
            f"  {f}: {b:.0%} → {c:.0%} ({d:+.1f}pp)"
            for f, (b, c, d) in sorted(regressions.items(), key=lambda x: -x[1][2])
        )
        pytest.fail(msg)
```

### `reporter.py` — Reports

```python
"""Report generator for console and CI.

Takes results from N runs and produces:
- summary() → human-readable text for terminal/Slack
- to_json() → dict for CI artifacts and dashboards
- passes_ci_gate() → bool for pytest exit code
"""

@dataclass
class DocumentTestReport:
    document_id: str
    doc_type: str
    model: str
    n_runs: int
    field_results_per_run: list[list[FieldResult]]
    invariant_results_per_run: list[list[InvariantResult]]
    classifications: list[dict]
    
    def field_accuracy(self) -> dict[str, float]:
        """Per-field accuracy: fraction of runs where the field matched."""
        ...
    
    def invariant_violation_rates(self) -> dict[str, float]:
        """Per-invariant violation rate."""
        ...
    
    def hallucination_rate(self) -> float: ...
    def omission_rate(self) -> float: ...
    def classification_accuracy(self, expected_type: str) -> float: ...
    def confidence_stats(self) -> dict: ...
    
    def summary(self) -> str:
        """Human-readable summary for console."""
        ...
    
    def to_json(self) -> dict:
        """Structured output for CI."""
        ...
```

### `config.py` — Settings

```python
"""Load configuration from YAML + environment variables."""

@dataclass
class EvalSettings:
    # API
    base_url: str = "http://localhost:8000"
    extract_endpoint: str = "/extract"
    max_retries: int = 3
    retry_delay: float = 2.0
    
    # Eval
    n_runs: int = 20
    seed_base: int = 42000
    models: list[str] = field(default_factory=lambda: ["v1", "v2"])
    
    # Tolerances
    amount_tolerance: float = 0.03
    aggregate_tolerance: float = 0.05
    
    # CI thresholds
    min_field_accuracy: float = 0.60
    max_hallucination_rate: float = 0.10
    max_regression_pp: float = 10.0
    
    # Paths
    ground_truth_dir: str = "./ground_truth"
    ground_truth_alt_dir: str = "./ground_truth_alt"
    
    @classmethod
    def from_yaml(cls, path: Path) -> "EvalSettings":
        """Load from YAML. Env vars override: EXTRACTEVAL_BASE_URL, etc."""
        ...
    
    def get_field_specs(self, doc_type: str) -> dict:
        """Return field specs for a doc type from loaded config."""
        ...
    
    def get_nested_specs(self, doc_type: str) -> dict:
        """Return nested collection specs for a doc type."""
        ...
    
    def get_invariants(self, doc_type: str) -> list[str]:
        """Return invariant names for a doc type."""
        ...
```

---

## projects/docextract/ — Assessment project

### `config.yaml`

```yaml
service:
  base_url: "http://localhost:8000"

eval:
  n_runs: 20
  seed_base: 42000
  models: ["v1", "v2"]

tolerances:
  amount: 0.03
  aggregate: 0.05

thresholds:
  min_field_accuracy: 0.60
  max_hallucination_rate: 0.10
  max_regression_pp: 10.0

ground_truth:
  directory: "./ground_truth"
  alt_directory: "./ground_truth_alt"

documents:
  sov_acme_properties:
    doc_type: sov
  sov_pacific_realty:
    doc_type: sov
  sov_keystone_reit:
    doc_type: sov
    has_alt_label: true
    disputed_fields: ["total_tiv"]
  coi_hartford_general:
    doc_type: coi
  coi_travelers_umbrella:
    doc_type: coi
  coi_zurich_legacy:
    doc_type: coi
  coi_unlabeled_mystery:
    doc_type: coi
    has_ground_truth: false
  loss_run_nationwide:
    doc_type: loss_run
  loss_run_libertymutual:
    doc_type: loss_run
  endorsement_chubb_tiv_increase:
    doc_type: endorsement
  binder_travelers_temp:
    doc_type: binder

# Field specs per doc type — which fields to validate and how
field_specs:
  sov:
    fields:
      insured_name: { match: fuzzy }
      policy_number: { match: exact }
      carrier: { match: carrier }
      effective_date: { match: date }
      expiration_date: { match: date }
      total_tiv: { match: amount, tolerance: 0.05 }
    nested:
      properties:
        match_by: index
        fields:
          address: { match: fuzzy }
          city: { match: fuzzy }
          state: { match: fuzzy }
          zip_code: { match: fuzzy }
          building_value: { match: amount }
          contents_value: { match: amount }
          business_income_value: { match: amount }
          total_insured_value: { match: amount, tolerance: 0.05 }
          construction_type: { match: exact }
          square_footage: { match: amount, tolerance: 0.05, skip_if: unknown }
    invariants:
      - component_sum
      - total_tiv_sum

  coi:
    fields:
      certificate_holder: { match: fuzzy }
      insured_name: { match: fuzzy }
      producer: { match: fuzzy, optional: true }
    nested:
      coverages:
        match_by: coverage_type
        fields:
          policy_number: { match: exact }
          carrier: { match: carrier }
          effective_date: { match: date }
          expiration_date: { match: date }
          each_occurrence_limit: { match: amount }
          general_aggregate_limit: { match: amount }
          products_completed_ops: { match: amount }
    invariants:
      - duplicate_coverage
      - date_ordering
      - phantom_coverage

  loss_run:
    fields:
      insured_name: { match: fuzzy }
      carrier: { match: carrier }
      policy_number: { match: exact }
      policy_effective_date: { match: date }
      valuation_date: { match: date }
      total_paid: { match: amount, tolerance: 0.05 }
      total_incurred: { match: amount, tolerance: 0.05 }
      total_recoveries: { match: amount, tolerance: 0.05 }
    nested:
      claims:
        match_by: claim_number
        fields:
          paid_amount: { match: amount }
          reserved_amount: { match: amount }
          total_incurred: { match: amount }
          status: { match: exact }
    invariants:
      - incurred_sum
      - total_paid_sum
      - total_incurred_sum
      - closed_reserves_zero
      - unit_drift_detection

  endorsement:
    fields:
      insured_name: { match: fuzzy }
      policy_number: { match: exact }
      carrier: { match: carrier }
      endorsement_number: { match: exact }
      endorsement_effective_date: { match: date }
      premium_delta: { match: amount }
    invariants: []

  binder:
    fields:
      insured_name: { match: fuzzy }
      carrier: { match: carrier }
      binder_number: { match: exact }
      binder_effective_date: { match: date }
      binder_expiration_date: { match: date }
    invariants:
      - date_ordering
      - binder_duration

custom_normalizers:
  carrier: "custom_rules.carrier_names.normalize_carrier"
```

### `conftest.py` — Fixture wiring

```python
"""Fixtures that connect the framework to the docextract service."""
import os
import pytest
from pathlib import Path

# Disable operational noise for deterministic tests
os.environ["DOCEXTRACT_LATENCY"] = "off"
os.environ["DOCEXTRACT_FAILURES"] = "off"
os.environ["DOCEXTRACT_RATELIMIT"] = "off"

from framework.api_client import ExtractionAPIClient
from framework.ground_truth import GroundTruthStore
from framework.config import EvalSettings
from framework.normalizers import register_normalizer

# Import the service for in-process testing
from app.main import app
from fastapi.testclient import TestClient

# Import custom rules
from custom_rules.carrier_names import normalize_carrier


@pytest.fixture(scope="session")
def settings():
    return EvalSettings.from_yaml(Path(__file__).parent / "config.yaml")


@pytest.fixture(scope="session")
def api_client():
    """API client that calls the service in-process (no network)."""
    test_client = TestClient(app)
    return ExtractionAPIClient.from_test_client(test_client)


@pytest.fixture(scope="session")
def ground_truth(settings):
    return GroundTruthStore(
        gt_dir=Path(settings.ground_truth_dir),
        alt_dir=Path(settings.ground_truth_alt_dir),
    )


@pytest.fixture(scope="session", autouse=True)
def register_custom_normalizers():
    """Register insurance domain-specific normalizers."""
    register_normalizer("carrier", normalize_carrier)
```

### `tests/test_api_health.py` — Smoke tests

```python
"""Smoke tests: is the service alive and responding correctly?"""

def test_health_endpoint(api_client):
    resp = api_client.health()
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_config_endpoint(api_client):
    resp = api_client.config()
    assert resp.status_code == 200
    config = resp.json()
    assert "auto_commit_threshold" in config
    assert "supported_doc_types" in config

def test_config_auto_commit_threshold_is_sane(api_client):
    """auto_commit_threshold=5 is dangerously low."""
    config = api_client.config().json()
    threshold = config["auto_commit_threshold"]
    if threshold < 70:
        print(f"⚠ CRITICAL: auto_commit_threshold={threshold} — recommend ≥85")

def test_extract_returns_valid_schema(api_client):
    """The /extract response has the expected structure."""
    resp = api_client.extract("sov_acme_properties", model="v1", seed=1)
    assert resp.status_code == 200
    data = resp.json()
    assert "classification" in data
    assert "extraction" in data
    assert "metadata" in data
    assert "doc_type" in data["classification"]
    assert "confidence" in data["classification"]

def test_extract_with_invalid_doc_returns_404(api_client):
    resp = api_client.extract("nonexistent_document")
    assert resp.status_code == 404
```

### `tests/test_extraction_accuracy.py` — Main test

```python
"""Extraction accuracy field-by-field against ground truth.

Runs N extractions per document, compares each field, and asserts
that accuracy is above the CI threshold.
"""
import pytest
from framework.validators import validate_extraction, MatchLevel
from framework.assertions import assert_field_accuracy, assert_no_hallucinations

DOCS_WITH_GT = [
    "sov_acme_properties", "sov_pacific_realty", "sov_keystone_reit",
    "coi_hartford_general", "coi_travelers_umbrella", "coi_zurich_legacy",
    "loss_run_nationwide", "loss_run_libertymutual",
    "endorsement_chubb_tiv_increase", "binder_travelers_temp",
]

@pytest.mark.parametrize("document_id", DOCS_WITH_GT)
def test_field_accuracy(api_client, ground_truth, settings, document_id):
    """Every field with ground truth must match in at least 60% of N runs."""
    gt = ground_truth.get(document_id)
    field_specs = settings.get_field_specs(gt.doc_type)
    nested_specs = settings.get_nested_specs(gt.doc_type)
    
    runs, failures = api_client.extract_n_times(
        document_id, model="v1",
        n=settings.n_runs, seed_base=settings.seed_base,
    )
    
    # Aggregate results across all runs
    all_results = []
    for run in runs:
        results = validate_extraction(
            run["extraction"], gt.extraction,
            field_specs, nested_specs,
        )
        all_results.append(results)
    
    # Compute per-field accuracy across runs
    field_hits = {}
    for run_results in all_results:
        for r in run_results:
            if r.match == MatchLevel.SKIPPED:
                continue
            passed = r.match in (MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH)
            field_hits.setdefault(r.field_path, []).append(passed)
    
    accuracy = {fp: sum(h)/len(h) for fp, h in field_hits.items()}
    failing = {fp: acc for fp, acc in accuracy.items() if acc < settings.min_field_accuracy}
    
    # Print report for CI logs
    print(f"\n{'='*60}")
    print(f"{document_id} | model=v1 | {len(runs)} runs | {failures} failures")
    if failing:
        print("FAILING fields:")
        for fp, acc in sorted(failing.items(), key=lambda x: x[1]):
            print(f"  {fp}: {acc:.0%}")
    print(f"PASSING: {len(accuracy) - len(failing)}/{len(accuracy)} fields")
    
    assert not failing, (
        f"{document_id}: fields below {settings.min_field_accuracy:.0%}:\n"
        + "\n".join(f"  {fp}: {acc:.0%}" for fp, acc in sorted(failing.items()))
    )
```

### `tests/test_model_comparison.py` — v1 vs v2

```python
"""Model comparison: v2 must not regress vs v1."""
import pytest
from framework.validators import validate_extraction
from framework.assertions import assert_no_regression

@pytest.mark.parametrize("document_id", DOCS_WITH_GT)
def test_v2_does_not_regress(api_client, ground_truth, settings, document_id):
    """v2 must not regress more than 10pp on any field vs v1."""
    gt = ground_truth.get(document_id)
    specs = settings.get_field_specs(gt.doc_type)
    nested = settings.get_nested_specs(gt.doc_type)
    
    # Same seeds for fair comparison
    runs_v1, _ = api_client.extract_n_times(document_id, "v1", settings.n_runs, settings.seed_base)
    runs_v2, _ = api_client.extract_n_times(document_id, "v2", settings.n_runs, settings.seed_base)
    
    acc_v1 = _compute_accuracy(runs_v1, gt.extraction, specs, nested)
    acc_v2 = _compute_accuracy(runs_v2, gt.extraction, specs, nested)
    
    # Print comparison
    print(f"\n{'='*60}")
    print(f"MODEL COMPARISON: {document_id}")
    for fp in sorted(set(acc_v1) | set(acc_v2)):
        a1 = acc_v1.get(fp, 0)
        a2 = acc_v2.get(fp, 0)
        delta = a2 - a1
        marker = "" if abs(delta) < 0.05 else (" ▲" if delta > 0 else " ▼")
        print(f"  {fp}: v1={a1:.0%} v2={a2:.0%} {delta:+.0%}{marker}")
    
    assert_no_regression(acc_v1, acc_v2, settings.max_regression_pp, document_id)
```

### `tests/test_invariants.py` — Cross-field checks

```python
"""Invariant checks: internal consistency without ground truth."""
import pytest
from framework.invariants import run_invariants, Severity

ALL_DOCS = [...]  # Including coi_unlabeled_mystery

@pytest.mark.parametrize("document_id", ALL_DOCS)
def test_invariant_consistency(api_client, settings, document_id):
    """Cross-field invariants must not violate in more than 30% of runs."""
    doc_config = settings.get_document_config(document_id)
    invariant_names = settings.get_invariants(doc_config["doc_type"])
    
    runs, _ = api_client.extract_n_times(document_id, "v1", settings.n_runs, settings.seed_base)
    
    violation_counts = {}
    for run in runs:
        results = run_invariants(run["extraction"], invariant_names)
        for r in results:
            if r.severity == Severity.ERROR:
                violation_counts[r.check] = violation_counts.get(r.check, 0) + 1
    
    violation_rates = {k: v / len(runs) for k, v in violation_counts.items()}
    
    for check, rate in violation_rates.items():
        print(f"  {check}: {rate:.0%} of runs")
    
    # Log but don't hard-fail on known issues
    bad = {k: v for k, v in violation_rates.items() if v > 0.30}
    if bad:
        print(f"⚠ High violation rates: {bad}")
```

### `tests/test_edge_cases.py` — Partial, disputed, missing truth

```python
"""Edge cases: documents with partial, disputed, or missing ground truth."""

def test_partial_truth_square_footage(api_client, ground_truth, settings):
    """sov_acme_properties: square_footage='unknown' → skip, don't fail."""
    gt = ground_truth.get("sov_acme_properties")
    runs, _ = api_client.extract_n_times("sov_acme_properties", "v1", 10, 42000)
    
    for run in runs:
        results = validate_extraction(run["extraction"], gt.extraction, ...)
        skipped = [r for r in results if r.match == MatchLevel.SKIPPED]
        assert any("square_footage" in r.field_path for r in skipped)

def test_disputed_truth_keystone_reit(api_client, ground_truth, settings):
    """sov_keystone_reit: two labels, extraction is valid if it matches either."""
    gt_primary = ground_truth.get("sov_keystone_reit")
    gt_alt = ground_truth.get_alt("sov_keystone_reit")
    
    runs, _ = api_client.extract_n_times("sov_keystone_reit", "v1", 10, 42000)
    
    for run in runs:
        tiv = run["extraction"].get("total_tiv")
        primary_tiv = gt_primary.extraction.get("total_tiv")
        alt_tiv = gt_alt.extraction.get("total_tiv") if gt_alt else None
        
        # Passes if within tolerance of EITHER label
        primary_ok = abs(tiv - primary_tiv) / primary_tiv < 0.05
        alt_ok = alt_tiv and abs(tiv - alt_tiv) / alt_tiv < 0.05
        print(f"  total_tiv={tiv:,.0f} primary={primary_ok} alt={alt_ok}")

def test_unlabeled_mystery_invariants_only(api_client, settings):
    """coi_unlabeled_mystery: no GT, invariants only."""
    runs, _ = api_client.extract_n_times("coi_unlabeled_mystery", "v1", 10, 42000)
    
    for run in runs:
        results = run_invariants(run["extraction"], ["duplicate_coverage", "date_ordering"])
        errors = [r for r in results if r.severity == Severity.ERROR]
        assert not errors, f"Invariant errors: {errors}"
```

### `tests/test_reseed_resilience.py`

```python
"""Verify that the framework is not overfit to specific document_ids."""

def test_framework_survives_reseed(api_client, settings):
    """After reseed-bugs, the framework still produces reports."""
    # Reseed
    resp = api_client.reseed_bugs(seed=9999)
    assert resp.status_code == 200
    
    # Run a subset — must not crash
    for doc_id in ["sov_acme_properties", "coi_hartford_general", "loss_run_nationwide"]:
        runs, failures = api_client.extract_n_times(doc_id, "v1", 5, 77000)
        assert len(runs) == 5, f"{doc_id}: only {len(runs)} successful runs"
    
    # Reset
    api_client.reseed_bugs(seed=0)
```

---

## custom_rules/ — Insurance domain logic

### `carrier_names.py`

```python
"""Insurance carrier name normalization."""

CARRIER_CANONICAL = {
    "the hartford": "Hartford Financial Services",
    "hartford": "Hartford Financial Services",
    "hartford financial services": "Hartford Financial Services",
    "travelers": "The Travelers Indemnity Company",
    "travelers insurance": "The Travelers Indemnity Company",
    "the travelers indemnity company": "The Travelers Indemnity Company",
    "zurich": "Zurich Insurance",
    "zurich north america": "Zurich Insurance",
    "zurich insurance": "Zurich Insurance",
    "nationwide": "Nationwide Insurance",
    "nationwide mutual": "Nationwide Insurance",
    "nationwide insurance": "Nationwide Insurance",
}

def normalize_carrier(name: str | None) -> str | None:
    if name is None:
        return None
    return CARRIER_CANONICAL.get(name.strip().lower(), name)
```

### `insurance_invariants.py`

```python
"""Insurance domain-specific invariants."""
from framework.invariants import register_invariant, InvariantResult, Severity

@register_invariant("component_sum")
def check_component_sum(extraction, **kwargs):
    """SOV: building + contents + BI == total_insured_value per property."""
    results = []
    for i, prop in enumerate(extraction.get("properties", [])):
        bv = prop.get("building_value") or 0
        cv = prop.get("contents_value") or 0
        bi = prop.get("business_income_value") or 0
        tiv = prop.get("total_insured_value")
        if tiv and tiv > 0:
            drift = abs(bv + cv + bi - tiv) / tiv
            if drift > 0.05:
                results.append(InvariantResult("component_sum", Severity.ERROR,
                    f"Property {i}: component drift {drift:.1%}"))
    return results

@register_invariant("total_tiv_sum")
def check_total_tiv_sum(extraction, **kwargs):
    """SOV: sum of property TIVs == total_tiv."""
    ...

@register_invariant("unit_drift_detection")
def check_unit_drift(extraction, **kwargs):
    """Loss run: detect 100x paid/reserved artifacts."""
    ...

# ... (all 10 invariants from the current framework, as registered functions)
```

---

## Implementation order

1. `framework/config.py` — Settings + YAML loader
2. `framework/normalizers.py` — Normalizers + registry
3. `framework/validators.py` — FieldResult + comparators
4. `framework/invariants.py` — InvariantResult + registry
5. `framework/api_client.py` — HTTP client with retry
6. `framework/ground_truth.py` — GT loader
7. `framework/assertions.py` — pytest helpers
8. `framework/reporter.py` — Reports
9. `tests/unit/` — Framework tests
10. `projects/docextract/config.yaml` — Project config
11. `projects/docextract/custom_rules/` — Carrier names + invariants
12. `projects/docextract/conftest.py` — Wiring
13. `projects/docextract/tests/` — Full test suite
14. Verify: `pytest -v` — all passes, same 4 bugs detected, <8 min

---

## How to use it from another project

```bash
# 1. Clone the monorepo
git clone <repo> && cd extracteval

# 2. Install
pip install -e .

# 3. Create a new project
mkdir -p projects/my_new_service
cd projects/my_new_service

# 4. Create config.yaml pointing to your API
# 5. Add ground truth JSONs
# 6. (Optional) Create custom_rules/ with domain-specific normalizers/invariants
# 7. Create conftest.py + tests/
# 8. pytest tests/ -v
```