"""Extraction accuracy field-by-field against ground truth.

Runs N extractions per document, compares each field, and asserts
that accuracy is above the CI threshold (60%).

Expected controlled failures:
  - sov_pacific_realty: total_tiv calibration bias (factor 0.895)
  - sov_keystone_reit: construction_type omission (40%)
"""

import pytest

from framework.validators import validate_extraction, MatchLevel

DOCS_WITH_GT = [
    "sov_acme_properties",
    "sov_pacific_realty",
    "sov_keystone_reit",
    "coi_hartford_general",
    "coi_travelers_umbrella",
    "coi_zurich_legacy",
    "loss_run_nationwide",
    "loss_run_libertymutual",
    "endorsement_chubb_tiv_increase",
    "binder_travelers_temp",
]

PASSING = {MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH}


@pytest.mark.parametrize("document_id", DOCS_WITH_GT)
def test_field_accuracy(api_client, ground_truth, settings, document_id):
    """Every field with ground truth must match in at least 60% of N runs."""
    gt = ground_truth.get(document_id)
    if gt is None:
        pytest.skip(f"No ground truth for {document_id}")

    field_specs = settings.get_field_specs(gt.doc_type)
    nested_specs = settings.get_nested_specs(gt.doc_type)

    runs, failures = api_client.extract_n_times(
        document_id,
        model="v1",
        n=settings.n_runs,
        seed_base=settings.seed_base,
    )

    assert len(runs) > 0, f"{document_id}: all {failures} runs failed"

    # Aggregate per-field accuracy across all runs
    field_hits: dict[str, list[bool]] = {}
    for run in runs:
        results = validate_extraction(
            run["extraction"],
            gt.extraction,
            field_specs,
            nested_specs,
        )
        for r in results:
            if r.match == MatchLevel.SKIPPED:
                continue
            field_hits.setdefault(r.field_path, []).append(r.match in PASSING)

    accuracy = {fp: sum(h) / len(h) for fp, h in field_hits.items()}
    failing = {fp: acc for fp, acc in accuracy.items() if acc < settings.min_field_accuracy}

    print(f"\n{'='*60}")
    print(f"{document_id} | model=v1 | {len(runs)} runs | {failures} failures")
    if failing:
        print("FAILING fields:")
        for fp, acc in sorted(failing.items(), key=lambda x: x[1]):
            print(f"  {fp}: {acc:.0%}")
    print(f"PASSING: {len(accuracy) - len(failing)}/{len(accuracy)} fields")

    assert not failing, (
        f"{document_id}: fields below {settings.min_field_accuracy:.0%}:\n"
        + "\n".join(
            f"  {fp}: {acc:.0%}" for fp, acc in sorted(failing.items())
        )
    )
