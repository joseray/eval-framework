"""Invariants: internal consistency validations.

Do not need ground truth. Verify that the extraction is internally coherent.
Can run on ANY document, including those without labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class InvariantResult:
    check: str
    severity: Severity
    message: str
    details: dict | None = None


_INVARIANT_REGISTRY: dict[str, Callable] = {}


def register_invariant(name: str):
    """Decorator to register an invariant check."""
    def decorator(fn: Callable) -> Callable:
        _INVARIANT_REGISTRY[name] = fn
        return fn
    return decorator


def run_invariants(
    extraction: dict,
    invariant_names: list[str],
    **kwargs,
) -> list[InvariantResult]:
    """Run the listed invariants against an extraction."""
    results: list[InvariantResult] = []
    for name in invariant_names:
        fn = _INVARIANT_REGISTRY.get(name)
        if fn:
            results.extend(fn(extraction, **kwargs))
    return results


@register_invariant("date_ordering")
def check_date_ordering(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Verify effective_date < expiration_date for any standard date pair."""
    from framework.normalizers import normalize_date

    results: list[InvariantResult] = []

    pairs = [
        ("effective_date", "expiration_date"),
        ("policy_effective_date", "policy_expiration_date"),
        ("binder_effective_date", "binder_expiration_date"),
    ]

    for eff_key, exp_key in pairs:
        eff = normalize_date(extraction.get(eff_key))
        exp = normalize_date(extraction.get(exp_key))
        if eff and exp:
            if eff >= exp:
                results.append(InvariantResult(
                    check="date_ordering",
                    severity=Severity.ERROR,
                    message=f"{eff_key}={eff} is not before {exp_key}={exp}",
                    details={"effective": eff, "expiration": exp},
                ))

    # Also check coverages list if present
    for i, cov in enumerate(extraction.get("coverages", [])):
        eff = normalize_date(cov.get("effective_date"))
        exp = normalize_date(cov.get("expiration_date"))
        if eff and exp:
            if eff >= exp:
                results.append(InvariantResult(
                    check="date_ordering",
                    severity=Severity.ERROR,
                    message=f"coverage[{i}] effective={eff} not before expiration={exp}",
                    details={"index": i, "effective": eff, "expiration": exp},
                ))

    return results
