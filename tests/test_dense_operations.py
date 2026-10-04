"""Deterministic local vectors test orchestration, not cloud model quality."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jieba
import numpy as np
from pydantic import ValidationError

from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.domain.experiment import ExperimentConfig
from easyrag.retrieval.cloud_models import CloudBackendUnavailable, CloudModelError
from easyrag.retrieval.cloud_runtime import build_cloud_runtime
from easyrag.retrieval.operations import OperationsRunner
from easyrag.retrieval.vector_cache import CachedEmbeddings
from test_operations import node


class FakeModels:
    api_host = "https://dashscope.aliyuncs.com"

    def __init__(self):
        self.embeds, self.reranks = [], []

    def embed(self, texts, *, model, dimension, text_type):
        self.embeds.append((list(texts), text_type))
        rows = []
        for text in texts:
            digest = hashlib.sha256((text_type + text).encode()).digest()
            vector = np.resize(np.frombuffer(digest, dtype=np.uint8).astype(np.float64) - 127, dimension)
            rows.append((vector / np.linalg.norm(vector)).astype(np.float32))
        return np.stack(rows)

    def rerank(self, query, documents, *, model):
        self.reranks.append((query, list(documents)))
        return [float(i) for i in range(len(documents))] # reverse candidate order deliberately


class DenseOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokenizer = jieba.Tokenizer()
        cls.incident = json.loads((Path(__file__).resolve().parents[1] /
            "examples/incidents/checkoutservice-incident.v1.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.models = FakeModels()
        self.cache = CachedEmbeddings(self.models, Path(self.directory.name) / "vectors.sqlite")
        self.nodes = [node("runbook", "a", "checkoutservice latency connection"),
                      node("runbook", "b", "checkoutservice timeout"),
                      node("runbook", "c", "磁盘清理")]
        self.hybrid = {"retrieval": {"mode": "hybrid"}, "embedding": {"enabled": True, "dimension": 64}}

    def runner(self, nodes=None):
        return OperationsRunner(self.nodes if nodes is None else nodes, tokenizer=self.tokenizer,
                                stopwords=set(), cloud_models=self.models, embedding_cache=self.cache)

    def test_bm25_and_empty_sources_make_no_cloud_calls(self):
        runner = self.runner()
        with patch.object(self.models, "embed", side_effect=AssertionError("disabled embedding called")), \
             patch.object(self.models, "rerank", side_effect=AssertionError("disabled reranker called")):
            asyncio.run(runner.run("checkoutservice"))
            result = asyncio.run(runner.run("checkoutservice", overrides={**self.hybrid,
                "sources": {"doc": False, "case": False, "metric": False}}))
            self.assertFalse(result["pack"]["items"])

    def test_dense_uses_exact_cosine_even_for_zero_and_negative_scores(self):
        def polar(texts, *, model, dimension, text_type):
            rows = np.zeros((len(texts), dimension), dtype=np.float32)
            for i, text in enumerate(texts):
                rows[i, 0 if text_type == "query" or "latency" in text else 1] = -1 if "latency" in text else 1
            return rows
        with patch.object(self.models, "embed", side_effect=polar):
            result = asyncio.run(self.runner().run("semantic query", overrides={
                **self.hybrid, "retrieval": {"mode": "dense", "path_enabled": False}}))
        self.assertEqual([0., 0., -1.], [x["score"] for x in result["routes"]["doc.dense.body"]])
        self.assertNotIn("doc.body", result["routes"])

    def test_hybrid_keeps_bm25_and_deduplicates_evidence(self):
        runner = self.runner()
        sparse = asyncio.run(runner.run("checkoutservice"))
        hybrid = asyncio.run(runner.run("checkoutservice", overrides=self.hybrid))
        self.assertEqual(sparse["routes"]["doc.body"], hybrid["routes"]["doc.body"])
        self.assertEqual({"doc.body", "doc.path", "doc.dense.body", "doc.dense.path"},
                         {k for k in hybrid["routes"] if k.startswith("doc.")})
        ids = [x["evidence"]["evidence_id"] for x in hybrid["pack"]["items"]]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(hybrid["experiment"]["cloud"]["embedding"]["vectors"])

    def test_forbidden_evidence_is_filtered_before_cloud_encoding(self):
        verify = {"human_verified": True, "verified_by": "tester", "verified_at": "2023-08-20T00:00:00Z"}
        extra = [node("case", "self", "FORBIDDEN_SELF", source_incident_id=self.incident["incident_id"], **verify),
                 node("case", "future", "FORBIDDEN_FUTURE", source_incident_id="future",
                      **{**verify, "verified_at": "2030-01-01T00:00:00Z"}),
                 node("runbook", "wrong-system", "FORBIDDEN_SYSTEM", system="other"),
                 node("metric", "wrong-incident", "FORBIDDEN_METRIC", source_incident_id="other")]
        asyncio.run(self.runner([*self.nodes, *extra]).run("checkoutservice", incident=self.incident, overrides=self.hybrid))
        self.assertNotIn("FORBIDDEN", json.dumps(self.models.embeds))

    def test_no_path_and_no_rca_are_real_switches(self):
        result = asyncio.run(self.runner().run("如何处置", incident=self.incident, overrides={
            **self.hybrid, "retrieval": {"mode": "hybrid", "path_enabled": False}, "query": {"use_rca": False}}))
        self.assertFalse(any(k.endswith("path") for k in result["routes"]))
        queries = [text for texts, role in self.models.embeds if role == "query" for text in texts]
        self.assertEqual([result["query_context"]["query"]], queries)
        self.assertNotIn("checkoutservice", queries[0])
        self.assertFalse(any("operations/runbook" in t for ts, role in self.models.embeds for t in ts))

    def test_warm_cache_is_identical_without_network_and_contains_no_credentials(self):
        first = asyncio.run(self.runner().run("checkoutservice", overrides=self.hybrid))
        with patch.object(self.models, "embed", side_effect=AssertionError("cache miss")):
            second = asyncio.run(self.runner(list(reversed(self.nodes))).run("checkoutservice", overrides=self.hybrid))
        self.assertEqual(first["pack_id"], second["pack_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotIn("key_file", json.dumps(second))

    def test_rerank_operates_before_pack_budget_and_records_scores(self):
        result = asyncio.run(self.runner().run("checkoutservice", overrides={
            **self.hybrid, "reranker": {"enabled": True, "candidate_top_k": 2}, "pack": {"top_k": 1}}))
        self.assertEqual(2, len(self.models.reranks[0][1]))
        self.assertEqual(1, result["pack"]["items"][0]["rerank_score"])
        self.assertTrue(any(x["reason"] == "rerank_candidate_budget" for x in result["pack"]["dropped"]))
        EvidencePackRecord.parse_obj(result)
        bad = copy.deepcopy(result)
        bad["pack"]["items"][0]["rerank_score"] = 123.
        with self.assertRaises(ValidationError):
            EvidencePackRecord.parse_obj(bad)

    def test_bm25_rerank_does_not_embed(self):
        with patch.object(self.models, "embed", side_effect=AssertionError("embedding disabled")):
            result = asyncio.run(self.runner().run("checkoutservice", overrides={"reranker": {"enabled": True}}))
        self.assertTrue(result["pack"]["reranking"]["enabled"])
        self.assertEqual(1, len(self.models.reranks))

    def test_backend_failure_is_explicit_no_hidden_bm25_fallback(self):
        disabled = OperationsRunner(self.nodes, tokenizer=self.tokenizer, stopwords=set())
        with self.assertRaises(CloudBackendUnavailable):
            asyncio.run(disabled.run("checkoutservice", overrides=self.hybrid))
        with patch.object(self.models, "embed", side_effect=CloudModelError("Cloud request failed")):
            with self.assertRaises(CloudModelError):
                asyncio.run(self.runner().run("checkoutservice", overrides=self.hybrid))

    def test_server_settings_and_inconsistent_ablations_are_rejected(self):
        for patch_config in ({"cloud_services": {"key_file": "x"}}, {"embedding": {"enabled": True}},
                {"embedding": {"dimension": True}}, {"embedding": {"dimension": 100}},
                {"reranker": {"enabled": True, "candidate_top_k": 1}}):
            with self.assertRaises(ValidationError):
                ExperimentConfig().with_overrides(patch_config)
        with patch("easyrag.retrieval.cloud_models.read_api_key", side_effect=AssertionError("key accessed")):
            self.assertEqual((None, None), build_cloud_runtime())
            models, cache = build_cloud_runtime(allow_cloud=True, base_dir=self.directory.name)
            self.assertIsNotNone(models)
            self.assertFalse(cache.cache_path.exists())

    def test_saved_batch_audit_recomputes_cosines_and_detects_tampering(self):
        from run_ablation import run_batch
        from verify_operations_artifacts import audit
        directory, manifest = asyncio.run(run_batch(self.runner(), incident=self.incident,
            question="checkoutservice", profiles=["hybrid"], repeats=2, output_dir=self.directory.name))
        report = audit(directory / "manifest.json", self.cache.cache_path)
        self.assertEqual(2, report["runs_checked"])
        self.assertGreater(report["dense_scores_checked"], 0)
        self.assertEqual(1, report["profiles"]["hybrid"]["distinct_pack_ids"])
        path = directory / manifest["results"][0]["record"]
        record = json.loads(path.read_text(encoding="utf-8"))
        record["pack_id"] = "pack-" + "0" * 16
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            audit(directory / "manifest.json", self.cache.cache_path)


if __name__ == "__main__":
    unittest.main()
