"""Import RCA telemetry artifacts into a NEW incident-specific RAG corpus."""

import argparse
import json
from pathlib import Path
import uuid

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.adapters.signal_bundle import load_signal_bundle


def main(args):
    if args.base_manifest is None:
        from easyrag.console.store import Catalog
        catalog = Catalog(Path(__file__).resolve().parents[1])
        catalog.imports = args.output_dir
        result = catalog.import_bundle(json.loads(args.bundle.read_text(encoding="utf-8")))
        directory = args.output_dir / result["event_id"]
        print(json.dumps({**result, "incident": str(directory / "incident.json"),
            "corpus": str(directory / "corpus"), "knowledge_version": catalog.knowledge.description()["version"]}, indent=2))
        return
    incident, summaries = load_signal_bundle(args.bundle)
    documents = load_manifest(args.base_manifest)
    known = {d.knowledge_id: d for d in documents}
    for summary in summaries:
        record = summary.to_knowledge_document()
        if record.knowledge_id in known and record.to_dict() != known[record.knowledge_id].to_dict():
            raise ValueError("conflicting knowledge ID; base corpus was not changed")
        known[record.knowledge_id] = record
    directory = args.output_dir / ("import-" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    with (directory / "incident.json").open("x", encoding="utf-8") as stream:
        json.dump(incident, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    write_manifest(sorted(known.values(), key=lambda d: d.knowledge_id), directory / "corpus/manifest.jsonl")
    print(json.dumps({"incident": str(directory / "incident.json"), "corpus": str(directory / "corpus"),
                      "metric_summaries_added": len(summaries), "total_documents": len(known)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--base-manifest", type=Path, help="Explicit legacy override; default uses knowledge/active.json")
    parser.add_argument("--output-dir", type=Path, default=root / "outputs/signal_imports")
    main(parser.parse_args())
