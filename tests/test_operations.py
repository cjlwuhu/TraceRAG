import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

import jieba
from llama_index.core.schema import TextNode
from pydantic import ValidationError

from easyrag.domain.experiment import ExperimentConfig
from easyrag.domain.evidence_pack import EvidencePackRecord
from easyrag.domain.knowledge import KnowledgeDocument
from easyrag.retrieval.evidence_pack import fuse_evidence
from easyrag.retrieval.operations import OperationsRunner
from easyrag.retrieval.query_context import IncidentInput, build_query_context


ROOT = Path(__file__).resolve().parents[1]


def node(kind, ident, content, **metadata):
    return TextNode(text=content, metadata={"knowledge_id": ident, "knowledge_type": kind,
                    "source": "synthetic-unit-test", "raw_ref": "fixture:" + ident,
                    "know_path": "operations/" + kind + "/" + ident,
                    "system": "online-boutique", **metadata})


class OperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokenizer = jieba.Tokenizer()
        cls.incident = json.loads((ROOT / "examples/incidents/checkoutservice-incident.v1.json").read_text(encoding="utf-8"))
        cls.current_id = cls.incident["incident_id"]

    def make_runner(self, output=None):
        verify = {"human_verified": True, "verified_by": "test-operator",
                  "verified_at": "2023-08-20T00:00:00Z"}
        nodes = [
            node("runbook", "doc-first", "排查 checkoutservice latency"),
            node("runbook", "doc-no-match", "磁盘清理"),
            node("case", "case-self", "checkoutservice " * 20, source_incident_id=self.current_id, **verify),
            node("case", "case-past", "checkoutservice 连接池", source_incident_id="past", **verify),
            node("case", "case-future", "checkoutservice " * 20, source_incident_id="future",
                 **{**verify, "verified_at": "2030-01-01T00:00:00Z"}),
            node("case", "case-unverified", "checkoutservice", source_incident_id="unknown"),
            node("metric", "metric-current", "checkoutservice 均值上升", source_incident_id=self.current_id,
                 window_start_utc="2023-08-21T07:25:00Z", window_end_utc="2023-08-21T07:34:00Z"),
            node("metric", "metric-other", "checkoutservice " * 20, source_incident_id="other",
                 window_start_utc="2023-08-21T07:25:00Z", window_end_utc="2023-08-21T07:34:00Z"),
            node("metric", "metric-old", "checkoutservice " * 20, source_incident_id=self.current_id,
                 window_start_utc="2023-08-20T07:25:00Z", window_end_utc="2023-08-20T07:34:00Z"),
        ]
        return OperationsRunner(nodes, tokenizer=self.tokenizer, stopwords=set(), output_dir=output)

    def test_query_drops_prebuilt_text_and_disables_all_rca_derived_fields(self):
        incident = copy.deepcopy(self.incident)
        incident["retrieval_text"] = "NEVER_USE_PREBUILT_TEXT"
        incident["observations"]["suspected_services"] = ["RCA_INDIRECT_SENTINEL"]
        parsed = IncidentInput.parse_obj(incident)
        context = build_query_context("如何排查", parsed, ExperimentConfig().query)
        self.assertIn("checkoutservice_latency", context["query"])
        self.assertNotIn("NEVER_USE", json.dumps(context))
        no_rca = ExperimentConfig().with_overrides({"query": {"use_rca": False}})
        context = build_query_context("如何排查", parsed, no_rca.query)
        self.assertEqual([], context["rca_candidates"])
        self.assertEqual(["online-boutique"], context["expansion_terms"])
        self.assertNotIn("RCA_INDIRECT", json.dumps(context))
        self.assertEqual(self.current_id, context["current_incident_id"])

    def test_incident_rejects_labels_and_inconsistent_timestamps(self):
        payload = copy.deepcopy(self.incident)
        payload["ground_truth"] = {"service": "secret"}
        with self.assertRaises(ValidationError):
            IncidentInput.parse_obj(payload)
        payload = copy.deepcopy(self.incident)
        payload["detection"]["timestamp_unix"] += 1
        with self.assertRaises(ValidationError):
            IncidentInput.parse_obj(payload)

    def test_retrieval_filters_before_topk_and_deduplicates_field_hits(self):
        runner = self.make_runner()
        result = asyncio.run(runner.run("checkoutservice", incident=self.incident,
                                        overrides={"retrieval": {"per_route_top_k": 1}}))
        ids = {r["metadata"]["knowledge_id"] for ranked in result["routes"].values() for r in ranked}
        self.assertEqual({"doc-first", "case-past", "metric-current"}, ids)
        self.assertEqual(3, len(result["pack"]["items"]))
        self.assertEqual(1, result["diagnostics"]["case"]["node_counts"]["future_case"])
        self.assertEqual(1, result["diagnostics"]["metric"]["node_counts"]["outside_window"])
        self.assertTrue(all(r["score"] > 0 for rs in result["routes"].values() for r in rs))

    def test_request_switches_are_isolated_and_change_experiment_fingerprint(self):
        runner = self.make_runner()
        async def concurrent():
            return await asyncio.gather(
                runner.run("checkoutservice", incident=self.incident),
                runner.run("checkoutservice", incident=self.incident,
                           overrides={"sources": {"case": False, "metric": False},
                                      "retrieval": {"path_enabled": False}, "fusion": {"enabled": False}}))
        full, baseline = asyncio.run(concurrent())
        self.assertEqual({"doc.body"}, set(baseline["routes"]))
        self.assertEqual("round_robin", baseline["pack"]["strategy"])
        self.assertEqual("weighted_rrf", full["pack"]["strategy"])
        self.assertNotEqual(full["pack_id"], baseline["pack_id"])
        self.assertTrue(runner.defaults.sources.case)
        with self.assertRaises(ValidationError):
            runner.defaults.with_overrides({"retrieval": {"mode": "dense"}})
        with self.assertRaises(ValidationError):
            runner.defaults.with_overrides({"sources": {"case": "false"}})
        with self.assertRaises(ValidationError):
            runner.defaults.with_overrides({"fusion": {"weights": {"case": float("nan")}}})

    def test_empty_routes_and_budget_are_explicit(self):
        runner = self.make_runner()
        no_sources = asyncio.run(runner.run("checkoutservice", overrides={"sources": {"doc": False, "case": False, "metric": False}}))
        self.assertEqual([], no_sources["pack"]["items"])
        self.assertEqual("", no_sources["pack"]["context"])
        result = asyncio.run(runner.run("checkoutservice", incident=self.incident, overrides={"pack": {"max_context_chars": 1}}))
        self.assertEqual("", result["pack"]["context"])
        self.assertTrue(result["pack"]["dropped"])
        no_incident = asyncio.run(runner.run("checkoutservice"))
        self.assertFalse(no_incident["routes"]["metric.body"])

    def test_repeated_runs_preserve_history_and_stable_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = self.make_runner(temporary)
            first = asyncio.run(runner.run("checkoutservice", incident=self.incident, overrides={"save_intermediates": True}))
            second = asyncio.run(runner.run("checkoutservice", incident=self.incident, overrides={"save_intermediates": True}))
            self.assertEqual(first["pack_id"], second["pack_id"])
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertEqual(2, len(list(Path(temporary).glob("*.json"))))
            saved = json.loads((Path(temporary) / (first["run_id"] + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(first, saved)

    def test_rrf_rewards_agreement_and_is_not_raw_score_sorting(self):
        def ev(eid, score):
            return {"evidence_id": eid, "type": "doc", "content": eid, "raw_ref": eid,
                    "source": "test", "score": score}
        a, b, c = ev("a", 9999), ev("b", -10), ev("c", 8888)
        result = fuse_evidence({"doc.body": [a, b, b], "doc.path": [c, b]}, ExperimentConfig())
        self.assertEqual("b", result["items"][0]["evidence"]["evidence_id"])
        self.assertAlmostEqual(2 / 62, result["items"][0]["fusion_score"])
        self.assertEqual(2, len(result["items"][0]["contributions"]))
        self.assertEqual(len(result["context"]), result["context_chars"])

    def test_jsonl_cannot_bypass_verification_or_override_domain(self):
        common = dict(schema_version="1.0", knowledge_id="case-test", knowledge_type="case",
                      title="test", content="test", source="test", raw_ref="test")
        with self.assertRaises(ValueError):
            KnowledgeDocument(**common, metadata={"source_incident_id": "a", "system": "x"}).validate()
        with self.assertRaises(ValueError):
            KnowledgeDocument(**common, metadata={"knowledge_type": "runbook"}).validate()

    def test_empty_and_stopwords_only_corpus_are_safe(self):
        for nodes in ([], [node("runbook", "empty", "the and", know_path="the and")]):
            runner = OperationsRunner(nodes, tokenizer=self.tokenizer, stopwords={"the", "and"})
            result = asyncio.run(runner.run("checkoutservice", incident=self.incident))
            self.assertEqual([], result["pack"]["items"])

    def test_output_contract_rejects_tampered_context_and_invalid_config(self):
        result = asyncio.run(self.make_runner().run("checkoutservice", incident=self.incident))
        for kind in ("context", "config", "rank"):
            invalid = copy.deepcopy(result)
            if kind == "context":
                invalid["pack"]["context"] += "uncited conclusion"
            elif kind == "config":
                invalid["experiment"].pop("config")
            else:
                invalid["pack"]["items"][0]["pack_rank"] = 5
            with self.assertRaises(ValidationError):
                EvidencePackRecord.parse_obj(invalid)

    def test_corpus_and_code_provenance_are_recorded(self):
        first = self.make_runner()
        second = OperationsRunner(list(reversed(first.nodes)), tokenizer=self.tokenizer, stopwords=set())
        self.assertEqual(first.corpus_sha256, second.corpus_sha256)
        changed = list(first.nodes) + [node("runbook", "new", "new evidence")]
        third = OperationsRunner(changed, tokenizer=self.tokenizer, stopwords=set())
        self.assertNotEqual(first.corpus_sha256, third.corpus_sha256)
        result = asyncio.run(first.run("checkoutservice", incident=self.incident))
        self.assertEqual(64, len(result["experiment"]["code_sha256"]))
        self.assertIn("rank-bm25", result["experiment"]["versions"])

    def test_ablation_batch_keeps_repeats_and_records_consistent(self):
        from run_ablation import run_batch
        with tempfile.TemporaryDirectory() as temporary:
            runner = self.make_runner()
            directory, manifest = asyncio.run(run_batch(runner, incident=self.incident,
                question="checkoutservice", profiles=["full", "no_rca"], repeats=2, output_dir=temporary))
            self.assertIsNone(runner.output_dir)
            self.assertEqual(4, len(manifest["results"]))
            self.assertEqual(manifest, json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
            self.assertEqual(4, len({row["run_id"] for row in manifest["results"]}))
            for profile in ("full", "no_rca"):
                rows = [r for r in manifest["results"] if r["profile"] == profile]
                self.assertEqual(rows[0]["pack_id"], rows[1]["pack_id"])
            for row in manifest["results"]:
                saved = json.loads((directory / row["record"]).read_text(encoding="utf-8"))
                self.assertEqual(row["pack_id"], saved["pack_id"])
                self.assertEqual(row["config_sha256"], saved["experiment"]["config_sha256"])
                EvidencePackRecord.parse_obj(saved)


if __name__ == "__main__":
    unittest.main()
