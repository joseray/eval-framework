"""Unit tests for framework/validators.py."""

import pytest
from framework.validators import (
    validate_exact,
    validate_amount,
    validate_date,
    validate_string_fuzzy,
    validate_extraction,
    MatchLevel,
    FieldResult,
)


class TestValidateExact:
    def test_exact_match(self):
        r = validate_exact("field", "ABC-123", "ABC-123")
        assert r.match == MatchLevel.EXACT

    def test_mismatch(self):
        r = validate_exact("field", "ABC-123", "DEF-456")
        assert r.match == MatchLevel.MISMATCH

    def test_both_none(self):
        r = validate_exact("field", None, None)
        assert r.match == MatchLevel.MISSING_BOTH

    def test_expected_none_actual_present(self):
        r = validate_exact("field", None, "extra")
        assert r.match == MatchLevel.HALLUCINATED

    def test_expected_present_actual_none(self):
        r = validate_exact("field", "expected", None)
        assert r.match == MatchLevel.MISSING_EXPECTED

    def test_strips_whitespace(self):
        r = validate_exact("field", "ABC ", " ABC")
        assert r.match == MatchLevel.EXACT


class TestValidateAmount:
    def test_exact_match(self):
        r = validate_amount("f", 1000.0, 1000.0)
        assert r.match == MatchLevel.EXACT

    def test_within_tolerance(self):
        r = validate_amount("f", 1000.0, 1020.0, tolerance=0.03)
        assert r.match == MatchLevel.WITHIN_TOL

    def test_outside_tolerance(self):
        r = validate_amount("f", 1000.0, 1050.0, tolerance=0.03)
        assert r.match == MatchLevel.MISMATCH

    def test_unit_drift_100x_up(self):
        r = validate_amount("f", 1000.0, 100000.0)
        assert r.match == MatchLevel.MISMATCH
        assert "unit_drift_100x" in r.note

    def test_unit_drift_100x_down(self):
        r = validate_amount("f", 100000.0, 1000.0)
        assert r.match == MatchLevel.MISMATCH
        assert "unit_drift_100x" in r.note

    def test_both_none(self):
        r = validate_amount("f", None, None)
        assert r.match == MatchLevel.MISSING_BOTH

    def test_expected_none(self):
        r = validate_amount("f", None, 500.0)
        assert r.match == MatchLevel.HALLUCINATED

    def test_actual_none(self):
        r = validate_amount("f", 500.0, None)
        assert r.match == MatchLevel.MISSING_EXPECTED

    def test_zero_both(self):
        r = validate_amount("f", 0.0, 0.0)
        assert r.match == MatchLevel.EXACT

    def test_custom_tolerance(self):
        r = validate_amount("f", 1000.0, 1045.0, tolerance=0.05)
        assert r.match == MatchLevel.WITHIN_TOL


class TestValidateDate:
    def test_same_iso(self):
        r = validate_date("f", "2025-03-01", "2025-03-01")
        assert r.match == MatchLevel.EXACT

    def test_different_formats_same_date(self):
        r = validate_date("f", "2025-03-01", "03/01/2025")
        assert r.match == MatchLevel.EXACT

    def test_different_dates(self):
        r = validate_date("f", "2025-03-01", "2025-04-01")
        assert r.match == MatchLevel.MISMATCH

    def test_both_none(self):
        r = validate_date("f", None, None)
        assert r.match == MatchLevel.MISSING_BOTH


class TestValidateStringFuzzy:
    def test_exact_same(self):
        r = validate_string_fuzzy("f", "Acme Corp", "Acme Corp")
        assert r.match == MatchLevel.EXACT

    def test_case_insensitive(self):
        r = validate_string_fuzzy("f", "Acme Corp", "acme corp")
        assert r.match == MatchLevel.EXACT

    def test_whitespace_normalized(self):
        r = validate_string_fuzzy("f", "Acme  Corp", "Acme Corp")
        assert r.match == MatchLevel.EXACT

    def test_mismatch(self):
        r = validate_string_fuzzy("f", "Acme Corp", "Beta Corp")
        assert r.match == MatchLevel.MISMATCH

    def test_both_none(self):
        r = validate_string_fuzzy("f", None, None)
        assert r.match == MatchLevel.MISSING_BOTH


class TestValidateExtraction:
    def test_simple_fields(self):
        extraction = {"insured_name": "Acme LLC", "policy_number": "POL-001"}
        gt = {"insured_name": "acme llc", "policy_number": "POL-001"}
        specs = {
            "insured_name": {"match": "fuzzy"},
            "policy_number": {"match": "exact"},
        }
        results = validate_extraction(extraction, gt, specs)
        assert len(results) == 2
        by_field = {r.field_path: r for r in results}
        assert by_field["insured_name"].match == MatchLevel.EXACT
        assert by_field["policy_number"].match == MatchLevel.EXACT

    def test_amount_field(self):
        extraction = {"total_tiv": 1010000.0}
        gt = {"total_tiv": 1000000.0}
        specs = {"total_tiv": {"match": "amount", "tolerance": 0.05}}
        results = validate_extraction(extraction, gt, specs)
        assert results[0].match == MatchLevel.WITHIN_TOL

    def test_skip_if_unknown(self):
        extraction = {"square_footage": 50000}
        gt = {"square_footage": "unknown"}
        specs = {"square_footage": {"match": "amount", "skip_if": "unknown"}}
        results = validate_extraction(extraction, gt, specs)
        assert results[0].match == MatchLevel.SKIPPED

    def test_nested_by_index(self):
        extraction = {
            "properties": [{"building_value": 1000000.0}, {"building_value": 2000000.0}]
        }
        gt = {
            "properties": [{"building_value": 1000000.0}, {"building_value": 2000000.0}]
        }
        specs = {}
        nested = {
            "properties": {
                "match_by": "index",
                "fields": {"building_value": {"match": "amount"}},
            }
        }
        results = validate_extraction(extraction, gt, specs, nested)
        assert len(results) == 2
        assert all(r.match == MatchLevel.EXACT for r in results)

    def test_nested_by_key(self):
        extraction = {
            "coverages": [
                {"coverage_type": "gl", "policy_number": "POL-001"},
                {"coverage_type": "auto", "policy_number": "POL-002"},
            ]
        }
        gt = {
            "coverages": [
                {"coverage_type": "gl", "policy_number": "POL-001"},
                {"coverage_type": "auto", "policy_number": "POL-999"},  # mismatch
            ]
        }
        specs = {}
        nested = {
            "coverages": {
                "match_by": "coverage_type",
                "fields": {"policy_number": {"match": "exact"}},
            }
        }
        results = validate_extraction(extraction, gt, specs, nested)
        by_key = {r.field_path: r for r in results}
        assert by_key["coverages.gl.policy_number"].match == MatchLevel.EXACT
        assert by_key["coverages.auto.policy_number"].match == MatchLevel.MISMATCH
