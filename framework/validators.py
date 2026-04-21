"""Validators that compare extraction values against ground truth.

Each validator returns a FieldResult with the comparison outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from framework.normalizers import normalize_date, normalize_amount, normalize_string, normalize


class MatchLevel(str, Enum):
    EXACT = "exact"
    WITHIN_TOL = "within_tolerance"
    MISMATCH = "mismatch"
    MISSING_EXPECTED = "missing_expected"
    MISSING_BOTH = "missing_both"
    HALLUCINATED = "hallucinated"
    SKIPPED = "skipped"


PASSING = {MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH}


@dataclass
class FieldResult:
    field_path: str
    match: MatchLevel
    expected: Any = None
    actual: Any = None
    tolerance: float | None = None
    note: str = ""


def validate_exact(path: str, expected: Any, actual: Any) -> FieldResult:
    """Exact match. For identifiers, enums."""
    if expected is None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_BOTH, expected, actual)
    if expected is None and actual is not None:
        return FieldResult(path, MatchLevel.HALLUCINATED, expected, actual)
    if expected is not None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_EXPECTED, expected, actual)
    if str(expected).strip() == str(actual).strip():
        return FieldResult(path, MatchLevel.EXACT, expected, actual)
    return FieldResult(path, MatchLevel.MISMATCH, expected, actual)


def validate_amount(
    path: str,
    expected: float | None,
    actual: float | None,
    tolerance: float = 0.03,
) -> FieldResult:
    """Relative tolerance comparison. Detects 100x unit drift explicitly."""
    if expected is None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_BOTH, expected, actual, tolerance)
    if expected is None and actual is not None:
        return FieldResult(path, MatchLevel.HALLUCINATED, expected, actual, tolerance)
    if expected is not None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_EXPECTED, expected, actual, tolerance)

    exp_norm = normalize_amount(expected)
    act_norm = normalize_amount(actual)

    if exp_norm == 0:
        if act_norm == 0:
            return FieldResult(path, MatchLevel.EXACT, expected, actual, tolerance)
        return FieldResult(path, MatchLevel.MISMATCH, expected, actual, tolerance,
                           note="expected zero but got non-zero")

    rel_diff = abs(exp_norm - act_norm) / abs(exp_norm)

    # 100x unit drift detection (either direction)
    if abs(act_norm / exp_norm - 100.0) < 5.0 or abs(exp_norm / act_norm - 100.0) < 5.0:
        return FieldResult(path, MatchLevel.MISMATCH, expected, actual, tolerance,
                           note="unit_drift_100x")

    if rel_diff <= tolerance:
        match = MatchLevel.EXACT if rel_diff == 0 else MatchLevel.WITHIN_TOL
        return FieldResult(path, match, expected, actual, tolerance)

    return FieldResult(path, MatchLevel.MISMATCH, expected, actual, tolerance,
                       note=f"rel_diff={rel_diff:.3f}")


def validate_date(path: str, expected: str | None, actual: str | None) -> FieldResult:
    """Date comparison after normalization to ISO."""
    if expected is None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_BOTH, expected, actual)
    if expected is None and actual is not None:
        return FieldResult(path, MatchLevel.HALLUCINATED, expected, actual)
    if expected is not None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_EXPECTED, expected, actual)

    exp_iso = normalize_date(expected)
    act_iso = normalize_date(actual)

    if exp_iso == act_iso:
        return FieldResult(path, MatchLevel.EXACT, expected, actual)
    return FieldResult(path, MatchLevel.MISMATCH, expected, actual,
                       note=f"normalized: {exp_iso} != {act_iso}")


def validate_string_fuzzy(path: str, expected: str | None, actual: str | None) -> FieldResult:
    """Case-insensitive, whitespace-normalized comparison."""
    if expected is None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_BOTH, expected, actual)
    if expected is None and actual is not None:
        return FieldResult(path, MatchLevel.HALLUCINATED, expected, actual)
    if expected is not None and actual is None:
        return FieldResult(path, MatchLevel.MISSING_EXPECTED, expected, actual)

    exp_norm = normalize_string(expected).lower() if expected else ""
    act_norm = normalize_string(actual).lower() if actual else ""

    if exp_norm == act_norm:
        return FieldResult(path, MatchLevel.EXACT, expected, actual)
    return FieldResult(path, MatchLevel.MISMATCH, expected, actual)


def _validate_field(path: str, expected: Any, actual: Any, spec: dict) -> FieldResult:
    """Dispatch to the right validator based on field spec."""
    match_type = spec.get("match", "exact")
    tolerance = spec.get("tolerance", 0.03)

    # skip_if: skip when ground truth equals the sentinel value
    skip_if = spec.get("skip_if")
    if skip_if is not None and str(expected).lower() == str(skip_if).lower():
        return FieldResult(path, MatchLevel.SKIPPED, expected, actual,
                           note=f"skip_if={skip_if}")

    if match_type == "fuzzy":
        return validate_string_fuzzy(path, expected, actual)
    elif match_type == "amount":
        return validate_amount(path, expected, actual, tolerance=tolerance)
    elif match_type == "date":
        return validate_date(path, expected, actual)
    elif match_type in ("carrier", "string"):
        # Carrier uses a registered normalizer; apply before comparing
        exp_norm = normalize(expected, match_type)
        act_norm = normalize(actual, match_type)
        return validate_string_fuzzy(path, exp_norm, act_norm)
    else:
        return validate_exact(path, expected, actual)


def validate_extraction(
    extraction: dict,
    ground_truth: dict,
    field_specs: dict,
    nested_specs: dict | None = None,
) -> list[FieldResult]:
    """Validate a full extraction against ground truth.

    Args:
        extraction: The extracted values from the API response.
        ground_truth: The expected values from the ground truth store.
        field_specs: {field_name: {match, tolerance?, skip_if?, optional?}}
        nested_specs: {collection_name: {match_by, fields}}

    Returns:
        list[FieldResult] — one entry per compared field.
    """
    results: list[FieldResult] = []

    # Top-level fields
    for field_name, spec in field_specs.items():
        expected = ground_truth.get(field_name)
        actual = extraction.get(field_name)
        path = field_name

        # Optional fields: skip if both missing
        if spec.get("optional") and expected is None and actual is None:
            continue

        results.append(_validate_field(path, expected, actual, spec))

    # Nested collections
    if nested_specs:
        for collection_name, nested_cfg in nested_specs.items():
            match_by = nested_cfg.get("match_by", "index")
            sub_field_specs = nested_cfg.get("fields", {})

            gt_items: list[dict] = ground_truth.get(collection_name) or []
            ext_items: list[dict] = extraction.get(collection_name) or []

            if match_by == "index":
                # Zip by position
                for i, gt_item in enumerate(gt_items):
                    ext_item = ext_items[i] if i < len(ext_items) else {}
                    prefix = f"{collection_name}.{i}"
                    for sub_field, sub_spec in sub_field_specs.items():
                        path = f"{prefix}.{sub_field}"
                        expected = gt_item.get(sub_field)
                        actual = ext_item.get(sub_field)
                        results.append(_validate_field(path, expected, actual, sub_spec))
            else:
                # Match by key value (e.g., match_by="coverage_type", "claim_number")
                # Build a lookup from gt
                gt_lookup: dict[Any, dict] = {}
                for item in gt_items:
                    key = item.get(match_by)
                    if key is not None:
                        gt_lookup[key] = item

                # Build lookup from extraction
                ext_lookup: dict[Any, dict] = {}
                for item in ext_items:
                    key = item.get(match_by)
                    if key is not None:
                        ext_lookup[key] = item

                # Validate all GT items
                for key, gt_item in gt_lookup.items():
                    ext_item = ext_lookup.get(key, {})
                    prefix = f"{collection_name}.{key}"
                    for sub_field, sub_spec in sub_field_specs.items():
                        path = f"{prefix}.{sub_field}"
                        expected = gt_item.get(sub_field)
                        actual = ext_item.get(sub_field)
                        results.append(_validate_field(path, expected, actual, sub_spec))

                # Detect hallucinated nested items (in extraction but not in GT)
                for key, ext_item in ext_lookup.items():
                    if key not in gt_lookup:
                        prefix = f"{collection_name}.{key}"
                        for sub_field in sub_field_specs:
                            path = f"{prefix}.{sub_field}"
                            actual = ext_item.get(sub_field)
                            results.append(FieldResult(
                                path, MatchLevel.HALLUCINATED, None, actual,
                                note="extra_item_not_in_gt",
                            ))

    return results
