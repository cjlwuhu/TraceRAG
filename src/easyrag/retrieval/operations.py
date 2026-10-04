"""Configurable Incident -> eligible evidence -> EvidencePack experiment runner."""

import asyncio
import json
import re
import time
import uuid
import sys
import hashlib
from importlib.metadata import version
from datetime import datetime, timezone
from pathlib import Path

from llama_index.core.schema import NodeWithScore

from easyrag.custom.retrievers import BM25Retriever
from easyrag.domain.experiment import ExperimentConfig
from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.retrieval.evidence_pack import fingerprint, fuse_evidence
from easyrag.retrieval.evidence_retrievers import (
    CaseRetriever, DocRetriever, MetricRetriever, SAFE_METADATA_KEYS, node_to_evidence,
    LogRetriever, TraceRetriever, TopologyRetriever, ImageRetriever,
)
from easyrag.retrieval.query_context import IncidentInput, build_query_context, parse_time
from easyrag.retrieval.cloud_models import CloudBackendUnavailable


class LexicalTokenizer:
    """Use the same punctuation-free tokenizer at indexing and query time."""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def cut(self, text):
        return (word for word in self.tokenizer.cut(text.lower()) if re.search(r"[\w\u4e00-\u9fff]", word)
                and any(char.isalnum() for char in word))


def eligible(metadata: dict, source: str, context: dict) -> tuple[bool, str]:
    """These integrity constraints stay enabled in every ablation."""
    # 先按系统、事件和证据截止时间过滤，再建索引，避免不适用资料影响 IDF/Top-K。
    current_id, system, detection = (context[k] for k in ("current_incident_id", "system", "detection"))
    if system and metadata.get("system") and metadata["system"] != system:
        return False, "different_system"
    if system and source != "doc" and metadata.get("system") != system:
        return False, "different_system"
    if source == "case":
        if metadata.get("human_verified") is not True or not metadata.get("verified_by"):
            return False, "unverified_case"
        if current_id and metadata.get("source_incident_id") == current_id:
            return False, "self_case"
        try:
            verified = parse_time(metadata["verified_at"])
        except (KeyError, ValueError, TypeError):
            return False, "missing_verification_time"
        if detection and verified.timestamp() > detection["timestamp_unix"]:
            return False, "future_case"
    if source in {"metric", "log", "trace", "topology", "image"}:
        if not current_id or not detection:
            return False, "incident_required_for_metric"
        if metadata.get("source_incident_id") != current_id:
            return False, "different_incident"
        if metadata.get("system") != system:
            return False, "different_system"
        try:
            start, end = (parse_time(metadata[k]).timestamp() for k in ("window_start_utc", "window_end_utc"))
            if detection.get("evidence_cutoff_utc") and end > parse_time(detection["evidence_cutoff_utc"]).timestamp():
                return False, "after_evidence_cutoff"
            center = detection["timestamp_unix"]
            window = detection.get("window_minutes", 0) * 60
            intersects = (start < center + window and end > center - window) if window else (start <= center < end)
            if end <= start or not intersects:
                return False, "outside_window"
        except (KeyError, ValueError, TypeError):
            return False, "invalid_metric_window"
    return True, "eligible"


class OperationsRunner:
    def __init__(self, nodes, *, tokenizer, stopwords, config=None, output_dir=None,
                 cloud_models=None, embedding_cache=None):
        self.nodes = list(nodes)
        self.tokenizer = LexicalTokenizer(tokenizer)
        self.stopwords = set(stopwords)
        self.defaults = ExperimentConfig.parse_obj(config or {})
        self.output_dir = Path(output_dir) if output_dir else None
        self.cloud_models = cloud_models
        self.embedding_cache = embedding_cache
        portable = [{"content": n.text, "metadata": {k: v for k, v in n.metadata.items()
                     if k in SAFE_METADATA_KEYS or k in {"source", "raw_ref"}}} for n in self.nodes]
        self.corpus_sha256 = fingerprint(sorted(portable, key=fingerprint))
        self.index_settings = {"bm25_idf": "log1p", "k1": 1.5, "b": 0.75,
                               "tokenizer": "jieba_lexical_v1", "stopwords_sha256": fingerprint(sorted(stopwords))}
        package_root = Path(__file__).resolve().parents[1]
        modules = ["retrieval/operations.py", "retrieval/query_context.py", "retrieval/evidence_pack.py",
                   "retrieval/evidence_retrievers.py", "domain/experiment.py", "domain/evidence_pack.py",
                   "domain/knowledge.py", "domain/evidence.py", "custom/retrievers.py", "pipeline/ingestion.py",
                   "custom/splitter.py", "custom/transformation.py", "custom/hierarchical.py",
                   "retrieval/cloud_models.py", "retrieval/cloud_runtime.py", "retrieval/vector_cache.py"]
        self.code_sha256 = fingerprint({p: (package_root / p).read_text(encoding="utf-8") for p in modules})
        self.versions = {name: version(name) for name in ("jieba", "rank-bm25", "llama-index-core", "pydantic", "numpy", "httpx")}
        self.versions["python"] = sys.version.split()[0]

    def _retrieve_routes(self, context, config):
        routes, diagnostics = {}, {}
        vector_provenance = {}
        query_vector = None
        classes = {"doc": DocRetriever, "case": CaseRetriever, "metric": MetricRetriever,
                   "log": LogRetriever, "trace": TraceRetriever, "topology": TopologyRetriever, "image": ImageRetriever}
        for source, cls in classes.items():
            reasons = {}
            nodes = []
            enabled = getattr(config.sources, source)
            for node in self.nodes:
                if node.metadata.get("knowledge_type") != cls.knowledge_type:
                    continue
                allowed, reason = eligible(node.metadata, source, context)
                if not enabled:
                    reason, allowed = "disabled", False
                reasons[reason] = reasons.get(reason, 0) + 1
                if allowed:
                    nodes.append(node)
            diagnostics[source] = {"enabled": enabled, "node_counts": reasons}
            # 不同子库的 BM25 分数不可直接比较；后续融合使用各路名次。
            fields = {"body": 0, **({"path": 5} if config.retrieval.path_enabled else {})}
            if not enabled:
                continue
            for field, embed_type in fields.items():
                algorithms = (["bm25"] if config.retrieval.mode == "bm25" else
                              ["dense"] if config.retrieval.mode == "dense" else ["bm25", "dense"])
                for algorithm in algorithms:
                    route = f"{source}.{field}" if algorithm == "bm25" else f"{source}.dense.{field}"
                    routes[route] = []
                    def field_text(n):
                        return n.text if field == "body" else n.metadata.get("know_path", "")
                    field_nodes = [n for n in nodes if (any(word not in self.stopwords for word in
                        self.tokenizer.cut(field_text(n))) if algorithm == "bm25" else bool(field_text(n).strip()))]
                    field_nodes.sort(key=lambda n: (n.metadata["knowledge_id"], n.text))
                    if not field_nodes:
                        continue
                    if algorithm == "bm25":
                        retriever = BM25Retriever.from_defaults(
                            nodes=field_nodes, tokenizer=self.tokenizer, similarity_top_k=len(field_nodes),
                            stopwords=self.stopwords, embed_type=embed_type, bm25_type=2)
                        scores = retriever.get_scores(context["query"])
                    else:
                        options = dict(model=config.embedding.model, dimension=config.embedding.dimension)
                        if query_vector is None:
                            query_vector, _ = self.embedding_cache.encode([context["query"]], text_type="query", **options)
                        vectors, _ = self.embedding_cache.encode([field_text(n) for n in field_nodes],
                                                                 text_type="document", **options)
                        scores = vectors @ query_vector[0]
                        vector_provenance[route] = {
                            "query_vector_sha256": hashlib.sha256(query_vector.astype("<f4").tobytes()).hexdigest(),
                            "documents": [{"text_sha256": hashlib.sha256(field_text(n).encode("utf-8")).hexdigest(),
                                           "vector_sha256": hashlib.sha256(v.astype("<f4").tobytes()).hexdigest()}
                                          for n, v in zip(field_nodes, vectors)]}
                    # Cosine can legitimately be <= 0; don't reuse BM25's positive-match gate.
                    hits = [NodeWithScore(node=n, score=float(s)) for n, s in zip(field_nodes, scores)
                            if algorithm == "dense" or s > 0]
                    hits.sort(key=lambda h: (-h.score, h.node.metadata["knowledge_id"], h.node.text))
                    seen = set()
                    for hit in hits:
                        evidence = node_to_evidence(hit, len(routes[route]) + 1, source).to_dict()
                        if evidence["evidence_id"] in seen:
                            continue
                        seen.add(evidence["evidence_id"])
                        evidence["retriever"] = (f"bm25_log1p_{field}" if algorithm == "bm25" else
                                                 f"dense_cosine_{config.embedding.model}_{field}")
                        routes[route].append(evidence)
                        if len(routes[route]) >= config.retrieval.per_route_top_k:
                            break
        return routes, diagnostics, vector_provenance

    async def run(self, question, *, incident=None, overrides=None):
        started = time.perf_counter()
        config = self.defaults.with_overrides(overrides)
        if config.embedding.enabled and self.embedding_cache is None:
            raise CloudBackendUnavailable("Dense backend is disabled on this server; enable cloud_services explicitly")
        if config.reranker.enabled and self.cloud_models is None:
            raise CloudBackendUnavailable("Reranker backend is disabled on this server; enable cloud_services explicitly")
        parsed = IncidentInput.parse_obj(incident) if incident is not None else None
        context = build_query_context(question, parsed, config.query)
        routes, diagnostics, vector_provenance = await asyncio.to_thread(self._retrieve_routes, context, config)
        rerank = (lambda documents: self.cloud_models.rerank(context["query"], documents,
                   model=config.reranker.model)) if config.reranker.enabled else None
        pack = await asyncio.to_thread(fuse_evidence, routes, config, rerank=rerank)
        limitations = ["Evidence relevance is not causal confirmation."]
        if parsed and parsed.detection.source == "benchmark_annotation":
            limitations.append("Detection time is supplied by the benchmark, not an online detector.")
        if parsed and parsed.detection.evidence_cutoff_utc:
            cutoff = parsed.detection.evidence_cutoff_utc
            limitations.append(f"Evidence is bounded by {cutoff}; post-alarm observations require collection delay.")
        if parsed and not parsed.rca_runs:
            limitations.append("No RCA algorithm output is available for this incident.")
        for source, info in diagnostics.items():
            if info["enabled"] and not any(items for route, items in routes.items() if route.startswith(source + ".")):
                limitations.append(f"No eligible matching {source} evidence was retrieved.")
        if any(item["evidence"]["source"].startswith("synthetic") for item in pack["items"]):
            limitations.append("Pack contains synthetic learning fixtures; not empirical validation.")
        if config.embedding.enabled or config.reranker.enabled:
            limitations.append("Cloud model aliases may change; cached vectors and recorded scores identify this run, not a frozen provider checkpoint.")
        experiment = {"config": config.dict(), "corpus_sha256": self.corpus_sha256,
                      "index": self.index_settings, "implementation": "operations-v1",
                      "code_sha256": self.code_sha256, "versions": self.versions}
        experiment["config_sha256"] = fingerprint(config.dict())
        experiment["cloud"] = {
            "api_host": (self.cloud_models or self.embedding_cache.models).api_host
                        if config.embedding.enabled or config.reranker.enabled else None,
            "embedding": {"enabled": config.embedding.enabled,
                          "normalization": "l2_float64_to_float32", "search": "exact_cosine",
                          "vectors": vector_provenance},
            "reranker": {"enabled": config.reranker.enabled,
                         "scores_sha256": fingerprint(pack["reranking"]) if config.reranker.enabled else None}}
        record = {"schema_version": "1.0", "query_context": context, "experiment": experiment,
                  "routes": routes, "diagnostics": diagnostics, "pack": pack, "limitations": limitations}
        record["pack_id"] = "pack-" + fingerprint(record)[:16]
        record["run_id"] = "run-" + uuid.uuid4().hex
        record["created_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record["elapsed_seconds"] = time.perf_counter() - started
        EvidencePackRecord.parse_obj(record)
        if config.save_intermediates:
            if self.output_dir is None:
                raise ValueError("output_dir required when saving intermediates")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            # Unique, exclusive creation preserves repeated runs of the same experiment.
            with (self.output_dir / (record["run_id"] + ".json")).open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        return record
