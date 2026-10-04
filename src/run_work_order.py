"""Generate a draft from a saved pack or run retrieval first; cloud is explicit."""

import argparse
import asyncio
import json
from pathlib import Path

import yaml

from easyrag.generation.work_order import WorkOrderGenerator
from easyrag.retrieval.cloud_runtime import build_cloud_runtime
from easyrag.retrieval.cloud_models import CloudModelError
from run_operations import build_runner


SRC = Path(__file__).resolve().parent


async def run(args):
    settings = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.pack:
        if args.query or args.incident or args.corpus or args.retrieval_profile:
            raise ValueError("--pack cannot be combined with fresh retrieval arguments")
        record = json.loads(args.pack.read_text(encoding="utf-8"))
        models, _ = build_cloud_runtime(settings.get("cloud_services"), allow_cloud=args.cloud, base_dir=SRC)
    else:
        if not args.query or not args.incident:
            raise ValueError("Provide --pack, or --query and --incident for a new retrieval")
        runner = await build_runner(args.config, args.corpus, allow_cloud=args.cloud)
        incident = json.loads(args.incident.read_text(encoding="utf-8"))
        overrides = yaml.safe_load(args.retrieval_profile.read_text(encoding="utf-8")) if args.retrieval_profile else None
        record = await runner.run(args.query, incident=incident, overrides=overrides)
        models = runner.cloud_models
    config = settings.get("work_order", {})
    overrides = yaml.safe_load(args.generation_profile.read_text(encoding="utf-8")) if args.generation_profile else None
    output = args.output_dir or (SRC / settings.get("work_order_output_dir", "../outputs/work_orders"))
    generator = WorkOrderGenerator(config=config, models=models, output_dir=output)
    result = await generator.run(record, overrides=overrides)
    print(json.dumps({"generation_run_id": result["generation_run_id"], "status": result["status"],
                      "work_order_id": result["work_order_id"], "source_pack_id": result["source_pack_id"],
                      "citation_integrity": result["provenance"]["citation_integrity"],
                      "output_dir": str((output / result["generation_run_id"]).resolve()) if result["config"]["save_intermediates"] else None},
                     ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=SRC / "configs/easyrag.operations.windows.yaml")
    parser.add_argument("--pack", type=Path, help="Saved EvidencePack JSON; no retrieval is repeated")
    parser.add_argument("--incident", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--retrieval-profile", type=Path)
    parser.add_argument("--generation-profile", type=Path)
    parser.add_argument("--cloud", action="store_true", default=None, help="Allow billable cloud model calls")
    parser.add_argument("--output-dir", type=Path)
    try:
        asyncio.run(run(parser.parse_args()))
    except CloudModelError as exc:
        print(json.dumps({"status": "failed", "error": str(exc),
                          "generation_run_id": getattr(exc, "generation_run_id", None)}, ensure_ascii=False))
        raise SystemExit(1) from None
