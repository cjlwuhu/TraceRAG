"""Isolated synthetic browser fixtures; no dependency on private historical outputs."""

import asyncio
import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
import yaml
from easyrag.console.server import create_app
from easyrag.console.store import Catalog, write_json
from easyrag.console.knowledge_registry import KnowledgeRegistry
from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.generation.work_order import WorkOrderGenerator
from run_operations import build_runner
from test_signal_bundle import fixture, signed


async def prepare_browser_fixture(root):
    """从已跟踪的教学数据生成临时工单，不复制本机历史实验或人工审核。"""
    config = yaml.safe_load((ROOT / "src/configs/easyrag.operations.windows.yaml").read_text(encoding="utf-8"))
    config["operations_output_dir"] = str(root / "outputs/operations")
    config_path = root / "src/configs/easyrag.operations.windows.yaml"
    write_json(config_path, config)
    shutil.copytree(ROOT / "examples/operations_knowledge", root / "examples/operations_knowledge")
    if (ROOT / "web/dist").is_dir():
        shutil.copytree(ROOT / "web/dist", root / "web/dist")
    docs = [d for d in load_manifest(root / "examples/operations_knowledge/manifest.jsonl")
            if d.knowledge_type in {"runbook", "case"}]
    write_manifest(docs, root / "browser-fixture/manifest.jsonl")
    KnowledgeRegistry(root).publish([root / "browser-fixture/manifest.jsonl"], reason="BROWSER TEST FIXTURE ONLY")
    bundle = fixture()
    bundle["config"]["rca"]["enabled"] = True
    bundle["rca_runs"] = [{"method": "fixture_observation_rank", "runtime_seconds": 0.0,
        "candidates": [{"rank": 1, "metric": "checkoutservice_latency"}]}]
    template = bundle["metric_summaries"][0]
    bundle["metric_summaries"] = []
    for i in range(10):
        summary = copy.deepcopy(template)
        summary.update(summary_id=f"metric-browser-fixture-{i}", metric=f"checkoutservice_latency{i}")
        summary["raw_ref"] = "telemetry:sha256:" + bundle["telemetry"]["sha256"] + f"#column=checkoutservice_latency{i}"
        bundle["metric_summaries"].append(summary)
    catalog = Catalog(root)
    imported = catalog.import_bundle(signed(bundle))
    incident, corpus, _ = catalog.query_corpus(imported["event_id"])
    runner = await build_runner(config_path, corpus, allow_cloud=False)
    pack = await runner.run("当前异常如何验证和处置", incident=incident)
    generator = WorkOrderGenerator(output_dir=catalog.orders)
    order = await generator.run(pack)
    source = catalog.orders / order["generation_run_id"]
    ids = ("gen-87c6bcfc46b44a338b2ba0f62a94aeed", "gen-a1928301846d4c5284cb1b85dd93d05c",
           "gen-29028755684a403ab86b9cb37958c07e")
    for index, ident in enumerate(ids):
        folder = catalog.orders / ident
        shutil.copytree(source, folder)
        stored = copy.deepcopy(order)
        stored["generation_run_id"] = ident
        if index == 2:
            stored["schema_version"] = "0.7"
        write_json(folder / "work-order.json", stored, replace=True)
    write_json(catalog.orders / ids[0] / "engineering-review.json", {
        "synthetic_fixture": True, "semantic_support": "not_fully_supported",
        "note": "自动化界面夹具，不是研究人工复核。"})


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tracerag-browser-test-") as temporary:
        root = Path(temporary)
        asyncio.run(prepare_browser_fixture(root))
        uvicorn.run(create_app(root,
                    rca_root=Path(os.environ.get("TRACERAG_TEST_RCA_ROOT", ROOT.parent / "RCA")),
                    rca_python=os.environ.get("TRACERAG_TEST_RCA_PYTHON")),
                    host="127.0.0.1", port=8766, access_log=False)
