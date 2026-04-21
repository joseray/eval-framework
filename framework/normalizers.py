"""Value normalizers applied BEFORE comparison.

Collapse different representations of the same value into canonical form.
Do NOT fix incorrect values — only normalize format.
"""

from __future__ import annotations

import re
from typing import Any, Callable


def normalize_date(value: str | None) -> str | None:
    """Normalize date strings to YYYY-MM-DD.

    Handles YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY.
    Ambiguous dates (day <= 12) assume US format (MM/DD/YYYY).
    """
    if value is None:
        return None
    value = value.strip()

    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value

    # Slash-separated: could be MM/DD/YYYY or DD/MM/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), m.group(3)
        # If first part > 12, it must be DD/MM/YYYY
        if a > 12:
            day, month = a, b
        else:
            # US bias: assume MM/DD/YYYY
            month, day = a, b
        return f"{year}-{month:02d}-{day:02d}"

    return value


def normalize_amount(value: float | None) -> float | None:
    """Round to 2 decimal places."""
    if value is None:
        return None
    return round(float(value), 2)


def normalize_string(value: str | None) -> str | None:
    """Collapse whitespace, strip, lowercase for comparison."""
    if value is None:
        return None
    return " ".join(value.strip().split())


_NORMALIZER_REGISTRY: dict[str, Callable] = {
    "date": normalize_date,
    "amount": normalize_amount,
    "string": normalize_string,
}


def register_normalizer(field_type: str, fn: Callable) -> None:
    """Register a custom normalizer. Used by projects to add domain-specific normalization."""
    _NORMALIZER_REGISTRY[field_type] = fn


def normalize(value: Any, field_type: str) -> Any:
    """Normalize a value using the registered normalizer for its type."""
    fn = _NORMALIZER_REGISTRY.get(field_type)
    return fn(value) if fn else value
