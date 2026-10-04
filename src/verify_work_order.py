"""Read-only source/citation/artifact verification, not a factuality evaluator."""

import argparse
import json
from pathlib import Path

from easyrag.domain.work_order import WorkOrderRecord, cited_statements
from easyrag.generation.work_order import validate_pack, render_markdown
from easyrag.retrieval.evidence_pack import fingerprint


def audit(folder):
    data = json.loads((folder / "work-order.json").read_text(encoding="utf-8"))
    source = json.loads((folder / "evidence-pack.json").read_text(encoding="utf-8"))
    result, pack = WorkOrderRecord.parse_obj(data), validate_pack(source)
    if result.source_pack_id != pack.pack_id or result.source_retrieval_run_id != pack.run_id:
        raise ValueError("Source pack identity mismatch")
    if result.incident_id != pack.query_context.get("current_incident_id") or result.system != pack.query_context.get("system"):
        raise ValueError("Incident identity mismatch")
    if [e.dict() for e in result.evidence] != [x.evidence.dict() for x in pack.pack.items]:
        raise ValueError("Work order evidence differs from the final pack")
    if result.provenance["input_record_sha256"] != fingerprint(source):
        raise ValueError("Source artifact fingerprint mismatch")
    if result.draft:
        # Hash stored fields, not defaults newly added by a later schema release.
        payload = {key: data[key] for key in ("source_pack_id", "config", "provenance", "draft")}
        if result.work_order_id != "wo-" + fingerprint(payload)[:16]:
            raise ValueError("Work order fingerprint mismatch")
    if result.config.mode == "cloud" and result.draft:
        messages = json.loads((folder / "prompt.json").read_text(encoding="utf-8"))
        if fingerprint(messages) != result.provenance["prompt_sha256"]:
            raise ValueError("Prompt fingerprint mismatch")
        context = json.loads(messages[1]["content"])
        if context["question"] != pack.query_context["question"] or context["system"] != pack.query_context.get("system"):
            raise ValueError("Prompt incident/question mismatch")
        expected_rca = pack.query_context.get("rca_candidates", []) if result.config.include_rca_candidates else []
        if context["rca_candidates"] != expected_rca:
            raise ValueError("Prompt RCA ablation mismatch")
        expected = [{"evidence_id": e.evidence_id, "type": e.type, "content": e.content,
                     "source": e.source, "metadata": e.metadata} for e in result.evidence]
        if context["evidence"] != expected:
            raise ValueError("Prompt contains different evidence")
    if (folder / "work-order.md").read_text(encoding="utf-8") != render_markdown(result):
        raise ValueError("Markdown does not match the structured draft")
    statements = list(cited_statements(result.draft)) if result.draft else []
    return {"generation_run_id": result.generation_run_id, "status": result.status,
            "work_order_id": result.work_order_id, "statements": len(statements),
            "citations": sum(len(x.citations) for x in statements),
            "final_evidence": len(result.evidence), "citation_integrity": "passed" if statements else "not_applicable",
            "semantic_support": result.semantic_support, "actions_executed": result.actions_executed,
            "case_promotion_in_this_tool": "not_supported"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.run_dir), ensure_ascii=False, indent=2))
