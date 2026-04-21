"""Report generator for console and CI.

Takes results from N runs and produces human-readable summaries,
JSON artifacts for CI, and a CI gate boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from framework.validators import FieldResult, MatchLevel
from framework.invariants import InvariantResult, Severity


PASSING = {MatchLevel.EXACT, MatchLevel.WITHIN_TOL, MatchLevel.MISSING_BOTH}


@dataclass
class DocumentTestReport:
    document_id: str
    doc_type: str
    model: str
    n_runs: int
    field_results_per_run: list[list[FieldResult]]
    invariant_results_per_run: list[list[InvariantResult]]
    classifications: list[dict]

    def field_accuracy(self) -> dict[str, float]:
        """Per-field accuracy: fraction of runs where the field matched."""
        hits: dict[str, list[bool]] = {}
        for run_results in self.field_results_per_run:
            for r in run_results:
                if r.match == MatchLevel.SKIPPED:
                    continue
                hits.setdefault(r.field_path, []).append(r.match in PASSING)
        return {fp: sum(h) / len(h) for fp, h in hits.items()}

    def invariant_violation_rates(self) -> dict[str, float]:
        """Per-invariant violation rate (ERROR severity only)."""
        counts: dict[str, int] = {}
        for run_results in self.invariant_results_per_run:
            for r in run_results:
                if r.severity == Severity.ERROR:
                    counts[r.check] = counts.get(r.check, 0) + 1
        n = max(self.n_runs, 1)
        return {check: count / n for check, count in counts.items()}

    def hallucination_rate(self) -> float:
        """Fraction of field comparisons that are hallucinations."""
        total = 0
        hallucinated = 0
        for run_results in self.field_results_per_run:
            for r in run_results:
                if r.match != MatchLevel.SKIPPED:
                    total += 1
                    if r.match == MatchLevel.HALLUCINATED:
                        hallucinated += 1
        return hallucinated / total if total else 0.0

    def omission_rate(self) -> float:
        """Fraction of field comparisons that are missing_expected."""
        total = 0
        missing = 0
        for run_results in self.field_results_per_run:
            for r in run_results:
                if r.match != MatchLevel.SKIPPED:
                    total += 1
                    if r.match == MatchLevel.MISSING_EXPECTED:
                        missing += 1
        return missing / total if total else 0.0

    def classification_accuracy(self, expected_type: str) -> float:
        """Fraction of runs where doc_type matched expected."""
        if not self.classifications:
            return 0.0
        correct = sum(
            1 for c in self.classifications if c.get("doc_type") == expected_type
        )
        return correct / len(self.classifications)

    def confidence_stats(self) -> dict[str, float]:
        """Min/max/mean confidence across classifications."""
        confs = [c.get("confidence", 0.0) for c in self.classifications]
        if not confs:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "min": min(confs),
            "max": max(confs),
            "mean": sum(confs) / len(confs),
        }

    def summary(self) -> str:
        """Human-readable summary for console."""
        acc = self.field_accuracy()
        lines = [
            f"{'='*60}",
            f"DOCUMENT: {self.document_id} | model={self.model} | runs={self.n_runs}",
            f"{'='*60}",
        ]

        if acc:
            overall = sum(acc.values()) / len(acc)
            lines.append(f"Overall field accuracy: {overall:.0%}")
            failing = {fp: v for fp, v in acc.items() if v < 0.60}
            if failing:
                lines.append("Failing fields (< 60%):")
                for fp, v in sorted(failing.items(), key=lambda x: x[1]):
                    lines.append(f"  {fp}: {v:.0%}")

        inv_rates = self.invariant_violation_rates()
        if inv_rates:
            lines.append("Invariant violation rates:")
            for check, rate in sorted(inv_rates.items()):
                lines.append(f"  {check}: {rate:.0%}")

        conf_stats = self.confidence_stats()
        lines.append(
            f"Classification confidence: min={conf_stats['min']:.1f} "
            f"max={conf_stats['max']:.1f} mean={conf_stats['mean']:.1f}"
        )
        lines.append(f"Hallucination rate: {self.hallucination_rate():.1%}")
        lines.append(f"Omission rate: {self.omission_rate():.1%}")

        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        """Structured output for CI."""
        return {
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "model": self.model,
            "n_runs": self.n_runs,
            "field_accuracy": self.field_accuracy(),
            "invariant_violation_rates": self.invariant_violation_rates(),
            "hallucination_rate": self.hallucination_rate(),
            "omission_rate": self.omission_rate(),
            "confidence_stats": self.confidence_stats(),
        }

    def passes_ci_gate(
        self,
        min_field_accuracy: float = 0.60,
        max_hallucination_rate: float = 0.10,
    ) -> bool:
        """Return True if the report passes the CI gate."""
        acc = self.field_accuracy()
        if any(v < min_field_accuracy for v in acc.values()):
            return False
        if self.hallucination_rate() > max_hallucination_rate:
            return False
        return True
