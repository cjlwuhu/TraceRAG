import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import jieba

from easyrag.adapters.knowledge_corpus import build_knowledge_corpus, write_manifest
from easyrag.pipeline.ingestion import build_preprocess_pipeline, read_data
from easyrag.retrieval import build_evidence_retriever


class EvidenceRetrieverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runbook_dir = self.root / "runbook"
        self.case_dir = self.root / "case"
        self.metric_dir = self.root / "metric"
        for directory in (self.runbook_dir, self.case_dir, self.metric_dir):
            directory.mkdir()

        (self.runbook_dir / "checkout.txt").write_text(
            "结算延迟手册\nrunbooktoken 检查调用链超时和下游服务。",
            encoding="utf-8",
        )
        self._write_case(
            "self.json",
            case_id="case-current-self-001",
            source_incident_id="inc-current-001",
            marker="current-only-marker",
        )
        self._write_case(
            "past.json",
            case_id="case-past-payment-001",
            source_incident_id="inc-past-001",
            marker="pastcasetoken past-pool-exhaustion",
        )
        self._write_case(
            "other.json",
            case_id="case-past-network-001",
            source_incident_id="inc-past-002",
            marker="unrelated-network-marker",
        )
        metric = {
            "schema_version": "1.0",
            "summary_id": "metric-current-latency-001",
            "source_incident_id": "inc-current-001",
            "system": "online-boutique",
            "service": "checkoutservice",
            "metric": "checkoutservice_latency",
            "window_start_utc": "2026-08-30T01:00:00Z",
            "window_end_utc": "2026-08-30T01:05:00Z",
            "direction": "increase",
            "summary": "metrictoken 时延窗口均值明显上升。",
            "statistics": {"normal_mean": 0.1, "abnormal_mean": 1.2},
            "source": "anomaly-detector",
            "raw_ref": "telemetry:current-window",
        }
        (self.metric_dir / "metric.json").write_text(
            json.dumps(metric, ensure_ascii=False), encoding="utf-8"
        )

        records = build_knowledge_corpus(
            runbook_paths=[self.runbook_dir],
            historical_case_paths=[self.case_dir],
            metric_summary_paths=[self.metric_dir],
            base_path=self.root,
        )
        self.corpus_dir = self.root / "corpus"
        write_manifest(records, self.corpus_dir / "manifest.jsonl")
        documents = read_data(str(self.corpus_dir))
        pipeline = build_preprocess_pipeline(
            data_path=str(self.corpus_dir), chunk_size=256, chunk_overlap=16
        )
        nodes = asyncio.run(pipeline.arun(documents=documents, num_workers=1))
        self.snapshot_dir = self.root / "snapshots"
        self.retriever = build_evidence_retriever(
            nodes,
            tokenizer=jieba.Tokenizer(),
            similarity_top_k=3,
            stopwords=set(),
            embed_type=2,
            bm25_type=0,
            snapshot_dir=self.snapshot_dir,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_case(
        self, filename: str, *, case_id: str, source_incident_id: str, marker: str
    ) -> None:
        payload = {
            "schema_version": "1.0",
            "case_id": case_id,
            "source_incident_id": source_incident_id,
            "system": "online-boutique",
            "title": marker,
            "symptoms": [marker],
            "rca_candidates": ["checkoutservice_latency"],
            "verified_root_cause": f"已确认原因 {marker}",
            "actions": ["执行已验证处置"],
            "evidence_refs": [f"evidence:{marker}"],
            "human_verified": True,
            "verified_by": "operator-a",
            "verified_at": "2026-08-29T10:00:00Z",
            "source": "verified-ticket",
            "raw_ref": f"ticket:{case_id}",
        }
        (self.case_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_three_retrievers_return_unified_evidence(self) -> None:
        snapshot = asyncio.run(
            self.retriever.retrieve(
                "runbooktoken pastcasetoken metrictoken",
                current_incident_id="inc-current-001",
            )
        )

        self.assertTrue(snapshot.results["doc"])
        self.assertTrue(snapshot.results["case"])
        self.assertTrue(snapshot.results["metric"])
        for evidence_type, items in snapshot.results.items():
            for rank, item in enumerate(items, 1):
                self.assertEqual(evidence_type, item.type)
                self.assertEqual(rank, item.rank)
                self.assertEqual("bm25", item.retriever)
                self.assertTrue(item.evidence_id.startswith(f"ev-{evidence_type}-"))
                self.assertNotIn("file_abs_path", item.metadata)

    def test_current_incident_is_excluded_only_from_cases(self) -> None:
        self_case = asyncio.run(
            self.retriever.retrieve(
                "current-only-marker",
                current_incident_id="inc-current-001",
            )
        )
        current_metric = asyncio.run(
            self.retriever.retrieve(
                "metrictoken",
                current_incident_id="inc-current-001",
            )
        )

        self.assertFalse(
            any(
                item.metadata.get("source_incident_id") == "inc-current-001"
                for item in self_case.results["case"]
            )
        )
        self.assertTrue(
            any(
                item.metadata.get("source_incident_id") == "inc-current-001"
                for item in current_metric.results["metric"]
            )
        )

    def test_snapshot_is_saved_and_ids_are_stable(self) -> None:
        first = asyncio.run(
            self.retriever.retrieve(
                "runbooktoken pastcasetoken metrictoken",
                current_incident_id="inc-current-001",
                persist=True,
            )
        )
        second = asyncio.run(
            self.retriever.retrieve(
                "runbooktoken pastcasetoken metrictoken",
                current_incident_id="inc-current-001",
            )
        )

        self.assertEqual(first.retrieval_id, second.retrieval_id)
        self.assertIsNotNone(first.saved_to)
        saved_path = Path(first.saved_to)
        self.assertTrue(saved_path.is_file())
        payload = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertEqual(first.retrieval_id, payload["retrieval_id"])
        self.assertNotIn("saved_to", payload)
        self.assertEqual(
            [item.evidence_id for item in first.results["doc"]],
            [item.evidence_id for item in second.results["doc"]],
        )

    def test_unrelated_query_does_not_trigger_tiny_corpus_fallback(self) -> None:
        snapshot = asyncio.run(
            self.retriever.retrieve("token-that-exists-nowhere-987654321")
        )

        self.assertTrue(all(not items for items in snapshot.results.values()))


if __name__ == "__main__":
    unittest.main()
