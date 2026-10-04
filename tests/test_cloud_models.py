import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
import numpy as np

from easyrag.retrieval.cloud_models import CloudModelError, DashScopeModels, read_api_key
from easyrag.retrieval.vector_cache import CachedEmbeddings


class CloudModelTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-unit-test-not-a-real-secret"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.calls = []

    def provider(self, handler=None):
        def successful(request):
            body = json.loads(request.content)
            self.calls.append(body)
            dimension = body["parameters"]["dimension"]
            rows = [{"text_index": i, "embedding": [float(i + 1)] * dimension}
                    for i in range(len(body["input"]["texts"]))]
            return httpx.Response(200, json={"output": {"embeddings": list(reversed(rows))}})
        return DashScopeModels(transport=httpx.MockTransport(handler or successful))

    def test_batch_size_query_type_order_and_normalization(self):
        result = self.provider().embed([f"text {i}" for i in range(12)], dimension=64, text_type="query")
        self.assertEqual((12, 64), result.shape)
        self.assertEqual([10, 2], [len(b["input"]["texts"]) for b in self.calls])
        self.assertEqual("query", self.calls[0]["parameters"]["text_type"])
        np.testing.assert_allclose(np.linalg.norm(result, axis=1), 1, atol=1e-6)

    def test_rejects_wrong_count_dimension_indexes_and_zero_vectors(self):
        cases = [[], [{"text_index": 0, "embedding": [1.]}],
                 [{"text_index": True, "embedding": [1.] * 64}],
                 [{"text_index": 0, "embedding": [0.] * 64}],
                 [{"text_index": 0, "embedding": [True] * 64}]]
        for rows in cases:
            model = self.provider(lambda request: httpx.Response(200, json={"output": {"embeddings": rows}}))
            with self.assertRaises(CloudModelError):
                model.embed(["hello"], dimension=64)

    def test_errors_never_echo_provider_body_or_secret(self):
        marker = "PRIVATE_PROVIDER_BODY_123"
        model = self.provider(lambda request: httpx.Response(401, text=marker + os.environ["DASHSCOPE_API_KEY"]))
        with self.assertRaises(CloudModelError) as context:
            model.embed(["hello"], dimension=64)
        self.assertEqual("Cloud model returned HTTP 401", str(context.exception))
        self.assertNotIn(marker, str(context.exception))

    def test_input_limit_and_host_allowlist(self):
        for host in ("http://dashscope.aliyuncs.com", "https://example.com", "https://dashscope.aliyuncs.com/redirect",
                     "https://dashscope.aliyuncs.com?key=secret"):
            with self.assertRaises(ValueError):
                DashScopeModels(api_host=host)
        with self.assertRaises(ValueError):
            self.provider().embed(["中" * 3000], dimension=64)
        self.assertEqual([], self.calls)

    def test_cache_deduplicates_and_keeps_query_document_and_dimension_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = CachedEmbeddings(self.provider(), Path(temporary) / "cache.sqlite")
            a, first = cache.encode(["hello", "hello"], model="text-embedding-v4", dimension=64, text_type="document")
            b, second = cache.encode(["hello"], model="text-embedding-v4", dimension=64, text_type="document")
            self.assertEqual(1, first["misses"])
            self.assertEqual(0, second["misses"])
            np.testing.assert_array_equal(a[0], b[0])
            for role, dim in (("query", 64), ("document", 128)):
                _, stats = cache.encode(["hello"], model="text-embedding-v4", dimension=dim, text_type=role)
                self.assertEqual(1, stats["misses"])
            self.assertEqual(3, len(self.calls))
            self.assertNotIn(b"sk-unit-test", (Path(temporary) / "cache.sqlite").read_bytes())

    def test_corrupt_cache_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cache.sqlite"
            cache = CachedEmbeddings(self.provider(), path)
            cache.encode(["hello"], model="text-embedding-v4", dimension=64, text_type="document")
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("UPDATE vectors SET vector=?", (b"bad",))
                connection.commit()
            with self.assertRaisesRegex(CloudModelError, "integrity"):
                cache.encode(["hello"], model="text-embedding-v4", dimension=64, text_type="document")

    def test_rerank_restores_input_order_and_rejects_duplicate_indexes(self):
        rows = [{"index": 1, "relevance_score": .9}, {"index": 0, "relevance_score": .1}]
        model = self.provider(lambda request: httpx.Response(200, json={"output": {"results": rows}}))
        self.assertEqual([.1, .9], model.rerank("query", ["a", "b"]))
        rows[0]["index"] = 0
        with self.assertRaises(CloudModelError):
            model.rerank("query", ["a", "b"])

    def test_key_file_deduplicates_but_rejects_multiple_keys(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            path = Path(temporary) / "key.txt"
            path.write_text("sk-unit-test-one-key\nsk-unit-test-one-key", encoding="utf-8")
            self.assertEqual("sk-unit-test-one-key", read_api_key(path))
            path.write_text("sk-unit-test-one-key\nsk-unit-test-second-key", encoding="utf-8")
            with self.assertRaises(CloudModelError):
                read_api_key(path)


if __name__ == "__main__":
    unittest.main()
