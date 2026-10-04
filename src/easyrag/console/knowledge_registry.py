"""Versioned long-lived knowledge, distinct from per-event/per-query snapshots."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest

LOCK = threading.RLock()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_bytes(encode(value)); temp.replace(path)


class KnowledgeRegistry:
    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "knowledge"
        self.active = self.home / "active.json"

    def description(self):
        with LOCK:
            if not self.active.exists():
                return {"version": None, "mode": "legacy_demo", "counts": {}, "systems": [],
                        "message": "尚未登记长期知识库；保留教学库兼容入口"}
            pointer = json.loads(self.active.read_text(encoding="utf-8"))
            folder = self.version_path(pointer["version"])
            info = json.loads((folder / "version.json").read_text(encoding="utf-8"))
            if digest(folder / "manifest.jsonl") != info["manifest_sha256"]:
                raise ValueError("registered knowledge manifest changed")
            return info

    def version_path(self, version):
        if not re.fullmatch(r"kb-[a-f0-9]{32}", version): raise ValueError("invalid knowledge version")
        path = (self.home / "versions" / version).resolve()
        if path.parent != (self.home / "versions").resolve(): raise ValueError("knowledge path outside registry")
        return path

    def documents(self, version=None):
        if version is None: version = self.description()["version"]
        if version is None:
            return load_manifest(self.root / "examples/operations_knowledge/manifest.jsonl")
        folder = self.version_path(version)
        info = json.loads((folder / "version.json").read_text(encoding="utf-8"))
        if digest(folder / "manifest.jsonl") != info["manifest_sha256"]: raise ValueError("knowledge hash mismatch")
        return load_manifest(folder / "manifest.jsonl")

    def publish(self, manifests, *, reason):
        with LOCK:
            known, sources = {}, []
            for manifest in manifests:
                manifest = Path(manifest)
                sources.append({"path": str(manifest.resolve()), "sha256": digest(manifest)})
                for document in load_manifest(manifest):
                    if document.knowledge_type not in {"runbook", "case"}:
                        raise ValueError("long-lived knowledge only admits runbooks and verified historical cases")
                    if document.knowledge_id in known and known[document.knowledge_id].to_dict() != document.to_dict():
                        raise ValueError("conflicting knowledge ID")
                    known[document.knowledge_id] = document
            documents = sorted(known.values(), key=lambda d: d.knowledge_id)
            content_hash = hashlib.sha256(encode([d.to_dict() for d in documents])).hexdigest()
            version = "kb-" + content_hash[:32]
            folder = self.version_path(version)
            if not folder.exists():
                folder.mkdir(parents=True)
                write_manifest(documents, folder / "manifest.jsonl")
                info = {"version": version, "mode": "registered", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "reason": reason, "counts": dict(Counter(d.knowledge_type for d in documents)),
                    "systems": sorted({d.metadata["system"] for d in documents if d.metadata.get("system")}),
                    "manifest_sha256": digest(folder / "manifest.jsonl"), "sources": sources}
                save(folder / "version.json", info)
            else:
                self.documents(version)  # never activate a modified on-disk version
            save(self.active, {"schema_version": "1.0", "version": version})
            return self.description()

    def snapshot(self, *, incident=None, event_documents=(), system=None, version=None):
        with LOCK:
            description = self.description()
            version = version or description["version"]
            system = incident["system"] if incident else system
            if not system: raise ValueError("document-only queries require a system scope")
            documents = [d for d in self.documents(version) if
                (not d.metadata.get("system") or d.metadata["system"] == system) and
                (incident is not None or d.knowledge_type == "runbook")]
            # Event observations never come from another event's manifest.
            documents += [d for d in event_documents if incident and d.knowledge_type not in {"runbook", "case"}
                          and d.metadata.get("source_incident_id") == incident["incident_id"]]
            known = {}
            for d in documents:
                if d.knowledge_id in known and known[d.knowledge_id].to_dict() != d.to_dict():
                    raise ValueError("conflicting snapshot knowledge ID")
                known[d.knowledge_id] = d
            documents = sorted(known.values(), key=lambda d: d.knowledge_id)
            signature = {"knowledge_version": version, "system": system,
                         "incident_id": incident["incident_id"] if incident else None,
                         "documents_sha256": hashlib.sha256(encode([d.to_dict() for d in documents])).hexdigest()}
            ident = "snapshot-" + hashlib.sha256(encode(signature)).hexdigest()[:32]
            folder = self.root / "outputs/query_corpora" / ident
            if not folder.exists():
                folder.mkdir(parents=True)
                if documents: write_manifest(documents, folder / "manifest.jsonl")
                else: (folder / "manifest.jsonl").write_bytes(b"")
                save(folder / "snapshot.json", {**signature, "snapshot_id": ident, "documents": len(documents),
                    "manifest_sha256": digest(folder / "manifest.jsonl")})
            info = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
            if digest(folder / "manifest.jsonl") != info["manifest_sha256"]: raise ValueError("query snapshot changed")
            return folder, info
