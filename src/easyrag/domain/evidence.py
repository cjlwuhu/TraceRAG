"""Unified retrieval evidence and reproducible retrieval snapshots."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .knowledge import assert_no_evaluation_keys


EVIDENCE_TYPES = frozenset({"doc", "case", "metric", "log", "trace", "topology", "image"})


@dataclass(frozen=True)
class EvidenceItem:
    schema_version: str
    evidence_id: str
    type: str
    content: str
    score: float
    rank: int
    retriever: str
    source: str
    raw_ref: str
    metadata: dict[str, Any]

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("EvidenceItem schema_version must be '1.0'")
        if not re.fullmatch(r"ev-(doc|case|metric|log|trace|topology|image)-[a-f0-9]{16}", self.evidence_id):
            raise ValueError("invalid evidence_id")
        if self.type not in EVIDENCE_TYPES:
            raise ValueError(f"unsupported evidence type: {self.type}")
        if not self.content.strip():
            raise ValueError("evidence content must not be empty")
        if not math.isfinite(self.score):
            raise ValueError("evidence score must be finite")
        if self.rank < 1:
            raise ValueError("evidence rank must be positive")
        for name, value in {
            "retriever": self.retriever,
            "source": self.source,
            "raw_ref": self.raw_ref,
        }.items():
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        assert_no_evaluation_keys(self.metadata)
        if any(Path(str(value)).is_absolute() for value in self.metadata.values() if isinstance(value, str)):
            raise ValueError("evidence metadata must not contain absolute local paths")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class RetrievalSnapshot:
    schema_version: str
    retrieval_id: str
    created_at_utc: str
    query: str
    current_incident_id: str | None
    results: dict[str, list[EvidenceItem]]
    saved_to: str | None = None

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("RetrievalSnapshot schema_version must be '1.0'")
        if not re.fullmatch(r"ret-[a-f0-9]{16}", self.retrieval_id):
            raise ValueError("invalid retrieval_id")
        try:
            parsed = datetime.fromisoformat(self.created_at_utc.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_at_utc must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("created_at_utc must include a timezone")
        if not self.query.strip():
            raise ValueError("retrieval query must not be empty")
        if set(self.results) != {"doc", "case", "metric"}:
            raise ValueError("snapshot results must contain doc, case, and metric")
        for evidence_type, items in self.results.items():
            expected_ranks = list(range(1, len(items) + 1))
            if [item.rank for item in items] != expected_ranks:
                raise ValueError(f"{evidence_type} ranks must be consecutive")
            if any(item.type != evidence_type for item in items):
                raise ValueError(f"{evidence_type} result contains the wrong type")
            for item in items:
                item.validate()

    def to_dict(self, *, include_saved_to: bool = True) -> dict[str, Any]:
        self.validate()
        output: dict[str, Any] = {
            "schema_version": self.schema_version,
            "retrieval_id": self.retrieval_id,
            "created_at_utc": self.created_at_utc,
            "query": self.query,
            "results": {
                key: [item.to_dict() for item in items]
                for key, items in self.results.items()
            },
        }
        if self.current_incident_id:
            output["current_incident_id"] = self.current_incident_id
        if include_saved_to and self.saved_to:
            output["saved_to"] = self.saved_to
        return output
