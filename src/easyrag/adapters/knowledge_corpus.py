"""Build a versioned JSONL knowledge corpus from operations sources."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from easyrag.domain.knowledge import HistoricalCase, KnowledgeDocument, MetricSummary


def build_knowledge_corpus(
    *,
    runbook_paths: Iterable[str | Path] = (),
    historical_case_paths: Iterable[str | Path] = (),
    metric_summary_paths: Iterable[str | Path] = (),
    base_path: str | Path | None = None,
) -> list[KnowledgeDocument]:
    """Normalize three knowledge domains into one deterministic manifest."""
    base = Path(base_path or Path.cwd()).resolve()
    documents: list[KnowledgeDocument] = []
    for path in _expand_paths(runbook_paths, "*.txt"):
        documents.append(_load_runbook(path, base))
    for path in _expand_paths(historical_case_paths, "*.json"):
        documents.append(_load_historical_case(path))
    for path in _expand_paths(metric_summary_paths, "*.json"):
        documents.append(_load_metric_summary(path))
    if not documents:
        raise ValueError("no runbook, historical case, or metric summary sources found")

    ids: set[str] = set()
    for document in documents:
        document.validate()
        if document.knowledge_id in ids:
            raise ValueError(f"duplicate knowledge_id: {document.knowledge_id}")
        ids.add(document.knowledge_id)
    return sorted(documents, key=lambda item: (item.knowledge_type, item.knowledge_id))


def write_manifest(documents: Iterable[KnowledgeDocument], output_path: str | Path) -> None:
    """Write UTF-8 JSONL atomically enough for a local build command."""
    output = Path(output_path)
    records = list(documents)
    if not records:
        raise ValueError("cannot write an empty knowledge manifest")
    output.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        json.dumps(document.to_dict(), ensure_ascii=False, sort_keys=True)
        for document in records
    )
    output.write_text(text + "\n", encoding="utf-8")


def load_manifest(path: str | Path) -> list[KnowledgeDocument]:
    """Load and validate a KnowledgeDocument JSONL manifest."""
    manifest = Path(path)
    documents: list[KnowledgeDocument] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{manifest}:{line_number}: invalid JSONL; each nonempty line must contain one complete JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{manifest}:{line_number}: record must be an object")
        document = KnowledgeDocument.from_dict(payload)
        if document.knowledge_id in seen_ids:
            raise ValueError(
                f"{manifest}:{line_number}: duplicate knowledge_id {document.knowledge_id}"
            )
        seen_ids.add(document.knowledge_id)
        documents.append(document)
    if not documents:
        raise ValueError(f"knowledge manifest is empty: {manifest}")
    return documents


def _load_runbook(path: Path, base: Path) -> KnowledgeDocument:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: runbook must be UTF-8") from exc
    if not text:
        raise ValueError(f"{path}: runbook is empty")
    title = next((line.strip("# ") for line in text.splitlines() if line.strip()), path.stem)
    digest = hashlib.sha256(raw).hexdigest()
    return KnowledgeDocument(
        schema_version="1.0",
        knowledge_id=f"runbook-{digest[:16]}",
        knowledge_type="runbook",
        title=title,
        content=text,
        source=path.name,
        raw_ref=_portable_file_ref(path, base),
        metadata={"sha256": digest},
    )


def _load_historical_case(path: Path) -> KnowledgeDocument:
    return HistoricalCase.from_dict(_read_json_object(path)).to_knowledge_document()


def _load_metric_summary(path: Path) -> KnowledgeDocument:
    return MetricSummary.from_dict(_read_json_object(path)).to_knowledge_document()


def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: source must be a JSON object")
    return payload


def _expand_paths(paths: Iterable[str | Path], pattern: str) -> list[Path]:
    expanded: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            expanded.append(path.resolve())
        elif path.is_dir():
            expanded.extend(item.resolve() for item in path.rglob(pattern) if item.is_file())
        else:
            raise FileNotFoundError(f"knowledge source does not exist: {path}")
    return sorted(set(expanded), key=lambda item: str(item).lower())


def _portable_file_ref(path: Path, base: Path) -> str:
    try:
        relative = os.path.relpath(path.resolve(), base)
    except ValueError:
        relative = str(path.resolve())
    return "file:" + relative.replace(os.sep, "/")
