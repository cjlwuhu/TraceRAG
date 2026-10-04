import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import jieba

from easyrag.adapters.knowledge_corpus import (
    build_knowledge_corpus,
    load_manifest,
    write_manifest,
)
from easyrag.custom.retrievers import BM25Retriever
from easyrag.pipeline.ingestion import build_preprocess_pipeline, read_data


class KnowledgeCorpusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runbook_dir = self.root / "runbook"
        self.case_dir = self.root / "case"
        self.metric_dir = self.root / "metric"
        for path in (self.runbook_dir, self.case_dir, self.metric_dir):
            path.mkdir()
        (self.runbook_dir / "checkout.txt").write_text(
            "CheckoutService 延迟处置手册\n检查 PaymentService 连接池和超时日志。",
            encoding="utf-8",
        )
        self.case_payload = {
            "schema_version": "1.0",
            "case_id": "case-payment-pool-001",
            "source_incident_id": "inc-previous-001",
            "system": "online-boutique",
            "title": "支付连接池耗尽案例",
            "symptoms": ["结算请求超时"],
            "rca_candidates": ["paymentservice_latency"],
            "verified_root_cause": "数据库连接池耗尽",
            "actions": ["扩容实例", "调整连接池上限"],
            "evidence_refs": ["log-pool-timeout"],
            "human_verified": True,
            "verified_by": "operator-a",
            "verified_at": "2026-08-30T10:00:00+08:00",
            "source": "verified-ticket",
            "raw_ref": "ticket:001",
        }
        self.metric_payload = {
            "schema_version": "1.0",
            "summary_id": "metric-checkout-latency-001",
            "source_incident_id": "inc-current-001",
            "system": "online-boutique",
            "service": "checkoutservice",
            "metric": "checkoutservice_latency",
            "window_start_utc": "2026-08-30T01:00:00Z",
            "window_end_utc": "2026-08-30T01:05:00Z",
            "direction": "increase",
            "summary": "结算时延均值显著上升，但尚未确认根因。",
            "statistics": {"normal_mean": 0.1, "abnormal_mean": 1.2},
            "source": "anomaly-detector",
            "raw_ref": "telemetry:window-001",
        }
        self._write_json(self.case_dir / "case.json", self.case_payload)
        self._write_json(self.metric_dir / "metric.json", self.metric_payload)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _build_manifest(self) -> Path:
        documents = build_knowledge_corpus(
            runbook_paths=[self.runbook_dir],
            historical_case_paths=[self.case_dir],
            metric_summary_paths=[self.metric_dir],
            base_path=self.root,
        )
        corpus_dir = self.root / "corpus"
        manifest = corpus_dir / "manifest.jsonl"
        write_manifest(documents, manifest)
        return manifest

    def test_builds_three_domains_and_ingestion_preserves_metadata(self) -> None:
        manifest = self._build_manifest()

        records = load_manifest(manifest)
        documents = read_data(str(manifest.parent))

        self.assertEqual(["case", "metric", "runbook"], [item.knowledge_type for item in records])
        self.assertEqual(3, len(documents))
        self.assertEqual(
            {"case", "metric", "runbook"},
            {document.metadata["knowledge_type"] for document in documents},
        )
        self.assertTrue(
            all("raw_ref" in document.metadata for document in documents)
        )
        runbook = next(
            document
            for document in documents
            if document.metadata["knowledge_type"] == "runbook"
        )
        self.assertEqual(1, runbook.text.count("CheckoutService 延迟处置手册"))

    def test_each_domain_can_be_filtered_and_retrieved_with_bm25(self) -> None:
        manifest = self._build_manifest()
        documents = read_data(str(manifest.parent))
        pipeline = build_preprocess_pipeline(
            data_path=str(manifest.parent), chunk_size=256, chunk_overlap=16
        )
        nodes = asyncio.run(pipeline.arun(documents=documents, num_workers=1))
        retriever = BM25Retriever.from_defaults(
            nodes=nodes,
            tokenizer=jieba.Tokenizer(),
            similarity_top_k=3,
            stopwords=set(),
            embed_type=2,
            bm25_type=0,
        )

        queries = {
            "runbook": "CheckoutService 处置手册 超时日志",
            "case": "数据库连接池耗尽 历史案例",
            "metric": "checkoutservice_latency 均值上升",
        }
        for knowledge_type, query in queries.items():
            with self.subTest(knowledge_type=knowledge_type):
                retriever.filter_dict = {"knowledge_type": knowledge_type}
                results = retriever.retrieve(query)
                self.assertTrue(results)
                self.assertTrue(
                    all(
                        result.node.metadata["knowledge_type"] == knowledge_type
                        for result in results
                    )
                )

    def test_unverified_case_is_rejected(self) -> None:
        payload = dict(self.case_payload)
        payload["human_verified"] = False
        self._write_json(self.case_dir / "case.json", payload)

        with self.assertRaisesRegex(ValueError, "unverified incident"):
            build_knowledge_corpus(historical_case_paths=[self.case_dir])

    def test_evaluation_fields_are_rejected(self) -> None:
        payload = dict(self.metric_payload)
        payload["ground_truth"] = {"service": "secret-service"}
        self._write_json(self.metric_dir / "metric.json", payload)

        with self.assertRaisesRegex(ValueError, "forbidden evaluation key"):
            build_knowledge_corpus(metric_summary_paths=[self.metric_dir])

    def test_incident_document_cannot_be_used_as_historical_case(self) -> None:
        current_incident = {
            "schema_version": "1.0",
            "incident_id": "inc-1700000000-123456789abc",
            "lifecycle": "active",
            "system": "demo",
        }
        self._write_json(self.case_dir / "case.json", current_incident)

        with self.assertRaises(ValueError):
            build_knowledge_corpus(historical_case_paths=[self.case_dir])


if __name__ == "__main__":
    unittest.main()
