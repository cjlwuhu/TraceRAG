"""Append-only artifacts and ID-only lookup (no user supplied filesystem paths)."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.adapters.signal_bundle import convert_signal_bundle
from easyrag.retrieval.query_context import IncidentInput
from easyrag.console.knowledge_registry import KnowledgeRegistry


_JSON_LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path):
    # Windows disallows replacing a file while another local thread holds it open.
    with _JSON_LOCK:
        return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value, *, replace=False):
    with _JSON_LOCK:
        _write_json(path, value, replace=replace)


def _write_json(path, value, *, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if replace:
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        temp.replace(path)
    else:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)


def locate(root, ident, prefix):
    if not re.fullmatch(re.escape(prefix) + r"-[a-f0-9]{32}", ident):
        raise ValueError("invalid artifact ID")
    root = Path(root).resolve()
    path = (root / ident).resolve()
    if path.parent != root:  # Also reject symlinks/junctions outside the catalog.
        raise ValueError("artifact outside catalog")
    if not path.is_dir():
        raise FileNotFoundError(ident)
    return path


class Catalog:
    def __init__(self, root):
        self.root = Path(root)
        self.imports = self.root / "outputs/signal_imports"
        self.orders = self.root / "outputs/work_orders"
        self.state = self.root / "outputs/console"
        self.jobs = self.state / "jobs"
        self.reviews = self.state / "reviews"
        self.knowledge = KnowledgeRegistry(self.root)

    def event(self, ident):
        folder = locate(self.imports, ident, "import")
        incident = read_json(folder / "incident.json")
        IncidentInput.parse_obj(incident)
        corpus = (folder / "corpus").resolve()
        if corpus.parent != folder or not (corpus / "manifest.jsonl").is_file():
            raise ValueError("invalid event corpus")
        return incident, corpus

    def events(self):
        items, skipped = [], 0
        for folder in sorted(self.imports.glob("import-*"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                incident, _ = self.event(folder.name)
                items.append({"id": folder.name, "incident_id": incident["incident_id"],
                    "system": incident["system"], "detection": incident["detection"],
                    "methods": [r["method"] for r in incident["rca_runs"]]})
            except (ValueError, OSError, TypeError, KeyError):
                skipped += 1
        return {"items": items, "skipped_invalid": skipped}

    def import_bundle(self, payload):
        # Hash exactly the normalized UTF-8/LF artifact that write_json persists,
        # not a different canonical serialization or the discarded upload filename.
        raw = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        incident, summaries = convert_signal_bundle(payload, hashlib.sha256(raw).hexdigest())
        base = self.knowledge.description()
        known = {d.knowledge_id: d for d in self.knowledge.documents(base["version"])
                 if not d.metadata.get("system") or d.metadata["system"] == incident["system"]}
        for summary in summaries:
            item = summary.to_knowledge_document()
            if item.knowledge_id in known and item.to_dict() != known[item.knowledge_id].to_dict():
                raise ValueError("conflicting knowledge ID")
            known[item.knowledge_id] = item
        ident = "import-" + uuid.uuid4().hex
        folder = self.imports / ident
        folder.mkdir(parents=True, exist_ok=False)
        write_json(folder / "signal-bundle.json", payload)
        (folder / "corpus").mkdir()
        if known:
            write_manifest(sorted(known.values(), key=lambda d: d.knowledge_id), folder / "corpus/manifest.jsonl")
        else:
            (folder / "corpus/manifest.jsonl").write_bytes(b"")
        write_json(folder / "knowledge-source.json", {"knowledge_version": base["version"], "mode": base["mode"]})
        # 最后发布事件文件：半途失败的导入不会成为网页可选事件。
        write_json(folder / "incident.json", incident)
        return {"event_id": ident, "incident_id": incident["incident_id"], "metric_summaries": len(summaries)}

    def query_corpus(self, event_id=None, *, mode="current", system=None):
        incident, corpus = self.event(event_id) if event_id else (None, None)
        if mode == "event_snapshot":
            if corpus is None: raise ValueError("event snapshot requires an event")
            return incident, corpus, {"mode": mode, "event_id": event_id}
        from easyrag.console.research import observations
        attachments = observations(self, event_id) if event_id else []
        if not self.knowledge.active.exists() and incident and not attachments:
            return incident, corpus, {"mode": "legacy_event_snapshot", "event_id": event_id}
        documents = load_manifest(corpus / "manifest.jsonl") if corpus and (corpus / "manifest.jsonl").stat().st_size else []
        documents += attachments
        snapshot, info = self.knowledge.snapshot(incident=incident, event_documents=documents, system=system)
        return incident, snapshot, info
