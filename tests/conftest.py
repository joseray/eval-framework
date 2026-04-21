"""Shared pytest fixtures for the DocExtract eval harness.

Operational noise (latency, transient 5xx, rate limiting) is disabled for
all eval runs so tests are fast and deterministic. The toggles are set here
before the app module is imported so that config.py reads the env vars first.
"""

import os

# Must be set before importing app.main so config.py reads them at import time.
os.environ["DOCEXTRACT_LATENCY"] = "off"
os.environ["DOCEXTRACT_FAILURES"] = "off"
os.environ["DOCEXTRACT_RATELIMIT"] = "off"

import pytest
from fastapi.testclient import TestClient

from app.main import app
from eval.runner import ALL_DOCUMENT_IDS, DOCUMENTS_WITH_GROUND_TRUTH, EvalRunner


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def runner(client: TestClient) -> EvalRunner:
    return EvalRunner(client, n_runs=20, seed_base=42000)
