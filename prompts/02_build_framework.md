# Prompt 02 — Build the Complete extracteval Framework

## Request
> Build the complete extracteval framework following the implementation order in CLAUDE.md.

### Phase 1: Framework core (framework/)
Create these files in order. Each file must be fully functional, not a stub.
1. framework/__init__.py
2. framework/config.py — EvalSettings dataclass with from_yaml(), env var overrides, get_field_specs/get_nested_specs/get_invariants/get_document_config methods
3. framework/normalizers.py — normalize_date (ISO/US/EU→ISO with US-bias), normalize_amount, normalize_string, registry, register_normalizer(), normalize()
4. framework/validators.py — MatchLevel enum, FieldResult, validate_exact/amount/date/string_fuzzy, validate_extraction() with nested collection support and skip_if
5. framework/invariants.py — Severity, InvariantResult, registry, register_invariant decorator, run_invariants(), built-in date_ordering
6. framework/api_client.py — ExtractionAPIClient with retry, extract_n_times, health, config, reseed_bugs, from_test_client()
7. framework/ground_truth.py — GroundTruthStore loading primary + alt labels
8. framework/assertions.py — assert_field_accuracy, assert_no_hallucinations, assert_invariants_pass, assert_no_regression
9. framework/reporter.py — DocumentTestReport with field_accuracy, invariant_violation_rates, summary, to_json, passes_ci_gate

### Phase 2: Framework unit tests (tests/)
- tests/unit/test_normalizers.py
- tests/unit/test_validators.py
- tests/unit/test_invariants.py

### Phase 3: Docextract project (projects/docextract/)
- Clone vendor: `git clone https://github.com/Vishalopeninsurance/docextract-eval.git vendor/docextract-eval`
- config.yaml (11 documents, 5 doc types, field_specs, invariants)
- custom_rules/carrier_names.py
- custom_rules/insurance_invariants.py (10 invariants)
- Copy ground truth from vendor
- conftest.py (TestClient in-process, register normalizers)
- 7 test files: test_api_health, test_extraction_accuracy, test_model_comparison, test_invariants, test_classification, test_edge_cases, test_reseed_resilience

### Phase 4: Verify
```
pytest tests/ -v --tb=short
pytest projects/docextract/tests/ -v --tb=short
```

### Expected results
- Framework tests: ALL pass
- Docextract tests: Most pass, with 4 controlled failures:
  1. sov_pacific_realty: TIV calibration bias (v1 factor 0.895)
  2. sov_keystone_reit: construction_type omission (40%)
  3. coi_zurich_legacy: phantom cyber coverage (20%)
  4. loss_run_libertymutual: paid_amount unit drift x100 (15%)
- Runtime < 8 minutes

## Outcome
Framework built end-to-end. All 62 framework unit tests pass.
4 controlled extraction_accuracy failures confirmed + 4 additional model_comparison
failures (legitimate v2 regressions documented in the service).
Total runtime: ~30 seconds.

**Key implementation note:** `validate_extraction()` was extended to detect
hallucinated nested items (extra items in extraction not present in ground truth),
which is what surfaces the coi_zurich_legacy phantom cyber coverage bug.
