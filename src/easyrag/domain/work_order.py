"""Draft work orders: citations are mandatory; no automatic resolution or case promotion."""

from typing import Literal, Optional

from pydantic import Field, StrictBool, confloat, conint, constr, root_validator

from easyrag.domain.experiment import StrictModel
from easyrag.domain.evidence_pack import EvidencePayload


Text = constr(strict=True, strip_whitespace=True, min_length=1, max_length=1600)
EvidenceId = constr(strict=True, regex=r"^ev-(doc|case|metric|log|trace|topology|image)-[a-f0-9]{16}$")


class GenerationConfig(StrictModel):
    enabled: StrictBool = True
    mode: Literal["extractive", "cloud"] = "extractive"
    model: constr(strict=True, max_length=80, regex=r"^(qwen-plus|qwen-flash|glm-[a-zA-Z0-9.-]+)$") = "qwen-plus"
    temperature: confloat(ge=0, le=1) = 0.0
    max_output_tokens: conint(strict=True, ge=512, le=8192) = 4096
    max_input_chars: conint(strict=True, ge=1000, le=100000) = 40000
    include_rca_candidates: StrictBool = True
    max_candidates: conint(strict=True, ge=1, le=8) = 3
    max_steps: conint(strict=True, ge=1, le=8) = 5
    repair_attempts: conint(strict=True, ge=0, le=1) = 0
    save_intermediates: StrictBool = True

    def with_overrides(self, overrides=None):
        return GenerationConfig.parse_obj({**self.dict(), **(overrides or {})})


class Citation(StrictModel):
    evidence_id: EvidenceId
    quote: constr(strict=True, strip_whitespace=True, min_length=8, max_length=1200)


class CitedStatement(StrictModel):
    text: Text
    citations: list[Citation] = Field(..., min_items=1, max_items=8)


class Hypothesis(StrictModel):
    statement: CitedStatement
    verification: Text


class ProposedAction(CitedStatement):
    preconditions: Text
    risks: list[Text] = Field(..., min_items=1, max_items=5)
    rollback: Text
    requires_approval: Literal[True] = True
    execution_status: Literal["not_executed"] = "not_executed"


class WorkOrderDraft(StrictModel):
    summary: CitedStatement
    root_cause_candidates: list[Hypothesis] = Field(..., max_items=8)
    # The current three-domain contract has no verified business/dependency scope.
    # Expand this with the future log/topology evidence contract, not free-text guesses.
    impact: None = None
    verification_steps: list[CitedStatement] = Field(..., min_items=1, max_items=8)
    proposed_actions: list[ProposedAction] = Field(..., max_items=8)
    missing_information: list[Text] = Field(..., min_items=1, max_items=12)
    limitations: list[Text] = Field(..., min_items=1, max_items=12)


def cited_statements(draft):
    yield draft.summary
    for candidate in draft.root_cause_candidates:
        yield candidate.statement
    if draft.impact:
        yield draft.impact
    yield from draft.verification_steps
    yield from draft.proposed_actions


def check_citations(draft, evidence):
    """Reference/quotation integrity, explicitly NOT semantic entailment checking."""
    selected = {e.evidence_id: e for e in evidence}
    for statement in cited_statements(draft):
        seen = set()
        for citation in statement.citations:
            if citation.evidence_id not in selected:
                raise ValueError("citation_not_in_final_pack")
            if citation.quote not in selected[citation.evidence_id].content:
                raise ValueError("citation_quote_not_in_source")
            if citation.evidence_id in seen:
                raise ValueError("duplicate_statement_citation")
            seen.add(citation.evidence_id)


class WorkOrderRecord(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    generation_run_id: constr(strict=True, regex=r"^gen-[a-f0-9]{32}$")
    work_order_id: Optional[constr(strict=True, regex=r"^wo-[a-f0-9]{16}$")]
    status: Literal["draft", "generation_disabled", "insufficient_evidence"]
    created_at_utc: str
    elapsed_seconds: confloat(ge=0)
    incident_id: Optional[str]
    system: Optional[str]
    source_pack_id: str
    source_retrieval_run_id: str
    config: GenerationConfig
    # These are server-owned constants; the model cannot set them.
    review_status: Literal["pending_human_review"] = "pending_human_review"
    root_cause_status: Literal["unconfirmed"] = "unconfirmed"
    actions_executed: Literal[False] = False
    semantic_support: Literal["not_automatically_verified"] = "not_automatically_verified"
    draft: Optional[WorkOrderDraft]
    evidence: list[EvidencePayload]
    provenance: dict
    limitations: list[str]

    @root_validator(skip_on_failure=True)
    def coherent_draft(cls, values):
        draft, config = values["draft"], values["config"]
        if values["status"] == "draft":
            if not draft or not values["work_order_id"] or not values["evidence"] or not config.enabled:
                raise ValueError("draft requires enabled generation, evidence and identity")
            if len(draft.root_cause_candidates) > config.max_candidates:
                raise ValueError("too many root cause candidates")
            if len(draft.verification_steps) > config.max_steps or len(draft.proposed_actions) > config.max_steps:
                raise ValueError("too many work order steps")
            check_citations(draft, values["evidence"])
        elif draft or values["work_order_id"]:
            raise ValueError("non-generated result must not contain a draft")
        if values["status"] == "generation_disabled" and config.enabled:
            raise ValueError("disabled status requires generation.enabled=false")
        if values["status"] == "insufficient_evidence" and (not config.enabled or values["evidence"]):
            raise ValueError("insufficient evidence status requires an empty pack")
        return values
