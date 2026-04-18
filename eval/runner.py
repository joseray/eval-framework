"""EvalRunner — orchestrates multi-run document evaluation.

Usage:
    runner = EvalRunner(client, n_runs=20, seed_base=42000)
    report = runner.evaluate_document("sov_pacific_realty", model="v1")
    print(report.mean_field_accuracy)
    print(report.hallucination_rate)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from eval.comparators import FieldResult, MatchLevel, compare_field
from eval.invariants import InvariantLevel, InvariantResult, check_invariants

# ---------------------------------------------------------------------------
# Document registry
# ---------------------------------------------------------------------------

DOCUMENTS_WITH_GROUND_TRUTH: list[str] = [
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

ALL_DOCUMENT_IDS: list[str] = DOCUMENTS_WITH_GROUND_TRUTH + ["coi_unlabeled_mystery"]

_GT_DIR = Path(__file__).resolve().parent.parent / "data" / "ground_truth"
_GT_ALT_DIR = Path(__file__).resolve().parent.parent / "data" / "ground_truth_alt"


def _load_ground_truth(doc_id: str) -> dict | None:
    path = _GT_DIR / f"{doc_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _load_alt_ground_truth(doc_id: str) -> dict | None:
    path = _GT_ALT_DIR / f"{doc_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


# ---------------------------------------------------------------------------
# Field-comparison helpers
# ---------------------------------------------------------------------------

def _compare_top_level(extracted: dict, expected: dict) -> list[FieldResult]:
    """Compare scalar top-level fields, skipping list/dict values."""
    results = []
    for key, exp_val in expected.items():
        if isinstance(exp_val, (list, dict)):
            continue
        results.append(compare_field(key, extracted.get(key), exp_val))
    return results


def _compare_sov(extracted: dict, expected: dict) -> list[FieldResult]:
    results = _compare_top_level(extracted, expected)
    exp_props = expected.get("properties", [])
    ext_props = extracted.get("properties", [])
    for i, exp_prop in enumerate(exp_props):
        ext_prop = ext_props[i] if i < len(ext_props) else {}
        for k, v in exp_prop.items():
            results.append(compare_field(k, ext_prop.get(k), v))
    return results


def _compare_coi_or_binder(extracted: dict, expected: dict) -> list[FieldResult]:
    results = _compare_top_level(extracted, expected)
    exp_covs = expected.get("coverages", [])
    ext_covs = extracted.get("coverages", [])
    # Flag extra coverages as hallucinated
    if len(ext_covs) > len(exp_covs):
        for _ in range(len(ext_covs) - len(exp_covs)):
            results.append(FieldResult(
                "coverages", MatchLevel.HALLUCINATED, ext_covs, exp_covs,
                "extra coverage not present in ground truth",
            ))
    for i, exp_cov in enumerate(exp_covs):
        ext_cov = ext_covs[i] if i < len(ext_covs) else {}
        for k, v in exp_cov.items():
            results.append(compare_field(k, ext_cov.get(k), v))
    return results


def _compare_loss_run(extracted: dict, expected: dict) -> list[FieldResult]:
    results = _compare_top_level(extracted, expected)
    exp_claims = expected.get("claims", [])
    ext_claims = extracted.get("claims", [])
    for i, exp_claim in enumerate(exp_claims):
        ext_claim = ext_claims[i] if i < len(ext_claims) else {}
        for k, v in exp_claim.items():
            results.append(compare_field(k, ext_claim.get(k), v))
    return results


def _flatten_and_compare(
    extracted: dict, expected: dict, doc_type: str
) -> list[FieldResult]:
    dispatch = {
        "sov": _compare_sov,
        "coi": _compare_coi_or_binder,
        "binder": _compare_coi_or_binder,
        "loss_run": _compare_loss_run,
    }
    fn = dispatch.get(doc_type)
    if fn:
        return fn(extracted, expected)
    return _compare_top_level(extracted, expected)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    doc_id: str
    model: str
    seed: int
    field_results: list[FieldResult]
    invariant_results: list[InvariantResult]
    classification: dict

    @property
    def field_accuracy(self) -> float:
        scorable = [r for r in self.field_results if r.level != MatchLevel.SKIPPED]
        if not scorable:
            return 1.0
        return sum(1 for r in scorable if r.passed) / len(scorable)

    @property
    def hallucination_count(self) -> int:
        return sum(1 for r in self.field_results if r.level == MatchLevel.HALLUCINATED)

    @property
    def missing_count(self) -> int:
        return sum(1 for r in self.field_results if r.level == MatchLevel.MISSING)


@dataclass
class DocumentReport:
    doc_id: str
    model: str
    n_runs: int
    run_results: list[RunResult]

    @property
    def mean_field_accuracy(self) -> float:
        if not self.run_results:
            return 0.0
        return sum(r.field_accuracy for r in self.run_results) / len(self.run_results)

    def field_accuracy_by_name(self, field_name: str) -> float:
        """Fraction of runs where a specific named field passed comparison."""
        passes = []
        for run in self.run_results:
            for fr in run.field_results:
                if fr.field == field_name:
                    passes.append(fr.passed)
                    break
        return sum(passes) / len(passes) if passes else 1.0

    @property
    def hallucination_rate(self) -> float:
        """Mean hallucinated-field count per run."""
        if not self.run_results:
            return 0.0
        return sum(r.hallucination_count for r in self.run_results) / len(self.run_results)

    @property
    def omission_rate(self) -> float:
        """Mean missing-field count per run."""
        if not self.run_results:
            return 0.0
        return sum(r.missing_count for r in self.run_results) / len(self.run_results)

    def invariant_violation_rate(self, level: InvariantLevel = InvariantLevel.ERROR) -> float:
        """Fraction of runs that had at least one invariant violation at `level`."""
        if not self.run_results:
            return 0.0
        violations = sum(
            1 for run in self.run_results
            if any(inv.level == level for inv in run.invariant_results)
        )
        return violations / len(self.run_results)


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------

class EvalRunner:
    """Runs N seeded extractions per document and aggregates results."""

    def __init__(self, client, n_runs: int = 20, seed_base: int = 42000):
        self.client = client
        self.n_runs = n_runs
        self.seed_base = seed_base

    def extract_once(
        self, doc_id: str, model: str = "v1", seed: int | None = None
    ) -> dict:
        payload: dict = {"document_id": doc_id, "model": model}
        if seed is not None:
            payload["seed"] = seed
        resp = self.client.post("/extract", json=payload)
        resp.raise_for_status()
        return resp.json()

    def evaluate_document(
        self,
        doc_id: str,
        model: str = "v1",
        n: int | None = None,
    ) -> DocumentReport:
        n = n if n is not None else self.n_runs
        gt = _load_ground_truth(doc_id)
        run_results: list[RunResult] = []

        for i in range(n):
            seed = self.seed_base + i
            response = self.extract_once(doc_id, model, seed)

            classification = response.get("classification", {})
            extraction = response.get("extraction", {})
            doc_type = (
                gt["doc_type"] if gt
                else classification.get("doc_type", "unknown")
            )

            field_results: list[FieldResult] = []
            if gt:
                field_results = _flatten_and_compare(
                    extraction, gt["extraction"], doc_type
                )

            invariant_results = check_invariants(doc_type, extraction)

            run_results.append(RunResult(
                doc_id=doc_id,
                model=model,
                seed=seed,
                field_results=field_results,
                invariant_results=invariant_results,
                classification=classification,
            ))

        return DocumentReport(
            doc_id=doc_id,
            model=model,
            n_runs=n,
            run_results=run_results,
        )

    def compare_models(
        self, doc_id: str, n: int | None = None
    ) -> dict:
        """Compare v1 and v2 accuracy on the same seeds."""
        n = n if n is not None else self.n_runs
        v1 = self.evaluate_document(doc_id, model="v1", n=n)
        v2 = self.evaluate_document(doc_id, model="v2", n=n)
        return {
            "doc_id": doc_id,
            "v1_accuracy": v1.mean_field_accuracy,
            "v2_accuracy": v2.mean_field_accuracy,
            "delta": v2.mean_field_accuracy - v1.mean_field_accuracy,
            "v1_hallucination_rate": v1.hallucination_rate,
            "v2_hallucination_rate": v2.hallucination_rate,
        }

    def evaluate_all(self, model: str = "v1") -> list[DocumentReport]:
        return [
            self.evaluate_document(doc_id, model=model)
            for doc_id in DOCUMENTS_WITH_GROUND_TRUTH
        ]
