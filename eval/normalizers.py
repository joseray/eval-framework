"""Field normalization for comparison against ground truth.

All normalizers are pure functions: given a raw value, return a canonical form
that can be compared for equality. Apply to both the extracted value and the
ground-truth value before comparison so that format differences (date formats,
carrier name variants, whitespace) don't produce false mismatches.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Carrier canonicalization
# ---------------------------------------------------------------------------

# Maps marketing / short-form carrier names to the legal entity name.
# Both extracted values and ground-truth values are normalized before
# comparison so that "The Hartford" == "Hartford Financial Services".
_CARRIER_CANONICAL: dict[str, str] = {
    "The Hartford": "Hartford Financial Services",
    "Hartford": "Hartford Financial Services",
    "Travelers": "The Travelers Indemnity Company",
    "Travelers Insurance": "The Travelers Indemnity Company",
    "Zurich": "Zurich Insurance",
    "Zurich North America": "Zurich Insurance",
    "Nationwide": "Nationwide Insurance",
    "Nationwide Mutual": "Nationwide Insurance",
}


def normalize_carrier(name: str | None) -> str | None:
    """Return the canonical legal-entity carrier name, or the input unchanged."""
    if name is None:
        return None
    return _CARRIER_CANONICAL.get(name, name)


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

def normalize_date(value: str | None) -> str | None:
    """Normalize a date string to ISO YYYY-MM-DD.

    Handles:
      - ISO:           YYYY-MM-DD  (pass-through)
      - US format:     MM/DD/YYYY
      - EU format:     DD/MM/YYYY  (detected when first token > 12)
      - Ambiguous:     both tokens ≤ 12, US bias (MM/DD) per pipeline config

    Returns None for None input; returns the original string if parsing fails.
    """
    if value is None:
        return None
    value = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12:
            # First token cannot be a month — must be DD/MM/YYYY
            mm, dd = b, a
        else:
            # US bias: treat as MM/DD/YYYY (matches pipeline config)
            mm, dd = a, b
        return f"{year}-{mm:02d}-{dd:02d}"
    return value


# ---------------------------------------------------------------------------
# Amount normalization
# ---------------------------------------------------------------------------

def normalize_amount(value: float | None) -> float | None:
    """Round to 2 decimal places for consistent comparison."""
    if value is None:
        return None
    return round(float(value), 2)


# ---------------------------------------------------------------------------
# String normalization
# ---------------------------------------------------------------------------

def normalize_string(value: str | None) -> str | None:
    """Collapse internal whitespace and strip leading/trailing whitespace."""
    if value is None:
        return None
    return " ".join(value.split())
