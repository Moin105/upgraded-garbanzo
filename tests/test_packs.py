from __future__ import annotations

from pathlib import Path

import pytest

from karst.analyze import analyze_repo
from karst.packs.models import Pack
from karst.packs.store import PackStore
from karst.packs.suggest import suggest_packs
from karst.packs.tagger import compile_packs, tag_relpath


def test_pack_store_roundtrip(tmp_path: Path) -> None:
    store = PackStore(tmp_path / "p.sqlite")
    p = Pack(
        id="pack_auth",
        label="Auth",
        scope=["src/auth/**"],
        summary="Auth module",
        token_estimate=1840,
        chunk_count=42,
        auto=True,
    )
    store.upsert(p)

    loaded = store.get("pack_auth")
    assert loaded is not None
    assert loaded.label == "Auth"
    assert loaded.scope == ["src/auth/**"]
    assert loaded.chunk_count == 42
    assert loaded.auto is True

    listing = store.list()
    assert len(listing) == 1
    assert listing[0].id == "pack_auth"


def test_pack_store_upsert_overwrites(tmp_path: Path) -> None:
    store = PackStore(tmp_path / "p.sqlite")
    store.upsert(Pack(id="pack_x", label="X", scope=["a/**"]))
    store.upsert(Pack(id="pack_x", label="X-renamed", scope=["b/**"]))
    p = store.get("pack_x")
    assert p is not None
    assert p.label == "X-renamed"
    assert p.scope == ["b/**"]


def test_pack_store_delete_auto_only(tmp_path: Path) -> None:
    store = PackStore(tmp_path / "p.sqlite")
    store.upsert(Pack(id="pack_a", label="A", scope=["a/**"], auto=True))
    store.upsert(Pack(id="pack_b", label="B", scope=["b/**"], auto=False))
    removed = store.delete_auto()
    assert removed == 1
    assert store.get("pack_a") is None
    assert store.get("pack_b") is not None


def test_pack_slug_normalizes() -> None:
    assert Pack.slug_from_label("Auth Module") == "pack_auth_module"
    assert Pack.slug_from_label("API / Gateway") == "pack_api_gateway"
    # If the label already looks like a pack id, don't double-prefix.
    assert Pack.slug_from_label("pack_already") == "pack_already"


def test_tagger_matches_globs() -> None:
    p1 = Pack(id="pack_auth", label="Auth", scope=["backend/auth/**"])
    p2 = Pack(id="pack_users", label="Users", scope=["backend/users/**"])
    compiled = compile_packs([p1, p2])

    assert tag_relpath(compiled, "backend/auth/login.ts") == ["pack_auth"]
    assert tag_relpath(compiled, "backend/users/users.service.ts") == ["pack_users"]
    assert tag_relpath(compiled, "frontend/index.tsx") == []


def test_suggest_groups_by_top_segments() -> None:
    fixtures = Path(__file__).parent / "fixtures" / "graph_repo"
    chunks = []
    for result in analyze_repo(fixtures):
        chunks.extend(result.chunks)

    # graph_repo has 3 top-level files (users.py, auth.py, billing.py) — no
    # subdirectories. Suggest should produce 0 packs because every file is
    # at the root.
    candidates = suggest_packs(chunks, min_chunks_per_pack=1)
    assert candidates == []


def test_suggest_groups_real_repo_subdirs(tmp_path: Path) -> None:
    # Two distinct top-level dirs (backend/, cms/) so top-1 grouping yields
    # two packs.
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "auth.py").write_text(
        "def login(u):\n    return u\n\ndef logout(u):\n    return None\n\n"
        "def refresh(u):\n    return u\n",
        encoding="utf-8",
    )
    cms = tmp_path / "cms"
    cms.mkdir()
    (cms / "store.py").write_text(
        "def get(u):\n    return u\n\ndef save(u):\n    return u\n\n"
        "def delete(u):\n    return None\n",
        encoding="utf-8",
    )

    chunks = []
    for result in analyze_repo(tmp_path):
        chunks.extend(result.chunks)

    candidates = suggest_packs(chunks, min_chunks_per_pack=2)
    assert len(candidates) >= 2
    labels = {c.pack.label for c in candidates}
    assert "Backend" in labels
    assert "Cms" in labels
