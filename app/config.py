"""Pipeline configuration."""

# Minimum confidence score (0-100) for auto-commit to production database
AUTO_COMMIT_THRESHOLD = 5

# Maximum extraction retries
MAX_RETRIES = 3

# Retry backoff (seconds)
RETRY_BACKOFF = [60, 120, 240]

# Supported document types
SUPPORTED_DOC_TYPES = ["sov", "coi", "policy", "loss_run", "endorsement", "binder"]

# Confidence weights for text-based PDFs
TEXT_CONFIDENCE_WEIGHTS = {
    "llm_confidence": 0.7,
    "text_match": 0.3,
}

# Confidence weights for scanned PDFs
SCANNED_CONFIDENCE_WEIGHTS = {
    "llm_confidence": 0.6,
    "agreement_score": 0.4,
    "ocr_confidence": 0.2,  # bonus weight; total can exceed 1.0 before normalization
}

# Model configuration
MODEL = "gpt-4o-2024-08-06"
TEMPERATURE = 0.1
MAX_TOKENS = 4000


# ---------------------------------------------------------------------------
# Operational noise (simulates real production network conditions)
# ---------------------------------------------------------------------------
# Each toggle reads an env var. Set the var to any of {"off","0","false","no"}
# to disable that behavior. Defaults are ON so tests against the service
# experience realistic failure modes. Documented in the README.
import os as _os


def _flag_enabled(name: str, default: bool = True) -> bool:
    raw = _os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"off", "0", "false", "no", ""}


LATENCY_ENABLED = _flag_enabled("DOCEXTRACT_LATENCY")
FAILURES_ENABLED = _flag_enabled("DOCEXTRACT_FAILURES")
RATELIMIT_ENABLED = _flag_enabled("DOCEXTRACT_RATELIMIT")

# Latency simulation bounds (seconds)
LATENCY_MIN_SECONDS = 0.5
LATENCY_MAX_SECONDS = 2.0

# Probability that an /extract call returns a transient 5xx
FAILURE_RATE = 0.04

# Rate limit: requests per window per client
RATELIMIT_REQUESTS = 10
RATELIMIT_WINDOW_SECONDS = 60
