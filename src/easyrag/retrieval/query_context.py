"""Construct queries only from selected observed fields, never prebuilt text."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import Field, StrictStr, confloat, conint, constr, root_validator

from easyrag.domain.experiment import QueryOptions, StrictModel
from easyrag.domain.knowledge import assert_no_evaluation_keys


NonEmpty = constr(strict=True, strip_whitespace=True, min_length=1, max_length=500)


class DetectionInput(StrictModel):
    timestamp_unix: conint(strict=True, ge=0)
    timestamp_utc: NonEmpty
    source: Literal["benchmark_annotation", "anomaly_detector", "operator"]
    window_minutes: Optional[conint(strict=True, ge=1, le=10080)] = None
    evidence_cutoff_utc: Optional[NonEmpty] = None

    @root_validator(skip_on_failure=True)
    def consistent_timestamp(cls, values):
        parsed = parse_time(values["timestamp_utc"])
        if parsed.timestamp() != values["timestamp_unix"]:
            raise ValueError("detection UTC and Unix timestamps disagree")
        if values.get("evidence_cutoff_utc") and parse_time(values["evidence_cutoff_utc"]) < parsed:
            raise ValueError("evidence cutoff must not precede detection")
        return values


class CandidateInput(StrictModel):
    rank: conint(strict=True, ge=1)
    metric: NonEmpty
    service: Optional[NonEmpty] = None
    signal: Optional[NonEmpty] = None
    score: Optional[confloat()] = None


class RunInput(StrictModel):
    method: NonEmpty
    candidates: list[CandidateInput]
    runtime_seconds: Optional[confloat(ge=0)] = None

    @root_validator(skip_on_failure=True)
    def ranked(cls, values):
        if [c.rank for c in values["candidates"]] != list(range(1, len(values["candidates"]) + 1)):
            raise ValueError("RCA ranks must be consecutive")
        return values


class ObservationInput(StrictModel):
    suspected_services: list[NonEmpty]
    candidate_metrics: list[NonEmpty]


class SourceInput(StrictModel):
    source_type: Literal["rca_result", "telemetry_bundle"]
    method: NonEmpty
    sha256: constr(strict=True, regex=r"^[a-f0-9]{64}$")


class IncidentInput(StrictModel):
    schema_version: Literal["1.0", "1.1"]
    incident_id: constr(strict=True, regex=r"^inc-[0-9]{10}-[a-f0-9]{12}$")
    system: NonEmpty
    lifecycle: Literal["active", "resolved"]
    detection: DetectionInput
    observations: ObservationInput
    rca_runs: list[RunInput]
    retrieval_text: StrictStr
    source_refs: list[SourceInput] = Field(..., min_items=1)
    limitations: list[StrictStr]

    @root_validator(pre=True)
    def reject_labels(cls, values):
        assert_no_evaluation_keys(values)
        return values

    @root_validator(skip_on_failure=True)
    def unique_methods(cls, values):
        if values["schema_version"] == "1.0":
            if not values["rca_runs"] or any(r.source_type != "rca_result" for r in values["source_refs"]):
                raise ValueError("v1.0 requires RCA runs and RCA result sources")
            if values["detection"].evidence_cutoff_utc is not None:
                raise ValueError("evidence cutoff requires IncidentDocument v1.1")
        elif not values["detection"].evidence_cutoff_utc:
            raise ValueError("v1.1 requires an explicit evidence cutoff")
        methods = [r.method for r in values["rca_runs"]]
        if len(methods) != len(set(methods)):
            raise ValueError("duplicate RCA methods")
        return values


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def build_query_context(question: str, incident: IncidentInput | None, options: QueryOptions) -> dict:
    question = question.strip()
    if not question or len(question) > 10000:
        raise ValueError("question must contain 1..10000 characters")
    terms = []
    selected = []
    if incident and options.use_incident:
        terms.append(incident.system)
        if options.use_rca:
            for run in incident.rca_runs:
                for candidate in run.candidates[:options.rca_top_k]:
                    selected.append({"method": run.method, "rank": candidate.rank,
                                     "metric": candidate.metric, "service": candidate.service})
                    terms.append(candidate.metric)
                    if candidate.service:
                        terms.append(candidate.service)
    # observations in v1 were derived from RCA: never use them in a no-RCA run.
    # Ignore retrieval_text, source_refs, and arbitrary prose supplied in limitations.
    terms = list(dict.fromkeys(terms))
    return {"question": question, "query": "\n".join([question, " ".join(terms)]).strip(),
            "expansion_terms": terms, "rca_candidates": selected,
            "current_incident_id": incident.incident_id if incident else None,
            "system": incident.system if incident else None,
            "detection": incident.detection.dict(exclude_none=True) if incident else None}
