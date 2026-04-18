"""Cross-field invariant checks — no ground truth required.

Invariants express consistency rules that hold for any valid extraction
regardless of what the source document says. They catch structural bugs
(unit drift, sign errors, date swaps) that field-level comparison can miss
when ground truth is unavailable or stale.

Each check_*_invariants function returns a list of InvariantResult objects.
Callers aggregate these across runs to compute violation rates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InvariantLevel(Enum):
    OK = "ok"
    WARNING = "warning"  # logged and dashboarded; does not block deploy
    ERROR = "error"      # blocks auto-commit; SEV-2 signal if sustained


@dataclass
class InvariantResult:
    name: str
    level: InvariantLevel
    message: str
    doc_type: str = ""


# ---------------------------------------------------------------------------
# SOV invariants
# ---------------------------------------------------------------------------

def check_sov_invariants(extraction: dict) -> list[InvariantResult]:
    results: list[InvariantResult] = []
    properties = extraction.get("properties", [])
    total_tiv = extraction.get("total_tiv")

    # sum(properties[i].total_insured_value) == total_tiv (±1% OK, ±5% ERROR)
    if total_tiv is not None and properties:
        computed = sum(p.get("total_insured_value") or 0.0 for p in properties)
        if computed > 0:
            rel_err = abs(computed - total_tiv) / computed
            if rel_err > 0.01:
                level = InvariantLevel.ERROR if rel_err > 0.05 else InvariantLevel.WARNING
                results.append(InvariantResult(
                    "sov_tiv_sum", level,
                    f"sum(properties.total_insured_value)={computed:.2f} != "
                    f"total_tiv={total_tiv:.2f} (rel_err={rel_err:.3f})",
                    "sov",
                ))
            else:
                results.append(InvariantResult("sov_tiv_sum", InvariantLevel.OK, "TIV sum consistent", "sov"))

    # building_value + contents_value + business_income_value == total_insured_value per property (±5%)
    for i, prop in enumerate(properties):
        bv = prop.get("building_value") or 0.0
        cv = prop.get("contents_value") or 0.0
        bi = prop.get("business_income_value") or 0.0
        tiv = prop.get("total_insured_value")
        if tiv is not None and (bv + cv + bi) > 0:
            comp_sum = bv + cv + bi
            rel_err = abs(comp_sum - tiv) / tiv if tiv > 0 else abs(comp_sum)
            if rel_err > 0.05:
                results.append(InvariantResult(
                    f"sov_component_sum_prop_{i}", InvariantLevel.WARNING,
                    f"Property {i}: components={comp_sum:.2f} != "
                    f"total_insured_value={tiv:.2f} (rel_err={rel_err:.3f})",
                    "sov",
                ))

    return results


# ---------------------------------------------------------------------------
# COI invariants
# ---------------------------------------------------------------------------

def check_coi_invariants(extraction: dict) -> list[InvariantResult]:
    results: list[InvariantResult] = []
    coverages = extraction.get("coverages", [])

    # effective_date < expiration_date for each coverage
    for i, cov in enumerate(coverages):
        eff = cov.get("effective_date")
        exp = cov.get("expiration_date")
        if eff and exp:
            from eval.normalizers import normalize_date
            n_eff = normalize_date(eff)
            n_exp = normalize_date(exp)
            if n_eff and n_exp and n_eff >= n_exp:
                results.append(InvariantResult(
                    f"coi_date_ordering_cov_{i}", InvariantLevel.ERROR,
                    f"Coverage {i}: effective_date={eff} >= expiration_date={exp}",
                    "coi",
                ))

    # No duplicate coverage_type within a single COI
    from collections import Counter
    cov_types = [c.get("coverage_type") for c in coverages if c.get("coverage_type")]
    for ctype, cnt in Counter(cov_types).items():
        if cnt > 1:
            results.append(InvariantResult(
                "coi_duplicate_coverage_type", InvariantLevel.WARNING,
                f"coverage_type '{ctype}' appears {cnt} times",
                "coi",
            ))

    return results


# ---------------------------------------------------------------------------
# Loss run invariants
# ---------------------------------------------------------------------------

def check_loss_run_invariants(extraction: dict) -> list[InvariantResult]:
    results: list[InvariantResult] = []
    claims = extraction.get("claims", [])
    total_paid = extraction.get("total_paid")
    total_incurred = extraction.get("total_incurred")

    # Per-claim: paid_amount + reserved_amount == total_incurred (±2%)
    for claim in claims:
        paid = claim.get("paid_amount") or 0.0
        reserved = claim.get("reserved_amount") or 0.0
        incurred = claim.get("total_incurred")
        if incurred is not None:
            expected_incurred = round(paid + reserved, 2)
            if abs(incurred) > 0.01:
                rel_err = abs(expected_incurred - incurred) / abs(incurred)
            else:
                rel_err = abs(expected_incurred - incurred)
            if rel_err > 0.02:
                results.append(InvariantResult(
                    f"loss_run_claim_incurred_{claim.get('claim_number', 'unknown')}",
                    InvariantLevel.ERROR,
                    f"paid({paid}) + reserved({reserved}) = {expected_incurred:.2f} "
                    f"!= total_incurred({incurred}) (rel_err={rel_err:.3f})",
                    "loss_run",
                ))

    # Closed claims must have reserved_amount == 0
    for claim in claims:
        if claim.get("status") == "closed" and (claim.get("reserved_amount") or 0.0) > 0:
            results.append(InvariantResult(
                f"loss_run_closed_reserve_{claim.get('claim_number', 'unknown')}",
                InvariantLevel.WARNING,
                f"Closed claim {claim.get('claim_number')} has "
                f"reserved_amount={claim.get('reserved_amount')}",
                "loss_run",
            ))

    # sum(claims[i].paid_amount) == total_paid (±2%)
    if total_paid is not None and claims:
        computed_paid = round(sum(c.get("paid_amount") or 0.0 for c in claims), 2)
        ref = abs(total_paid) if abs(total_paid) > 0.01 else 1.0
        rel_err = abs(computed_paid - total_paid) / ref
        if rel_err > 0.02:
            results.append(InvariantResult(
                "loss_run_total_paid_sum", InvariantLevel.ERROR,
                f"sum(paid_amounts)={computed_paid:.2f} != total_paid={total_paid:.2f} "
                f"(rel_err={rel_err:.3f})",
                "loss_run",
            ))

    # sum(claims[i].total_incurred) == total_incurred (±2%)
    # Catches v2 abs() sign bug: aggregate total_incurred is recomputed with abs(),
    # but per-claim total_incurred values remain correct.
    if total_incurred is not None and claims:
        computed_incurred = round(
            sum(c.get("total_incurred") or 0.0 for c in claims), 2
        )
        ref = abs(total_incurred) if abs(total_incurred) > 0.01 else 1.0
        rel_err = abs(computed_incurred - total_incurred) / ref
        if rel_err > 0.02:
            results.append(InvariantResult(
                "loss_run_total_incurred_sum", InvariantLevel.ERROR,
                f"sum(claim.total_incurred)={computed_incurred:.2f} != "
                f"total_incurred={total_incurred:.2f} (rel_err={rel_err:.3f})",
                "loss_run",
            ))

    # Unit drift detection: flag any claim whose |paid_amount| exceeds 50× median
    amounts = [
        abs(c.get("paid_amount") or 0.0)
        for c in claims
        if c.get("paid_amount") is not None and abs(c.get("paid_amount") or 0.0) > 0.01
    ]
    if amounts:
        sorted_amounts = sorted(amounts)
        median_abs = sorted_amounts[len(sorted_amounts) // 2]
        if median_abs > 0:
            for claim in claims:
                amt = claim.get("paid_amount")
                if amt is not None and abs(amt) > median_abs * 50:
                    results.append(InvariantResult(
                        f"loss_run_unit_drift_{claim.get('claim_number', 'unknown')}",
                        InvariantLevel.ERROR,
                        f"Claim {claim.get('claim_number')}: paid_amount={amt:.2f} is "
                        f"{abs(amt) / median_abs:.0f}× above median {median_abs:.2f}. "
                        "Possible unit drift (cents vs dollars).",
                        "loss_run",
                    ))

    return results


# ---------------------------------------------------------------------------
# Binder invariants
# ---------------------------------------------------------------------------

def check_binder_invariants(extraction: dict) -> list[InvariantResult]:
    results: list[InvariantResult] = []
    from eval.normalizers import normalize_date

    eff = extraction.get("binder_effective_date")
    exp = extraction.get("binder_expiration_date")
    if eff and exp:
        n_eff = normalize_date(eff)
        n_exp = normalize_date(exp)
        if n_eff and n_exp:
            if n_eff >= n_exp:
                results.append(InvariantResult(
                    "binder_date_ordering", InvariantLevel.ERROR,
                    f"binder_effective_date={eff} >= binder_expiration_date={exp}",
                    "binder",
                ))
            else:
                try:
                    from datetime import date
                    d_eff = date.fromisoformat(n_eff)
                    d_exp = date.fromisoformat(n_exp)
                    duration = (d_exp - d_eff).days
                    if duration > 180:
                        results.append(InvariantResult(
                            "binder_duration_excessive", InvariantLevel.WARNING,
                            f"Binder duration {duration} days exceeds typical 30-60 day range",
                            "binder",
                        ))
                except ValueError:
                    pass

    # Coverage date ordering within binder
    for i, cov in enumerate(extraction.get("coverages", [])):
        cov_eff = cov.get("effective_date")
        cov_exp = cov.get("expiration_date")
        if cov_eff and cov_exp:
            n_ceff = normalize_date(cov_eff)
            n_cexp = normalize_date(cov_exp)
            if n_ceff and n_cexp and n_ceff >= n_cexp:
                results.append(InvariantResult(
                    f"binder_coverage_date_ordering_{i}", InvariantLevel.ERROR,
                    f"Coverage {i}: effective_date={cov_eff} >= expiration_date={cov_exp}",
                    "binder",
                ))

    return results


# ---------------------------------------------------------------------------
# Endorsement invariants
# ---------------------------------------------------------------------------

def check_endorsement_invariants(extraction: dict) -> list[InvariantResult]:
    results: list[InvariantResult] = []
    from eval.normalizers import normalize_date

    eff = extraction.get("endorsement_effective_date")
    if eff and normalize_date(eff) is None:
        results.append(InvariantResult(
            "endorsement_date_unparseable", InvariantLevel.WARNING,
            f"Cannot parse endorsement_effective_date: {eff!r}",
            "endorsement",
        ))
    return results


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_DISPATCH = {
    "sov": check_sov_invariants,
    "coi": check_coi_invariants,
    "loss_run": check_loss_run_invariants,
    "binder": check_binder_invariants,
    "endorsement": check_endorsement_invariants,
}


def check_invariants(doc_type: str, extraction: dict) -> list[InvariantResult]:
    """Run all invariant checks for the given doc_type and return results."""
    fn = _DISPATCH.get(doc_type)
    return fn(extraction) if fn else []
