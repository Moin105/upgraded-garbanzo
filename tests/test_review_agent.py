"""End-to-end review agent test with a mock LLM.

We index a tiny in-memory repo, parse a hand-written diff against it, and run
the reviewer with a MockLLM that returns canned findings. Exercises:
- diff parsing
- containing-chunk lookup from Qdrant by file path + line range
- structured-output dispatch (mock)
- hallucination clamp (out-of-diff finding is dropped)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from coderchecker.analyze import analyze_repo
from coderchecker.embedder import EmbeddedChunk
from coderchecker.llm import LLM, LLMResponse
from coderchecker.models import Chunk
from coderchecker.review.agent import review_diff
from coderchecker.review.diff import parse_diff
from coderchecker.review.findings import Severity
from coderchecker.store import ChunkStore

VECTOR_DIM = 8


def _fake_vector(text: str) -> list[float]:
    d = hashlib.sha1(text.encode()).digest()[:VECTOR_DIM]
    v = [b / 255.0 for b in d]
    mag = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / mag for x in v]


class MockLLM(LLM):
    provider = "mock"
    model = "mock-v1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text="ok", provider=self.provider, model=self.model)

    def generate_structured(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        tool_name: str = "report",
        tool_description: str = "",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.payload


@pytest.fixture
def indexed_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChunkStore:
    """Index the test fixtures with fake vectors so we don't load fastembed."""
    fixtures = Path(__file__).parent / "fixtures"
    chunks: list[Chunk] = []
    for result in analyze_repo(fixtures):
        chunks.extend(result.chunks)

    store = ChunkStore(location=tmp_path / "store", collection="t")
    store.ensure_collection(vector_size=VECTOR_DIM)
    store.upsert(EmbeddedChunk(chunk=c, vector=_fake_vector(c.code)) for c in chunks)
    return store


def _diff_against_sample_py() -> str:
    """Pretend we modified Greeter.greet inside sample.py.

    sample.py has Greeter.greet at lines 13-14 in the fixture; we tweak the
    return expression and add a print.
    """
    return """\
diff --git a/sample.py b/sample.py
--- a/sample.py
+++ b/sample.py
@@ -12,3 +12,4 @@ class Greeter:

     def greet(self) -> str:
-        return f"hello, {self.name}"
+        print("greeting")  # noisy log on hot path
+        return f"hi, {self.name}"
"""


def test_review_returns_in_diff_findings_and_clamps_out_of_diff(
    indexed_store: ChunkStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = _diff_against_sample_py()
    parsed = parse_diff(diff)
    assert parsed.reviewable_files()

    canned = {
        "findings": [
            {
                "file": "sample.py",
                "line": 14,
                "severity": "medium",
                "category": "performance",
                "message": "print() in a hot path is wasteful logging.",
                "fix": "Use the project logger.",
            },
            # Hallucinated: line 99 is not in the diff — must be clamped out.
            {
                "file": "sample.py",
                "line": 99,
                "severity": "low",
                "category": "style",
                "message": "imaginary finding outside the diff",
            },
            # Hallucinated file path — must be clamped out.
            {
                "file": "ghost.py",
                "line": 3,
                "severity": "low",
                "category": "style",
                "message": "no such file in this diff",
            },
        ]
    }
    mock = MockLLM(canned)

    # The store from the fixture is open; agent re-opens by path, so close ours
    # first so qdrant's local lock is free.
    storage_path = indexed_store._location
    indexed_store.close()

    result = review_diff(
        parsed,
        storage_path=storage_path,
        collection="t",
        llm=mock,
        use_semantic_neighbors=False,
    )

    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.file == "sample.py"
    assert f.line == 14
    assert f.severity == Severity.MEDIUM
    assert mock.calls, "LLM should have been invoked"


def test_review_handles_no_findings(
    indexed_store: ChunkStore, tmp_path: Path
) -> None:
    diff = _diff_against_sample_py()
    parsed = parse_diff(diff)
    mock = MockLLM({"findings": []})

    storage_path = indexed_store._location
    indexed_store.close()

    result = review_diff(
        parsed,
        storage_path=storage_path,
        collection="t",
        llm=mock,
        use_semantic_neighbors=False,
    )
    assert result.findings == []
    assert result.files_reviewed == 1
