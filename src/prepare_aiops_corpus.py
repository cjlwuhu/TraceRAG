"""Import the upstream AIOps text archive into a separate, traceable runbook corpus.

Reads ZIP members in memory: no shell extraction, OCR, model calls or questions
are admitted. Original ZEDX files are archived and verified, not reparsed here.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid
from urllib.parse import quote
import zipfile

from easyrag.adapters.knowledge_corpus import write_manifest
from easyrag.domain.knowledge import KnowledgeDocument


ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = {"director", "emsplus", "rcp", "umac"}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sources(source_dir, lock):
    for name, expected in lock["files"].items():
        path = source_dir / name
        if path.stat().st_size != expected["size"] or sha256_file(path) != expected["sha256"]:
            raise ValueError(f"source checksum/size mismatch (or unhydrated LFS pointer): {name}")


def decode_member_name(info):
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            name = name.encode("cp437").decode("gb18030")
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in name:
        raise ValueError("unsafe archive member")
    return path


def read_documents(archive_path, lock):
    documents, by_text, entries = [], {}, []
    counts = Counter()
    with zipfile.ZipFile(archive_path) as archive:
        for info in sorted(archive.infolist(), key=lambda i: i.filename):
            path = decode_member_name(info)
            if info.is_dir():
                continue
            if path.suffix.lower() != ".txt":
                counts["non_text_excluded"] += 1
                continue
            if len(path.parts) < 3 or path.parts[0] != "data" or path.parts[1] not in PRODUCTS:
                raise ValueError("text outside known product scope")
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError("oversized archive document")
            raw = archive.read(info)  # verifies ZIP CRC as well
            content = raw.decode("utf-8-sig").replace("\r\n", "\n").strip()
            counts["text_members"] += 1
            ref = f"archive:sha256:{lock['files']['data.zip']['sha256']}#member={quote(str(path), safe='/')}"
            entry = {"member": str(path), "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_ref": ref}
            if not content:
                counts["empty_excluded"] += 1
                entries.append({**entry, "status": "empty"})
                continue
            product = path.parts[1]
            digest = hashlib.sha256((product + "\n" + content).encode("utf-8")).hexdigest()
            if digest in by_text:
                counts["duplicate_excluded"] += 1
                entries.append({**entry, "status": "duplicate", "knowledge_id": by_text[digest]})
                continue
            title = next(line.strip("# ") for line in content.splitlines() if line.strip())[:240]
            ident = "runbook-aiops2024-" + digest[:24]
            document = KnowledgeDocument(
                schema_version="1.0", knowledge_id=ident, knowledge_type="runbook",
                title=f"{product} / {title}", content=content,
                source=lock["source_url"] + "@" + lock["revision"], raw_ref=ref,
                metadata={"system": lock["system"], "product": product,
                          "sha256": entry["raw_sha256"], "dataset_revision": lock["revision"]})
            document.validate()
            documents.append(document)
            by_text[digest] = ident
            entries.append({**entry, "status": "imported", "knowledge_id": ident})
    return sorted(documents, key=lambda d: d.knowledge_id), entries, dict(counts)


def prepare(source_dir, output_root, lock_path):
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    verify_sources(source_dir, lock)
    documents, entries, counts = read_documents(source_dir / "data.zip", lock)
    if not documents:
        raise ValueError("no nonempty runbooks")
    directory = output_root / ("corpus-" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    write_manifest(documents, directory / "corpus/manifest.jsonl")
    with (directory / "source-members.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for entry in entries:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    report = {"schema_version": "1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": lock, "archive_checksums_verified": True,
        "counts": {**counts, "documents": len(documents)},
        "products": dict(Counter(d.metadata["product"] for d in documents)),
        "scope": "text-only upstream parsed corpus; ZEDX preserved separately; no image/OCR reconstruction",
        "deduplication": "exact content after UTF-8 BOM/CRLF/edge-whitespace normalization, within each product",
        "excluded_inputs": ["question.jsonl", "LLM answers", "historical cases", "telemetry labels"],
        "system_filter": lock["system"],
        "console_default_changed": False,
        "manifest_sha256": sha256_file(directory / "corpus/manifest.jsonl"),
        "source_members_sha256": sha256_file(directory / "source-members.jsonl"),
        "importer_sha256": sha256_file(Path(__file__))}
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"directory": str(directory), "counts": report["counts"], "products": report["products"]}, ensure_ascii=False, indent=2))
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "data/external/aiops2024-challenge-dataset")
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/knowledge_builds/aiops2024")
    parser.add_argument("--source-lock", type=Path, default=ROOT / "src/configs/data_sources/aiops2024.json")
    args = parser.parse_args()
    prepare(args.source_dir, args.output_root, args.source_lock)
