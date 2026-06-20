"""End-to-end test: pack tags cut Qdrant retrieval scope.

Indexes the graph_repo fixture with fake vectors, tags chunks with two
packs, then verifies that search filtered by pack_ids only returns chunks
from that pack — never from other packs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from coderchecker.analyze import analyze_repo
from coderchecker.embedder import EmbeddedChunk
from coderchecker.packs.models import Pack
from coderchecker.packs.tagger import compile_packs, tag_relpath
from coderchecker.store import ChunkStore

FIXTURES = Path(__file__).parent / "fixtures" / "graph_repo"
DIM = 8


def _fake_vector(text: str) -> list[float]:
    d = hashlib.sha1(text.encode()).digest()[:DIM]
    v = [b / 255.0 for b in d]
    mag = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / mag for x in v]


def test_pack_filter_scopes_retrieval(tmp_path: Path) -> None:
    chunks = []
    for r in analyze_repo(FIXTURES):
        chunks.extend(r.chunks)
    assert chunks

    pack_auth = Pack(id="pack_auth", label="Auth", scope=["auth.py"])
    pack_users = Pack(id="pack_users", label="Users", scope=["users.py"])
    compiled = compile_packs([pack_auth, pack_users])

    def tagger(relpath: str) -> list[str]:
        return tag_relpath(compiled, relpath)

    store = ChunkStore(location=tmp_path / "store", collection="t")
    store.ensure_collection(vector_size=DIM)
    store.upsert(
        (EmbeddedChunk(chunk=c, vector=_fake_vector(c.code)) for c in chunks),
        pack_tagger=tagger,
    )

    # Pick a query vector (any chunk's vector — we just need a target).
    query = _fake_vector(chunks[0].code)

    # Without pack filter: returns chunks from any file.
    raw = store.search(query, limit=12)
    raw_files = {h.chunk.file_relpath for h in raw}
    assert "auth.py" in raw_files
    assert "users.py" in raw_files
    assert "billing.py" in raw_files

    # With pack_auth filter: ONLY auth.py chunks come back.
    auth_only = store.search(query, limit=12, pack_ids=["pack_auth"])
    assert auth_only, "auth pack should return at least one chunk"
    auth_files = {h.chunk.file_relpath for h in auth_only}
    assert auth_files == {"auth.py"}, f"got files {auth_files}"

    # Combining packs union them.
    both = store.search(query, limit=12, pack_ids=["pack_auth", "pack_users"])
    both_files = {h.chunk.file_relpath for h in both}
    assert "auth.py" in both_files
    assert "users.py" in both_files
    assert "billing.py" not in both_files

    store.close()


def test_retag_updates_existing_chunks(tmp_path: Path) -> None:
    chunks = []
    for r in analyze_repo(FIXTURES):
        chunks.extend(r.chunks)

    store = ChunkStore(location=tmp_path / "store", collection="t")
    store.ensure_collection(vector_size=DIM)
    # Initial upsert with NO pack tags (empty tagger).
    store.upsert(EmbeddedChunk(chunk=c, vector=_fake_vector(c.code)) for c in chunks)

    query = _fake_vector(chunks[0].code)
    before = store.search(query, limit=8, pack_ids=["pack_auth"])
    assert before == [], "no chunks should be tagged before retag"

    # Now add a pack scope and retag everything.
    pack_auth = Pack(id="pack_auth", label="Auth", scope=["auth.py"])
    compiled = compile_packs([pack_auth])

    def tagger(relpath: str) -> list[str]:
        return tag_relpath(compiled, relpath)

    updated = store.retag_with_packs(tagger)
    assert updated > 0

    after = store.search(query, limit=8, pack_ids=["pack_auth"])
    assert after, "after retag, pack_auth should match auth.py chunks"
    assert all(h.chunk.file_relpath == "auth.py" for h in after)

    store.close()
