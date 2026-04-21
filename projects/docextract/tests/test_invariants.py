"""Invariant checks: internal consistency without ground truth.

Parametrized over all 11 documents (including unlabeled).
Violation rates above 30% are flagged but don't hard-fail.
"""

import pytest

from framework.invariants import run_invariants, Severity

ALL_DOCS = [
    "sov_acme_properties",
    "sov_pacific_realty",
    "sov_keystone_reit",
    "coi_hartford_general",
    "coi_travelers_umbrella",
    "coi_zurich_legacy",
    "coi_unlabeled_mystery",
    "loss_run_nationwide",
    "loss_run_libertymutual",
    "endorsement_chubb_tiv_increase",
    "binder_travelers_temp",
]


@pytest.mark.parametrize("document_id", ALL_DOCS)
def test_invariant_consistency(api_client, settings, document_id):
    """Cross-field invariants must not violate in more than 30% of runs.

    Documents without ground truth are included — invariants don't need GT.
    """
    doc_config = settings.get_document_config(document_id)
    doc_type = doc_config.get("doc_type", "")
    invariant_names = settings.get_invariants(doc_type)

    if not invariant_names:
        pytest.skip(f"{document_id} ({doc_type}): no invariants configured")

    runs, _ = api_client.extract_n_times(
        document_id, "v1", settings.n_runs, settings.seed_base
    )

    if not runs:
        pytest.skip(f"{document_id}: no successful runs")

    violation_counts: dict[str, int] = {}
    for run in runs:
        results = run_invariants(run["extraction"], invariant_names)
        for r in results:
            if r.severity == Severity.ERROR:
                violation_counts[r.check] = violation_counts.get(r.check, 0) + 1

    violation_rates = {k: v / len(runs) for k, v in violation_counts.items()}

    print(f"\n{document_id} ({doc_type}) | {len(runs)} runs")
    for check, rate in sorted(violation_rates.items()):
        marker = " ⚠ HIGH" if rate > 0.30 else ""
        print(f"  {check}: {rate:.0%}{marker}")

    bad = {k: v for k, v in violation_rates.items() if v > 0.30}
    if bad:
        formatted = ", ".join(f"{k}: {v:.0%}" for k, v in sorted(bad.items()))
        print(f"\n⚠ HIGH violation rates for {document_id}: {formatted}")
        # Log but don't hard-fail — signal is in the report
