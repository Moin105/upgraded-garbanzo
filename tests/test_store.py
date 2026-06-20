"""Integration test for the Qdrant local store.

Uses synthetic vectors (no embedding model) so the test is fast and offline.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from karst.analyze import analyze_repo
from karst.embedder import EmbeddedChunk
from karst.models import Chunk
from karst.store import ChunkStore

FIXTURES = Path(__file__).parent / "fixtures"
VECTOR_DIM = 8


def _fake_vector(chunk: Chunk) -> list[float]:
    """Deterministic per-chunk vector — same chunk hashes to the same point."""
    digest = hashlib.sha1(chunk.chunk_id.encode()).digest()
    floats = [b / 255.0 for b in digest[:VECTOR_DIM]]
    # Normalize so cosine distance is well-behaved.
    mag = sum(f * f for f in floats) ** 0.5 or 1.0
    return [f / mag for f in floats]


def _chunks_from_fixtures() -> list[Chunk]:
    out: list[Chunk] = []
    for result in analyze_repo(FIXTURES):
        out.extend(result.chunks)
    return out


def test_upsert_and_search_roundtrip(tmp_path: Path) -> None:
    chunks = _chunks_from_fixtures()
    assert chunks, "fixture analyze produced no chunks"

    store = ChunkStore(location=tmp_path / "store", collection="t")
    try:
        store.ensure_collection(vector_size=VECTOR_DIM)
        n = store.upsert(EmbeddedChunk(chunk=c, vector=_fake_vector(c)) for c in chunks)
        assert n == len(chunks)
        assert store.count() == len(chunks)

        # Search with one chunk's own vector — it must come back as a top hit.
        target = chunks[0]
        hits = store.search(_fake_vector(target), limit=3)
        assert hits, "expected at least one hit"
        assert hits[0].chunk.chunk_id == target.chunk_id
        assert hits[0].score == pytest.approx(1.0, abs=1e-3)

        # Payload round-trip preserves citation metadata.
        rt = hits[0].chunk
        assert rt.file_relpath == target.file_relpath
        assert rt.start_line == target.start_line
        assert rt.end_line == target.end_line
        assert rt.qualified_name == target.qualified_name
        assert rt.kind == target.kind
    finally:
        store.close()


def test_reset_collection_clears(tmp_path: Path) -> None:
    chunks = _chunks_from_fixtures()
    store = ChunkStore(location=tmp_path / "store", collection="t")
    try:
        store.ensure_collection(vector_size=VECTOR_DIM)
        store.upsert(EmbeddedChunk(chunk=c, vector=_fake_vector(c)) for c in chunks)
        assert store.count() > 0

        store.reset_collection(vector_size=VECTOR_DIM)
        assert store.count() == 0
    finally:
        store.close()
