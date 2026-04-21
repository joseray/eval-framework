# Eval Rubric

> Fill in this template (or rewrite it entirely) as part of Deliverable 2.
> The goal is a 1–2 page document a reviewer could use to decide whether a
> given extraction is "good enough to auto-commit," "needs human review,"
> or "must reject."

## Field Categories and Match Rules

Describe how you'd compare each kind of field against ground truth.

| Field type | Example fields | Match rule | Tolerance / notes |
| --- | --- | --- | --- |
| Identifiers | `policy_number`, `claim_number`, `binder_number`, `endorsement_number` | | |
| Party names | `insured_name`, `certificate_holder`, `claimant` | | |
| Carrier names | `carrier` | | |
| Dates | `effective_date`, `expiration_date`, `date_of_loss`, `policy_effective_date`, `valuation_date`, `binder_effective_date`, `binder_expiration_date`, `endorsement_effective_date` | | |
| Dollar amounts | `building_value`, `total_insured_value`, `paid_amount`, limits, `premium_delta` | | |
| Structural / enum | `doc_type`, `construction_type`, `coverage_type`, `status`, `change_type` | | |
| Derived aggregates | `total_tiv`, `total_paid`, `total_recoveries`, `total_incurred`, `loss_ratio` | | |
| Optional / nullable | `producer`, `year_built`, `square_footage`, `business_income_value`, `old_value`, `binding_authority_reference` | | |

## Cross-Field Invariants

What internal consistency checks apply regardless of ground truth? Examples to consider:

- `building_value + contents_value + business_income_value` vs. `total_insured_value`
- `sum(properties[i].total_insured_value)` vs. `total_tiv`
- `paid_amount + reserved_amount` vs. `total_incurred` (with subrogation edge cases)
- `expiration_date >= effective_date` (coverages, binders, endorsements)
- Claims: `status == "closed"` implies `reserved_amount == 0`
- `total_paid` interpretation when subrogation recoveries are present
- Every coverage returned must be traceable to the source `raw_text`

## Classification Calibration

How should confidence relate to correctness?

- When the pipeline mis-routes a document (wrong `doc_type`), should confidence go up or down?
- What's the expected confidence distribution for correct vs. incorrect classifications?
- What do you do if confidence is *higher* on a wrong answer than on a right one?

## Pass / Review / Reject Thresholds

Define what proportion of fields (or which specific fields) must match for each tier:

- **Auto-commit pass**: ...
- **Human review**: ...
- **Reject**: ...

## Per-Doc-Type Considerations

Are there different thresholds for SOVs vs. COIs vs. Loss Runs vs. Endorsements vs. Binders? Why?

- SOV: ...
- COI: ...
- Loss Run: ...
- Endorsement: ...
- Binder: ...

## Normalization Rules

How do you canonicalize values before comparison?

- Carrier names: ...
- Dates: ...
- Dollar amounts / currency formatting (and cross-currency handling): ...
- Addresses and whitespace: ...
- Legacy vs. modern ACORD layouts: ...

## Handling Non-Determinism

If the pipeline is run N times against the same document, how do you decide whether a field's variance is acceptable?

- ...

## Partial, Disputed, and Missing Ground Truth

How does your rubric handle:

- A ground-truth value that is explicitly `"unknown"`? (e.g., `sov_acme_properties` square_footage)
- A document with two ground-truth labelings that disagree? (e.g., `sov_keystone_reit`)
- A document with no ground truth at all? (e.g., `coi_unlabeled_mystery`)

## Precision vs. Recall

- Hallucinated fields (model returns something not in source): ...
- Omitted fields (model drops something present in source): ...

## Out of Scope / Known Gaps

What would you add to this rubric with more time?

- ...
