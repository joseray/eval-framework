"""Field-level comparison returning MatchLevel results.

Each compare_* function returns a FieldResult. The runner aggregates these
into per-document accuracy metrics. Field type is inferred from field name
so callers don't need to carry schema information through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from eval.normalizers import (
    normalize_carrier,
    normalize_date,
    normalize_string,
)


class MatchLevel(Enum):
    EXACT = "exact"
    APPROX = "approx"           # within configured tolerance
    SKIPPED = "skipped"         # partial / disputed / unknown ground truth
    MISMATCH = "mismatch"
    MISSING = "missing"         # expected non-null, extraction returned null
    HALLUCINATED = "hallucinated"  # extraction returned value, expected null


@dataclass
class FieldResult:
    field: str
    level: MatchLevel
    extracted: object
    expected: object
    notes: str = ""

    @property
    def passed(self) -> bool:
        return self.level in (MatchLevel.EXACT, MatchLevel.APPROX, MatchLevel.SKIPPED)


# ---------------------------------------------------------------------------
# Field type sets — drive dispatch in compare_field()
# ---------------------------------------------------------------------------

_DATE_FIELDS = frozenset({
    "effective_date", "expiration_date", "date_of_loss",
    "policy_effective_date", "valuation_date",
    "binder_effective_date", "binder_expiration_date",
    "endorsement_effective_date",
})

_CARRIER_FIELDS = frozenset({"carrier"})

_AMOUNT_FIELDS = frozenset({
    "building_value", "contents_value", "business_income_value",
    "total_insured_value", "total_tiv", "paid_amount", "reserved_amount",
    "total_incurred", "total_paid", "total_recoveries", "loss_ratio",
    "each_occurrence_limit", "general_aggregate_limit",
    "products_completed_ops", "premium_delta",
})

# Aggregate fields get a looser ±5% tolerance (rounding accumulates over N items).
_AGGREGATE_FIELDS = frozenset({
    "total_tiv", "total_paid", "total_recoveries", "total_incurred",
})

_PARTY_FIELDS = frozenset({
    "insured_name", "certificate_holder", "claimant", "producer",
})


# ---------------------------------------------------------------------------
# Levenshtein distance (used for fuzzy party-name matching)
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


# ---------------------------------------------------------------------------
# Primary dispatch
# ---------------------------------------------------------------------------

def compare_field(field_name: str, extracted, expected) -> FieldResult:
    """Compare one extracted value against its ground-truth expectation.

    Returns a FieldResult whose .passed property summarizes whether the
    comparison should count as a success.
    """
    # Partial / disputed / unknown ground truth — skip scoring
    if isinstance(expected, str) and expected.lower() == "unknown":
        return FieldResult(field_name, MatchLevel.SKIPPED, extracted, expected, "partial truth")

    # Null handling — must come before any type-specific dispatch
    if expected is None and extracted is None:
        return FieldResult(field_name, MatchLevel.EXACT, extracted, expected)
    if expected is None and extracted is not None:
        return FieldResult(
            field_name, MatchLevel.HALLUCINATED, extracted, expected,
            "extraction returned value where none expected",
        )
    if expected is not None and extracted is None:
        return FieldResult(
            field_name, MatchLevel.MISSING, extracted, expected,
            "extraction returned null for non-null expected value",
        )

    # Date fields — normalize both to ISO, then exact-compare
    if field_name in _DATE_FIELDS:
        n_ext = normalize_date(str(extracted))
        n_exp = normalize_date(str(expected))
        level = MatchLevel.EXACT if n_ext == n_exp else MatchLevel.MISMATCH
        return FieldResult(field_name, level, extracted, expected)

    # Carrier fields — canonicalize, then exact-compare
    if field_name in _CARRIER_FIELDS:
        n_ext = normalize_carrier(str(extracted))
        n_exp = normalize_carrier(str(expected))
        level = MatchLevel.EXACT if n_ext == n_exp else MatchLevel.MISMATCH
        return FieldResult(field_name, level, extracted, expected)

    # Numeric amount fields — relative tolerance comparison
    if field_name in _AMOUNT_FIELDS:
        tolerance = 0.05 if field_name in _AGGREGATE_FIELDS else 0.03
        try:
            e_val = float(extracted)
            exp_val = float(expected)
        except (TypeError, ValueError):
            return FieldResult(field_name, MatchLevel.MISMATCH, extracted, expected, "non-numeric amount")
        if abs(exp_val) < 0.01:
            level = MatchLevel.EXACT if abs(e_val - exp_val) <= 1.0 else MatchLevel.MISMATCH
            return FieldResult(field_name, level, extracted, expected)
        rel_err = abs(e_val - exp_val) / abs(exp_val)
        level = MatchLevel.APPROX if rel_err <= tolerance else MatchLevel.MISMATCH
        return FieldResult(field_name, level, extracted, expected, f"rel_err={rel_err:.4f}")

    # Party names — fuzzy match with Levenshtein ≤ 2 after normalization
    if field_name in _PARTY_FIELDS:
        n_ext = normalize_string(str(extracted)).lower()
        n_exp = normalize_string(str(expected)).lower()
        if n_ext == n_exp:
            return FieldResult(field_name, MatchLevel.EXACT, extracted, expected)
        dist = _levenshtein(n_ext, n_exp)
        level = MatchLevel.APPROX if dist <= 2 else MatchLevel.MISMATCH
        return FieldResult(field_name, level, extracted, expected, f"levenshtein={dist}")

    # Default: case-insensitive exact match (identifiers, enums, strings)
    n_ext = normalize_string(str(extracted)).lower()
    n_exp = normalize_string(str(expected)).lower()
    level = MatchLevel.EXACT if n_ext == n_exp else MatchLevel.MISMATCH
    return FieldResult(field_name, level, extracted, expected)
