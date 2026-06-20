from __future__ import annotations

from pathlib import Path

from karst.embedding_cache import EmbeddingCache
from karst.manifest import (
    FileEntry,
    Manifest,
    file_sha,
    load_manifest,
    save_manifest,
)


def test_manifest_roundtrip(tmp_path: Path) -> None:
    m = Manifest(embedding_model="bge-small")
    m.files["a.py"] = FileEntry(sha="abc", chunk_count=3, indexed_at="2026-06-20T00:00:00+00:00")
    m.files["b.ts"] = FileEntry(sha="def", chunk_count=5)
    save_manifest(tmp_path, m)

    loaded = load_manifest(tmp_path)
    assert loaded.embedding_model == "bge-small"
    assert loaded.files["a.py"].sha == "abc"
    assert loaded.files["a.py"].chunk_count == 3
    assert loaded.files["b.ts"].chunk_count == 5


def test_manifest_missing_file_returns_default(tmp_path: Path) -> None:
    m = load_manifest(tmp_path)
    assert m.files == {}
    assert m.embedding_model is None


def test_file_sha_stable(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hello world", encoding="utf-8")
    s1 = file_sha(p)
    s2 = file_sha(p)
    assert s1 == s2
    assert len(s1) == 40  # sha1 hex length

    p.write_text("hello world!", encoding="utf-8")
    s3 = file_sha(p)
    assert s1 != s3


def test_embedding_cache_hit(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "ec.sqlite")
    text = "some chunk of code"
    sha = EmbeddingCache.text_sha(text)
    vec = [0.1, 0.2, 0.3, 0.4]
    cache.put_many("bge-small", [(sha, vec)])

    hits = cache.get_many("bge-small", [sha])
    # float32 round-trip has ~1e-7 precision loss; compare with tolerance.
    rt = hits[sha]
    assert len(rt) == len(vec)
    for a, b in zip(rt, vec, strict=True):
        assert abs(a - b) < 1e-6


def test_embedding_cache_per_model_isolation(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "ec.sqlite")
    sha = EmbeddingCache.text_sha("text")
    cache.put_many("model-a", [(sha, [1.0, 0.0])])
    cache.put_many("model-b", [(sha, [0.0, 1.0])])

    a = cache.get_many("model-a", [sha])
    b = cache.get_many("model-b", [sha])
    assert a[sha] == [1.0, 0.0]
    assert b[sha] == [0.0, 1.0]


def test_embedding_cache_miss(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "ec.sqlite")
    hits = cache.get_many("bge-small", ["does-not-exist"])
    assert hits == {}


def test_embedding_cache_chunked_query(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "ec.sqlite")
    shas = [f"sha-{i:04d}" for i in range(1200)]  # > sqlite chunk size of 500
    for s in shas:
        cache.put_many("m", [(s, [0.5, 0.5])])
    hits = cache.get_many("m", shas)
    assert len(hits) == 1200
