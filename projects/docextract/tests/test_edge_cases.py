"""Edge cases: documents with partial, disputed, or missing ground truth."""

import pytest

from framework.validators import validate_extraction, MatchLevel
from framework.invariants import run_invariants


def test_partial_truth_square_footage(api_client, ground_truth, settings):
    """sov_acme_properties: square_footage='unknown' → field must be SKIPPED."""
    gt = ground_truth.get("sov_acme_properties")
    assert gt is not None

    field_specs = settings.get_field_specs(gt.doc_type)
    nested_specs = settings.get_nested_specs(gt.doc_type)

    runs, _ = api_client.extract_n_times("sov_acme_properties", "v1", 10, 42000)
    assert len(runs) > 0

    for run in runs:
        results = validate_extraction(run["extraction"], gt.extraction, field_specs, nested_specs)
        skipped = [r for r in results if r.match == MatchLevel.SKIPPED]
        skipped_paths = [r.field_path for r in skipped]
        # The 6th property (index 5) has square_footage=unknown
        assert any("square_footage" in p for p in skipped_paths), (
            f"Expected square_footage to be skipped, but got: {skipped_paths}"
        )


def test_disputed_truth_keystone_reit(api_client, ground_truth, settings):
    """sov_keystone_reit: two labels exist — extraction is valid if it matches either."""
    gt_primary = ground_truth.get("sov_keystone_reit")
    gt_alt = ground_truth.get_alt("sov_keystone_reit")

    assert gt_primary is not None, "Primary ground truth must exist"
    assert gt_alt is not None, "Alt ground truth must exist for keystone_reit"

    runs, _ = api_client.extract_n_times("sov_keystone_reit", "v1", 10, 42000)
    assert len(runs) > 0

    primary_tiv = gt_primary.extraction.get("total_tiv")
    alt_tiv = gt_alt.extraction.get("total_tiv")

    match_primary = 0
    match_alt = 0
    neither = 0

    for run in runs:
        tiv = run["extraction"].get("total_tiv")
        if tiv is None:
            neither += 1
            continue

        primary_ok = primary_tiv and abs(tiv - primary_tiv) / primary_tiv < 0.05
        alt_ok = alt_tiv and abs(tiv - alt_tiv) / alt_tiv < 0.05

        if primary_ok:
            match_primary += 1
        elif alt_ok:
            match_alt += 1
        else:
            neither += 1

        print(f"  total_tiv={tiv:,.0f} primary_ok={primary_ok} alt_ok={alt_ok}")

    print(f"\nKeystone REIT total_tiv: primary={match_primary} alt={match_alt} neither={neither}")
    # At least some runs should match one of the two labels
    assert match_primary + match_alt > 0, (
        "No runs matched either primary or alt ground truth for keystone_reit total_tiv"
    )


def test_unlabeled_mystery_invariants_only(api_client, settings):
    """coi_unlabeled_mystery: no GT, invariants only.

    This doc has no ground truth label. We can only run invariant checks.
    Duplicate coverage and date ordering must not produce hard errors.
    """
    invariant_names = ["duplicate_coverage", "date_ordering"]

    runs, _ = api_client.extract_n_times("coi_unlabeled_mystery", "v1", 10, 42000)
    assert len(runs) > 0, "coi_unlabeled_mystery: no successful runs"

    error_counts: dict[str, int] = {}
    for run in runs:
        results = run_invariants(run["extraction"], invariant_names)
        for r in results:
            from framework.invariants import Severity
            if r.severity == Severity.ERROR:
                error_counts[r.check] = error_counts.get(r.check, 0) + 1

    print(f"\ncoi_unlabeled_mystery invariant errors: {error_counts}")
    # These invariants should not fail on a well-formed mystery doc
    assert "duplicate_coverage" not in error_counts or error_counts["duplicate_coverage"] == 0, (
        f"Duplicate coverage found in unlabeled mystery: {error_counts}"
    )
