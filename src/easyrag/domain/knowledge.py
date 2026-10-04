"""Versioned knowledge objects used by the operations RAG corpus."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


OBSERVATION_TYPES = frozenset({"metric", "log", "trace", "topology", "image"})
KNOWLEDGE_TYPES = frozenset({"runbook", "case", *OBSERVATION_TYPES})
FORBIDDEN_EVALUATION_KEYS = frozenset(
    {
        "ground_truth",
        "fault_type",
        "root_cause_service",
        "root_cause_metric",
        "label",
        "labels",
    }
)


@dataclass(frozen=True)
class KnowledgeDocument:
    """The single JSONL record shape consumed by EasyRAG ingestion."""

    schema_version: str
    knowledge_id: str
    knowledge_type: str
    title: str
    content: str
    source: str
    raw_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("schema_version must be '1.0'")
        if not re.fullmatch(r"[a-z][a-z0-9._-]{2,127}", self.knowledge_id):
            raise ValueError("knowledge_id contains unsupported characters")
        if self.knowledge_type not in KNOWLEDGE_TYPES:
            raise ValueError(f"unsupported knowledge_type: {self.knowledge_type}")
        for name, value in {
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "raw_ref": self.raw_ref,
        }.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        assert_no_evaluation_keys(self.metadata)
        _validate_json_value(self.metadata, "metadata")
        reserved = {"knowledge_id", "knowledge_type", "source", "raw_ref", "file_path",
                    "file_abs_path", "document_title", "know_path", "dir"}
        if reserved & self.metadata.keys():
            raise ValueError("metadata must not override reserved document fields")
        if self.knowledge_type in {"case", *OBSERVATION_TYPES}:
            _required_text(self.metadata, "source_incident_id")
            _required_text(self.metadata, "system")
        if self.knowledge_type == "case":
            if self.metadata.get("human_verified") is not True:
                raise ValueError("unverified incident cannot enter HistoricalCase corpus")
            _required_text(self.metadata, "verified_by")
            _parse_iso_datetime(_required_text(self.metadata, "verified_at"), "verified_at")
        if self.knowledge_type in OBSERVATION_TYPES:
            start = _parse_iso_datetime(_required_text(self.metadata, "window_start_utc"), "window_start_utc")
            end = _parse_iso_datetime(_required_text(self.metadata, "window_end_utc"), "window_end_utc")
            if end <= start:
                raise ValueError("metric window end must be after start")
        if self.knowledge_type in {"log", "trace", "topology", "image"}:
            if len(self.content) > 8000: raise ValueError("observation must be compact (8000 characters)")
            if not re.fullmatch(r"[a-f0-9]{64}", _required_text(self.metadata, "asset_sha256")):
                raise ValueError("observation requires a SHA-256 source asset fingerprint")
            _required_text(self.metadata, "derivation")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeDocument":
        allowed = {
            "schema_version",
            "knowledge_id",
            "knowledge_type",
            "title",
            "content",
            "source",
            "raw_ref",
            "metadata",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown KnowledgeDocument fields: {sorted(unknown)}")
        document = cls(
            schema_version=_required_text(data, "schema_version"),
            knowledge_id=_required_text(data, "knowledge_id"),
            knowledge_type=_required_text(data, "knowledge_type"),
            title=_required_text(data, "title"),
            content=_required_text(data, "content"),
            source=_required_text(data, "source"),
            raw_ref=_required_text(data, "raw_ref"),
            metadata=_required_mapping(data, "metadata"),
        )
        document.validate()
        return document


@dataclass(frozen=True)
class HistoricalCase:
    """A resolved incident that passed an explicit verification gate."""

    schema_version: str
    case_id: str
    source_incident_id: str
    system: str
    title: str
    symptoms: list[str]
    rca_candidates: list[str]
    verified_root_cause: str
    actions: list[str]
    evidence_refs: list[str]
    human_verified: bool
    verified_by: str
    verified_at: str
    source: str
    raw_ref: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HistoricalCase":
        assert_no_evaluation_keys(data)
        allowed = {
            "schema_version",
            "case_id",
            "source_incident_id",
            "system",
            "title",
            "symptoms",
            "rca_candidates",
            "verified_root_cause",
            "actions",
            "evidence_refs",
            "human_verified",
            "verified_by",
            "verified_at",
            "source",
            "raw_ref",
        }
        _reject_unknown(data, allowed, "HistoricalCase")
        item = cls(
            schema_version=_required_text(data, "schema_version"),
            case_id=_required_text(data, "case_id"),
            source_incident_id=_required_text(data, "source_incident_id"),
            system=_required_text(data, "system"),
            title=_required_text(data, "title"),
            symptoms=_required_text_list(data, "symptoms", minimum=1),
            rca_candidates=_required_text_list(data, "rca_candidates", minimum=0),
            verified_root_cause=_required_text(data, "verified_root_cause"),
            actions=_required_text_list(data, "actions", minimum=1),
            evidence_refs=_required_text_list(data, "evidence_refs", minimum=1),
            human_verified=data.get("human_verified"),
            verified_by=_required_text(data, "verified_by"),
            verified_at=_required_text(data, "verified_at"),
            source=_required_text(data, "source"),
            raw_ref=_required_text(data, "raw_ref"),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("HistoricalCase schema_version must be '1.0'")
        if self.human_verified is not True:
            raise ValueError("unverified incident cannot enter HistoricalCase corpus")
        _parse_iso_datetime(self.verified_at, "verified_at")

    def to_knowledge_document(self) -> KnowledgeDocument:
        self.validate()
        candidate_text = "；".join(self.rca_candidates) or "未记录"
        content = "\n".join(
            [
                f"系统：{self.system}",
                f"故障症状：{'；'.join(self.symptoms)}",
                f"当时的 RCA 候选：{candidate_text}",
                f"已确认根因：{self.verified_root_cause}",
                f"处置措施：{'；'.join(self.actions)}",
                f"确认依据：{'；'.join(self.evidence_refs)}",
            ]
        )
        return KnowledgeDocument(
            schema_version="1.0",
            knowledge_id=self.case_id,
            knowledge_type="case",
            title=self.title,
            content=content,
            source=self.source,
            raw_ref=self.raw_ref,
            metadata={
                "source_incident_id": self.source_incident_id,
                "system": self.system,
                "human_verified": True,
                "verified_by": self.verified_by,
                "verified_at": self.verified_at,
            },
        )


@dataclass(frozen=True)
class MetricSummary:
    """A compact observation derived from a bounded telemetry window."""

    schema_version: str
    summary_id: str
    source_incident_id: str
    system: str
    service: str
    metric: str
    window_start_utc: str
    window_end_utc: str
    direction: str
    summary: str
    statistics: dict[str, float]
    source: str
    raw_ref: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricSummary":
        assert_no_evaluation_keys(data)
        allowed = {
            "schema_version",
            "summary_id",
            "source_incident_id",
            "system",
            "service",
            "metric",
            "window_start_utc",
            "window_end_utc",
            "direction",
            "summary",
            "statistics",
            "source",
            "raw_ref",
        }
        _reject_unknown(data, allowed, "MetricSummary")
        statistics = _required_mapping(data, "statistics")
        numeric_statistics: dict[str, float] = {}
        for key, value in statistics.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("statistics keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"statistics.{key} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"statistics.{key} must be finite")
            numeric_statistics[key] = numeric
        item = cls(
            schema_version=_required_text(data, "schema_version"),
            summary_id=_required_text(data, "summary_id"),
            source_incident_id=_required_text(data, "source_incident_id"),
            system=_required_text(data, "system"),
            service=_required_text(data, "service"),
            metric=_required_text(data, "metric"),
            window_start_utc=_required_text(data, "window_start_utc"),
            window_end_utc=_required_text(data, "window_end_utc"),
            direction=_required_text(data, "direction"),
            summary=_required_text(data, "summary"),
            statistics=numeric_statistics,
            source=_required_text(data, "source"),
            raw_ref=_required_text(data, "raw_ref"),
        )
        item.validate()
        return item

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("MetricSummary schema_version must be '1.0'")
        if self.direction not in {"increase", "decrease", "flat", "unknown"}:
            raise ValueError("unsupported metric direction")
        start = _parse_iso_datetime(self.window_start_utc, "window_start_utc")
        end = _parse_iso_datetime(self.window_end_utc, "window_end_utc")
        if end <= start:
            raise ValueError("metric window end must be after start")

    def to_knowledge_document(self) -> KnowledgeDocument:
        self.validate()
        statistic_text = "；".join(
            f"{key}={value:.6g}" for key, value in sorted(self.statistics.items())
        )
        content = "\n".join(
            [
                f"时序指标摘要：{self.metric}",
                f"系统与服务：{self.system} / {self.service}",
                f"观测窗口：{self.window_start_utc} 至 {self.window_end_utc}",
                f"变化方向：{self.direction}",
                f"观测结论：{self.summary}",
                f"统计量：{statistic_text or '未提供'}",
            ]
        )
        return KnowledgeDocument(
            schema_version="1.0",
            knowledge_id=self.summary_id,
            knowledge_type="metric",
            title=f"{self.service} {self.metric} 时序摘要",
            content=content,
            source=self.source,
            raw_ref=self.raw_ref,
            metadata={
                "source_incident_id": self.source_incident_id,
                "system": self.system,
                "service": self.service,
                "metric": self.metric,
                "window_start_utc": self.window_start_utc,
                "window_end_utc": self.window_end_utc,
                "direction": self.direction,
            },
        )


def assert_no_evaluation_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in FORBIDDEN_EVALUATION_KEYS:
                raise ValueError(f"forbidden evaluation key at {path}.{key}")
            assert_no_evaluation_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_evaluation_keys(item, f"{path}[{index}]")


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _required_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _required_text_list(
    data: dict[str, Any], key: str, *, minimum: int
) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{key} must contain at least {minimum} item(s)")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must contain only non-empty strings")
    return [item.strip() for item in value]


def _reject_unknown(data: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown {name} fields: {sorted(unknown)}")


def _parse_iso_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _validate_json_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value")
