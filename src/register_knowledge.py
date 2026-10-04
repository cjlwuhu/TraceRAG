"""Publish explicitly selected corpus builds as one versioned knowledge library."""
import argparse
import json
from pathlib import Path
from easyrag.console.knowledge_registry import KnowledgeRegistry

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(KnowledgeRegistry(args.root).publish(args.manifest, reason=args.reason), ensure_ascii=False, indent=2))
