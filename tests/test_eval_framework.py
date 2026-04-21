"""DocExtract eval harness — full test suite.

Test layout (45 tests total; 41 pass, 4 fail on the known bugs):

  Section 1  — Normalizer unit tests          (6, all pass)
  Section 2  — Comparator unit tests          (6, all pass)
  Section 3  — Invariant logic unit tests     (4, all pass)
  Section 4  — Config / contract tests        (3, all pass)
  Section 5  — Integration tests              (4, all pass)
  Section 6  — Classification tests           (3, all pass)
  Section 7  — Accuracy: known-good docs      (6, all pass)
  Section 8  — Bug detection tests            (4, ALL FAIL — bugs are present)
  Section 9  — Model comparison tests         (3, all pass)
  Section 10 — Reseed resilience              (2, all pass)
  Section 11 — Bias detection                 (1, passes)
  Section 12 — Unlabeled doc invariants       (1, passes)
  Section 13 — Runner infrastructure          (2, all pass)

The 4 expected failures surface real pipeline defects:
  - sov_pacific_realty  : TIV calibration bias (factor 0.895 in v1)
  - sov_keystone_reit   : construction_type omission rate 40%
  - coi_zurich_legacy   : phantom "cyber" coverage injection 20%
  - loss_run_libertymutual: paid_amount unit drift ×100 at 15%
"""

from __future__ import annotations

import pytest

from eval.comparators import FieldResult, MatchLevel, compare_field
from eval.invariants import InvariantLevel, check_invariants, check_loss_run_invariants
from eval.normalizers import (
    normalize_amount,
    normalize_carrier,
    normalize_date,
    normalize_string,
)
from eval.runner import (
    ALL_DOCUMENT_IDS,
    DOCUMENTS_WITH_GROUND_TRUTH,
    DocumentReport,
    EvalRunner,
)


# ===========================================================================
# Section 1: Normalizer unit tests
# ===========================================================================

def test_normalize_date_iso_passthrough():
    assert normalize_date("2025-07-15") == "2025-07-15"


def test_normalize_date_us_format():
    # 07/15/2025 — second token > 12, so first must be month
    assert normalize_date("07/15/2025") == "2025-07-15"


def test_normalize_date_eu_unambiguous():
    # 15/07/2025 — first token > 12, must be DD/MM
    assert normalize_date("15/07/2025") == "2025-07-15"


def test_normalize_carrier_short_form():
    assert normalize_carrier("The Hartford") == "Hartford Financial Services"
    assert normalize_carrier("Travelers") == "The Travelers Indemnity Company"
    assert normalize_carrier("Zurich North America") == "Zurich Insurance"


def test_normalize_carrier_unknown_passthrough():
    # Carriers not in the canonical map are returned unchanged
    assert normalize_carrier("XL Catlin Commercial") == "XL Catlin Commercial"
    assert normalize_carrier("Chubb Commercial Insurance") == "Chubb Commercial Insurance"


def test_normalize_amount_rounds_to_2dp():
    assert normalize_amount(None) is None
    assert normalize_amount(100.0) == 100.0
    assert normalize_amount(100.999) == 101.0   # third decimal forces second decimal up
    assert normalize_amount(9000000.1234) == 9000000.12


# ===========================================================================
# Section 2: Comparator unit tests
# ===========================================================================

def test_compare_identifier_exact_match():
    result = compare_field("policy_number", "LM-CP-2021-887412", "LM-CP-2021-887412")
    assert result.level == MatchLevel.EXACT
    assert result.passed


def test_compare_identifier_case_insensitive():
    result = compare_field("policy_number", "lm-cp-2021-887412", "LM-CP-2021-887412")
    assert result.level == MatchLevel.EXACT
    assert result.passed


def test_compare_date_cross_format():
    # US format vs ISO ground truth — both normalize to same ISO
    result = compare_field("effective_date", "07/15/2025", "2025-07-15")
    assert result.passed


def test_compare_amount_within_3pct_tolerance():
    result = compare_field("building_value", 9270000.0, 9000000.0)  # +3% exactly
    assert result.passed


def test_compare_amount_outside_tolerance():
    result = compare_field("building_value", 9450000.0, 9000000.0)  # +5% > 3% tolerance
    assert result.level == MatchLevel.MISMATCH
    assert not result.passed


def test_compare_carrier_via_normalization():
    # "The Hartford" and "Hartford Financial Services" both normalize to canonical
    result = compare_field("carrier", "The Hartford", "Hartford Financial Services")
    assert result.passed


# ===========================================================================
# Section 3: Invariant logic unit tests
# ===========================================================================

def test_invariant_sov_tiv_sum_detects_mismatch():
    bad_extraction = {
        "properties": [
            {"total_insured_value": 10000000.0},
            {"total_insured_value": 5000000.0},
        ],
        "total_tiv": 20000000.0,  # wrong — components sum to 15M
    }
    results = check_invariants("sov", bad_extraction)
    error_names = [r.name for r in results if r.level == InvariantLevel.ERROR]
    assert "sov_tiv_sum" in error_names


def test_invariant_sov_component_sum_ok():
    good_extraction = {
        "properties": [
            {
                "building_value": 9000000.0,
                "contents_value": 2800000.0,
                "business_income_value": 1000000.0,
                "total_insured_value": 12800000.0,
            }
        ],
        "total_tiv": 12800000.0,
    }
    results = check_invariants("sov", good_extraction)
    assert not any(r.level == InvariantLevel.ERROR for r in results)


def test_invariant_loss_run_closed_reserve_violation():
    bad_extraction = {
        "claims": [
            {
                "claim_number": "CLM-001",
                "status": "closed",
                "paid_amount": 10000.0,
                "reserved_amount": 5000.0,  # closed but has reserve
                "total_incurred": 15000.0,
            }
        ],
        "total_paid": 10000.0,
        "total_incurred": 15000.0,
    }
    results = check_invariants("loss_run", bad_extraction)
    warning_names = [r.name for r in results if r.level == InvariantLevel.WARNING]
    assert any("closed_reserve" in name for name in warning_names)


def test_invariant_binder_date_swap_detected():
    swapped = {
        "binder_effective_date": "2025-04-11",   # later date is effective
        "binder_expiration_date": "2025-03-12",  # earlier date is expiration
        "coverages": [],
    }
    results = check_invariants("binder", swapped)
    assert any(r.name == "binder_date_ordering" and r.level == InvariantLevel.ERROR for r in results)


# ===========================================================================
# Section 4: Config / contract tests
# ===========================================================================

def test_health_endpoint_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_config_endpoint_returns_expected_fields(client):
    resp = client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("auto_commit_threshold", "max_retries", "supported_doc_types", "model"):
        assert key in data, f"Missing config key: {key}"


def test_config_auto_commit_threshold_is_known_low(client):
    # AUTO_COMMIT_THRESHOLD=5 is a documented misconfiguration.
    # This test asserts the current (wrong) value so any accidental change is caught.
    # The correct production value should be ≥ 85.
    threshold = client.get("/config").json()["auto_commit_threshold"]
    assert threshold == 5, (
        f"AUTO_COMMIT_THRESHOLD changed to {threshold}. "
        "If this is intentional, update this test. Target production value is ≥ 85."
    )


# ===========================================================================
# Section 5: Integration tests
# ===========================================================================

def test_extract_sov_returns_200(client):
    resp = client.post("/extract", json={"document_id": "sov_acme_properties", "seed": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert "extraction" in data
    assert "classification" in data
    assert "total_tiv" in data["extraction"]


def test_extract_coi_returns_200(client):
    resp = client.post("/extract", json={"document_id": "coi_hartford_general", "seed": 1})
    assert resp.status_code == 200
    assert "coverages" in resp.json()["extraction"]


def test_extract_loss_run_returns_200(client):
    resp = client.post("/extract", json={"document_id": "loss_run_nationwide", "seed": 1})
    assert resp.status_code == 200
    assert "claims" in resp.json()["extraction"]


def test_extract_unknown_doc_returns_404(client):
    resp = client.post("/extract", json={"document_id": "does_not_exist", "seed": 1})
    assert resp.status_code == 404


# ===========================================================================
# Section 6: Classification tests
# ===========================================================================

def test_classification_travelers_umbrella_misrouted_with_high_confidence(client):
    # coi_travelers_umbrella is a known misclassification: always routed as "policy"
    # with 95-99% confidence. This test confirms the bug is present and measurable.
    for seed in range(5):
        data = client.post(
            "/extract",
            json={"document_id": "coi_travelers_umbrella", "seed": seed},
        ).json()
        assert data["classification"]["doc_type"] == "policy", (
            f"seed={seed}: expected 'policy' override, got {data['classification']['doc_type']}"
        )
        assert data["classification"]["confidence"] >= 95.0


def test_classification_confidence_in_standard_range_for_correct_doc(client):
    # Documents without classification overrides should land in the 82-94 band.
    confidences = []
    for seed in range(10):
        data = client.post(
            "/extract",
            json={"document_id": "sov_acme_properties", "seed": seed},
        ).json()
        confidences.append(data["classification"]["confidence"])
    assert all(82.0 <= c <= 94.0 for c in confidences), (
        f"Confidence values outside expected [82, 94] range: {confidences}"
    )


def test_classification_correct_doc_not_misrouted(client):
    # SOV and loss-run docs should be classified with their canonical type.
    for doc_id, expected_type in [
        ("sov_pacific_realty", "sov"),
        ("loss_run_nationwide", "loss_run"),
        ("endorsement_chubb_tiv_increase", "endorsement"),
    ]:
        data = client.post(
            "/extract",
            json={"document_id": doc_id, "seed": 1},
        ).json()
        assert data["classification"]["doc_type"] == expected_type, (
            f"{doc_id}: expected type={expected_type}, got {data['classification']['doc_type']}"
        )


# ===========================================================================
# Section 7: Accuracy tests for known-good documents (parametrized)
# ===========================================================================

# These docs have no systematic extraction bugs. n=10 deterministic runs
# should produce ≥ 70% overall field accuracy under ±2% amount noise and
# 8% optional-field omission.
_KNOWN_GOOD_DOCS = [
    "sov_acme_properties",
    "coi_hartford_general",
    "coi_travelers_umbrella",
    "loss_run_nationwide",
    "endorsement_chubb_tiv_increase",
    "binder_travelers_temp",
]


@pytest.mark.parametrize("doc_id", _KNOWN_GOOD_DOCS)
def test_accuracy_known_good_docs(doc_id, runner):
    report = runner.evaluate_document(doc_id, model="v1", n=10)
    assert report.mean_field_accuracy >= 0.70, (
        f"{doc_id}: mean_field_accuracy={report.mean_field_accuracy:.3f} < 0.70. "
        "Unexpected regression on a doc with no known systematic bugs."
    )


# ===========================================================================
# Section 8: Bug detection tests  ← THESE 4 TESTS FAIL (bugs are real)
# ===========================================================================

def test_sov_pacific_realty_tiv_calibration_bias(runner):
    """v1 applies a 0.895 calibration factor to all TIV fields on Pacific Realty.

    With ±5% aggregate tolerance, a consistent 10.5% underestimate means every
    single run fails. Expected total_tiv accuracy = 0%. This test will FAIL
    until the calibration offset is corrected in the extraction model.
    """
    report = runner.evaluate_document("sov_pacific_realty", model="v1", n=20)
    tiv_accuracy = report.field_accuracy_by_name("total_tiv")
    assert tiv_accuracy >= 0.90, (
        f"total_tiv accuracy={tiv_accuracy:.2f} < 0.90. "
        f"Systematic TIV calibration bias detected (factor ~0.895). "
        f"Mean field accuracy={report.mean_field_accuracy:.2f}."
    )


def test_sov_keystone_reit_construction_type_omission(runner):
    """construction_type is dropped at a 40% rate for sov_keystone_reit properties.

    Over 20 runs × 15 properties = 300 comparisons, ~120 will be MISSING.
    Expected omission rate ≈ 40%. This test will FAIL until the extraction
    model's construction_type reporting gap is fixed.
    """
    report = runner.evaluate_document("sov_keystone_reit", model="v1", n=20)
    ct_results = [
        fr
        for run in report.run_results
        for fr in run.field_results
        if fr.field == "construction_type"
    ]
    total = len(ct_results)
    missing = sum(1 for fr in ct_results if fr.level == MatchLevel.MISSING)
    omission_rate = missing / total if total > 0 else 0.0
    assert omission_rate <= 0.15, (
        f"construction_type omission_rate={omission_rate:.2f} > 0.15 "
        f"({missing}/{total} comparisons are MISSING). "
        "40% systematic gap detected in sov_keystone_reit."
    )


def test_coi_zurich_legacy_phantom_coverage(runner):
    """A phantom 'cyber' coverage is injected in ~20% of coi_zurich_legacy runs.

    Ground truth has exactly 3 coverages. Any run with 4+ coverages is a
    hallucination. This test will FAIL until the phantom-coverage injection
    bug in the legacy ACORD 25 template-matching layer is fixed.
    """
    report = runner.evaluate_document("coi_zurich_legacy", model="v1", n=20)
    # hallucination_rate is mean hallucinated-field count per run; with 20% phantom
    # injection, ~4/20 runs will produce a hallucinated coverage entry.
    assert report.hallucination_rate == 0.0, (
        f"Phantom coverage detected: hallucination_rate={report.hallucination_rate:.2f} "
        f"({report.hallucination_rate * 20:.0f}/20 runs had extra coverage). "
        "Known bug: coi_zurich_legacy phantom 'cyber' coverage at ~20% rate."
    )


def test_loss_run_libertymutual_paid_unit_drift(runner):
    """paid_amount is occasionally emitted in cents instead of dollars (×100 drift).

    With 24 claims each having a 15% drift probability, nearly every run (≈98%)
    will have at least one drifted claim. This inflates total_paid massively.
    Expected total_paid accuracy ≈ 2-5%. This test will FAIL until the unit-
    normalization bug is fixed in the loss-run post-processor.
    """
    report = runner.evaluate_document("loss_run_libertymutual", model="v1", n=20)
    total_paid_accuracy = report.field_accuracy_by_name("total_paid")
    assert total_paid_accuracy >= 0.90, (
        f"total_paid accuracy={total_paid_accuracy:.2f} < 0.90. "
        "Paid-amount unit drift (×100) detected on loss_run_libertymutual. "
        f"Mean field accuracy={report.mean_field_accuracy:.2f}."
    )


# ===========================================================================
# Section 9: Model comparison tests
# ===========================================================================

def test_model_v2_corrects_tiv_calibration(runner):
    """v2 fixes the Pacific Realty TIV calibration offset; v1 has 0% accuracy."""
    v1_report = runner.evaluate_document("sov_pacific_realty", model="v1", n=10)
    v2_report = runner.evaluate_document("sov_pacific_realty", model="v2", n=10)
    v1_tiv = v1_report.field_accuracy_by_name("total_tiv")
    v2_tiv = v2_report.field_accuracy_by_name("total_tiv")
    assert v2_tiv > v1_tiv, (
        f"v2 total_tiv accuracy ({v2_tiv:.2f}) should exceed v1 ({v1_tiv:.2f}). "
        "v2 is supposed to have corrected the TIV calibration offset."
    )


def test_model_v2_subrogation_sign_bug_confirmed(runner):
    """v2 uses abs() when summing total_incurred, flipping negative subrogation amounts.

    This causes sum(claim.total_incurred) != total_incurred in every v2 run for
    loss_run_libertymutual. The invariant loss_run_total_incurred_sum should fire.
    """
    report = runner.evaluate_document("loss_run_libertymutual", model="v2", n=10)
    sign_bug_violations = sum(
        1
        for run in report.run_results
        for inv in run.invariant_results
        if inv.name == "loss_run_total_incurred_sum" and inv.level == InvariantLevel.ERROR
    )
    # The sign bug fires whenever the relative discrepancy stays above 2%.
    # Heavy unit drift can inflate the denominator and temporarily mask it,
    # so we require ≥ half of 10 runs rather than all 10.
    assert sign_bug_violations >= len(report.run_results) // 2, (
        f"Expected total_incurred_sum to fire in most v2 runs (sign bug is deterministic) "
        f"but got {sign_bug_violations}/{len(report.run_results)}. "
        "v2 abs() sign bug should dominate when unit drift doesn't inflate the denominator."
    )


def test_model_v2_producer_omission_regression(runner):
    """v2 has a 30% producer omission rate on COI documents vs 8% in v1."""
    v1_report = runner.evaluate_document("coi_hartford_general", model="v1", n=20)
    v2_report = runner.evaluate_document("coi_hartford_general", model="v2", n=20)

    def _producer_omission_rate(report: DocumentReport) -> float:
        missing = sum(
            1
            for run in report.run_results
            for fr in run.field_results
            if fr.field == "producer" and fr.level == MatchLevel.MISSING
        )
        total = sum(
            1
            for run in report.run_results
            for fr in run.field_results
            if fr.field == "producer"
        )
        return missing / total if total > 0 else 0.0

    v1_rate = _producer_omission_rate(v1_report)
    v2_rate = _producer_omission_rate(v2_report)
    assert v2_rate > v1_rate, (
        f"v2 producer omission ({v2_rate:.2f}) should exceed v1 ({v1_rate:.2f}). "
        "v2 regression: 30% producer omission vs v1's baseline 8%."
    )


# ===========================================================================
# Section 10: Reseed resilience
# ===========================================================================

def test_reseed_endpoint_succeeds(client):
    """POST /admin/reseed-bugs should return 200 and rotate assignments."""
    resp = client.post("/admin/reseed-bugs", params={"seed": 777})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "construction_type_omission" in data["current_assignments"]
    # Restore default assignment
    client.post("/admin/reseed-bugs", params={"seed": 42})


def test_reseed_eval_still_runs(runner, client):
    """Eval should complete without errors after a reseed.

    An eval framework that hardcodes document_id checks will fail after reseed
    because bug patterns rotate across documents. This test confirms our
    framework is not overfit to specific doc_ids.
    """
    client.post("/admin/reseed-bugs", params={"seed": 12345})
    for doc_id in ["sov_acme_properties", "coi_hartford_general"]:
        report = runner.evaluate_document(doc_id, n=3)
        assert report.n_runs == 3
    # Restore
    client.post("/admin/reseed-bugs", params={"seed": 42})


# ===========================================================================
# Section 11: Bias detection
# ===========================================================================

def test_pacific_realty_v1_tiv_bias_is_systematic(runner):
    """Confirms the Pacific Realty v1 TIV bias is directional, not random noise.

    A random error would oscillate above and below truth. A calibration factor
    consistently under-estimates. Here we confirm every seed underestimates TIV
    by a significant margin, characterizing the bias as systematic.
    """
    import json
    from pathlib import Path

    gt_path = Path(__file__).resolve().parent.parent / "data/ground_truth/sov_pacific_realty.json"
    truth_tiv = json.loads(gt_path.read_text())["extraction"]["total_tiv"]

    underestimates = 0
    for i in range(20):
        resp = runner.extract_once("sov_pacific_realty", model="v1", seed=42000 + i)
        extracted_tiv = resp["extraction"]["total_tiv"]
        if extracted_tiv < truth_tiv:
            underestimates += 1

    # If this were random noise, we'd expect ~10/20 underestimates.
    # Systematic calibration factor 0.895 means ALL 20 should underestimate.
    assert underestimates >= 18, (
        f"Only {underestimates}/20 runs underestimated TIV. "
        "Expected ≥18/20 for systematic bias. "
        "Random noise wouldn't produce this pattern."
    )


# ===========================================================================
# Section 12: Unlabeled document invariants
# ===========================================================================

def test_unlabeled_coi_mystery_invariants_pass(runner):
    """coi_unlabeled_mystery has no ground truth but should pass COI invariants.

    Without labels, we rely entirely on cross-field consistency checks.
    Date ordering (effective < expiration) and no duplicate coverage types
    should hold for any valid extraction.
    """
    report = runner.evaluate_document("coi_unlabeled_mystery", n=10)
    error_rate = report.invariant_violation_rate(InvariantLevel.ERROR)
    assert error_rate == 0.0, (
        f"coi_unlabeled_mystery has ERROR-level invariant violations in "
        f"{error_rate * 100:.0f}% of runs. "
        "Date ordering or structural invariants are failing."
    )


# ===========================================================================
# Section 13: Runner infrastructure
# ===========================================================================

def test_runner_evaluate_returns_document_report(runner):
    """EvalRunner.evaluate_document should return a DocumentReport with correct shape."""
    report = runner.evaluate_document("sov_acme_properties", n=3)
    assert isinstance(report, DocumentReport)
    assert report.doc_id == "sov_acme_properties"
    assert report.n_runs == 3
    assert len(report.run_results) == 3
    assert 0.0 <= report.mean_field_accuracy <= 1.0


def test_disputed_label_keystone_reit_alt_label(runner):
    """sov_keystone_reit has two valid labels (92.4M vs 96.6M total_tiv).

    An extraction matching either label within ±5% tolerance should be
    considered passing. The primary label (Toronto excluded) is 92.4M USD;
    the alt label (Toronto at 1:1 FX) is 96.6M. Both are defensible.
    """
    resp = runner.extract_once("sov_keystone_reit", seed=42000)
    ext_tiv = resp["extraction"]["total_tiv"]

    primary_result = compare_field("total_tiv", ext_tiv, 92400000.0)
    alt_result = compare_field("total_tiv", ext_tiv, 96600000.0)

    assert primary_result.passed or alt_result.passed, (
        f"total_tiv={ext_tiv} doesn't match either label within ±5% tolerance. "
        f"Primary (92.4M): {primary_result.level.value}, "
        f"Alt (96.6M): {alt_result.level.value}."
    )
