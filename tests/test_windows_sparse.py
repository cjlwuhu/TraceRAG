import asyncio
import tempfile
import unittest
from pathlib import Path

import jieba

from easyrag.custom.retrievers import BM25Retriever
from easyrag.pipeline.ingestion import build_preprocess_pipeline, read_data


class WindowsSparseRetrievalTests(unittest.TestCase):
    def test_bm25_retrieves_runbook_and_keeps_relative_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runbook_dir = root / "runbook"
            runbook_dir.mkdir()
            (runbook_dir / "latency.txt").write_text(
                "CheckoutService 延迟故障。检查 paymentservice 超时日志。",
                encoding="utf-8",
            )
            (runbook_dir / "cpu.txt").write_text(
                "CPU 使用率异常。检查容器资源限制。",
                encoding="utf-8",
            )

            documents = read_data(str(root))
            pipeline = build_preprocess_pipeline(
                data_path=str(root),
                chunk_size=128,
                chunk_overlap=16,
            )
            nodes = asyncio.run(pipeline.arun(documents=documents, num_workers=1))

            retriever = BM25Retriever.from_defaults(
                nodes=nodes,
                tokenizer=jieba.Tokenizer(),
                similarity_top_k=2,
                stopwords=set(),
                embed_type=2,
                bm25_type=0,
            )
            results = retriever.retrieve("checkoutservice 延迟 paymentservice 超时")

            self.assertTrue(results)
            self.assertIn("CheckoutService", results[0].node.text)
            self.assertEqual(results[0].node.metadata["dir"], "runbook")
            self.assertEqual(
                results[0].node.metadata["file_path"],
                "runbook/latency.txt",
            )


if __name__ == "__main__":
    unittest.main()
