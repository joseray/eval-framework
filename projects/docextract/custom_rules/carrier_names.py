"""Insurance carrier name normalization."""

from __future__ import annotations

CARRIER_CANONICAL: dict[str, str] = {
    "the hartford": "Hartford Financial Services",
    "hartford": "Hartford Financial Services",
    "hartford financial services": "Hartford Financial Services",
    "travelers": "The Travelers Indemnity Company",
    "travelers insurance": "The Travelers Indemnity Company",
    "the travelers indemnity company": "The Travelers Indemnity Company",
    "zurich": "Zurich Insurance",
    "zurich north america": "Zurich Insurance",
    "zurich insurance": "Zurich Insurance",
    "nationwide": "Nationwide Insurance",
    "nationwide mutual": "Nationwide Insurance",
    "nationwide insurance": "Nationwide Insurance",
    "liberty mutual": "Liberty Mutual Fire Insurance Company",
    "liberty mutual fire insurance company": "Liberty Mutual Fire Insurance Company",
    "chubb": "Chubb Insurance",
    "chubb insurance": "Chubb Insurance",
}


def normalize_carrier(name: str | None) -> str | None:
    if name is None:
        return None
    return CARRIER_CANONICAL.get(name.strip().lower(), name)
