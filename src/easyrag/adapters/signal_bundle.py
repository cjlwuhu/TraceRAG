"""Strict boundary from RCA's label-free file artifact to RAG contracts.

No import of RCA internals; either project can run in a different process/env.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import unquote

from pydantic import constr, root_validator

from easyrag.adapters.rca_result import split_metric
from easyrag.domain.experiment import StrictModel
from easyrag.domain.incident import Detection, IncidentDocument, Observations, RcaRun, RootCauseCandidate, SourceRef
from easyrag.domain.knowledge import MetricSummary, assert_no_evaluation_keys
from easyrag.retrieval.evidence_pack import fingerprint
from easyrag.retrieval.query_context import IncidentInput, RunInput, parse_time


class SignalEnvelope(StrictModel):
    schema_version: Literal["1.0"]
    bundle_id: constr(strict=True, regex=r"^signal-[a-f0-9]{20}$")
    incident_id: Optional[str]
    system: constr(strict=True, min_length=1, max_length=100)
    telemetry: dict
    detection: dict
    analysis: dict
    rca_runs: list[dict]
    metric_summaries: list[dict]
    config: dict
    provenance: dict
    limitations: list[str]

    @root_validator(pre=True)
    def no_labels(cls, values):
        assert_no_evaluation_keys(values)
        return values


def convert_signal_bundle(payload, source_sha256):
    # 跨项目只传版本化文件：先拒绝标签泄漏，再校验事件、窗口与内容指纹。
    report = SignalEnvelope.parse_obj(payload)
    if report.analysis.get("status") != "complete" or not report.incident_id:
        raise ValueError("signal bundle is not ready for RAG: " + str(report.analysis.get("status")))
    trigger = report.detection
    if trigger.get("status") not in {"detected", "supplied"}:
        raise ValueError("a detected or explicitly supplied event is required")
    center = trigger.get("timestamp_unix")
    if type(center) is not int or center < 0:
        raise ValueError("event timestamp must be a nonnegative integer")
    try:
        digest = report.telemetry["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid telemetry digest")
        expected_id = f"inc-{center}-" + fingerprint([report.system, center, digest])[:12]
        if expected_id != report.incident_id:
            raise ValueError("incident identity does not match telemetry/system/time")
        detection_enabled = report.config["detection"]["enabled"]
        if type(detection_enabled) is not bool:
            raise ValueError("detection.enabled must be boolean")
        expected_source = "anomaly_detector" if detection_enabled else report.config["trigger"]["source"]
        if trigger.get("source") != expected_source:
            raise ValueError("event source does not match configured detection mode")
        if detection_enabled != (trigger["status"] == "detected"):
            raise ValueError("event status does not match detection mode")
        if not report.config["rca"]["enabled"] and report.rca_runs:
            raise ValueError("disabled RCA cannot contain algorithm runs")
        if not report.config["summaries"]["enabled"] and report.metric_summaries:
            raise ValueError("disabled summaries cannot contain metric evidence")
        cutoff = report.analysis["evidence_cutoff_utc"]
        if parse_time(cutoff) != parse_time(report.analysis["observation_end_utc"]):
            raise ValueError("evidence cutoff must match observation end")
        if parse_time(report.analysis["observation_start_utc"]).timestamp() != center:
            raise ValueError("observation must start at detection")
        if parse_time(cutoff) > parse_time(report.telemetry["end_utc"]):
            raise ValueError("observation extends beyond available telemetry")
        minutes = max(report.config["window"]["reference_minutes"], report.config["window"]["observation_minutes"])
    except KeyError as exc:
        raise ValueError(f"incomplete signal bundle: {exc}") from exc
    # 耗时会随机器变化，不参与确定性 ID；其余字段都必须匹配签入时的指纹。
    stable = {k: v for k, v in payload.items() if k != "bundle_id"}
    stable["rca_runs"] = [{k: v for k, v in r.items() if k != "runtime_seconds"} for r in payload["rca_runs"]]
    if "signal-" + fingerprint(stable)[:20] != report.bundle_id:
        raise ValueError("signal bundle fingerprint mismatch")
    runs, metrics, services = [], [], []
    for run in report.rca_runs:
        safe = {k: v for k, v in run.items() if k != "preprocessing"}
        parsed = RunInput.parse_obj(safe)
        candidates = []
        for item in parsed.candidates:
            service, signal = split_metric(item.metric)
            candidates.append(RootCauseCandidate(item.rank, item.metric, service, signal, item.score))
            metrics.append(item.metric)
            if service:
                services.append(service)
        runs.append(RcaRun(parsed.method, candidates, parsed.runtime_seconds))
    incident = IncidentDocument(
        schema_version="1.1", incident_id=report.incident_id, system=report.system, lifecycle="active",
        detection=Detection(center, trigger["timestamp_utc"], trigger["source"], minutes, cutoff),
        observations=Observations(list(dict.fromkeys(services)), list(dict.fromkeys(metrics))), rca_runs=runs,
        retrieval_text=f"{report.system} event at {datetime.fromtimestamp(center, timezone.utc).isoformat()}",
        source_refs=[SourceRef("telemetry_bundle", "signal_workflow", source_sha256)],
        limitations=report.limitations).to_dict()
    IncidentInput.parse_obj(incident)
    summaries = []
    for raw in report.metric_summaries:
        summary = MetricSummary.from_dict(raw)
        if summary.source_incident_id != report.incident_id or summary.system != report.system:
            raise ValueError("metric summary belongs to a different incident/system")
        if parse_time(summary.window_start_utc).timestamp() != center or parse_time(summary.window_end_utc) != parse_time(cutoff):
            raise ValueError("metric summary window does not match completed analysis")
        prefix = f"telemetry:sha256:{digest}#column="
        if not summary.raw_ref.startswith(prefix) or unquote(summary.raw_ref[len(prefix):]) != summary.metric:
            raise ValueError("metric raw reference does not match telemetry digest/column")
        summaries.append(summary)
    return incident, summaries


def load_signal_bundle(path):
    raw = Path(path).read_bytes()
    return convert_signal_bundle(json.loads(raw), hashlib.sha256(raw).hexdigest())
