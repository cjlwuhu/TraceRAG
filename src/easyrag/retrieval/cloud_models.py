"""Small, fail-closed DashScope adapters; secrets never enter experiment models."""

import math
import os
from pathlib import Path
import re
from urllib.parse import urlparse

import httpx
import numpy as np


class CloudModelError(RuntimeError):
    """A deliberately sanitized exception safe for API/logging boundaries."""

    def __init__(self, message, *, code=None, service=None, status=None):
        super().__init__(message)
        self.code, self.service, self.status = code, service, status


class CloudBackendUnavailable(CloudModelError):
    """The server has not enabled the requested backend."""


def read_api_key(key_file=None):
    configured = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if configured:
        if not re.fullmatch(r"sk-[A-Za-z0-9_-]{10,}", configured):
            raise CloudModelError("DASHSCOPE_API_KEY has an unsupported format")
        return configured
    location = key_file or os.getenv("EASYRAG_DASHSCOPE_KEY_FILE")
    if not location:
        raise CloudModelError("Configure DASHSCOPE_API_KEY or EASYRAG_DASHSCOPE_KEY_FILE")
    try:
        text = Path(location).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise CloudModelError("Cannot read the configured credential file") from None
    candidates = set(re.findall(r"sk-[A-Za-z0-9_-]{10,}", text))
    if len(candidates) != 1:
        raise CloudModelError("Credential file must contain exactly one distinct API key")
    return candidates.pop()


def validate_vectors(values, expected_count, dimension):
    if not isinstance(values, list) or len(values) != expected_count:
        raise CloudModelError("Embedding response count mismatch")
    for row in values:
        if not isinstance(row, list) or len(row) != dimension:
            raise CloudModelError("Embedding response dimension mismatch")
        if any(type(x) not in (float, int) or not math.isfinite(x) for x in row):
            raise CloudModelError("Embedding response contains invalid numeric values")
    array = np.asarray(values, dtype=np.float64)
    norms = np.linalg.norm(array, axis=1)
    if not np.isfinite(norms).all() or (norms <= 1e-15).any():
        raise CloudModelError("Embedding response contains zero or overflowing vectors")
    # Cosine retrieval uses these exact cached float32, unit-normalized vectors.
    return (array / norms[:, None]).astype(np.float32)


class DashScopeModels:
    def __init__(self, *, api_host="https://dashscope.aliyuncs.com", key_file=None,
                 proxy=None, timeout_seconds=30, transport=None, api_key=None):
        parsed = urlparse(api_host)
        allowed = parsed.hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com"}
        allowed = allowed or bool(re.fullmatch(r"[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1|us-east-1)\.maas\.aliyuncs\.com", parsed.hostname or ""))
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or parsed.path not in ("", "/"):
            raise ValueError("Cloud host must be a supported HTTPS Alibaba API host, without a path or credentials")
        self.api_host = api_host.rstrip("/")
        self.key_file = key_file
        self.proxy = proxy
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._api_key = api_key

    def _post(self, path, payload):
        key = self._api_key or read_api_key(self.key_file)
        try:
            with httpx.Client(timeout=self.timeout_seconds, proxy=self.proxy, transport=self.transport,
                              trust_env=False, follow_redirects=False) as client:
                response = client.post(self.api_host + path, json=payload,
                                       headers={"Authorization": "Bearer " + key})
        except httpx.HTTPError as exc:
            raise CloudModelError(f"Cloud model network request failed ({type(exc).__name__}); check connectivity/proxy",
                                  code="timeout" if isinstance(exc, httpx.TimeoutException) else "network",
                                  service="Qwen / DashScope") from None
        # Never attach the provider body, request object or headers to an exception.
        if response.status_code != 200:
            raise CloudModelError(f"Cloud model returned HTTP {response.status_code}",
                                  code="provider_http", service="Qwen / DashScope", status=response.status_code)
        try:
            body = response.json()
        except ValueError:
            raise CloudModelError("Cloud model returned invalid JSON") from None
        if not isinstance(body, dict):
            raise CloudModelError("Cloud model returned an invalid response object")
        return body

    @staticmethod
    def _validate_texts(texts):
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("Model inputs must be nonempty strings")
        # Conservative byte budget, not a claim to implement the model's tokenizer.
        if any(len(t.encode("utf-8")) > 8000 for t in texts):
            raise ValueError("Model input exceeds the 8000 UTF-8 byte safety budget; no silent truncation")

    def embed(self, texts, *, model="text-embedding-v4", dimension=1024, text_type="document"):
        if model != "text-embedding-v4" or dimension not in {64, 128, 256, 512, 768, 1024, 1536, 2048}:
            raise ValueError("Unsupported embedding model/dimension")
        if text_type not in {"query", "document"}:
            raise ValueError("Embedding text_type must be query or document")
        self._validate_texts(texts)
        rows = []
        for offset in range(0, len(texts), 10):
            batch = texts[offset:offset + 10]
            body = self._post("/api/v1/services/embeddings/text-embedding/text-embedding",
                              {"model": model, "input": {"texts": batch},
                               "parameters": {"dimension": dimension, "text_type": text_type}})
            embeddings = body.get("output", {}).get("embeddings") if isinstance(body.get("output"), dict) else None
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise CloudModelError("Embedding response count mismatch")
            indexed = {}
            for item in embeddings:
                index = item.get("text_index") if isinstance(item, dict) else None
                if type(index) is not int or index not in range(len(batch)) or index in indexed:
                    raise CloudModelError("Embedding response indexes are invalid")
                indexed[index] = item.get("embedding")
            rows.extend(indexed[i] for i in range(len(batch)))
        if not texts:
            return np.empty((0, dimension), dtype=np.float32)
        return validate_vectors(rows, len(texts), dimension)

    def rerank(self, query, documents, *, model="gte-rerank-v2"):
        if model != "gte-rerank-v2":
            raise ValueError("Unsupported reranker model")
        self._validate_texts([query, *documents])
        if len(documents) > 100:
            raise ValueError("Rerank candidate budget is 100 documents")
        if not documents:
            return []
        body = self._post("/api/v1/services/rerank/text-rerank/text-rerank",
                          {"model": model, "input": {"query": query, "documents": documents},
                           "parameters": {"top_n": len(documents), "return_documents": False}})
        results = body.get("output", {}).get("results") if isinstance(body.get("output"), dict) else None
        if not isinstance(results, list) or len(results) != len(documents):
            raise CloudModelError("Rerank response count mismatch")
        scores = {}
        for item in results:
            if not isinstance(item, dict):
                raise CloudModelError("Rerank response item is invalid")
            index, score = item.get("index"), item.get("relevance_score")
            if type(index) is not int or index not in range(len(documents)) or index in scores:
                raise CloudModelError("Rerank response indexes are invalid")
            if type(score) not in (float, int) or not math.isfinite(score):
                raise CloudModelError("Rerank response score is invalid")
            scores[index] = float(score)
        return [scores[i] for i in range(len(documents))]
