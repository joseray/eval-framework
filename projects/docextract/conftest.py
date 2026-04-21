"""Fixtures that connect the framework to the docextract service."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable for 'framework.*' and 'projects.*'
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from framework.api_client import ExtractionAPIClient
from framework.ground_truth import GroundTruthStore
from framework.config import EvalSettings
from framework.normalizers import register_normalizer

# Import insurance invariants to register them with the framework
import projects.docextract.custom_rules.insurance_invariants  # noqa: F401

from projects.docextract.custom_rules.carrier_names import normalize_carrier


@pytest.fixture(scope="session")
def settings():
    return EvalSettings.from_yaml(Path(__file__).parent / "config.yaml")


@pytest.fixture(scope="session")
def api_client(settings):
    """HTTP client pointing at the locally running docextract container."""
    return ExtractionAPIClient(
        base_url=settings.base_url,
        max_retries=settings.max_retries,
        retry_delay=settings.retry_delay,
    )


@pytest.fixture(scope="session")
def ground_truth(settings):
    return GroundTruthStore(
        gt_dir=Path(settings.ground_truth_dir),
        alt_dir=Path(settings.ground_truth_alt_dir),
    )


@pytest.fixture(scope="session", autouse=True)
def register_custom_normalizers():
    """Register insurance domain-specific normalizers."""
    register_normalizer("carrier", normalize_carrier)
