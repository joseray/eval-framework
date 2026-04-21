"""Load configuration from YAML + environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class EvalSettings:
    # API
    base_url: str = "http://localhost:8000"
    extract_endpoint: str = "/extract"
    max_retries: int = 3
    retry_delay: float = 2.0

    # Eval
    n_runs: int = 20
    seed_base: int = 42000
    models: list[str] = field(default_factory=lambda: ["v1", "v2"])

    # Tolerances
    amount_tolerance: float = 0.03
    aggregate_tolerance: float = 0.05

    # CI thresholds
    min_field_accuracy: float = 0.60
    max_hallucination_rate: float = 0.10
    max_regression_pp: float = 10.0

    # Paths
    ground_truth_dir: str = "./ground_truth"
    ground_truth_alt_dir: str = "./ground_truth_alt"

    # Raw YAML data for field/nested/invariant specs
    _raw: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_yaml(cls, path: Path) -> "EvalSettings":
        """Load from YAML. Env vars override: EXTRACTEVAL_BASE_URL, etc."""
        with open(path) as f:
            raw = yaml.safe_load(f)

        settings = cls()
        settings._raw = raw

        svc = raw.get("service", {})
        ev = raw.get("eval", {})
        tol = raw.get("tolerances", {})
        thr = raw.get("thresholds", {})
        gt = raw.get("ground_truth", {})

        settings.base_url = svc.get("base_url", settings.base_url)
        settings.n_runs = ev.get("n_runs", settings.n_runs)
        settings.seed_base = ev.get("seed_base", settings.seed_base)
        settings.models = ev.get("models", settings.models)
        settings.amount_tolerance = tol.get("amount", settings.amount_tolerance)
        settings.aggregate_tolerance = tol.get("aggregate", settings.aggregate_tolerance)
        settings.min_field_accuracy = thr.get("min_field_accuracy", settings.min_field_accuracy)
        settings.max_hallucination_rate = thr.get("max_hallucination_rate", settings.max_hallucination_rate)
        settings.max_regression_pp = thr.get("max_regression_pp", settings.max_regression_pp)

        # Ground truth dirs — resolve relative to config file location
        config_dir = path.parent
        gt_dir = gt.get("directory", settings.ground_truth_dir)
        gt_alt_dir = gt.get("alt_directory", settings.ground_truth_alt_dir)
        settings.ground_truth_dir = str((config_dir / gt_dir).resolve())
        settings.ground_truth_alt_dir = str((config_dir / gt_alt_dir).resolve())

        # Env var overrides
        if v := os.environ.get("EXTRACTEVAL_BASE_URL"):
            settings.base_url = v
        if v := os.environ.get("EXTRACTEVAL_N_RUNS"):
            settings.n_runs = int(v)
        if v := os.environ.get("EXTRACTEVAL_SEED_BASE"):
            settings.seed_base = int(v)
        if v := os.environ.get("EXTRACTEVAL_MIN_FIELD_ACCURACY"):
            settings.min_field_accuracy = float(v)
        if v := os.environ.get("EXTRACTEVAL_MAX_REGRESSION_PP"):
            settings.max_regression_pp = float(v)

        return settings

    def get_field_specs(self, doc_type: str) -> dict[str, Any]:
        """Return top-level field specs for a doc type."""
        return self._raw.get("field_specs", {}).get(doc_type, {}).get("fields", {})

    def get_nested_specs(self, doc_type: str) -> dict[str, Any]:
        """Return nested collection specs for a doc type."""
        return self._raw.get("field_specs", {}).get(doc_type, {}).get("nested", {})

    def get_invariants(self, doc_type: str) -> list[str]:
        """Return invariant names for a doc type."""
        return self._raw.get("field_specs", {}).get(doc_type, {}).get("invariants", [])

    def get_document_config(self, document_id: str) -> dict[str, Any]:
        """Return config for a specific document."""
        return self._raw.get("documents", {}).get(document_id, {})

    def all_document_ids(self) -> list[str]:
        return list(self._raw.get("documents", {}).keys())
