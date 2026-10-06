"""Exercise the offline retrieval boundary without optional storage/model stacks."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RetrievalDependencyTests(unittest.TestCase):
    def run_script(self, script):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(ROOT / "src"), environment.get("PYTHONPATH", ""))))
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT,
            env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_ingestion_and_bm25_query_work_without_optional_dependencies(self):
        self.run_script('''
            import asyncio
            import importlib.abc
            from pathlib import Path
            import socket
            import sys
            import tempfile

            class OptionalDependencyBlocker(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + ".")
                           for name in ("qdrant_client", "llama_index.vector_stores.qdrant",
                                        "bm25s", "torch", "transformers", "paddleocr")):
                        raise ModuleNotFoundError("offline retrieval imported " + fullname)

            connect = socket.socket.connect

            def deny_external_network(self, address):
                # Windows asyncio creates its wakeup socketpair through loopback.
                if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                    return connect(self, address)
                raise AssertionError("offline retrieval attempted a network connection")

            sys.meta_path.insert(0, OptionalDependencyBlocker())
            socket.socket.connect = deny_external_network

            import jieba
            from easyrag.custom.retrievers import BM25Retriever
            from easyrag.pipeline.ingestion import build_preprocess_pipeline, read_data

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runbooks = root / "runbook"
                runbooks.mkdir()
                (runbooks / "latency.txt").write_text(
                    "CheckoutService latency\\npaymentservice timeout investigation", encoding="utf-8")
                (runbooks / "cpu.txt").write_text("CPU pressure\\ncontainer resource limits", encoding="utf-8")
                (runbooks / "disk.txt").write_text("Disk capacity\\nstorage cleanup", encoding="utf-8")
                pipeline = build_preprocess_pipeline(str(root), chunk_size=128, chunk_overlap=16)
                nodes = asyncio.run(pipeline.arun(documents=read_data(str(root)), num_workers=1))
                for bm25_type in (0, 2):
                    retriever = BM25Retriever.from_defaults(
                        nodes=nodes, tokenizer=jieba.Tokenizer(), similarity_top_k=2,
                        stopwords=set(), embed_type=2, bm25_type=bm25_type)
                    retriever.filter_dict = {"file_path": "runbook/latency.txt"}
                    hits = retriever.retrieve("checkoutservice paymentservice timeout")
                    assert len(hits) == 1, hits
                    assert "paymentservice timeout" in hits[0].node.text
                    assert hits[0].node.metadata["document_title"] == "CheckoutService latency"
                    assert hits[0].node.metadata["dir"] == "runbook"
                    scores = retriever.get_scores("timeout", ["paymentservice timeout", "cpu pressure", "disk capacity"])
                    assert len(scores) == 3 and scores[0] > scores[1] and scores[0] > scores[2], scores
        ''')

    def test_preprocessing_keeps_pathmap_without_reading_obsolete_image_map(self):
        self.run_script('''
            import asyncio
            import contextlib
            import io
            import json
            from pathlib import Path
            import tempfile

            from easyrag.pipeline.ingestion import build_preprocess_pipeline, read_data

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "runbook").mkdir()
                (root / "runbook/latency.txt").write_text("Latency guide\\nCheck timeout logs", encoding="utf-8")
                (root / "pathmap.json").write_text(json.dumps({"runbook/latency.txt": ["operations", "latency"]}), encoding="utf-8")
                (root / "imgmap_filtered.json").write_text("obsolete content is not JSON", encoding="utf-8")
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    pipeline = build_preprocess_pipeline(str(root), chunk_size=128, chunk_overlap=16)
                    nodes = asyncio.run(pipeline.arun(documents=read_data(str(root)), num_workers=1))
                assert captured.getvalue() == "", captured.getvalue()
                assert len(nodes) == 1, nodes
                metadata = nodes[0].metadata
                assert metadata["file_path"] == "runbook/latency.txt", metadata
                assert metadata["know_path"] == "operations/latency", metadata
                assert metadata["document_title"] == "Latency guide", metadata
                assert metadata["file_abs_path"] == str((root / "runbook/latency.txt").resolve()), metadata
        ''')

    def test_removed_bm25_variant_does_not_silently_change_the_scoring_algorithm(self):
        self.run_script('''
            import jieba
            from llama_index.core.schema import TextNode
            from easyrag.custom.retrievers import BM25Retriever

            try:
                BM25Retriever.from_defaults(
                    nodes=[TextNode(text="paymentservice timeout")],
                    tokenizer=jieba.Tokenizer(), bm25_type=1)
            except ValueError:
                pass
            else:
                raise AssertionError("removed BM25 variant must be rejected explicitly")
        ''')


if __name__ == "__main__":
    unittest.main()
