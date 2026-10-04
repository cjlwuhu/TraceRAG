"""Exercise the real app, ingestion and retrieval, with no model/network calls."""

import copy
import importlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


class OperationsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original_directory = Path.cwd()
        try:
            os.chdir(ROOT / "src")
            with patch.dict(os.environ, {"EASYRAG_CONFIG": "configs/easyrag.operations.windows.yaml"}):
                cls.module = importlib.import_module("api")
        finally:
            os.chdir(original_directory)
        cls.client = TestClient(cls.module.app)
        cls.incident = json.loads((ROOT / "examples/incidents/checkoutservice-incident.v1.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def request(self, **changes):
        body = {"query": "checkoutservice 延迟如何验证和处置", "incident": self.incident,
                "overrides": {"save_intermediates": False}}
        body.update(changes)
        return self.client.post("/v1/evidence/pack", json=body)

    def test_config_is_safe_and_describes_only_implemented_capabilities(self):
        response = self.client.get("/v1/operations/config")
        self.assertEqual(200, response.status_code)
        result = response.json()
        self.assertTrue(result["capabilities"]["bm25"])
        self.assertFalse(result["capabilities"]["dense"])
        self.assertTrue(result["capabilities"]["work_order_generation"])
        self.assertFalse(result["capabilities"]["cloud_work_order_generation"])
        self.assertNotIn("llm_keys", response.text)
        self.assertEqual("object", result["schema"]["type"])

    def test_full_request_uses_real_ingestion_and_excludes_unrelated_metric_fixture(self):
        response = self.request()
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertTrue(result["routes"]["doc.body"])
        self.assertTrue(result["routes"]["case.body"])
        self.assertEqual([], result["routes"]["metric.body"])
        self.assertEqual(1, result["diagnostics"]["metric"]["node_counts"]["different_incident"])
        self.assertEqual(len(result["pack"]["context"]), result["pack"]["context_chars"])
        self.assertTrue(any("synthetic" in x for x in result["limitations"]))

    def test_overrides_and_validation_flow_through_http(self):
        response = self.request(overrides={"sources": {"case": False, "metric": False},
                                          "query": {"use_rca": False},
                                          "retrieval": {"path_enabled": False}, "save_intermediates": False})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"doc.body"}, set(response.json()["routes"]))
        self.assertEqual([], response.json()["query_context"]["rca_candidates"])
        self.assertTrue(self.client.get("/v1/operations/config").json()["defaults"]["sources"]["case"])
        for invalid in ({"query": " "}, {"overrides": {"retrieval": {"mode": "dense"}}},
                        {"overrides": {"unknown_option": True}},
                        {"overrides": {"sources": {"case": "false"}}}):
            self.assertEqual(422, self.request(**invalid).status_code)
        incident = copy.deepcopy(self.incident)
        incident["fault_type"] = "label"
        self.assertEqual(422, self.request(incident=incident).status_code)

    def test_unconfigured_runner_is_reported_as_unavailable(self):
        with patch.object(self.module.easyrag, "operations_runner", None):
            self.assertEqual(503, self.request().status_code)
            self.assertEqual(503, self.client.get("/v1/operations/config").status_code)

    def test_v11_without_rca_roundtrips_through_response_validation(self):
        from test_signal_bundle import fixture
        from easyrag.adapters.signal_bundle import convert_signal_bundle
        incident, _ = convert_signal_bundle(fixture(), "b" * 64)
        response = self.request(incident=incident)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([], response.json()["query_context"]["rca_candidates"])
        self.assertEqual("2023-11-14T22:16:00Z", response.json()["query_context"]["detection"]["evidence_cutoff_utc"])

    def test_cloud_unavailable_and_server_only_settings_cannot_be_overridden(self):
        response = self.request(overrides={"retrieval": {"mode": "hybrid"}, "embedding": {"enabled": True}})
        self.assertEqual(503, response.status_code)
        response = self.request(overrides={"cloud_services": {"api_host": "https://example.com"}})
        self.assertEqual(422, response.status_code)

    def test_hybrid_and_reranker_work_through_http_with_sanitized_errors(self):
        import tempfile
        from test_dense_operations import FakeModels
        from easyrag.retrieval.vector_cache import CachedEmbeddings
        from easyrag.retrieval.cloud_models import CloudModelError
        runner = self.module.easyrag.operations_runner
        models = FakeModels()
        overrides = {"retrieval": {"mode": "hybrid"}, "embedding": {"enabled": True, "dimension": 64},
                     "reranker": {"enabled": True}, "save_intermediates": False}
        with tempfile.TemporaryDirectory() as temporary, patch.object(runner, "cloud_models", models), \
                patch.object(runner, "embedding_cache", CachedEmbeddings(models, Path(temporary) / "cache.sqlite")):
            self.assertTrue(self.client.get("/v1/operations/config").json()["capabilities"]["dense"])
            response = self.request(overrides=overrides)
            self.assertEqual(200, response.status_code, response.text)
            self.assertTrue(response.json()["pack"]["reranking"]["enabled"])
            self.assertIn("doc.dense.body", response.json()["routes"])
            with patch.object(models, "rerank", side_effect=CloudModelError("Cloud model returned HTTP 401")):
                response = self.request(overrides=overrides)
                self.assertEqual(502, response.status_code)
                self.assertEqual({"detail": "Cloud model returned HTTP 401"}, response.json())

    def test_work_order_http_extractive_disabled_and_empty(self):
        base = {"query": "checkoutservice 如何验证", "incident": self.incident,
                "overrides": {"save_intermediates": False},
                "generation_overrides": {"save_intermediates": False}}
        response = self.client.post("/v1/work-orders", json=base)
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual("draft", result["status"])
        self.assertEqual("unconfirmed", result["root_cause_status"])
        self.assertFalse(result["actions_executed"])
        self.assertEqual("passed", result["provenance"]["citation_integrity"])
        disabled = copy.deepcopy(base)
        disabled["generation_overrides"].update(enabled=False, mode="cloud")
        response = self.client.post("/v1/work-orders", json=disabled)
        self.assertEqual("generation_disabled", response.json()["status"])
        self.assertIsNone(response.json()["draft"])
        empty = copy.deepcopy(base)
        empty["overrides"]["sources"] = {"doc": False, "case": False, "metric": False}
        response = self.client.post("/v1/work-orders", json=empty)
        self.assertEqual("insufficient_evidence", response.json()["status"])

    def test_work_order_http_cloud_capability_validation_and_response(self):
        from easyrag.domain.work_order import GenerationConfig
        from easyrag.generation.work_order import extractive_draft
        from easyrag.domain.evidence_pack import EvidencePackRecord
        from easyrag.retrieval.cloud_models import DashScopeModels
        evidence_response = self.request()
        draft = extractive_draft(EvidencePackRecord.parse_obj(evidence_response.json()), GenerationConfig()).dict()
        body = {"query": "checkoutservice 延迟如何验证和处置", "incident": self.incident,
                "overrides": {"save_intermediates": False},
                "generation_overrides": {"mode": "cloud", "save_intermediates": False}}
        self.assertEqual(503, self.client.post("/v1/work-orders", json=body).status_code)
        with patch.object(self.module.easyrag.operations_runner, "cloud_models", DashScopeModels()), \
                patch("easyrag.generation.work_order.generate_json", return_value=(draft, {})) as call:
            response = self.client.post("/v1/work-orders", json=body)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(1, call.call_count)
            draft["summary"]["citations"][0]["quote"] = "NOT_IN_SOURCE_PRIVATE_MARKER"
            response = self.client.post("/v1/work-orders", json=body)
            self.assertEqual(502, response.status_code)
            self.assertNotIn("PRIVATE_MARKER", response.text)
        body["generation_overrides"]["enabled"] = "false"
        self.assertEqual(422, self.client.post("/v1/work-orders", json=body).status_code)


if __name__ == "__main__":
    unittest.main()
