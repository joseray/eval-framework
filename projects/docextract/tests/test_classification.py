"""Classification accuracy and confidence tests.

Checks that doc_type is classified correctly and flags high-confidence
misroutes — a high-confidence wrong answer is worse than a low-confidence one.

Known controlled behavior:
  - coi_travelers_umbrella: classifier routes as 'policy' (template-matching
    artifact, confidence 95-99). This is a known misroute, flagged here.
"""

import pytest

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

# Documents known to misroute — these are documented bugs, not test failures
KNOWN_MISROUTES: dict[str, str] = {
    "coi_travelers_umbrella": "policy",
}


@pytest.mark.parametrize("document_id", DOCS_WITH_GT)
def test_classification_accuracy(api_client, ground_truth, settings, document_id):
    """Doc type classification must be correct in at least 80% of runs.

    Known misroutes are flagged but expected to fail — they're existing bugs.
    """
    gt = ground_truth.get(document_id)
    if gt is None:
        pytest.skip(f"No ground truth for {document_id}")

    expected_type = gt.doc_type
    runs, _ = api_client.extract_n_times(
        document_id, "v1", settings.n_runs, settings.seed_base
    )
    if not runs:
        pytest.skip(f"{document_id}: no successful runs")

    classifications = [r["classification"] for r in runs]
    correct = sum(1 for c in classifications if c["doc_type"] == expected_type)
    accuracy = correct / len(classifications)

    confs = [c["confidence"] for c in classifications]
    mean_conf = sum(confs) / len(confs)

    # Flag high-confidence misroutes specifically
    misroutes = [c for c in classifications if c["doc_type"] != expected_type]
    high_conf_misroutes = [c for c in misroutes if c["confidence"] >= 90]

    print(f"\n{document_id}: expected={expected_type} accuracy={accuracy:.0%} "
          f"mean_conf={mean_conf:.1f}")
    if misroutes:
        misroute_types = {c["doc_type"] for c in misroutes}
        print(f"  Misrouted as: {misroute_types}")
    if high_conf_misroutes:
        print(f"  ⚠ HIGH-CONFIDENCE misroutes: {len(high_conf_misroutes)}/{len(runs)}")

    # Known misroutes are documented bugs — skip the accuracy assertion
    if document_id in KNOWN_MISROUTES:
        expected_wrong_type = KNOWN_MISROUTES[document_id]
        wrong_count = sum(1 for c in classifications if c["doc_type"] == expected_wrong_type)
        wrong_rate = wrong_count / len(classifications)
        print(f"  Known misroute to '{expected_wrong_type}': {wrong_rate:.0%} of runs")
        pytest.xfail(
            f"{document_id}: known misroute to '{expected_wrong_type}' ({wrong_rate:.0%})"
        )

    assert accuracy >= 0.80, (
        f"{document_id}: classification accuracy {accuracy:.0%} < 80%"
    )
