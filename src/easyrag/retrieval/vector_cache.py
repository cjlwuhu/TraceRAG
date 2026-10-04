"""Content-addressed SQLite vector cache; no pickle or credential persistence."""

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import threading

import numpy as np

from easyrag.retrieval.cloud_models import CloudModelError
from easyrag.retrieval.evidence_pack import fingerprint


class CachedEmbeddings:
    def __init__(self, models, cache_path):
        self.models = models
        self.cache_path = Path(cache_path)
        self._lock = threading.RLock()

    @staticmethod
    def _read(connection, key, dimension):
        row = connection.execute("SELECT dimension, vector, sha256 FROM vectors WHERE cache_key=?", (key,)).fetchone()
        if row is None:
            return None
        width, blob, digest = row
        if width != dimension or len(blob) != dimension * 4 or hashlib.sha256(blob).hexdigest() != digest:
            raise CloudModelError("Embedding cache integrity check failed")
        vector = np.frombuffer(blob, dtype="<f4").copy()
        if not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1, atol=1e-5):
            raise CloudModelError("Embedding cache vector is invalid")
        return vector

    def encode(self, texts, *, model, dimension, text_type):
        if not texts:
            return np.empty((0, dimension), dtype=np.float32), {"requested": 0, "misses": 0}
        keys = [fingerprint({"version": "normalized-f32-v1", "host": self.models.api_host,
                             "model": model, "dimension": dimension, "text_type": text_type, "text": t}) for t in texts]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(sqlite3.connect(self.cache_path, timeout=30)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS vectors (cache_key TEXT PRIMARY KEY, dimension INTEGER NOT NULL, vector BLOB NOT NULL, sha256 TEXT NOT NULL)")
            found = {}
            for key in set(keys):
                vector = self._read(connection, key, dimension)
                if vector is not None:
                    found[key] = vector
            missing = dict((key, text) for key, text in zip(keys, texts) if key not in found)
            if missing:
                vectors = self.models.embed(list(missing.values()), model=model, dimension=dimension, text_type=text_type)
                if (vectors.shape != (len(missing), dimension) or not np.isfinite(vectors).all()
                        or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)):
                    raise CloudModelError("Embedding backend returned invalid normalized vectors")
                for key, vector in zip(missing, vectors):
                    blob = np.asarray(vector, dtype="<f4").tobytes()
                    connection.execute("INSERT OR IGNORE INTO vectors VALUES (?, ?, ?, ?)",
                                       (key, dimension, blob, hashlib.sha256(blob).hexdigest()))
                connection.commit()
                # Another process may have inserted the same key first. Return exactly
                # what is persisted, not a losing concurrent provider response.
                for key in missing:
                    found[key] = self._read(connection, key, dimension)
            result = np.stack([found[key] for key in keys])
        return result, {"requested": len(texts), "misses": len(missing)}
