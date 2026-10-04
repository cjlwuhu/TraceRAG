"""Versioned, retrieval-safe incident document model."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Detection:
    timestamp_unix: int
    timestamp_utc: str
    source: str
    window_minutes: int | None = None
    evidence_cutoff_utc: str | None = None


@dataclass(frozen=True)
class Observations:
    suspected_services: list[str] = field(default_factory=list)
    candidate_metrics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RootCauseCandidate:
    rank: int
    metric: str
    service: str | None = None
    signal: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class RcaRun:
    method: str
    candidates: list[RootCauseCandidate]
    runtime_seconds: float | None = None


@dataclass(frozen=True)
class SourceRef:
    source_type: str
    method: str
    sha256: str


@dataclass(frozen=True)
class IncidentDocument:
    schema_version: str
    incident_id: str
    system: str
    lifecycle: str
    detection: Detection
    observations: Observations
    rca_runs: list[RcaRun]
    retrieval_text: str
    source_refs: list[SourceRef]
    limitations: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate invariants without introducing a runtime schema dependency."""
        if self.schema_version not in {"1.0", "1.1"}:
            raise ValueError("schema_version must be '1.0' or '1.1'")
        if not re.fullmatch(r"inc-[0-9]{10}-[a-f0-9]{12}", self.incident_id):
            raise ValueError("incident_id must be a neutral generated identifier")
        if not self.system.strip():
            raise ValueError("system must not be empty")
        if self.lifecycle not in {"active", "resolved"}:
            raise ValueError("lifecycle must be active or resolved")
        if self.detection.timestamp_unix < 0:
            raise ValueError("detection timestamp must be non-negative")
        if self.detection.source not in {
            "benchmark_annotation",
            "anomaly_detector",
            "operator",
        }:
            raise ValueError("unsupported detection source")
        if self.detection.window_minutes is not None and self.detection.window_minutes < 1:
            raise ValueError("window_minutes must be positive")
        if self.schema_version == "1.0" and not self.rca_runs:
            raise ValueError("at least one RCA run is required")
        methods = [run.method for run in self.rca_runs]
        if len(methods) != len(set(methods)):
            raise ValueError("RCA methods must be unique within one incident")
        for run in self.rca_runs:
            expected_ranks = list(range(1, len(run.candidates) + 1))
            actual_ranks = [candidate.rank for candidate in run.candidates]
            if actual_ranks != expected_ranks:
                raise ValueError(f"candidate ranks for {run.method} must be consecutive")
            if run.runtime_seconds is not None and run.runtime_seconds < 0:
                raise ValueError("runtime_seconds must be non-negative")
        if not self.retrieval_text.strip():
            raise ValueError("retrieval_text must not be empty")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _drop_none(asdict(self))


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value
