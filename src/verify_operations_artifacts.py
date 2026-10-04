"""Read-only artifact audit; schema/hash/filter checks, optionally cached cosine scores."""

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.retrieval.evidence_pack import fingerprint
from easyrag.retrieval.operations import eligible


def audit(manifest_path, vector_cache=None):
    root = manifest_path.resolve().parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    vectors = {}
    if vector_cache:
        with closing(sqlite3.connect(vector_cache.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            for dimension, blob, digest in connection.execute("SELECT dimension, vector, sha256 FROM vectors"):
                if len(blob) != dimension * 4 or hashlib.sha256(blob).hexdigest() != digest:
                    raise ValueError("Cache integrity check failed")
                vector = np.frombuffer(blob, dtype="<f4")
                if not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-5):
                    raise ValueError("Invalid normalized cache vector")
                vectors[digest] = vector
    grouped, seen = defaultdict(list), set()
    checked_scores = 0
    for row in manifest["results"]:
        path = (root / row["record"]).resolve()
        if root not in path.parents:
            raise ValueError("Run artifact must be within the manifest directory")
        record = json.loads(path.read_text(encoding="utf-8"))
        EvidencePackRecord.parse_obj(record)
        payload = {k: v for k, v in record.items() if k not in {"pack_id", "run_id", "created_at_utc", "elapsed_seconds"}}
        if record["pack_id"] != "pack-" + fingerprint(payload)[:16] or record["pack_id"] != row["pack_id"]:
            raise ValueError("Evidence pack fingerprint mismatch")
        if record["run_id"] != row["run_id"] or row["run_id"] in seen:
            raise ValueError("Missing or duplicate run identity")
        seen.add(row["run_id"])
        for key in ("corpus_sha256", "code_sha256"):
            if record["experiment"][key] != manifest[key]:
                raise ValueError("Manifest and run provenance differ")
        if fingerprint(record["experiment"]["config"]) != row["config_sha256"]:
            raise ValueError("Configuration fingerprint mismatch")
        provenance = record["experiment"].get("cloud", {}).get("embedding", {}).get("vectors", {})
        for route, items in record["routes"].items():
            for item in items:
                allowed, reason = eligible(item["metadata"], item["type"], record["query_context"])
                if not allowed:
                    raise ValueError("Ineligible retrieved evidence: " + reason)
                if vector_cache and route in provenance:
                    trace = provenance[route]
                    text = item["metadata"]["know_path"] if route.endswith(".path") else item["content"]
                    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    candidates = {d["text_sha256"]: d["vector_sha256"] for d in trace["documents"]}
                    score = float(vectors[candidates[text_hash]] @ vectors[trace["query_vector_sha256"]])
                    if not math.isclose(score, item["score"], abs_tol=1e-6, rel_tol=1e-6):
                        raise ValueError("Dense score differs from cached vector cosine")
                    checked_scores += 1
        grouped[row["profile"]].append(record["pack_id"])
    if (set(grouped) != set(manifest["profiles"]) or
            any(len(ids) != manifest["repeats"] for ids in grouped.values())):
        raise ValueError("Batch is incomplete")
    return {"runs_checked": len(seen), "dense_scores_checked": checked_scores,
            "profiles": {name: {"runs": len(ids), "distinct_pack_ids": len(set(ids))}
                         for name, ids in grouped.items()},
            "note": "Integrity/repeatability checks, not relevance or causality evaluation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--vector-cache", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.manifest, args.vector_cache), ensure_ascii=False, indent=2))
