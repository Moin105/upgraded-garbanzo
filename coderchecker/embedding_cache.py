"""Content-hash → vector cache.

Sits between the indexer and the embedder. On every chunk we want to embed,
we hash the text payload and check sqlite first; on miss, embed and store.
This sidesteps wasted compute when the same code lives at a different path,
or when a file is moved (its chunks' text is unchanged → cache hits).

Stored as raw float32 little-endian bytes for compactness. A 384-dim vector
is 1.5 KB, so a 5000-chunk repo's cache is ~7 MB — trivially small.

Per-model isolation: cache rows are keyed by (model_name, text_sha) so
mixing embedding models doesn't return wrong-dim vectors.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

CACHE_FILENAME = "embedding_cache.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    model    TEXT NOT NULL,
    sha      TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL,
    PRIMARY KEY (model, sha)
);
"""


def cache_path(storage_dir: str | Path) -> Path:
    return Path(storage_dir) / CACHE_FILENAME


class EmbeddingCache:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    @staticmethod
    def text_sha(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()

    @contextmanager
    def _txn(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get_many(self, model: str, shas: Iterable[str]) -> dict[str, list[float]]:
        """Return {sha: vector} for every sha present in the cache."""
        ids = list(set(shas))
        if not ids:
            return {}
        # sqlite has a 999-parameter limit; chunk our query.
        out: dict[str, list[float]] = {}
        with self._connect() as conn:
            for i in range(0, len(ids), 500):
                window = ids[i : i + 500]
                placeholders = ",".join("?" for _ in window)
                rows = conn.execute(
                    f"SELECT sha, dim, vector FROM vectors WHERE model = ? AND sha IN ({placeholders})",
                    (model, *window),
                ).fetchall()
                for sha, dim, blob in rows:
                    out[sha] = _decode_vector(blob, dim)
        return out

    def put_many(
        self, model: str, items: Iterable[tuple[str, list[float]]]
    ) -> int:
        rows = []
        for sha, vec in items:
            rows.append((model, sha, len(vec), _encode_vector(vec)))
        if not rows:
            return 0
        with self._txn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO vectors (model, sha, dim, vector) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)


def _encode_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _decode_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))
