"""Insurance domain-specific invariants."""

from __future__ import annotations

from framework.invariants import register_invariant, InvariantResult, Severity


@register_invariant("component_sum")
def check_component_sum(extraction: dict, **kwargs) -> list[InvariantResult]:
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
                results.append(InvariantResult(
                    check="component_sum",
                    severity=Severity.ERROR,
                    message=f"Property {i}: component sum {bv+cv+bi:,.0f} != TIV {tiv:,.0f} (drift {drift:.1%})",
                    details={"index": i, "building": bv, "contents": cv, "bi": bi, "tiv": tiv, "drift": drift},
                ))
    return results


@register_invariant("total_tiv_sum")
def check_total_tiv_sum(extraction: dict, **kwargs) -> list[InvariantResult]:
    """SOV: sum of per-property total_insured_value == total_tiv."""
    results = []
    properties = extraction.get("properties", [])
    total_tiv = extraction.get("total_tiv")
    if not properties or total_tiv is None:
        return results

    prop_sum = sum(p.get("total_insured_value") or 0 for p in properties)
    if total_tiv > 0:
        drift = abs(prop_sum - total_tiv) / total_tiv
        if drift > 0.05:
            results.append(InvariantResult(
                check="total_tiv_sum",
                severity=Severity.ERROR,
                message=f"Property TIV sum {prop_sum:,.0f} != total_tiv {total_tiv:,.0f} (drift {drift:.1%})",
                details={"property_sum": prop_sum, "total_tiv": total_tiv, "drift": drift},
            ))
    return results


@register_invariant("duplicate_coverage")
def check_duplicate_coverage(extraction: dict, **kwargs) -> list[InvariantResult]:
    """COI: no duplicate coverage_type entries."""
    results = []
    seen: dict[str, int] = {}
    for i, cov in enumerate(extraction.get("coverages", [])):
        ct = cov.get("coverage_type")
        if ct:
            if ct in seen:
                results.append(InvariantResult(
                    check="duplicate_coverage",
                    severity=Severity.ERROR,
                    message=f"coverage_type '{ct}' appears at index {seen[ct]} and {i}",
                    details={"coverage_type": ct, "first_index": seen[ct], "second_index": i},
                ))
            else:
                seen[ct] = i
    return results


@register_invariant("phantom_coverage")
def check_phantom_coverage(extraction: dict, **kwargs) -> list[InvariantResult]:
    """COI: detect unexpected coverage types not in a known valid set.

    Known valid types are the standard ACORD 25 lines. Anything outside
    this set that appears in the extraction is flagged as potentially phantom.
    """
    KNOWN_COVERAGE_TYPES = {
        "general_liability",
        "auto",
        "umbrella",
        "workers_comp",
        "professional_liability",
        "cyber",
        "property",
        "inland_marine",
        "crime",
        "excess",
    }
    results = []
    for i, cov in enumerate(extraction.get("coverages", [])):
        ct = cov.get("coverage_type", "")
        if ct and ct.lower() not in KNOWN_COVERAGE_TYPES:
            results.append(InvariantResult(
                check="phantom_coverage",
                severity=Severity.ERROR,
                message=f"Unexpected coverage_type '{ct}' at index {i} — possible phantom",
                details={"index": i, "coverage_type": ct},
            ))
    return results


@register_invariant("incurred_sum")
def check_incurred_sum(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Loss run: per-claim total_incurred == paid_amount + reserved_amount."""
    results = []
    for i, claim in enumerate(extraction.get("claims", [])):
        paid = claim.get("paid_amount") or 0
        reserved = claim.get("reserved_amount") or 0
        incurred = claim.get("total_incurred")
        if incurred is not None and incurred > 0:
            expected = paid + reserved
            drift = abs(expected - incurred) / incurred
            if drift > 0.03:
                results.append(InvariantResult(
                    check="incurred_sum",
                    severity=Severity.ERROR,
                    message=(
                        f"Claim {i}: paid {paid:,.0f} + reserved {reserved:,.0f} = {expected:,.0f} "
                        f"!= total_incurred {incurred:,.0f} (drift {drift:.1%})"
                    ),
                    details={"index": i, "paid": paid, "reserved": reserved,
                             "expected_sum": expected, "total_incurred": incurred},
                ))
    return results


@register_invariant("total_paid_sum")
def check_total_paid_sum(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Loss run: sum of claim paid_amounts == total_paid."""
    results = []
    claims = extraction.get("claims", [])
    total_paid = extraction.get("total_paid")
    if not claims or total_paid is None:
        return results

    claim_sum = sum(c.get("paid_amount") or 0 for c in claims)
    if total_paid > 0:
        drift = abs(claim_sum - total_paid) / total_paid
        if drift > 0.05:
            results.append(InvariantResult(
                check="total_paid_sum",
                severity=Severity.ERROR,
                message=f"Claim paid sum {claim_sum:,.0f} != total_paid {total_paid:,.0f} (drift {drift:.1%})",
                details={"claim_sum": claim_sum, "total_paid": total_paid, "drift": drift},
            ))
    return results


@register_invariant("total_incurred_sum")
def check_total_incurred_sum(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Loss run: sum of claim total_incurreds == total_incurred."""
    results = []
    claims = extraction.get("claims", [])
    total_incurred = extraction.get("total_incurred")
    if not claims or total_incurred is None:
        return results

    claim_sum = sum(c.get("total_incurred") or 0 for c in claims)
    if total_incurred > 0:
        drift = abs(claim_sum - total_incurred) / total_incurred
        if drift > 0.05:
            results.append(InvariantResult(
                check="total_incurred_sum",
                severity=Severity.ERROR,
                message=f"Claim incurred sum {claim_sum:,.0f} != total_incurred {total_incurred:,.0f} (drift {drift:.1%})",
                details={"claim_sum": claim_sum, "total_incurred": total_incurred, "drift": drift},
            ))
    return results


@register_invariant("closed_reserves_zero")
def check_closed_reserves_zero(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Loss run: closed claims should have reserved_amount == 0."""
    results = []
    for i, claim in enumerate(extraction.get("claims", [])):
        if claim.get("status") == "closed":
            reserved = claim.get("reserved_amount") or 0
            if reserved > 0:
                results.append(InvariantResult(
                    check="closed_reserves_zero",
                    severity=Severity.ERROR,
                    message=f"Claim {i} is closed but has reserved_amount={reserved:,.0f}",
                    details={"index": i, "reserved_amount": reserved},
                ))
    return results


@register_invariant("unit_drift_detection")
def check_unit_drift_detection(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Loss run: detect 100x artifacts in paid/reserved amounts.

    Compares each claim's paid_amount to the median to detect outliers
    that are ~100x larger or smaller (unit conversion bugs).
    """
    results = []
    claims = extraction.get("claims", [])
    if len(claims) < 2:
        return results

    paid_amounts = [c.get("paid_amount") or 0 for c in claims]
    non_zero = [p for p in paid_amounts if p > 0]
    if len(non_zero) < 2:
        return results

    sorted_amounts = sorted(non_zero)
    median = sorted_amounts[len(sorted_amounts) // 2]
    if median == 0:
        return results

    for i, claim in enumerate(claims):
        paid = claim.get("paid_amount") or 0
        if paid > 0:
            ratio = paid / median
            if ratio > 50 or ratio < 0.02:
                results.append(InvariantResult(
                    check="unit_drift_detection",
                    severity=Severity.ERROR,
                    message=(
                        f"Claim {i}: paid_amount={paid:,.0f} is {ratio:.0f}x the median "
                        f"{median:,.0f} — possible unit drift"
                    ),
                    details={"index": i, "paid_amount": paid, "median": median, "ratio": ratio},
                ))
    return results


@register_invariant("binder_duration")
def check_binder_duration(extraction: dict, **kwargs) -> list[InvariantResult]:
    """Binder: expiration should be 30-90 days after effective date."""
    from framework.normalizers import normalize_date
    from datetime import date

    results = []
    eff_str = normalize_date(extraction.get("binder_effective_date"))
    exp_str = normalize_date(extraction.get("binder_expiration_date"))

    if not eff_str or not exp_str:
        return results

    try:
        eff = date.fromisoformat(eff_str)
        exp = date.fromisoformat(exp_str)
        delta_days = (exp - eff).days

        if delta_days <= 0:
            results.append(InvariantResult(
                check="binder_duration",
                severity=Severity.ERROR,
                message=f"Binder expiration {exp_str} is not after effective {eff_str}",
                details={"effective": eff_str, "expiration": exp_str, "days": delta_days},
            ))
        elif delta_days > 180:
            results.append(InvariantResult(
                check="binder_duration",
                severity=Severity.WARNING,
                message=f"Binder duration {delta_days} days exceeds typical 30-90 day range",
                details={"effective": eff_str, "expiration": exp_str, "days": delta_days},
            ))
    except ValueError:
        pass

    return results
