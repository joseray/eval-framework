"""Ground truth loader.

Loads JSONs from a directory. Supports primary labels,
alternate (disputed) labels, and documents without labels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GroundTruthEntry:
    document_id: str
    doc_type: str
    extraction: dict
    notes: str = ""
    alt_label: bool = False
    disagrees_on: list[str] = field(default_factory=list)
    partial_truth_fields: list[dict] = field(default_factory=list)


class GroundTruthStore:
    def __init__(self, gt_dir: Path, alt_dir: Path | None = None):
        self._cache: dict[str, GroundTruthEntry] = {}
        self._alt_cache: dict[str, GroundTruthEntry] = {}
        self._load(gt_dir, self._cache, alt_label=False)
        if alt_dir and Path(alt_dir).exists():
            self._load(alt_dir, self._alt_cache, alt_label=True)

    def _load(self, directory: Path, cache: dict, alt_label: bool) -> None:
        directory = Path(directory)
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.json")):
            try:
                with open(path) as f:
                    raw = json.load(f)
                doc_id = raw.get("document_id") or path.stem
                entry = GroundTruthEntry(
                    document_id=doc_id,
                    doc_type=raw["doc_type"],
                    extraction=raw["extraction"],
                    notes=raw.get("notes", ""),
                    alt_label=alt_label,
                    disagrees_on=raw.get("disagrees_on", []),
                    partial_truth_fields=raw.get("partial_truth_fields", []),
                )
                cache[doc_id] = entry
            except (KeyError, json.JSONDecodeError) as e:
                raise ValueError(f"Failed to load ground truth from {path}: {e}") from e

    def get(self, document_id: str) -> GroundTruthEntry | None:
        """Return the primary ground truth, or None if it doesn't exist."""
        return self._cache.get(document_id)

    def get_alt(self, document_id: str) -> GroundTruthEntry | None:
        """Return the alternate (disputed) ground truth."""
        return self._alt_cache.get(document_id)

    def has_label(self, document_id: str) -> bool:
        return document_id in self._cache

    def all_document_ids(self) -> list[str]:
        return list(self._cache.keys())

    def documents_with_ground_truth(self) -> list[str]:
        return list(self._cache.keys())
