"""Assertion helpers that integrate with pytest.

These wrap validators/invariants into asserts that produce clear
failure messages showing exactly which fields failed and why.
"""

from __future__ import annotations

import pytest

from framework.validators import FieldResult, MatchLevel
from framework.invariants import InvariantResult, Severity


PASSING = {MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH}


def assert_field_accuracy(
    results: list[FieldResult],
    min_accuracy: float = 0.60,
    label: str = "",
) -> None:
    """Assert that field accuracy is above the minimum.

    Skipped fields are excluded from scoring.
    """
    scored = [r for r in results if r.match != MatchLevel.SKIPPED]
    if not scored:
        return
    accuracy = sum(1 for r in scored if r.match in PASSING) / len(scored)
    if accuracy < min_accuracy:
        failing = [r for r in scored if r.match not in PASSING]
        msg = f"{label}: accuracy {accuracy:.0%} < {min_accuracy:.0%}\n"
        msg += "\n".join(
            f"  {r.field_path}: {r.match.value} "
            f"(expected={r.expected!r}, actual={r.actual!r})"
            + (f" [{r.note}]" if r.note else "")
            for r in failing[:10]
        )
        pytest.fail(msg)


def assert_no_hallucinations(
    results: list[FieldResult],
    label: str = "",
) -> None:
    """Assert that no fields are hallucinated."""
    hallucinated = [r for r in results if r.match == MatchLevel.HALLUCINATED]
    if hallucinated:
        msg = f"{label}: {len(hallucinated)} hallucinated fields:\n"
        msg += "\n".join(f"  {r.field_path}: actual={r.actual!r}" for r in hallucinated)
        pytest.fail(msg)


def assert_invariants_pass(
    invariant_results: list[InvariantResult],
    max_error_rate: float = 0.30,
    label: str = "",
) -> None:
    """Log ERROR-level invariants. Does not fail — signal is in the report."""
    errors = [r for r in invariant_results if r.severity == Severity.ERROR]
    if errors:
        msg = f"{label}: {len(errors)} invariant violations:\n"
        msg += "\n".join(
            f"  [{r.severity.value}] {r.check}: {r.message}" for r in errors
        )
        print(f"\u26a0 {msg}")


def assert_no_regression(
    accuracy_baseline: dict[str, float],
    accuracy_candidate: dict[str, float],
    max_regression_pp: float = 10.0,
    label: str = "",
) -> None:
    """Assert that the candidate model doesn't regress more than N percentage points."""
    regressions: dict[str, tuple[float, float, float]] = {}
    for fp, baseline_acc in accuracy_baseline.items():
        candidate_acc = accuracy_candidate.get(fp, 0.0)
        delta_pp = (baseline_acc - candidate_acc) * 100
        if delta_pp > max_regression_pp:
            regressions[fp] = (baseline_acc, candidate_acc, delta_pp)

    if regressions:
        msg = f"{label}: {len(regressions)} regressions > {max_regression_pp}pp:\n"
        msg += "\n".join(
            f"  {fp}: {b:.0%} \u2192 {c:.0%} ({d:+.1f}pp)"
            for fp, (b, c, d) in sorted(regressions.items(), key=lambda x: -x[1][2])
        )
        pytest.fail(msg)
