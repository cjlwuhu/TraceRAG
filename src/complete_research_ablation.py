"""Audit a frozen retrieval batch and produce matching offline work-order drafts."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import uuid
from easyrag.console.store import read_json, write_json
from easyrag.console.knowledge_registry import digest
from easyrag.domain.work_order import cited_statements, WorkOrderRecord
from easyrag.generation.work_order import WorkOrderGenerator
from verify_operations_artifacts import audit as audit_packs
from verify_work_order import audit as audit_order

ROOT = Path(__file__).resolve().parents[1]


async def main(manifest):
    checked = audit_packs(manifest)
    batch = read_json(manifest)
    out = ROOT / "outputs/research_reports" / ("ablation-" + uuid.uuid4().hex)
    out.mkdir(parents=True)
    generator = WorkOrderGenerator(output_dir=ROOT / "outputs/work_orders")
    rows = []
    for item in batch["results"]:
        pack = read_json(manifest.parent / item["record"])
        record = await generator.run(pack, overrides={"mode":"extractive", "enabled":True,
            "save_intermediates":True, "include_rca_candidates":item["profile"] not in {"no_rca", "question_only"}})
        folder = ROOT / "outputs/work_orders" / record["generation_run_id"]
        rows.append({"profile":item["profile"], "repeat":item["repeat"], **audit_order(folder),
            "work_order_sha256":digest(folder / "work-order.json"), "selected_by_type":item["selected_by_type"]})
        if item["profile"] == "multisource" and item["repeat"] == 1:
            pending = {"status":"pending_human_annotation", "generation_run_id":record["generation_run_id"],
                "pack_sha256":digest(folder / "evidence-pack.json"), "reviewer":"", "notes":"",
                "relevance":{x["evidence"]["evidence_id"]:None for x in pack["pack"]["items"]},
                "claim_support":[{"claim_index":i, "text":s.text, "support":None} for i,s in enumerate(cited_statements(WorkOrderRecord.parse_obj(record).draft))],
                "instructions":"Assess relevance 0/1/2 and claim support independently; never treat citations as semantic proof. Submit through console; this template is not a completed annotation."}
            write_json(out / "annotation.pending.json", pending)
    report = {"retrieval_manifest":str(manifest.resolve()), "retrieval_manifest_sha256":digest(manifest),
        "retrieval_audit":checked, "generation":rows,
        "quality_metrics":{"retrieval_precision":None,"claim_support_rate":None,"reason":"No real human judgments supplied"},
        "limitations":["One event, nine configurations, two repeats; not a representative multimodal benchmark",
            "full/no_rca/doc_only use the earlier 6-item budget; multimodal variants and metrics_only share 20 items / 40000 characters",
            "Only comparisons among multisource and without_* isolate a single modality; other profiles differ in budgets",
            "No verified historical cases available; case benefit is unmeasured"]}
    write_json(out / "report.json", report)
    print(json.dumps({"report":str(out / "report.json"), "runs":len(rows),"pending":str(out / "annotation.pending.json")},indent=2))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--manifest",type=Path,required=True)
    asyncio.run(main(parser.parse_args().manifest))
