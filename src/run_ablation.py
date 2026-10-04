"""Run controlled retrieval configurations; this is NOT a quality benchmark."""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import yaml

from run_operations import build_runner
from easyrag.retrieval.evidence_pack import fingerprint

SRC = Path(__file__).resolve().parent
OFFLINE_PROFILES = ("full", "no_rca", "question_only", "doc_only", "no_path", "no_fusion")
MULTISOURCE_PROFILES = ("multisource", "without_log", "without_trace", "without_topology", "without_image", "metrics_only")
PROFILES = (*OFFLINE_PROFILES, *MULTISOURCE_PROFILES, "dense", "hybrid", "hybrid_rerank", "hybrid_no_path", "bm25_rerank")


async def run_batch(runner, *, incident, question, profiles, output_dir, repeats=1):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if not profiles or len(profiles) != len(set(profiles)) or any(p not in PROFILES for p in profiles):
        raise ValueError("select distinct supported profiles")
    overrides = {name: ({} if name == "full" else yaml.safe_load(
        (SRC / "configs/experiments" / f"{name}.yaml").read_text(encoding="utf-8"))) for name in profiles}
    # Validate every profile before starting. Saving is always on for batch auditing.
    for name in overrides:
        overrides[name]["save_intermediates"] = True
        runner.defaults.with_overrides(overrides[name])
    batch_dir = Path(output_dir) / ("batch-" + uuid.uuid4().hex)
    batch_dir.mkdir(parents=True, exist_ok=False)
    previous_output = runner.output_dir
    runner.output_dir = batch_dir / "runs"
    manifest = {"schema_version": "1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "incident_sha256": fingerprint(incident), "question": question,
                "corpus_sha256": runner.corpus_sha256, "code_sha256": runner.code_sha256,
                "profiles": overrides, "repeats": repeats, "results": [],
                "limitations": ["Configuration smoke experiment, not a retrieval-quality evaluation.",
                                "No relevance judgments or ground-truth labels are used.",
                                "Elapsed time includes warm-up/cache effects; not a latency benchmark."]}
    try:
        for repeat in range(1, repeats + 1):
            for name in profiles:
                result = await runner.run(question, incident=incident, overrides=overrides[name])
                manifest["results"].append({"profile": name, "repeat": repeat,
                    "run_id": result["run_id"], "pack_id": result["pack_id"],
                    "record": "runs/" + result["run_id"] + ".json",
                    "config_sha256": result["experiment"]["config_sha256"],
                    "selected_by_type": dict(Counter(x["evidence"]["type"] for x in result["pack"]["items"])),
                    "evidence_ids": [x["evidence"]["evidence_id"] for x in result["pack"]["items"]],
                    "context_chars": result["pack"]["context_chars"],
                    "elapsed_seconds": result["elapsed_seconds"]})
        with (batch_dir / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    finally:
        runner.output_dir = previous_output
    return batch_dir, manifest


async def main(args):
    runner = await build_runner(args.config, args.corpus, allow_cloud=args.cloud)
    incident = json.loads(args.incident.read_text(encoding="utf-8"))
    path, result = await run_batch(runner, incident=incident, question=args.query,
                                   profiles=args.profiles, output_dir=args.output_dir, repeats=args.repeats)
    print(json.dumps({"manifest": str(path / "manifest.json"), "runs": len(result["results"]),
                      "results": result["results"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=SRC / "configs/easyrag.operations.windows.yaml")
    parser.add_argument("--incident", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, help="Explicit corpus directory (CLI only)")
    parser.add_argument("--query", required=True)
    parser.add_argument("--profiles", choices=PROFILES, nargs="+", default=list(OFFLINE_PROFILES))
    parser.add_argument("--cloud", action="store_true", default=None, help="Allow billable cloud calls for explicitly selected profiles")
    parser.add_argument("--output-dir", type=Path, default=SRC.parent / "outputs/ablations")
    parser.add_argument("--repeats", type=int, default=1)
    asyncio.run(main(parser.parse_args()))
