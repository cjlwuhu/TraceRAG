"""Reproducible, retrieval-only experiments; independent of model initialization."""

import argparse
import asyncio
import json
from pathlib import Path

import jieba
import yaml

from easyrag.pipeline.ingestion import build_preprocess_pipeline, read_data
from easyrag.retrieval.operations import OperationsRunner
from easyrag.retrieval.cloud_runtime import build_cloud_runtime


async def build_runner(config_path: Path, corpus_path: Path | None = None, *, allow_cloud=None, cloud_runtime=None) -> OperationsRunner:
    """Load one fixed corpus/index configuration for CLI and batch experiments."""
    src_dir = Path(__file__).resolve().parent
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    corpus = corpus_path.resolve() if corpus_path else (src_dir / config["data_path"]).resolve()
    manifest = corpus / "manifest.jsonl"
    data = [] if manifest.is_file() and manifest.stat().st_size == 0 else read_data(str(corpus))
    pipeline = build_preprocess_pipeline(str(corpus), config["chunk_size"],
                                        config["chunk_overlap"], config.get("split_type", 0))
    nodes = await pipeline.arun(documents=data, num_workers=1) if data else []
    if config.get("split_type", 0) == 1:
        from easyrag.custom.hierarchical import get_leaf_nodes
        nodes = get_leaf_nodes(nodes)
    stopwords_path = config.get("stopwords_path")
    stopwords = set((src_dir / stopwords_path).read_text(encoding="utf-8").splitlines()) if stopwords_path else set()
    models, cache = cloud_runtime if cloud_runtime is not None else build_cloud_runtime(
        config.get("cloud_services"), allow_cloud=allow_cloud, base_dir=src_dir)
    return OperationsRunner(nodes, tokenizer=jieba.Tokenizer(), stopwords=stopwords,
                              config=config.get("operations", {}),
                              cloud_models=models, embedding_cache=cache,
                              output_dir=(src_dir / config.get("operations_output_dir", "../outputs/operations")))


async def run(args):
    runner = await build_runner(args.config, args.corpus, allow_cloud=args.cloud)
    incident = json.loads(args.incident.read_text(encoding="utf-8")) if args.incident else None
    overrides = yaml.safe_load(args.profile.read_text(encoding="utf-8")) if args.profile else None
    result = await runner.run(args.query, incident=incident, overrides=overrides)
    print(json.dumps({"run_id": result["run_id"], "pack_id": result["pack_id"],
                      "routes": {k: len(v) for k, v in result["routes"].items()},
                      "selected": len(result["pack"]["items"]),
                      "diagnostics": result["diagnostics"],
                      "limitations": result["limitations"]}, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent / "configs/easyrag.operations.windows.yaml")
    parser.add_argument("--incident", type=Path)
    parser.add_argument("--corpus", type=Path, help="Explicit corpus directory (CLI only)")
    parser.add_argument("--query", required=True)
    parser.add_argument("--profile", type=Path, help="YAML experiment overrides")
    parser.add_argument("--cloud", action="store_true", default=None, help="Allow billable cloud embedding/rerank; credentials from server config/environment")
    asyncio.run(run(parser.parse_args()))
