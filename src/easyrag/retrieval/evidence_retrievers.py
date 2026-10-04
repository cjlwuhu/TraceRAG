"""Typed BM25 retrievers that return a shared EvidenceItem contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from llama_index.core import QueryBundle
from llama_index.core.schema import BaseNode

from easyrag.custom.retrievers import BM25Retriever, tokenize_and_remove_stopwords
from easyrag.domain.evidence import EvidenceItem, RetrievalSnapshot
from easyrag.pipeline.ingestion import get_node_content


SAFE_METADATA_KEYS = frozenset(
    {
        "knowledge_id",
        "knowledge_type",
        "document_title",
        "source_incident_id",
        "system",
        "service",
        "metric",
        "direction",
        "window_start_utc",
        "window_end_utc",
        "human_verified",
        "verified_by",
        "verified_at",
        "file_path",
        "know_path",
        "asset_sha256",
        "derivation",
        "asset_media_type",
    }
)


class KnowledgeDomainRetriever:
    knowledge_type: str
    evidence_type: str

    def __init__(
        self,
        nodes: Iterable[BaseNode],
        *,
        tokenizer: Callable,
        similarity_top_k: int,
        stopwords: set[str],
        embed_type: int = 2,
        bm25_type: int = 0,
    ) -> None:
        if similarity_top_k < 1:
            raise ValueError("similarity_top_k must be positive")
        self.similarity_top_k = similarity_top_k
        self.tokenizer = tokenizer
        self.stopwords = stopwords
        self.embed_type = embed_type
        self.nodes = [
            node
            for node in nodes
            if node.metadata.get("knowledge_type") == self.knowledge_type
        ]
        self.retriever = None
        if self.nodes:
            self.retriever = BM25Retriever.from_defaults(
                nodes=self.nodes,
                tokenizer=tokenizer,
                similarity_top_k=len(self.nodes),
                stopwords=stopwords,
                embed_type=embed_type,
                bm25_type=bm25_type,
            )

    async def retrieve(
        self, query: str, *, current_incident_id: str | None = None
    ) -> list[EvidenceItem]:
        if not query.strip():
            raise ValueError("retrieval query must not be empty")
        if self.retriever is None:
            return []
        hits = await self.retriever.aretrieve(QueryBundle(query_str=query))
        query_tokens = self._lexical_tokens(query)
        filtered = [
            hit
            for hit in hits
            if self._has_lexical_overlap(hit, query_tokens)
            and not self._must_exclude(
                hit.node.metadata, current_incident_id=current_incident_id
            )
        ][: self.similarity_top_k]
        return [self._to_evidence(hit, rank) for rank, hit in enumerate(filtered, 1)]

    def _has_lexical_overlap(self, hit, query_tokens: set[str]) -> bool:
        if not query_tokens:
            return False
        node_tokens = self._lexical_tokens(
            get_node_content(hit, embed_type=self.embed_type)
        )
        return bool(query_tokens & node_tokens)

    def _lexical_tokens(self, text: str) -> set[str]:
        tokens = tokenize_and_remove_stopwords(
            self.tokenizer, text, stopwords=self.stopwords
        )
        return {
            token.casefold()
            for token in tokens
            if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", token)
        }

    def _must_exclude(
        self, metadata: dict[str, Any], *, current_incident_id: str | None
    ) -> bool:
        return False

    def _to_evidence(self, hit, rank: int) -> EvidenceItem:
        return node_to_evidence(hit, rank, self.evidence_type)


def node_to_evidence(hit, rank: int, evidence_type: str) -> EvidenceItem:
    """Shared pure conversion, independent of any index or query state."""
    metadata = {key: value for key, value in hit.node.metadata.items() if key in SAFE_METADATA_KEYS}
    content = hit.node.get_content().strip()
    knowledge_id = str(metadata.get("knowledge_id", "unknown"))
    digest = hashlib.sha256(f"{knowledge_id}\0{content}".encode("utf-8")).hexdigest()[:16]
    item = EvidenceItem(
        schema_version="1.0", evidence_id=f"ev-{evidence_type}-{digest}", type=evidence_type,
        content=content, score=float(hit.score or 0.0), rank=rank, retriever="bm25",
        source=str(hit.node.metadata.get("source", "unknown-source")),
        raw_ref=str(hit.node.metadata.get("raw_ref", "unknown-ref")), metadata=metadata,
    )
    item.validate()
    return item


class DocRetriever(KnowledgeDomainRetriever):
    knowledge_type = "runbook"
    evidence_type = "doc"


class CaseRetriever(KnowledgeDomainRetriever):
    knowledge_type = "case"
    evidence_type = "case"

    def _must_exclude(
        self, metadata: dict[str, Any], *, current_incident_id: str | None
    ) -> bool:
        return bool(
            current_incident_id
            and metadata.get("source_incident_id") == current_incident_id
        )


class MetricRetriever(KnowledgeDomainRetriever):
    knowledge_type = "metric"
    evidence_type = "metric"


class LogRetriever(KnowledgeDomainRetriever):
    knowledge_type = evidence_type = "log"


class TraceRetriever(KnowledgeDomainRetriever):
    knowledge_type = evidence_type = "trace"


class TopologyRetriever(KnowledgeDomainRetriever):
    knowledge_type = evidence_type = "topology"


class ImageRetriever(KnowledgeDomainRetriever):
    knowledge_type = evidence_type = "image"


class MultiSourceEvidenceRetriever:
    def __init__(
        self,
        doc_retriever: DocRetriever,
        case_retriever: CaseRetriever,
        metric_retriever: MetricRetriever,
        *,
        snapshot_dir: str | Path | None = None,
    ) -> None:
        self.retrievers = {
            "doc": doc_retriever,
            "case": case_retriever,
            "metric": metric_retriever,
        }
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None

    async def retrieve(
        self,
        query: str,
        *,
        current_incident_id: str | None = None,
        persist: bool = False,
    ) -> RetrievalSnapshot:
        lists = await asyncio.gather(
            *(
                retriever.retrieve(
                    query, current_incident_id=current_incident_id
                )
                for retriever in self.retrievers.values()
            )
        )
        results = dict(zip(self.retrievers, lists))
        retrieval_id = _retrieval_id(query, current_incident_id, results)
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        saved_to = None
        snapshot = RetrievalSnapshot(
            schema_version="1.0",
            retrieval_id=retrieval_id,
            created_at_utc=created_at,
            query=query.strip(),
            current_incident_id=current_incident_id or None,
            results=results,
        )
        if persist:
            if self.snapshot_dir is None:
                raise ValueError("snapshot_dir is required when persist=True")
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            output_path = self.snapshot_dir / f"{retrieval_id}.json"
            output_path.write_text(
                json.dumps(
                    snapshot.to_dict(include_saved_to=False),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            saved_to = str(output_path.resolve())
            snapshot = RetrievalSnapshot(
                schema_version=snapshot.schema_version,
                retrieval_id=snapshot.retrieval_id,
                created_at_utc=snapshot.created_at_utc,
                query=snapshot.query,
                current_incident_id=snapshot.current_incident_id,
                results=snapshot.results,
                saved_to=saved_to,
            )
        snapshot.validate()
        return snapshot


def build_evidence_retriever(
    nodes: Iterable[BaseNode],
    *,
    tokenizer: Callable,
    similarity_top_k: int,
    stopwords: set[str],
    embed_type: int = 2,
    bm25_type: int = 0,
    snapshot_dir: str | Path | None = None,
) -> MultiSourceEvidenceRetriever:
    shared = {
        "nodes": list(nodes),
        "tokenizer": tokenizer,
        "similarity_top_k": similarity_top_k,
        "stopwords": stopwords,
        "embed_type": embed_type,
        "bm25_type": bm25_type,
    }
    return MultiSourceEvidenceRetriever(
        DocRetriever(**shared),
        CaseRetriever(**shared),
        MetricRetriever(**shared),
        snapshot_dir=snapshot_dir,
    )


def _retrieval_id(
    query: str,
    current_incident_id: str | None,
    results: dict[str, list[EvidenceItem]],
) -> str:
    evidence_signature = "\0".join(
        f"{key}:{item.evidence_id}:{item.rank}:{item.score:.12g}"
        for key in ("doc", "case", "metric")
        for item in results[key]
    )
    seed = f"{query.strip()}\0{current_incident_id or ''}\0{evidence_signature}"
    return "ret-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
