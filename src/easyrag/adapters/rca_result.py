"""Convert RCA benchmark outputs into retrieval-safe incident documents."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from easyrag.domain.incident import (
    Detection,
    IncidentDocument,
    Observations,
    RcaRun,
    RootCauseCandidate,
    SourceRef,
)


FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "ground_truth",
        "fault_type",
        "root_cause",
        "root_cause_service",
        "root_cause_metric",
        "label",
        "labels",
    }
)


def build_incident_document(
    source_paths: Iterable[str | Path],
    *,
    top_k: int = 5,
) -> IncidentDocument:
    """Merge RCA runs for one incident while excluding benchmark-only labels."""
    paths = [Path(path) for path in source_paths]
    if not paths:
        raise ValueError("at least one RCA result path is required")
    if top_k < 1:
        raise ValueError("top_k must be positive")

    loaded = [_load_result(path) for path in paths]
    first_data = loaded[0][1]
    source_identity = _required_text(first_data, "incident_id")
    system = _required_text(first_data, "system")
    timestamp = _required_int(first_data, "inject_time")

    runs: list[RcaRun] = []
    refs: list[SourceRef] = []
    seen_methods: set[str] = set()
    candidate_metrics: list[str] = []
    suspected_services: list[str] = []

    for path, data, digest in loaded:
        _require_same_incident(data, source_identity, system, timestamp, path)
        method = _required_text(data, "method")
        if method in seen_methods:
            raise ValueError(f"duplicate RCA method {method!r}")
        seen_methods.add(method)

        raw_candidates = data.get("root_causes")
        if not isinstance(raw_candidates, list):
            raise ValueError(f"{path}: root_causes must be a list")
        candidates: list[RootCauseCandidate] = []
        for output_rank, raw in enumerate(raw_candidates[:top_k], start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"{path}: every root cause candidate must be an object")
            metric = _required_text(raw, "metric")
            service, signal = split_metric(metric)
            score = raw.get("score")
            if score is not None and not isinstance(score, (int, float)):
                raise ValueError(f"{path}: candidate score must be numeric")
            candidates.append(
                RootCauseCandidate(
                    rank=output_rank,
                    metric=metric,
                    service=service,
                    signal=signal,
                    score=float(score) if score is not None else None,
                )
            )
            _append_unique(candidate_metrics, metric)
            if service:
                _append_unique(suspected_services, service)

        runtime = data.get("runtime_seconds")
        if runtime is not None and not isinstance(runtime, (int, float)):
            raise ValueError(f"{path}: runtime_seconds must be numeric")
        runs.append(
            RcaRun(
                method=method,
                candidates=candidates,
                runtime_seconds=float(runtime) if runtime is not None else None,
            )
        )
        refs.append(SourceRef(source_type="rca_result", method=method, sha256=digest))

    window_minutes = _consistent_window_minutes(item[1] for item in loaded)
    neutral_id = _neutral_incident_id(system, timestamp, source_identity)
    timestamp_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    observations = Observations(
        suspected_services=suspected_services,
        candidate_metrics=candidate_metrics,
    )
    document = IncidentDocument(
        schema_version="1.0",
        incident_id=neutral_id,
        system=system,
        lifecycle="active",
        detection=Detection(
            timestamp_unix=timestamp,
            timestamp_utc=timestamp_utc,
            source="benchmark_annotation",
            window_minutes=window_minutes,
        ),
        observations=observations,
        rca_runs=runs,
        retrieval_text=_build_retrieval_text(system, timestamp_utc, runs, observations),
        source_refs=refs,
        limitations=[
            "Benchmark ground truth and injected fault labels were excluded.",
            "The detection timestamp is a benchmark annotation, not an online detector result.",
            "Root-cause entries are model candidates and are not confirmed resolutions.",
        ],
    )
    output = document.to_dict()
    assert_no_forbidden_keys(output)
    return document


def split_metric(metric: str) -> tuple[str | None, str | None]:
    """Split the final underscore as a conservative service/signal hint."""
    service, separator, signal = metric.rpartition("_")
    if not separator or not service or not signal:
        return None, None
    return service, signal


def assert_no_forbidden_keys(value: Any, path: str = "$") -> None:
    """Fail closed if evaluation labels are accidentally added later."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in FORBIDDEN_OUTPUT_KEYS:
                raise ValueError(f"forbidden evaluation key at {path}.{key}")
            assert_no_forbidden_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_forbidden_keys(item, f"{path}[{index}]")


def _load_result(path: Path) -> tuple[Path, dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: RCA result must be a JSON object")
    return path, data, hashlib.sha256(raw).hexdigest()


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _required_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _require_same_incident(
    data: dict[str, Any],
    source_identity: str,
    system: str,
    timestamp: int,
    path: Path,
) -> None:
    identity = (
        _required_text(data, "incident_id"),
        _required_text(data, "system"),
        _required_int(data, "inject_time"),
    )
    if identity != (source_identity, system, timestamp):
        raise ValueError(f"{path}: RCA files do not describe the same incident")


def _consistent_window_minutes(results: Iterable[dict[str, Any]]) -> int | None:
    values: set[int] = set()
    for data in results:
        metadata = data.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("window_minutes") is None:
            continue
        value = metadata["window_minutes"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("metadata.window_minutes must be a positive integer")
        values.add(value)
    if len(values) > 1:
        raise ValueError("RCA files disagree on metadata.window_minutes")
    return next(iter(values), None)


def _neutral_incident_id(system: str, timestamp: int, source_identity: str) -> str:
    seed = f"{system}\0{timestamp}\0{source_identity}".encode("utf-8")
    suffix = hashlib.sha256(seed).hexdigest()[:12]
    return f"inc-{timestamp:010d}-{suffix}"


def _build_retrieval_text(
    system: str,
    timestamp_utc: str,
    runs: list[RcaRun],
    observations: Observations,
) -> str:
    services = "、".join(observations.suspected_services) or "未识别"
    lines = [
        f"系统：{system}",
        f"检测时间：{timestamp_utc}",
        f"RCA 候选服务：{services}",
    ]
    for run in runs:
        ranked = "；".join(
            f"第{candidate.rank}名 {candidate.metric}"
            + (f"（得分 {candidate.score:.6g}）" if candidate.score is not None else "")
            for candidate in run.candidates
        )
        lines.append(f"方法 {run.method} 的根因候选：{ranked or '无候选'}")
    lines.append("说明：以上均为算法候选，需结合日志、拓扑和工单证据确认。")
    return "\n".join(lines)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
