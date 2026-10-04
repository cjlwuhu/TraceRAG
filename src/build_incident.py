"""CLI for converting one incident's RCA outputs into IncidentDocument v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from easyrag.adapters.rca_result import build_incident_document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge RCA JSON files into a retrieval-safe IncidentDocument v1."
    )
    parser.add_argument("sources", nargs="+", type=Path, help="RCA result JSON files")
    parser.add_argument("--output", required=True, type=Path, help="output JSON path")
    parser.add_argument("--top-k", type=int, default=5, help="candidates kept per method")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    document = build_incident_document(args.sources, top_k=args.top_k)
    output = document.to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.output} ({len(output['rca_runs'])} RCA methods, "
        f"{len(output['observations']['candidate_metrics'])} unique candidates)."
    )


if __name__ == "__main__":
    main()
