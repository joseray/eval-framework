"""Model comparison: v2 must not regress vs v1.

Runs both models with the same seeds for a fair comparison.
Asserts no field regresses more than 10pp.
"""

import pytest

from framework.validators import validate_extraction, MatchLevel
from framework.assertions import assert_no_regression

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


def _compute_accuracy(
    runs: list[dict],
    gt_extraction: dict,
    field_specs: dict,
    nested_specs: dict,
) -> dict[str, float]:
    field_hits: dict[str, list[bool]] = {}
    for run in runs:
        results = validate_extraction(run["extraction"], gt_extraction, field_specs, nested_specs)
        for r in results:
            if r.match == MatchLevel.SKIPPED:
                continue
            field_hits.setdefault(r.field_path, []).append(r.match in PASSING)
    return {fp: sum(h) / len(h) for fp, h in field_hits.items()}


@pytest.mark.parametrize("document_id", DOCS_WITH_GT)
def test_v2_does_not_regress(api_client, ground_truth, settings, document_id):
    """v2 must not regress more than 10pp on any field vs v1."""
    gt = ground_truth.get(document_id)
    if gt is None:
        pytest.skip(f"No ground truth for {document_id}")

    specs = settings.get_field_specs(gt.doc_type)
    nested = settings.get_nested_specs(gt.doc_type)

    # Same seeds for fair comparison
    runs_v1, _ = api_client.extract_n_times(
        document_id, "v1", settings.n_runs, settings.seed_base
    )
    runs_v2, _ = api_client.extract_n_times(
        document_id, "v2", settings.n_runs, settings.seed_base
    )

    acc_v1 = _compute_accuracy(runs_v1, gt.extraction, specs, nested)
    acc_v2 = _compute_accuracy(runs_v2, gt.extraction, specs, nested)

    print(f"\n{'='*60}")
    print(f"MODEL COMPARISON: {document_id}")
    for fp in sorted(set(acc_v1) | set(acc_v2)):
        a1 = acc_v1.get(fp, 0.0)
        a2 = acc_v2.get(fp, 0.0)
        delta = a2 - a1
        marker = "" if abs(delta) < 0.05 else (" ▲" if delta > 0 else " ▼")
        print(f"  {fp}: v1={a1:.0%} v2={a2:.0%} {delta:+.0%}{marker}")

    assert_no_regression(acc_v1, acc_v2, settings.max_regression_pp, document_id)
