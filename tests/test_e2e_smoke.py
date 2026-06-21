"""Real end-to-end smoke test: index a tiny repo with the ACTUAL embedder, then
ask a question and check retrieval works.

Skipped unless KARST_E2E=1, because it loads the FastEmbed ONNX model (and
downloads it on first run). This is the full-pipeline path the fake-vector unit
tests can't cover — it's what catches integration bugs like the embedder
batch-size OOM. CI runs it with a cached model dir.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KARST_E2E"),
    reason="real-embedder e2e; set KARST_E2E=1 to run",
)


def _model_cache() -> Path | None:
    raw = os.environ.get("KARST_TEST_MODEL_CACHE")
    return Path(raw).expanduser() if raw else None


def test_index_then_ask_real_embedder(tmp_path: Path) -> None:
    from karst.ask import ask
    from karst.indexer import index_repo

    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "math_ops.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (pkg / "main.py").write_text(
        "from pkg.math_ops import add\n\n\ndef run():\n    return add(2, 3)\n",
        encoding="utf-8",
    )
    storage = tmp_path / "index"
    cache = _model_cache()

    result = index_repo(repo, storage_path=storage, embedder_cache_dir=cache)
    assert result.chunks > 0, "indexing produced no chunks"

    answer = ask(
        "how are two numbers added together?",
        storage_path=storage,
        use_llm=False,
        embedder_cache_dir=cache,
    )
    assert answer.hits, "expected at least one retrieved chunk"
    assert any("add" in h.chunk.qualified_name for h in answer.hits), (
        "the add() function should surface for an addition query"
    )
