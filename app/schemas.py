"""Pydantic extraction schemas for the DocExtract pipeline.

These mirror the structured outputs the LLM is prompted to produce for each
document type. Field names are intentionally stable — downstream consumers
(database writes, eval harnesses, ground-truth comparisons) key off them.
"""

from __future__ import annotations

from pydantic import BaseModel


# --- SOV (Statement of Values) ---------------------------------------------

class PropertyEntry(BaseModel):
    address: str
    city: str
    state: str
    zip_code: str
    building_value: float | None = None
    contents_value: float | None = None
    business_income_value: float | None = None
    total_insured_value: float
    construction_type: str | None = None  # frame, masonry, fire-resistive, ...
    year_built: int | None = None
    square_footage: int | None = None
    occupancy: str | None = None


class SOVExtraction(BaseModel):
    insured_name: str
    policy_number: str | None = None
    carrier: str
    effective_date: str  # ISO target (YYYY-MM-DD)
    expiration_date: str
    properties: list[PropertyEntry]
    total_tiv: float  # sum of per-property total_insured_value
    currency: str = "USD"


# --- COI (Certificate of Insurance / ACORD 25) -----------------------------

class CoverageEntry(BaseModel):
    coverage_type: str  # general_liability, auto, umbrella, workers_comp, ...
    policy_number: str
    carrier: str
    effective_date: str
    expiration_date: str
    each_occurrence_limit: float | None = None
    general_aggregate_limit: float | None = None
    products_completed_ops: float | None = None


class COIExtraction(BaseModel):
    certificate_holder: str
    insured_name: str
    producer: str | None = None  # broker / agent of record
    coverages: list[CoverageEntry]
    description_of_operations: str | None = None


# --- Loss Run --------------------------------------------------------------

class ClaimEntry(BaseModel):
    claim_number: str
    date_of_loss: str
    claimant: str | None = None
    claim_type: str  # property, liability, auto, workers_comp, ...
    status: str  # open, closed, reserved
    paid_amount: float
    reserved_amount: float
    total_incurred: float  # paid + reserved


class LossRunExtraction(BaseModel):
    insured_name: str
    carrier: str
    policy_number: str
    policy_period: str  # e.g. "01/01/2023 - 01/01/2024"
    policy_effective_date: str  # ISO target (YYYY-MM-DD)
    valuation_date: str
    claims: list[ClaimEntry]
    total_paid: float
    total_recoveries: float = 0.0  # subrogation / salvage offsets (always >= 0)
    total_incurred: float
    loss_ratio: float | None = None


# --- Endorsement (policy change document) ----------------------------------

class EndorsementExtraction(BaseModel):
    insured_name: str
    policy_number: str
    carrier: str
    endorsement_number: str
    endorsement_effective_date: str
    change_type: str  # add_location, change_limit, add_insured, premium_adjustment
    affected_field: str  # e.g. "total_insured_value", "each_occurrence_limit"
    old_value: str | None = None
    new_value: str | None = None
    premium_delta: float | None = None  # signed — positive for increase


# --- Binder (temporary coverage confirmation before full policy issues) ----

class BinderExtraction(BaseModel):
    insured_name: str
    producer: str | None = None
    binder_number: str
    binding_authority_reference: str | None = None
    carrier: str
    binder_effective_date: str
    binder_expiration_date: str  # typically 30-60 days out
    coverages: list[CoverageEntry]
    anticipated_policy_number: str | None = None
    description_of_operations: str | None = None
