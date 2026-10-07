"""Delete only owned console artifacts; retain immutable history and shared assets."""

from functools import wraps
import json
from pathlib import Path
import re
import stat
import uuid

from fastapi import HTTPException

from easyrag.adapters.knowledge_corpus import load_manifest
from easyrag.console.knowledge_registry import LOCK, digest
from easyrag.console.store import locate, now, read_json, write_json


def serialized(function):
    """Use on synchronous work only: never hold a thread lock across an await."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with LOCK:
            return function(*args, **kwargs)
    return wrapped


def locked_call(function, *args, **kwargs):
    with LOCK:
        return function(*args, **kwargs)


def _linked(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) &
                                   getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _asset_ids(value):
    found = set()
    if isinstance(value, dict):
        sha = value.get("asset_sha256")
        if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha): found.add(sha)
        for item in value.values(): found.update(_asset_ids(item))
    elif isinstance(value, list):
        for item in value: found.update(_asset_ids(item))
    elif isinstance(value, str):
        # Cases flatten evidence_refs into prose, and raw references may have
        # fragments or path prefixes. Match identities only; never follow paths.
        found.update(sha.lower() for sha in re.findall(
            r"(?<![A-Fa-f0-9])([A-Fa-f0-9]{64})(?![A-Fa-f0-9])", value))
    return found


def _retrieval_ids(value):
    found = set()
    if isinstance(value, dict):
        for item in value.values(): found.update(_retrieval_ids(item))
    elif isinstance(value, list):
        for item in value: found.update(_retrieval_ids(item))
    elif isinstance(value, str):
        found.update(re.findall(r"(?<![A-Za-z0-9])(run-[a-f0-9]{32})(?![a-f0-9])", value))
    return found


class Lifecycle:
    def __init__(self, catalog, queue):
        self.catalog, self.queue = catalog, queue
        # Deployment deliberately links these configured roots to persistent state.
        # Pin them once; reject changed links and every link below these roots.
        self.scopes = {catalog.root / name: (catalog.root / name).resolve()
                       for name in ("outputs", "knowledge")}

    def _idle(self):
        if self.queue.pending:
            raise HTTPException(409, {"code": "artifacts_in_use", "message": "任务正在排队或运行，暂时无法删除",
                "hint": "等待已有任务完成后重试。", "details": []})
        if any(configured.resolve() != pinned for configured, pinned in self.scopes.items()):
            raise ValueError("configured artifact root was redirected")

    def _path(self, path):
        path = Path(path).absolute()
        for configured, pinned in self.scopes.items():
            try: relative = path.relative_to(configured.absolute())
            except ValueError: continue
            if not relative.parts or ".." in relative.parts:
                raise ValueError("cannot delete an artifact root")
            expected = pinned / relative
            if configured.resolve() != pinned or path.resolve() != expected:
                raise ValueError("artifact path changed or escaped its owned root")
            for item in (expected, *expected.parents):
                if item == pinned: break
                if item.exists() or item.is_symlink():
                    if _linked(item): raise ValueError("linked artifact path is not deletable")
            return expected
        raise ValueError("artifact outside controlled roots")

    def _tree(self, path):
        """Preflight a whole target before removing any file, without following links."""
        actual = self._path(path)
        files, directories = [], []
        def visit(item):
            if _linked(item): raise ValueError("linked artifact is not deletable")
            info = item.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.append(item)
                for child in item.iterdir(): visit(child)
            elif stat.S_ISREG(info.st_mode): files.append(item)
            else: raise ValueError("unsupported artifact file type")
        if actual.exists(): visit(actual)
        return files, directories

    def _preflight(self, targets):
        files, directories = set(), set()
        for target in targets:
            owned_files, owned_dirs = self._tree(target)
            files.update(owned_files); directories.update(owned_dirs)
        return files, directories

    @staticmethod
    def _remove(preflight):
        files, directories = preflight
        count, reclaimed = 0, 0
        for path in sorted(files):
            info = path.stat()
            path.unlink()
            count += 1
            # An outside hard link still owns these bytes until its final unlink.
            if info.st_nlink <= 1: reclaimed += info.st_size
        for path in sorted(directories, key=lambda p: len(p.parts), reverse=True): path.rmdir()
        return {"deleted_files": count, "reclaimed_bytes": reclaimed}

    def _references(self, roots, *, excluded=(), matcher=_asset_ids):
        found, complete = set(), True
        excluded = [self._path(p) for p in excluded]
        for root in roots:
            try: files, _ = self._tree(root)
            except (ValueError, OSError):
                complete = False; continue
            for path in files:
                if any(path == target or target in path.parents for target in excluded): continue
                if path.suffix not in {".json", ".jsonl"}: continue
                try:
                    if path.suffix == ".jsonl":
                        values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                    else: values = [read_json(path)]
                    for value in values: found.update(matcher(value))
                except (ValueError, OSError, TypeError, RecursionError): complete = False
        return found, complete

    def _shared_reference_roots(self):
        root = self.catalog.root
        names = ("signal_imports", "event_evidence", "query_corpora", "work_orders", "operations", "retrieval",
                 "pipelines", "ablations", "multisource", "annotations", "research_reports", "case_review_queue",
                 "usage-rca-pipeline", "console/jobs", "console/reviews")
        return [root / "outputs" / name for name in names] + [root / "knowledge/versions", root / "knowledge/case_reviews"]

    def _asset_cleanup(self, targets):
        candidates, _ = self._references(targets)
        references, complete = self._references(self._shared_reference_roots(), excluded=targets)
        remove, retained = [], 0
        for sha in sorted(candidates):
            path = self.catalog.root / "outputs/evidence_assets" / sha
            if not path.exists(): continue
            if not complete or sha in references:
                retained += 1; continue
            # Validate content-addressed identity before taking ownership of removal.
            self._tree(path)
            if digest(path) != sha: raise ValueError("evidence asset changed")
            remove.append(path)
        return remove, retained

    def _jobs(self):
        self._tree(self.catalog.jobs)
        return [(path.parent, read_json(path)) for path in self.catalog.jobs.glob("job-*/job.json")]

    @serialized
    def delete_event(self, ident):
        self._idle()
        locate(self.catalog.imports, ident, "import")
        self._tree(self.catalog.imports / ident)
        incident, _ = self.catalog.event(ident)
        targets = [self.catalog.imports / ident, self.catalog.root / "outputs/event_evidence" / ident]
        for job_folder, job in self._jobs():
            if job.get("kind") == "telemetry" and job.get("result", {}).get("event_id") == ident:
                targets.extend(job_folder / name for name in ("telemetry.private.csv", "signals"))
        # Counting by incident is informational; never cascade by this non-unique ID.
        retained_orders = sum(read_json(path).get("incident_id") == incident["incident_id"]
                              for path in self.catalog.orders.glob("gen-*/work-order.json"))
        assets, retained_assets = self._asset_cleanup(targets)
        result = self._remove(self._preflight([*targets, *assets]))
        return {"deleted_id": ident, **result, "retained_orders": retained_orders,
                "retained_shared_assets": retained_assets}

    @serialized
    def delete_order(self, ident):
        self._idle()
        locate(self.catalog.orders, ident, "gen")
        self._tree(self.catalog.orders / ident)
        record = read_json(self.catalog.orders / ident / "work-order.json")
        targets = [self.catalog.orders / ident, self.catalog.reviews / ident]
        annotation_root = self.catalog.root / "outputs/annotations"
        self._tree(annotation_root)
        for path in annotation_root.glob("annotation-*.json"):
            if read_json(path).get("generation_run_id") == ident: targets.append(path)
        for folder, job in self._jobs():
            if job.get("status") == "completed" and job.get("result", {}).get("generation_run_id") == ident:
                targets.append(folder)
        run_id = record.get("source_retrieval_run_id")
        if isinstance(run_id, str) and re.fullmatch(r"run-[a-f0-9]{32}", run_id):
            path = self.catalog.root / "outputs/operations" / (run_id + ".json")
            references, complete = self._references(self._shared_reference_roots(),
                excluded=[*targets, path], matcher=_retrieval_ids)
            if complete and run_id not in references and path.exists() and read_json(path).get("run_id") == run_id:
                targets.append(path)
        assets, _ = self._asset_cleanup(targets)
        return {"deleted_id": ident, **self._remove(self._preflight([*targets, *assets]))}

    @serialized
    def cases(self):
        version = self.catalog.knowledge.description()["version"]
        if version is None: return {"knowledge_version": None, "items": []}
        items = []
        for doc in self.catalog.knowledge.documents(version):
            if doc.knowledge_type != "case": continue
            items.append({"case_id": doc.knowledge_id, "title": doc.title, "content": doc.content,
                **{key: doc.metadata.get(key) for key in ("system", "source_incident_id", "verified_by", "verified_at")},
                "identity_assurance": "local single-user self-attestation; no external identity verification"})
        return {"knowledge_version": version, "items": items}

    @serialized
    def delete_case(self, ident):
        self._idle()
        knowledge = self.catalog.knowledge
        previous = knowledge.description()["version"]
        if previous is None: raise FileNotFoundError("case")
        documents = knowledge.documents(previous)
        if not any(d.knowledge_type == "case" and d.knowledge_id == ident for d in documents):
            raise FileNotFoundError("case")
        targets = []
        reviews = self.catalog.root / "knowledge/case_reviews"
        self._tree(reviews)
        for path in reviews.glob("review-*/submission.json"):
            value = read_json(path)
            if value.get("case", {}).get("case_id") == ident:
                manifest = path.parent / "manifest.jsonl"
                if {d.knowledge_id for d in load_manifest(manifest)} == {ident}: targets.append(path.parent)
        preflight = self._preflight(targets)
        receipts = self.catalog.state / "deletions"
        self._tree(receipts)
        # No temporary duplicate manifest: publish the filtered documents directly.
        source = knowledge.version_path(previous) / "manifest.jsonl"
        result = knowledge.publish_documents([d for d in documents if d.knowledge_id != ident],
            reason="Explicit current case removal: " + ident,
            sources=[{"path": str(source.resolve()), "sha256": digest(source)}], excluded_knowledge_ids=[ident])
        deleted = self._remove(preflight)
        write_json(receipts / ("delete-" + uuid.uuid4().hex + ".json"), {
            "schema_version": "1.0", "kind": "current_case_removal", "case_id": ident,
            "created_at_utc": now(), "previous_knowledge_version": previous,
            "knowledge_version": result["version"], "excluded_knowledge_ids": [ident],
            "previous_manifest_sha256": digest(source), "manifest_sha256": result["manifest_sha256"],
            **deleted, "retained_audit_versions": True})
        return {"case_id": ident, "previous_knowledge_version": previous, "knowledge_version": result["version"],
                "reclaimed_bytes": deleted["reclaimed_bytes"], "retained_audit_versions": True}
