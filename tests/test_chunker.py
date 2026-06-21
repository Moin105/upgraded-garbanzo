from __future__ import annotations

from pathlib import Path

import pytest

from karst.analyze import analyze_repo
from karst.chunker import chunk_file
from karst.models import ChunkKind
from karst.parser import ParserRegistry, parse_file

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def registry() -> ParserRegistry:
    return ParserRegistry()


def test_python_chunks(registry: ParserRegistry) -> None:
    parsed = parse_file(FIXTURES / "sample.py", repo_root=FIXTURES, registry=registry)
    assert parsed is not None
    chunks = chunk_file(parsed)

    by_qname = {c.qualified_name: c for c in chunks}

    assert "top_level_fn" in by_qname
    assert by_qname["top_level_fn"].kind == ChunkKind.FUNCTION

    assert "Greeter" in by_qname
    assert by_qname["Greeter"].kind == ChunkKind.CLASS

    assert "Greeter.__init__" in by_qname
    assert by_qname["Greeter.__init__"].kind == ChunkKind.METHOD
    assert by_qname["Greeter.__init__"].parent == "Greeter"

    assert "Greeter.greet" in by_qname
    assert by_qname["Greeter.greet"].kind == ChunkKind.METHOD


def test_typescript_chunks(registry: ParserRegistry) -> None:
    parsed = parse_file(FIXTURES / "sample.ts", repo_root=FIXTURES, registry=registry)
    assert parsed is not None
    chunks = chunk_file(parsed)
    by_qname = {c.qualified_name: c for c in chunks}

    assert "User" in by_qname
    assert by_qname["User"].kind == ChunkKind.INTERFACE

    assert "makeUser" in by_qname
    assert by_qname["makeUser"].kind == ChunkKind.FUNCTION

    assert "UserService" in by_qname
    assert by_qname["UserService"].kind == ChunkKind.CLASS

    assert "UserService.add" in by_qname
    assert by_qname["UserService.add"].kind == ChunkKind.METHOD
    assert "UserService.get" in by_qname


def test_chunks_carry_line_citations(registry: ParserRegistry) -> None:
    parsed = parse_file(FIXTURES / "sample.py", repo_root=FIXTURES, registry=registry)
    assert parsed is not None
    chunks = chunk_file(parsed)

    for chunk in chunks:
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert chunk.file_relpath == "sample.py"
        assert chunk.file_sha
        assert chunk.chunk_id.startswith("chunk_")
        assert chunk.citation == f"sample.py:{chunk.start_line}-{chunk.end_line}"


def test_analyze_repo_streams_results() -> None:
    results = list(analyze_repo(FIXTURES))
    assert len(results) >= 2  # at least sample.py and sample.ts
    langs = {r.parsed.language for r in results}
    assert {"python", "typescript"}.issubset(langs)
    total_chunks = sum(len(r.chunks) for r in results)
    assert total_chunks >= 6


def test_giant_function_is_capped(tmp_path: Path, registry: ParserRegistry) -> None:
    """A huge function must not become one enormous chunk (retrieval-cost guard)."""
    from karst.chunker import _MAX_CHUNK_CHARS

    body = "\n".join(f"    x{i} = {i}" for i in range(4000))  # ~48k chars
    big = tmp_path / "big.py"
    big.write_text(f"def huge():\n{body}\n", encoding="utf-8")

    parsed = parse_file(big, repo_root=tmp_path, registry=registry)
    assert parsed is not None
    chunks = chunk_file(parsed)
    huge = next(c for c in chunks if c.qualified_name == "huge")

    # code is capped (cap + a short truncation marker), but the line range still
    # spans the whole definition so the citation is honest.
    assert len(huge.code) <= _MAX_CHUNK_CHARS + 200
    assert "truncated" in huge.code
    assert huge.end_line - huge.start_line > 1000
