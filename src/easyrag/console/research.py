"""Event evidence and explicit human research annotations; no automatic case promotion."""

import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from easyrag.adapters.knowledge_corpus import load_manifest, write_manifest
from easyrag.console.knowledge_registry import LOCK, digest
from easyrag.console.lifecycle import serialized
from easyrag.console.store import locate, now, read_json, write_json
from easyrag.domain.knowledge import HistoricalCase, KnowledgeDocument
from easyrag.retrieval.operations import eligible


def put_asset(root, raw):
    sha = hashlib.sha256(raw).hexdigest()
    path = Path(root) / "outputs/evidence_assets" / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if digest(path) != sha: raise ValueError("existing evidence asset changed")
    else:
        with path.open("xb") as stream: stream.write(raw)
    return sha


def asset_path(root, sha):
    if not re.fullmatch(r"[a-f0-9]{64}", sha): raise ValueError("invalid asset hash")
    path = Path(root) / "outputs/evidence_assets" / sha
    if digest(path) != sha: raise ValueError("evidence asset changed")
    return path


@serialized
def observations(catalog, event_id):
    catalog.event(event_id)
    docs = []
    for path in sorted((catalog.root / "outputs/event_evidence" / event_id).glob("batch-*/manifest.jsonl")):
        info = read_json(path.parent / "receipt.json")
        if digest(path) != info["manifest_sha256"]: raise ValueError("event evidence manifest changed")
        docs.extend(load_manifest(path))
    return docs


@serialized
def attach(catalog, event_id, value):
    if set(value) != {"documents", "assets"}: raise ValueError("expected documents and assets")
    if not isinstance(value["documents"], list) or not 1 <= len(value["documents"]) <= 100:
        raise ValueError("expected 1 to 100 observations")
    if not isinstance(value["assets"], list): raise ValueError("assets must be a list")
    incident, _ = catalog.event(event_id)
    context = {"current_incident_id": incident["incident_id"], "system": incident["system"], "detection": incident["detection"]}
    docs = [KnowledgeDocument.from_dict(d) for d in value["documents"]]
    incoming_assets = {}
    for item in value["assets"]:
        if set(item) != {"sha256", "base64"}: raise ValueError("invalid asset fields")
        raw = base64.b64decode(item["base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != item["sha256"]: raise ValueError("asset hash mismatch")
        incoming_assets[item["sha256"]] = raw
    with LOCK:
        known = {d.knowledge_id: d.to_dict() for d in observations(catalog, event_id)}
        for document in docs:
            if document.knowledge_type not in {"log", "trace", "topology", "image"}:
                raise ValueError("attachment must be a log, trace, topology or image observation")
            accepted, reason = eligible(document.metadata, document.knowledge_type, context)
            if not accepted: raise ValueError("ineligible observation: " + reason)
            if document.knowledge_id in known:
                raise ValueError("observation ID already exists; use a new revision ID")
            known[document.knowledge_id] = document.to_dict()
            sha = document.metadata["asset_sha256"]
            if sha not in incoming_assets: asset_path(catalog.root, sha)
        for raw in incoming_assets.values(): put_asset(catalog.root, raw)
        folder = catalog.root / "outputs/event_evidence" / event_id / ("batch-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        write_manifest(docs, folder / "manifest.jsonl")
        receipt = {"batch_id": folder.name, "event_id": event_id, "created_at_utc": now(),
                   "documents": len(docs), "manifest_sha256": digest(folder / "manifest.jsonl")}
        write_json(folder / "receipt.json", receipt)
        return receipt


@serialized
def admit_case(catalog, submission):
    if set(submission) != {"case", "outcome", "outcome_evidence", "attestation"}:
        raise ValueError("case, outcome, outcome_evidence and attestation are required")
    if submission["attestation"] is not True:
        raise ValueError("explicit human attestation required")
    for key in ("outcome", "outcome_evidence"):
        if not isinstance(submission[key], str) or not submission[key].strip():
            raise ValueError("actual outcome and supporting record are required")
    case = HistoricalCase.from_dict(submission["case"])
    document = case.to_knowledge_document()
    document = replace(document, content=document.content + "\n处置结果：" + submission["outcome"] +
        "\n处置结果依据：" + submission["outcome_evidence"])
    document.validate()
    checked = datetime.fromisoformat(case.verified_at.replace("Z", "+00:00"))
    if abs((datetime.now(timezone.utc) - checked).total_seconds()) > 600:
        raise ValueError("verified_at must be the actual current review time (within 10 minutes); do not backdate")
    with LOCK:
        description = catalog.knowledge.description()
        if not description["version"]: raise ValueError("register real runbooks before admitting cases")
        if any(d.knowledge_id == case.case_id for d in catalog.knowledge.documents(description["version"])):
            raise ValueError("case ID already exists; submit a new revision ID")
        folder = catalog.root / "knowledge/case_reviews" / ("review-" + uuid.uuid4().hex)
        folder.mkdir(parents=True)
        write_json(folder / "submission.json", {**submission, "received_at_utc": now(),
            "identity_assurance": "local single-user self-attestation; no external identity verification"})
        write_manifest([document], folder / "manifest.jsonl")
        result = catalog.knowledge.publish([
            catalog.knowledge.version_path(description["version"]) / "manifest.jsonl",
            folder / "manifest.jsonl"], reason="Explicit human case admission: " + case.case_id)
        return {"review_id": folder.name, "case_id": case.case_id, "knowledge_version": result["version"]}


@serialized
def annotate(catalog, generation_id, value):
    folder = locate(catalog.orders, generation_id, "gen")
    pack = read_json(folder / "evidence-pack.json")
    if set(value) != {"reviewer", "notes", "relevance"}: raise ValueError("invalid annotation fields")
    if not isinstance(value["reviewer"], str) or not value["reviewer"].strip(): raise ValueError("reviewer required")
    if not isinstance(value["notes"], str) or not value["notes"].strip(): raise ValueError("annotation rationale required")
    valid = {item["evidence"]["evidence_id"] for item in pack["pack"]["items"]}
    ratings = value["relevance"]
    if not isinstance(ratings, dict) or not ratings: raise ValueError("at least one judgment required")
    if not set(ratings) <= valid: raise ValueError("unknown evidence ID")
    if any(type(rating) is not int or rating not in (0, 1, 2) for rating in ratings.values()):
        raise ValueError("relevance must be 0 (irrelevant), 1 (related), 2 (directly useful)")
    ident = "annotation-" + uuid.uuid4().hex
    result = {"annotation_id": ident, "generation_run_id": generation_id, "created_at_utc": now(),
        "pack_sha256": digest(folder / "evidence-pack.json"), "pack_id": pack["pack_id"],
        "complete_pool": set(ratings) == valid, **value}
    write_json(catalog.root / "outputs/annotations" / (ident + ".json"), result)
    return result
