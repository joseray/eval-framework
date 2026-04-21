"""HTTP client for the extraction service.

Makes the calls, handles transient errors, returns raw JSON.
Does NOT validate anything — that's the tests' job.
"""

from __future__ import annotations

import time

import httpx


class ExtractionAPIClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.base_url = base_url
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = httpx.Client(base_url=base_url, timeout=30.0)

    def extract(
        self,
        document_id: str,
        model: str = "v1",
        seed: int | None = None,
    ) -> httpx.Response:
        """POST /extract with automatic retry for 429/5xx."""
        payload: dict = {"document_id": document_id, "model": model}
        if seed is not None:
            payload["seed"] = seed

        response = None
        for attempt in range(self.max_retries):
            response = self._client.post("/extract", json=payload)
            if response.status_code == 200:
                return response
            if response.status_code in (429, 500, 502, 503):
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            return response  # 4xx errors return immediately

        return response  # return last failed response

    def extract_n_times(
        self,
        document_id: str,
        model: str = "v1",
        n: int = 20,
        seed_base: int = 42000,
    ) -> tuple[list[dict], int]:
        """Run N extractions with sequential seeds.

        Returns (results, failure_count). Each result is a dict with
        {seed, extraction, classification, metadata}.
        """
        results: list[dict] = []
        failures = 0
        for i in range(n):
            seed = seed_base + i
            resp = self.extract(document_id, model=model, seed=seed)
            if resp.status_code == 200:
                data = resp.json()
                results.append({
                    "seed": seed,
                    "extraction": data["extraction"],
                    "classification": data["classification"],
                    "metadata": data["metadata"],
                })
            else:
                failures += 1
        return results, failures

    def health(self) -> httpx.Response:
        """GET /health"""
        return self._client.get("/health")

    def config(self) -> httpx.Response:
        """GET /config"""
        return self._client.get("/config")

    def reseed_bugs(self, seed: int | None = None) -> httpx.Response:
        """POST /admin/reseed-bugs"""
        url = "/admin/reseed-bugs"
        if seed is not None:
            url += f"?seed={seed}"
        return self._client.post(url)

    @classmethod
    def from_test_client(cls, test_client) -> "ExtractionAPIClient":
        """Create a client that uses FastAPI TestClient (in-process, no network)."""
        instance = cls.__new__(cls)
        instance.base_url = ""
        instance.max_retries = 3
        instance.retry_delay = 0.0  # no sleep in tests
        instance._client = test_client
        return instance
