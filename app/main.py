"""DocExtract mock extraction service.

Simulates a production LLM-backed document extraction pipeline. The service
loads a canonical extraction for each supported document_id from
`data/ground_truth/`, applies realistic perturbations (numeric variance,
date-format drift, carrier-name variants, occasional missing fields), and
returns a response shaped like the real pipeline's output.

Temperature and model metadata on the response are cosmetic; no real LLM call
is made.
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import config

# --- Paths ------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GROUND_TRUTH_DIR = _DATA_DIR / "ground_truth"

# --- App --------------------------------------------------------------------

app = FastAPI(
    title="DocExtract Mock Extraction Service",
    version="0.1.0",
    description="Simulates the document extraction pipeline for QA / eval work.",
)


class ExtractRequest(BaseModel):
    document_id: str
    seed: int | None = None
    model: str | None = None  # "v1" (default) or "v2"


# --- Carrier canonicalization -----------------------------------------------

# The service occasionally emits the short or marketing form of a carrier
# name instead of the full legal entity form. Downstream consumers should
# canonicalize before comparison.
_CARRIER_VARIANTS: dict[str, list[str]] = {
    "Hartford Financial Services": ["Hartford Financial Services", "The Hartford", "Hartford"],
    "The Travelers Indemnity Company": [
        "The Travelers Indemnity Company",
        "Travelers",
        "Travelers Insurance",
    ],
    "Zurich Insurance": ["Zurich Insurance", "Zurich", "Zurich North America"],
    "Nationwide Insurance": ["Nationwide Insurance", "Nationwide", "Nationwide Mutual"],
}


def _maybe_swap_carrier(name: str, rng: random.Random) -> str:
    variants = _CARRIER_VARIANTS.get(name)
    if not variants:
        return name
    return rng.choice(variants)


# --- Numeric perturbation ---------------------------------------------------

def _perturb_amount(value: float | None, rng: random.Random, scale: float = 0.02) -> float | None:
    """Apply a small multiplicative variance (±`scale`, default ±2%)."""
    if value is None:
        return None
    drift = rng.uniform(-scale, scale)
    return round(float(value) * (1.0 + drift), 2)


# --- Date handling ----------------------------------------------------------

def _iso_to_us(iso_date: str) -> str:
    """Convert YYYY-MM-DD to MM/DD/YYYY."""
    y, m, d = iso_date.split("-")
    return f"{m}/{d}/{y}"


def _iso_to_eu(iso_date: str) -> str:
    """Convert YYYY-MM-DD to DD/MM/YYYY."""
    y, m, d = iso_date.split("-")
    return f"{d}/{m}/{y}"


def _maybe_reformat_date(iso_date: str, rng: random.Random, us_bias: float = 0.7) -> str:
    """Occasionally reformat an ISO date to MM/DD/YYYY (and rarely ISO stays)."""
    r = rng.random()
    if r < us_bias:
        return _iso_to_us(iso_date)
    if r < 0.95:
        return iso_date  # ISO pass-through
    return _iso_to_eu(iso_date)


# --- Optional-field omission ------------------------------------------------

_OPTIONAL_FIELDS: dict[str, list[str]] = {
    "sov": ["policy_number"],
    "coi": ["producer", "description_of_operations"],
    "loss_run": ["loss_ratio"],
    "endorsement": ["old_value"],
    "binder": ["binding_authority_reference", "description_of_operations"],
}


def _maybe_omit_optional_fields(extraction: dict, doc_type: str, rng: random.Random) -> None:
    for field in _OPTIONAL_FIELDS.get(doc_type, []):
        if rng.random() < 0.08:  # ~8% chance
            extraction.pop(field, None)


# --- Doc-type specific perturbations ----------------------------------------

# Observed systematic calibration offset on Pacific Realty SOVs. Historical
# runs have shown total_insured_value coming in consistently below blanket
# schedule. Left in place while the upstream prompt is tuned.
_PACIFIC_REALTY_TIV_CALIBRATION = 0.895

# Per-property construction-type reporting gap, by document. Normal extraction
# omits optional fields ~8% of the time; this table records observed per-doc
# gaps where the pipeline has historically dropped a specific field at a
# higher rate (e.g., blurry schedule columns, scanned source material).
_CONSTRUCTION_TYPE_REPORT_GAP: dict[str, float] = {
    "sov_keystone_reit": 0.40,
}

# Mutable runtime registry of per-doc bug rates. /admin/reseed-bugs can
# rotate which document carries which behavior so that eval frameworks
# tuned to specific document_ids surface as overfit. Initialised below
# from the static defaults.
_BUG_REGISTRY: dict[str, dict[str, float]] = {}


def _init_bug_registry() -> None:
    _BUG_REGISTRY["construction_omission"] = dict(_CONSTRUCTION_TYPE_REPORT_GAP)
    _BUG_REGISTRY["paid_unit_drift"] = dict(_PAID_AMOUNT_UNIT_VARIANTS) if "_PAID_AMOUNT_UNIT_VARIANTS" in globals() else {}
    _BUG_REGISTRY["phantom_coverage"] = dict(_PHANTOM_COVERAGE_DOCS) if "_PHANTOM_COVERAGE_DOCS" in globals() else {}
    _BUG_REGISTRY["binder_date_swap"] = dict(_BINDER_DATE_SWAP_RATE) if "_BINDER_DATE_SWAP_RATE" in globals() else {}

# Pacific Realty cross-field rounding drift: the LLM post-processor rounds
# building/contents/BI components independently from total_insured_value,
# leaving a ~3% drift between component sum and totals. Observed since
# post-processor v2.3 rolled out.
_CROSS_FIELD_ROUNDING_DRIFT_DOCS = {"sov_pacific_realty"}

# Internal baseline "extractions" for documents that ship without a ground-
# truth label. The service still needs *some* reference output to perturb;
# this dict lives inside the service like model weights — it's not a label
# and is never exposed via the API surface.
_INTERNAL_BASELINE_EXTRACTIONS: dict[str, dict] = {
    "coi_unlabeled_mystery": {
        "doc_type": "coi",
        "extraction": {
            "certificate_holder": "Midwest Fabrication Partners LLC",
            "insured_name": "Lakeside Manufacturing Co.",
            "producer": "Great Lakes Surety & Commercial Insurance",
            "coverages": [
                {
                    "coverage_type": "general_liability",
                    "policy_number": "LMG-2024-55810",
                    "carrier": "Liberty Mutual Fire Insurance Company",
                    "effective_date": "2024-07-01",
                    "expiration_date": "2025-07-01",
                    "each_occurrence_limit": 2000000.0,
                    "general_aggregate_limit": 4000000.0,
                    "products_completed_ops": 4000000.0,
                },
                {
                    "coverage_type": "auto",
                    "policy_number": "LMB-2024-55810",
                    "carrier": "Liberty Mutual Fire Insurance Company",
                    "effective_date": "2024-07-01",
                    "expiration_date": "2025-07-01",
                    "each_occurrence_limit": 1000000.0,
                    "general_aggregate_limit": None,
                    "products_completed_ops": None,
                },
                {
                    "coverage_type": "workers_comp",
                    "policy_number": "CNA-WC-24-77216",
                    "carrier": "CNA Insurance",
                    "effective_date": "2024-07-01",
                    "expiration_date": "2025-07-01",
                    "each_occurrence_limit": 1000000.0,
                    "general_aggregate_limit": None,
                    "products_completed_ops": None,
                },
            ],
            "description_of_operations": "Light industrial manufacturing — precision metal components. Certificate holder named as additional insured on the GL policy with respect to operations performed for the certificate holder.",
        },
    },
}


def _apply_sov_perturbations(
    extraction: dict, rng: random.Random, document_id: str, model: str = "v1"
) -> dict:
    # Whether this SOV gets the calibration offset (consistent bias, not noise).
    # v2 of the model has corrected the TIV calibration drift.
    apply_tiv_calibration = document_id == "sov_pacific_realty" and model != "v2"
    tiv_calibration = (
        _PACIFIC_REALTY_TIV_CALIBRATION * (1.0 + rng.uniform(-0.015, 0.015))
        if apply_tiv_calibration
        else 1.0
    )
    # Calibration-relative drift: keeps comp_sum/total_insured_value diverging
    # by ~3% even when the doc-level TIV calibration is also active.
    component_drift = (
        tiv_calibration * 1.03 if document_id in _CROSS_FIELD_ROUNDING_DRIFT_DOCS else 1.0
    )

    ct_gap = _CONSTRUCTION_TYPE_REPORT_GAP.get(document_id, 0.0)

    for prop in extraction["properties"]:
        for field in ("building_value", "contents_value", "business_income_value"):
            base = prop.get(field)
            if base is None:
                continue
            prop[field] = _perturb_amount(float(base) * component_drift, rng)
        tiv = prop.get("total_insured_value")
        if tiv is not None:
            base = float(tiv) * tiv_calibration
            prop["total_insured_value"] = round(base * (1.0 + rng.uniform(-0.01, 0.01)), 2)

        # Per-doc higher drop rate for construction_type.
        if ct_gap > 0 and rng.random() < ct_gap:
            prop["construction_type"] = None

        # Pacific Realty partial-truth square_footage: if ground truth string-
        # flagged "unknown", emit a numeric guess occasionally.
        sqft = prop.get("square_footage")
        if isinstance(sqft, str):
            # Ground truth was flagged as unknown; the model guesses ~half the time.
            prop["square_footage"] = rng.choice([None, None, rng.randint(18000, 40000)])

    extraction["total_tiv"] = round(
        sum(p["total_insured_value"] for p in extraction["properties"]), 2
    )

    extraction["carrier"] = _maybe_swap_carrier(extraction["carrier"], rng)
    extraction["effective_date"] = _maybe_reformat_date(extraction["effective_date"], rng)
    extraction["expiration_date"] = _maybe_reformat_date(extraction["expiration_date"], rng)
    return extraction


# Docs where the post-processor has historically injected a spurious "cyber"
# coverage line not present in the source material. Observed on legacy ACORD
# 25 layouts; suspected template-matching artifact.
_PHANTOM_COVERAGE_DOCS: dict[str, float] = {
    "coi_zurich_legacy": 0.20,
}


def _maybe_inject_phantom_coverage(
    coverages: list[dict], rng: random.Random, document_id: str
) -> list[dict]:
    rate = _PHANTOM_COVERAGE_DOCS.get(document_id, 0.0)
    if rate <= 0.0 or rng.random() >= rate:
        return coverages
    phantom = {
        "coverage_type": "cyber",
        "policy_number": f"CYB-{rng.randint(10000, 99999)}-AUX",
        "carrier": coverages[0]["carrier"] if coverages else "Unknown",
        "effective_date": coverages[0]["effective_date"] if coverages else "2022-09-01",
        "expiration_date": coverages[0]["expiration_date"] if coverages else "2023-09-01",
        "each_occurrence_limit": 1000000.00,
        "general_aggregate_limit": 1000000.00,
        "products_completed_ops": None,
    }
    return coverages + [phantom]


def _apply_coi_perturbations(
    extraction: dict, rng: random.Random, document_id: str, model: str = "v1"
) -> dict:
    # v2 of the model has a higher rate of producer omission across all COIs
    # (suspected regression from a producer-name normalization layer added in v2).
    if model == "v2" and rng.random() < 0.30:
        extraction["producer"] = None
    for cov in extraction["coverages"]:
        for field in (
            "each_occurrence_limit",
            "general_aggregate_limit",
            "products_completed_ops",
        ):
            cov[field] = _perturb_amount(cov.get(field), rng)
        cov["carrier"] = _maybe_swap_carrier(cov["carrier"], rng)
        cov["effective_date"] = _maybe_reformat_date(cov["effective_date"], rng)
        cov["expiration_date"] = _maybe_reformat_date(cov["expiration_date"], rng)
    extraction["coverages"] = _maybe_inject_phantom_coverage(
        extraction["coverages"], rng, document_id
    )
    return extraction


def _format_loss_run_policy_effective_date(iso_date: str, rng: random.Random) -> str:
    """Loss-run policy effective date is returned in US or EU short-format.

    The upstream prompt does not enforce ISO output for this field, so the
    model alternates between MM/DD/YYYY and DD/MM/YYYY in practice.
    """
    y, m, d = iso_date.split("-")
    if rng.random() < 0.5:
        return f"{m}/{d}/{y}"
    return f"{d}/{m}/{y}"


# Historical unit-normalization drift: on a small subset of loss runs, the
# post-processor emits paid_amount in cents instead of dollars. Scale of the
# error is always exactly 100x so downstream aggregates are detectable.
_PAID_AMOUNT_UNIT_VARIANTS: dict[str, float] = {
    "loss_run_libertymutual": 0.15,
}


def _apply_loss_run_perturbations(
    extraction: dict, rng: random.Random, document_id: str, model: str = "v1"
) -> dict:
    if model == "v2":
        # v2 fixed the date-format bug; emits ISO consistently.
        pass
    else:
        extraction["policy_effective_date"] = _format_loss_run_policy_effective_date(
            extraction["policy_effective_date"], rng
        )
    extraction["valuation_date"] = _maybe_reformat_date(extraction["valuation_date"], rng)

    unit_drift_rate = _PAID_AMOUNT_UNIT_VARIANTS.get(document_id, 0.0)

    for claim in extraction["claims"]:
        claim["paid_amount"] = _perturb_amount(claim["paid_amount"], rng)
        claim["reserved_amount"] = _perturb_amount(claim["reserved_amount"], rng)
        # Unit-normalization drift applied post-perturbation.
        if unit_drift_rate > 0 and rng.random() < unit_drift_rate:
            claim["paid_amount"] = round(claim["paid_amount"] * 100, 2)
        claim["total_incurred"] = round(
            claim["paid_amount"] + claim["reserved_amount"], 2
        )
        if rng.random() < 0.05 and claim.get("claimant") is not None:
            claim["claimant"] = None

    extraction["total_paid"] = round(
        sum(c["paid_amount"] for c in extraction["claims"]), 2
    )
    extraction["total_incurred"] = round(
        sum(c["total_incurred"] for c in extraction["claims"]), 2
    )
    if "total_recoveries" in extraction:
        extraction["total_recoveries"] = round(
            sum(-c["paid_amount"] for c in extraction["claims"] if c["paid_amount"] < 0),
            2,
        )
    # v2 regression: aggregator treats all paid_amounts as positive when summing
    # total_incurred (suspected sign-handling bug introduced in v2.0.3).
    if model == "v2" and document_id == "loss_run_libertymutual":
        extraction["total_incurred"] = round(
            sum(abs(c["paid_amount"]) + c["reserved_amount"] for c in extraction["claims"]),
            2,
        )
    extraction["carrier"] = _maybe_swap_carrier(extraction["carrier"], rng)
    return extraction


# --- Endorsement & Binder ---------------------------------------------------

def _apply_endorsement_perturbations(
    extraction: dict, rng: random.Random, document_id: str, model: str = "v1"
) -> dict:
    extraction["carrier"] = _maybe_swap_carrier(extraction["carrier"], rng)
    extraction["endorsement_effective_date"] = _maybe_reformat_date(
        extraction["endorsement_effective_date"], rng
    )
    extraction["premium_delta"] = _perturb_amount(extraction.get("premium_delta"), rng)
    return extraction


# Binder date-swap rate: on a subset of binders, the post-processor swaps
# effective and expiration dates when both are within the same calendar month.
_BINDER_DATE_SWAP_RATE: dict[str, float] = {
    "binder_travelers_temp": 0.10,
}


def _apply_binder_perturbations(
    extraction: dict, rng: random.Random, document_id: str, model: str = "v1"
) -> dict:
    extraction["carrier"] = _maybe_swap_carrier(extraction["carrier"], rng)
    extraction["binder_effective_date"] = _maybe_reformat_date(
        extraction["binder_effective_date"], rng, us_bias=0.85
    )
    extraction["binder_expiration_date"] = _maybe_reformat_date(
        extraction["binder_expiration_date"], rng, us_bias=0.85
    )
    swap_rate = _BINDER_DATE_SWAP_RATE.get(document_id, 0.0)
    if swap_rate > 0 and rng.random() < swap_rate:
        extraction["binder_effective_date"], extraction["binder_expiration_date"] = (
            extraction["binder_expiration_date"],
            extraction["binder_effective_date"],
        )
    for cov in extraction.get("coverages", []):
        for field in (
            "each_occurrence_limit",
            "general_aggregate_limit",
            "products_completed_ops",
        ):
            cov[field] = _perturb_amount(cov.get(field), rng)
        cov["carrier"] = _maybe_swap_carrier(cov["carrier"], rng)
        cov["effective_date"] = _maybe_reformat_date(cov["effective_date"], rng)
        cov["expiration_date"] = _maybe_reformat_date(cov["expiration_date"], rng)
    return extraction


_PERTURBATION_DISPATCH = {
    "sov": _apply_sov_perturbations,
    "coi": _apply_coi_perturbations,
    "loss_run": _apply_loss_run_perturbations,
    "endorsement": _apply_endorsement_perturbations,
    "binder": _apply_binder_perturbations,
}


# --- Classification ---------------------------------------------------------

# Document IDs that the upstream classifier routes to non-canonical doc types.
# Historical hint: the travelers umbrella certificate has been observed to
# route as a policy document; suspected template-matching artifact.
_CLASSIFICATION_OVERRIDES = {
    "coi_travelers_umbrella": "policy",
}

# Confidence band used when the classifier emits an override. Empirically the
# classifier's template-matching branch reports inflated confidence (95-99)
# because template match is treated as a high-signal feature.
_FORCED_CLASSIFY_CONFIDENCE_RANGE = (95.0, 99.0)
_STANDARD_CLASSIFY_CONFIDENCE_RANGE = (82.0, 94.0)


def _classify(document_id: str, ground_truth_doc_type: str, rng: random.Random) -> dict:
    override = _CLASSIFICATION_OVERRIDES.get(document_id)
    doc_type = override or ground_truth_doc_type
    lo, hi = (
        _FORCED_CLASSIFY_CONFIDENCE_RANGE if override else _STANDARD_CLASSIFY_CONFIDENCE_RANGE
    )
    confidence = round(rng.uniform(lo, hi), 2)
    return {"doc_type": doc_type, "confidence": confidence}


# --- Loader -----------------------------------------------------------------

def _load_ground_truth(document_id: str) -> dict:
    baseline = _INTERNAL_BASELINE_EXTRACTIONS.get(document_id)
    if baseline is not None:
        return copy.deepcopy(baseline)
    path = _GROUND_TRUTH_DIR / f"{document_id}.json"
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown document_id: {document_id}",
        )
    with path.open() as f:
        return json.load(f)


# --- Operational noise middleware -------------------------------------------

# Rate-limit state: deque of recent request timestamps per client IP.
_rate_limit_state: dict[str, deque[float]] = defaultdict(deque)

# Routes subject to operational noise. /health and /config are exempt so
# operators can always introspect the service.
_NOISE_ROUTES = {"/extract"}


@app.middleware("http")
async def _operational_noise_middleware(request: Request, call_next):
    if request.url.path not in _NOISE_ROUTES:
        return await call_next(request)

    # Latency simulation.
    if config.LATENCY_ENABLED:
        delay = random.uniform(config.LATENCY_MIN_SECONDS, config.LATENCY_MAX_SECONDS)
        await asyncio.sleep(delay)

    # Rate limit (token-bucket-ish sliding window, per-client-IP).
    if config.RATELIMIT_ENABLED:
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start = now - config.RATELIMIT_WINDOW_SECONDS
        bucket = _rate_limit_state[client_ip]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= config.RATELIMIT_REQUESTS:
            retry_after = max(1, int(config.RATELIMIT_WINDOW_SECONDS - (now - bucket[0])))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "rate limit exceeded",
                    "limit": config.RATELIMIT_REQUESTS,
                    "window_seconds": config.RATELIMIT_WINDOW_SECONDS,
                },
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)

    # Transient 5xx injection.
    if config.FAILURES_ENABLED and random.random() < config.FAILURE_RATE:
        return JSONResponse(
            status_code=500,
            content={"detail": "transient extraction service error"},
        )

    return await call_next(request)


# --- Endpoints --------------------------------------------------------------

_MODEL_VERSIONS = {"v1": config.MODEL, "v2": "gpt-4o-2025-02-15"}


@app.post("/extract")
def extract(req: ExtractRequest) -> dict:
    model = req.model or "v1"
    if model not in _MODEL_VERSIONS:
        raise HTTPException(400, f"Unknown model: {model}. Try one of: {list(_MODEL_VERSIONS)}")

    rng = random.Random(req.seed) if req.seed is not None else random.Random()

    gt = _load_ground_truth(req.document_id)
    doc_type = gt["doc_type"]
    extraction = copy.deepcopy(gt["extraction"])

    perturb = _PERTURBATION_DISPATCH.get(doc_type)
    if perturb is not None:
        extraction = perturb(extraction, rng, req.document_id, model)

    _maybe_omit_optional_fields(extraction, doc_type, rng)

    classification = _classify(req.document_id, doc_type, rng)

    processing_time_ms = rng.randint(2800, 6400)

    return {
        "classification": classification,
        "extraction": extraction,
        "metadata": {
            "processing_time_ms": processing_time_ms,
            "model": _MODEL_VERSIONS[model],
            "model_version": model,
            "temperature": config.TEMPERATURE,
        },
    }


# --- Admin / runtime control ------------------------------------------------

@app.post("/admin/reseed-bugs")
def reseed_bugs(seed: int | None = None) -> dict:
    """Rotate which documents carry which behavioral patterns.

    Eval frameworks that hardcode `document_id == "..."` checks will surface
    as overfit after this call, since the same observable bug will now be
    attached to a different document. Useful for testing whether a pipeline
    eval generalizes or just memorizes.
    """
    rng = random.Random(seed)
    sov_docs = ["sov_acme_properties", "sov_pacific_realty", "sov_keystone_reit"]
    coi_docs = ["coi_hartford_general", "coi_travelers_umbrella", "coi_zurich_legacy"]
    loss_docs = ["loss_run_nationwide", "loss_run_libertymutual"]

    rng.shuffle(sov_docs)
    rng.shuffle(coi_docs)
    rng.shuffle(loss_docs)

    new_construction = {sov_docs[0]: 0.40}
    new_phantom = {coi_docs[0]: 0.20}
    new_unit_drift = {loss_docs[0]: 0.15}

    _CONSTRUCTION_TYPE_REPORT_GAP.clear()
    _CONSTRUCTION_TYPE_REPORT_GAP.update(new_construction)
    _PHANTOM_COVERAGE_DOCS.clear()
    _PHANTOM_COVERAGE_DOCS.update(new_phantom)
    _PAID_AMOUNT_UNIT_VARIANTS.clear()
    _PAID_AMOUNT_UNIT_VARIANTS.update(new_unit_drift)

    return {
        "ok": True,
        "current_assignments": {
            "construction_type_omission": new_construction,
            "phantom_coverage": new_phantom,
            "paid_amount_unit_drift": new_unit_drift,
        },
    }


@app.get("/admin/bug-registry")
def bug_registry() -> dict:
    """Read the current per-doc bug-rate assignments. Available only because
    the service is in eval/dev mode; would not exist in production."""
    return {
        "construction_type_omission": dict(_CONSTRUCTION_TYPE_REPORT_GAP),
        "phantom_coverage": dict(_PHANTOM_COVERAGE_DOCS),
        "paid_amount_unit_drift": dict(_PAID_AMOUNT_UNIT_VARIANTS),
        "binder_date_swap": dict(_BINDER_DATE_SWAP_RATE),
    }


@app.get("/config")
def get_config() -> dict:
    return {
        "auto_commit_threshold": config.AUTO_COMMIT_THRESHOLD,
        "max_retries": config.MAX_RETRIES,
        "retry_backoff": config.RETRY_BACKOFF,
        "supported_doc_types": config.SUPPORTED_DOC_TYPES,
        "text_confidence_weights": config.TEXT_CONFIDENCE_WEIGHTS,
        "scanned_confidence_weights": config.SCANNED_CONFIDENCE_WEIGHTS,
        "model": config.MODEL,
        "temperature": config.TEMPERATURE,
        "max_tokens": config.MAX_TOKENS,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Review console (UI) ----------------------------------------------------

from fastapi.responses import HTMLResponse  # noqa: E402

_REVIEW_CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>DocExtract Review Console</title>
<style>
  :root { --ok:#2e7d32; --warn:#ed6c02; --bad:#c62828; --bg:#0e1116; --fg:#e6e6e6; --panel:#161b22; --muted:#8b949e; }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--fg); font-family: -apple-system, system-ui, sans-serif; }
  h1 { margin:0 0 4px 0; font-size:20px; }
  .sub { color:var(--muted); margin-bottom:24px; }
  .row { display:flex; gap:24px; align-items:flex-start; }
  .col { flex:1; min-width:0; }
  .panel { background:var(--panel); padding:16px; border-radius:8px; }
  label { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }
  select, input, button { font:inherit; padding:8px 10px; border-radius:6px; border:1px solid #30363d; background:#0d1117; color:var(--fg); }
  button { cursor:pointer; }
  button.primary { background:#1f6feb; border-color:#1f6feb; color:white; font-weight:600; }
  button.commit { background:var(--ok); border-color:var(--ok); color:white; font-weight:600; padding:10px 16px; }
  button:disabled { opacity:0.4; cursor:not-allowed; }
  pre { background:#0d1117; padding:12px; border-radius:6px; overflow:auto; font-size:12px; line-height:1.5; max-height:520px; }
  .badge { display:inline-block; padding:4px 10px; border-radius:999px; font-weight:600; font-size:12px; }
  .badge.ok { background:rgba(46,125,50,0.18); color:#9be3a4; }
  .badge.warn { background:rgba(237,108,2,0.18); color:#ffcc80; }
  .badge.bad { background:rgba(198,40,40,0.18); color:#ff8a80; }
  .meta { display:flex; gap:16px; margin:12px 0; font-size:13px; color:var(--muted); }
  .meta span b { color:var(--fg); font-weight:600; }
  .actions { display:flex; gap:12px; margin-top:16px; }
  .doctype-pill { padding:3px 10px; border-radius:4px; background:#30363d; font-family:monospace; font-size:12px; }
</style>
</head>
<body>
  <h1>DocExtract Review Console</h1>
  <div class="sub">Operator UI for human-in-the-loop extraction review and commit-to-production.</div>

  <div class="row">
    <div class="col panel">
      <label for="doc">Document</label>
      <select id="doc">
        <option value="sov_acme_properties">sov_acme_properties</option>
        <option value="sov_pacific_realty">sov_pacific_realty</option>
        <option value="sov_keystone_reit">sov_keystone_reit</option>
        <option value="coi_hartford_general">coi_hartford_general</option>
        <option value="coi_travelers_umbrella">coi_travelers_umbrella</option>
        <option value="coi_zurich_legacy">coi_zurich_legacy</option>
        <option value="coi_unlabeled_mystery">coi_unlabeled_mystery</option>
        <option value="loss_run_nationwide">loss_run_nationwide</option>
        <option value="loss_run_libertymutual">loss_run_libertymutual</option>
        <option value="endorsement_chubb_tiv_increase">endorsement_chubb_tiv_increase</option>
        <option value="binder_travelers_temp">binder_travelers_temp</option>
      </select>

      <label for="model" style="margin-top:12px">Model version</label>
      <select id="model"><option value="v1">v1 (current production)</option><option value="v2">v2 (release candidate)</option></select>

      <label for="seed" style="margin-top:12px">Seed (optional)</label>
      <input id="seed" type="number" placeholder="leave blank for non-deterministic" />

      <div class="actions">
        <button class="primary" id="extract-btn" data-testid="extract-btn">Run Extraction</button>
      </div>
    </div>

    <div class="col panel">
      <div id="result-header">
        <div>
          <span class="doctype-pill" id="doc-type" data-testid="doc-type">—</span>
          <span class="badge" id="conf-badge" data-testid="conf-badge">no extraction yet</span>
        </div>
        <div class="meta" id="meta"></div>
      </div>
      <pre id="output" data-testid="extraction-output">{ }</pre>

      <div class="actions">
        <button class="commit" id="commit-btn" data-testid="commit-btn">Commit to Production</button>
        <button id="flag-btn" data-testid="flag-btn">Flag for Human Review</button>
      </div>
      <div id="commit-status" style="margin-top:12px;font-size:13px;color:var(--muted)" data-testid="commit-status"></div>
    </div>
  </div>

<script>
let lastConfidence = null;
let lastExtraction = null;
let configCache = null;

async function loadConfig() {
  const r = await fetch('/config');
  configCache = await r.json();
}

function setBadge(el, conf) {
  el.classList.remove('ok','warn','bad');
  // Threshold-based confidence color coding
  const t = configCache.auto_commit_threshold;
  if (conf >= t) el.classList.add('ok');
  else if (conf >= t / 2) el.classList.add('warn');
  else el.classList.add('bad');
  el.textContent = `confidence ${conf.toFixed(1)}`;
}

document.getElementById('extract-btn').addEventListener('click', async () => {
  const doc = document.getElementById('doc').value;
  const model = document.getElementById('model').value;
  const seedVal = document.getElementById('seed').value;
  const body = { document_id: doc, model };
  if (seedVal !== '') body.seed = parseInt(seedVal, 10);
  const out = document.getElementById('output');
  out.textContent = 'loading...';
  document.getElementById('commit-status').textContent = '';
  try {
    const r = await fetch('/extract', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) {
      out.textContent = `HTTP ${r.status}: ${await r.text()}`;
      return;
    }
    const data = await r.json();
    lastExtraction = data;
    lastConfidence = data.classification.confidence;
    document.getElementById('doc-type').textContent = data.classification.doc_type;
    setBadge(document.getElementById('conf-badge'), lastConfidence);
    document.getElementById('meta').innerHTML = `
      <span><b>model:</b> ${data.metadata.model_version || 'v1'}</span>
      <span><b>processing:</b> ${data.metadata.processing_time_ms}ms</span>
    `;
    out.textContent = JSON.stringify(data.extraction, null, 2);
    // Commit button is enabled whenever an extraction has loaded.
    document.getElementById('commit-btn').disabled = false;
  } catch (e) {
    out.textContent = `error: ${e.message}`;
  }
});

document.getElementById('commit-btn').addEventListener('click', () => {
  const status = document.getElementById('commit-status');
  if (!lastExtraction) { status.textContent = 'nothing to commit'; return; }
  status.textContent = `Committed to production at ${new Date().toISOString()} (confidence ${lastConfidence})`;
  status.style.color = 'var(--ok)';
});

document.getElementById('flag-btn').addEventListener('click', () => {
  const status = document.getElementById('commit-status');
  if (!lastExtraction) { status.textContent = 'nothing to flag'; return; }
  status.textContent = 'Flagged for human review.';
  status.style.color = 'var(--warn)';
});

// Initial state: commit button enabled by default. (Operators can re-enable
// after reviewing; we don't gate the button on confidence in the UI.)
document.getElementById('commit-btn').disabled = false;

loadConfig();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def review_console() -> HTMLResponse:
    return HTMLResponse(content=_REVIEW_CONSOLE_HTML)
