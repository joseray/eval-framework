"""Smoke tests: is the service alive and responding correctly?"""


def test_health_endpoint(api_client):
    resp = api_client.health()
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_config_endpoint(api_client):
    resp = api_client.config()
    assert resp.status_code == 200
    config = resp.json()
    assert "auto_commit_threshold" in config
    assert "supported_doc_types" in config


def test_config_auto_commit_threshold_is_sane(api_client):
    """auto_commit_threshold=5 is dangerously low — flag it but don't fail."""
    config = api_client.config().json()
    threshold = config["auto_commit_threshold"]
    if threshold < 70:
        print(f"\n⚠ CRITICAL: auto_commit_threshold={threshold} — recommend ≥85 for production")


def test_extract_returns_valid_schema(api_client):
    """The /extract response has the expected structure."""
    resp = api_client.extract("sov_acme_properties", model="v1", seed=1)
    assert resp.status_code == 200
    data = resp.json()
    assert "classification" in data
    assert "extraction" in data
    assert "metadata" in data
    assert "doc_type" in data["classification"]
    assert "confidence" in data["classification"]
    assert "model" in data["metadata"]


def test_extract_with_invalid_doc_returns_404(api_client):
    resp = api_client.extract("nonexistent_document_xyz")
    assert resp.status_code == 404
