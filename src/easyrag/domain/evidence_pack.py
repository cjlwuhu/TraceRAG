"""HTTP/output contract for an auditable, bounded evidence package."""

from typing import Literal, Optional

from pydantic import Field, StrictBool, confloat, conint, constr, root_validator

from .experiment import ExperimentConfig, StrictModel
from .evidence import EvidenceItem


class EvidencePayload(StrictModel):
    schema_version: Literal["1.0"]
    evidence_id: str
    type: Literal["doc", "case", "metric", "log", "trace", "topology", "image"]
    content: str
    score: float
    rank: conint(strict=True, ge=1)
    retriever: str
    source: str
    raw_ref: str
    metadata: dict

    @root_validator(skip_on_failure=True)
    def checked(cls, values):
        EvidenceItem(**values).validate()
        return values


class Contribution(StrictModel):
    route: str
    rank: conint(strict=True, ge=1)
    raw_score: float
    rrf_contribution: confloat(gt=0)


class PackedEvidence(StrictModel):
    evidence: EvidencePayload
    pack_rank: conint(strict=True, ge=1)
    fusion_score: Optional[float] = None
    rerank_score: Optional[float] = None
    contributions: list[Contribution]


class DroppedEvidence(StrictModel):
    evidence_id: str
    reason: Literal["top_k", "context_budget", "rerank_candidate_budget"]


class RerankCandidate(StrictModel):
    evidence_id: str
    score: float


class RerankTrace(StrictModel):
    enabled: StrictBool = False
    model: Optional[Literal["gte-rerank-v2"]] = None
    candidates: list[RerankCandidate] = Field(default_factory=list)


class PackPayload(StrictModel):
    strategy: Literal["weighted_rrf", "round_robin"]
    items: list[PackedEvidence]
    context: str
    context_chars: conint(strict=True, ge=0)
    dropped: list[DroppedEvidence]
    reranking: RerankTrace = Field(default_factory=RerankTrace)

    @root_validator(skip_on_failure=True)
    def consistent_context(cls, values):
        expected = "\n\n".join(
            f"[{x.evidence.evidence_id}] type={x.evidence.type} source={x.evidence.source}\n{x.evidence.content}"
            for x in values["items"])
        if values["context"] != expected or values["context_chars"] != len(expected):
            raise ValueError("pack context must match its cited evidence exactly")
        ids = [x.evidence.evidence_id for x in values["items"]]
        if len(ids) != len(set(ids)):
            raise ValueError("pack must not contain duplicate evidence IDs")
        if [x.pack_rank for x in values["items"]] != list(range(1, len(ids) + 1)):
            raise ValueError("pack ranks must be consecutive")
        trace = values["reranking"]
        ranked = {x.evidence_id: x.score for x in trace.candidates}
        if len(ranked) != len(trace.candidates):
            raise ValueError("rerank candidate IDs must be unique")
        if trace.enabled:
            if not trace.model or any(x.evidence.evidence_id not in ranked or
                    x.rerank_score != ranked[x.evidence.evidence_id] for x in values["items"]):
                raise ValueError("reranked evidence must match its recorded candidate score")
            scores = [x.rerank_score for x in values["items"]]
            if scores != sorted(scores, reverse=True):
                raise ValueError("reranked items must be sorted by score")
        elif trace.model or ranked or any(x.rerank_score is not None for x in values["items"]):
            raise ValueError("disabled reranker must not contain model scores")
        return values


class EvidencePackRecord(StrictModel):
    schema_version: Literal["1.0"]
    pack_id: constr(regex=r"^pack-[a-f0-9]{16}$")
    run_id: constr(regex=r"^run-[a-f0-9]{32}$")
    created_at_utc: str
    elapsed_seconds: confloat(ge=0)
    query_context: dict
    experiment: dict
    routes: dict[str, list[EvidencePayload]]
    diagnostics: dict
    pack: PackPayload
    limitations: list[str]

    @root_validator(skip_on_failure=True)
    def bounded(cls, values):
        if "config" not in values["experiment"]:
            raise ValueError("experiment.config is required")
        config = ExperimentConfig.parse_obj(values["experiment"]["config"])
        if len(values["pack"].items) > config.pack.top_k or values["pack"].context_chars > config.pack.max_context_chars:
            raise ValueError("pack exceeds configured budget")
        trace = values["pack"].reranking
        if trace.enabled != config.reranker.enabled or len(trace.candidates) > config.reranker.candidate_top_k:
            raise ValueError("reranker trace must respect experiment config")
        route_ids = {x.evidence_id for ranked in values["routes"].values() for x in ranked}
        if any(x.evidence_id not in route_ids for x in trace.candidates):
            raise ValueError("rerank candidates must come from retrieved evidence")
        return values
