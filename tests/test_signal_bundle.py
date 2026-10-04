import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

import jieba
from pydantic import ValidationError

from easyrag.adapters.signal_bundle import convert_signal_bundle
from easyrag.adapters.knowledge_corpus import write_manifest
from easyrag.retrieval.evidence_pack import fingerprint
from easyrag.retrieval.operations import OperationsRunner, eligible
from easyrag.retrieval.query_context import IncidentInput, build_query_context
from easyrag.domain.experiment import ExperimentConfig
from easyrag.pipeline.ingestion import read_data, build_preprocess_pipeline


def signed(payload):
    stable = {k: v for k, v in payload.items() if k != "bundle_id"}
    stable["rca_runs"] = [{k: v for k, v in r.items() if k != "runtime_seconds"} for r in payload["rca_runs"]]
    payload["bundle_id"] = "signal-" + fingerprint(stable)[:20]
    return payload


def fixture():
    digest = "a" * 64
    incident_id = "inc-1700000100-" + fingerprint(["online-boutique", 1700000100, digest])[:12]
    return signed({"schema_version": "1.0", "incident_id": incident_id, "system": "online-boutique",
        "telemetry": {"sha256": digest, "end_utc": "2023-11-14T22:18:20Z"},
        "detection": {"status": "detected", "source": "anomaly_detector",
                      "timestamp_unix": 1700000100, "timestamp_utc": "2023-11-14T22:15:00Z"},
        "analysis": {"status": "complete", "observation_start_utc": "2023-11-14T22:15:00Z",
                     "observation_end_utc": "2023-11-14T22:16:00Z", "evidence_cutoff_utc": "2023-11-14T22:16:00Z"},
        "rca_runs": [], "metric_summaries": [{"schema_version": "1.0", "summary_id": "metric-fixture",
            "source_incident_id": incident_id, "system": "online-boutique", "service": "checkoutservice",
            "metric": "checkoutservice_latency", "window_start_utc": "2023-11-14T22:15:00Z",
            "window_end_utc": "2023-11-14T22:16:00Z", "direction": "increase",
            "summary": "参考均值为1，当前均值为2；这是观测，不是因果。" * 10,
            "statistics": {"normal_mean": 1, "abnormal_mean": 2}, "source": "synthetic-contract-test",
            "raw_ref": "telemetry:sha256:" + digest + "#column=checkoutservice_latency"}],
        "config": {"detection": {"enabled": True}, "rca": {"enabled": False}, "summaries": {"enabled": True},
                   "window": {"reference_minutes": 1, "observation_minutes": 1}}, "provenance": {},
        "limitations": ["Synthetic contract fixture."]})


class SignalBundleTests(unittest.TestCase):
    def test_no_rca_contract_and_atomic_metric_ingestion(self):
        incident, summaries = convert_signal_bundle(fixture(), "b" * 64)
        self.assertEqual("1.1", incident["schema_version"])
        self.assertEqual([], incident["rca_runs"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_manifest([s.to_knowledge_document() for s in summaries], root / "manifest.jsonl")
            for split_type in (0, 1):
                pipeline = build_preprocess_pipeline(str(root), chunk_size=64, chunk_overlap=8, split_type=split_type)
                nodes = asyncio.run(pipeline.arun(documents=read_data(str(root)), num_workers=1))
                self.assertEqual(1, len(nodes))
                self.assertIn("abnormal_mean=2", nodes[0].text)
                self.assertIn(summaries[0].summary, nodes[0].text)
                runner = OperationsRunner(nodes, tokenizer=jieba.Tokenizer(), stopwords=set())
                result = asyncio.run(runner.run("checkoutservice", incident=incident))
                self.assertEqual(1, len(result["pack"]["items"]))
                self.assertEqual("metric", result["pack"]["items"][0]["evidence"]["type"])
                self.assertTrue(any("No RCA" in x for x in result["limitations"]))

    def test_rejects_unready_or_tampered_bundle(self):
        for field in ("status", "identity", "hash", "labels", "source", "disabled"):
            data = fixture()
            if field == "status":
                data["analysis"]["status"] = "awaiting_observation_window"
            elif field == "identity":
                data["incident_id"] = "inc-other"
            elif field == "hash":
                data["bundle_id"] = "signal-" + "0" * 20
            elif field == "labels":
                data["ground_truth"] = "checkoutservice"
            elif field == "source":
                data["detection"]["source"] = "benchmark_annotation"
            else:
                data["config"]["summaries"]["enabled"] = False
            with self.assertRaises(ValueError):
                convert_signal_bundle(data, "b" * 64)

    def test_rejects_wrong_metric_window_source_or_identity_even_when_resigned(self):
        for field in ("window", "source", "identity", "cutoff"):
            data = fixture()
            if field == "window":
                data["metric_summaries"][0]["window_end_utc"] = "2023-11-14T22:17:00Z"
            elif field == "source":
                data["metric_summaries"][0]["raw_ref"] = "file:answer.csv"
            elif field == "identity":
                data["metric_summaries"][0]["source_incident_id"] = "other"
            else:
                data["analysis"]["evidence_cutoff_utc"] = "2023-11-14T22:20:00Z"
            with self.assertRaises(ValueError):
                convert_signal_bundle(signed(data), "b" * 64)

    def test_cutoff_filters_future_metric_at_query_time(self):
        incident, summaries = convert_signal_bundle(fixture(), "b" * 64)
        context = build_query_context("test", IncidentInput.parse_obj(incident), ExperimentConfig().query)
        metadata = summaries[0].to_knowledge_document().metadata
        self.assertEqual((True, "eligible"), eligible(metadata, "metric", context))
        metadata["window_end_utc"] = "2023-11-14T22:16:01Z"
        self.assertEqual((False, "after_evidence_cutoff"), eligible(metadata, "metric", context))

    def test_version_specific_rules_remain_explicit(self):
        incident, _ = convert_signal_bundle(fixture(), "b" * 64)
        for version, cutoff in (("1.0", True), ("1.0", False), ("1.1", False)):
            value = copy.deepcopy(incident)
            value["schema_version"] = version
            if not cutoff:
                value["detection"].pop("evidence_cutoff_utc")
            with self.assertRaises(ValidationError):
                IncidentInput.parse_obj(value)


if __name__ == "__main__":
    unittest.main()
