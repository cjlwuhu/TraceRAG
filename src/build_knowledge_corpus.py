"""CLI for building EasyRAG's versioned operations knowledge manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from easyrag.adapters.knowledge_corpus import build_knowledge_corpus, write_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize runbooks, verified cases, and metric summaries to JSONL."
    )
    parser.add_argument("--runbook", action="append", default=[], type=Path)
    parser.add_argument("--case", action="append", default=[], type=Path)
    parser.add_argument("--metric", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    documents = build_knowledge_corpus(
        runbook_paths=args.runbook,
        historical_case_paths=args.case,
        metric_summary_paths=args.metric,
        base_path=Path.cwd(),
    )
    write_manifest(documents, args.output)
    counts = {
        knowledge_type: sum(
            document.knowledge_type == knowledge_type for document in documents
        )
        for knowledge_type in ("runbook", "case", "metric")
    }
    print(
        f"Wrote {args.output} ({len(documents)} records: "
        f"runbook={counts['runbook']}, case={counts['case']}, metric={counts['metric']})."
    )


if __name__ == "__main__":
    main()
