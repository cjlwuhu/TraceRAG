"""Adapters that translate external data into EasyRAG domain objects."""

from .knowledge_corpus import build_knowledge_corpus, load_manifest, write_manifest
from .rca_result import build_incident_document

__all__ = [
    "build_incident_document",
    "build_knowledge_corpus",
    "load_manifest",
    "write_manifest",
]
