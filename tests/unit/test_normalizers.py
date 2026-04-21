"""Unit tests for framework/normalizers.py."""

import pytest
from framework.normalizers import normalize_date, normalize_amount, normalize_string, normalize, register_normalizer


class TestNormalizeDate:
    def test_iso_passthrough(self):
        assert normalize_date("2025-03-01") == "2025-03-01"

    def test_us_format(self):
        assert normalize_date("03/01/2025") == "2025-03-01"

    def test_us_format_padding(self):
        assert normalize_date("3/1/2025") == "2025-03-01"

    def test_eu_format_unambiguous(self):
        # Day 25 > 12, must be DD/MM/YYYY
        assert normalize_date("25/06/2025") == "2025-06-25"

    def test_ambiguous_date_us_bias(self):
        # 06/07/2025: both day and month <= 12, US bias → MM/DD = June 7
        assert normalize_date("06/07/2025") == "2025-06-07"

    def test_none(self):
        assert normalize_date(None) is None

    def test_strips_whitespace(self):
        assert normalize_date("  2025-03-01  ") == "2025-03-01"


class TestNormalizeAmount:
    def test_round_two_dp(self):
        assert normalize_amount(1234567.891) == 1234567.89

    def test_round_up(self):
        assert normalize_amount(1.005) == 1.0  # float rounding

    def test_none(self):
        assert normalize_amount(None) is None

    def test_int_input(self):
        assert normalize_amount(100) == 100.0

    def test_already_two_dp(self):
        assert normalize_amount(99.99) == 99.99


class TestNormalizeString:
    def test_collapse_whitespace(self):
        assert normalize_string("  foo   bar  ") == "foo bar"

    def test_internal_spaces(self):
        assert normalize_string("Hartford  Financial   Services") == "Hartford Financial Services"

    def test_none(self):
        assert normalize_string(None) is None

    def test_empty(self):
        assert normalize_string("") == ""


class TestRegistry:
    def test_register_and_use_custom_normalizer(self):
        def upper(val):
            return val.upper() if val else val

        register_normalizer("upper_test", upper)
        assert normalize("hello", "upper_test") == "HELLO"

    def test_unknown_type_passthrough(self):
        assert normalize("anything", "nonexistent_type") == "anything"

    def test_dispatch_date(self):
        assert normalize("01/15/2025", "date") == "2025-01-15"

    def test_dispatch_amount(self):
        assert normalize(1000.123, "amount") == 1000.12
