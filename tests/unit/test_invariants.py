"""Unit tests for framework/invariants.py."""

import pytest
from framework.invariants import (
    register_invariant,
    run_invariants,
    InvariantResult,
    Severity,
    check_date_ordering,
    _INVARIANT_REGISTRY,
)


class TestRegisterInvariant:
    def test_decorator_registers(self):
        @register_invariant("test_dummy_xyz")
        def dummy_check(extraction, **kwargs):
            return []

        assert "test_dummy_xyz" in _INVARIANT_REGISTRY

    def test_registered_invariant_is_callable(self):
        @register_invariant("test_callable_abc")
        def my_check(extraction, **kwargs):
            return [InvariantResult("test_callable_abc", Severity.INFO, "ok")]

        results = run_invariants({}, ["test_callable_abc"])
        assert len(results) == 1
        assert results[0].check == "test_callable_abc"


class TestRunInvariants:
    def test_unknown_invariant_silently_skipped(self):
        results = run_invariants({}, ["nonexistent_invariant_xyz"])
        assert results == []

    def test_multiple_invariants(self):
        @register_invariant("inv_a")
        def check_a(extraction, **kwargs):
            return [InvariantResult("inv_a", Severity.INFO, "a")]

        @register_invariant("inv_b")
        def check_b(extraction, **kwargs):
            return [InvariantResult("inv_b", Severity.WARNING, "b")]

        results = run_invariants({}, ["inv_a", "inv_b"])
        checks = {r.check for r in results}
        assert "inv_a" in checks
        assert "inv_b" in checks

    def test_empty_list(self):
        results = run_invariants({"data": "foo"}, [])
        assert results == []


class TestDateOrdering:
    def test_valid_date_pair(self):
        extraction = {"effective_date": "2025-01-01", "expiration_date": "2026-01-01"}
        results = check_date_ordering(extraction)
        assert results == []

    def test_invalid_date_pair(self):
        extraction = {"effective_date": "2026-01-01", "expiration_date": "2025-01-01"}
        results = check_date_ordering(extraction)
        assert len(results) == 1
        assert results[0].severity == Severity.ERROR
        assert results[0].check == "date_ordering"

    def test_equal_dates_is_error(self):
        extraction = {"effective_date": "2025-06-01", "expiration_date": "2025-06-01"}
        results = check_date_ordering(extraction)
        assert len(results) == 1

    def test_us_format_dates_normalized(self):
        extraction = {"effective_date": "01/01/2025", "expiration_date": "01/01/2026"}
        results = check_date_ordering(extraction)
        assert results == []

    def test_coverage_date_ordering(self):
        extraction = {
            "coverages": [
                {"effective_date": "2025-01-01", "expiration_date": "2026-01-01"},
                {"effective_date": "2026-01-01", "expiration_date": "2025-01-01"},  # bad
            ]
        }
        results = check_date_ordering(extraction)
        errors = [r for r in results if r.severity == Severity.ERROR]
        assert len(errors) == 1

    def test_missing_dates_no_error(self):
        extraction = {}
        results = check_date_ordering(extraction)
        assert results == []

    def test_binder_dates(self):
        extraction = {
            "binder_effective_date": "2025-06-01",
            "binder_expiration_date": "2025-05-01",  # wrong
        }
        results = check_date_ordering(extraction)
        assert any(r.severity == Severity.ERROR for r in results)
