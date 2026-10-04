"""Attach bounded observations and fingerprinted assets to an imported event."""
import argparse
import json
from pathlib import Path
from easyrag.adapters.knowledge_corpus import load_manifest
from easyrag.console.store import Catalog
from easyrag.console.research import put_asset, attach

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--asset", type=Path, action="append", default=[])
    args = parser.parse_args()
    catalog = Catalog(Path(__file__).resolve().parents[1])
    for path in args.asset: put_asset(catalog.root, path.read_bytes())
    print(json.dumps(attach(catalog, args.event_id, {"documents": [d.to_dict() for d in load_manifest(args.manifest)], "assets": []}), indent=2))
