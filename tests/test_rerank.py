"""Model-free hybrid re-rank: dense order fused with a lexical identifier/path
score (RRF). Guards that exact-name queries surface the right chunk, and that
pure-semantic queries are never hurt."""
from __future__ import annotations

from karst.models import Chunk, ChunkKind
from karst.store import SearchHit, _rrf_rerank


def _hit(name: str, path: str, score: float) -> SearchHit:
    chunk = Chunk(
        file_relpath=path,
        language="python",
        kind=ChunkKind.FUNCTION,
        name=name,
        qualified_name=name,
        start_line=1,
        end_line=2,
        start_byte=0,
        end_byte=1,
        code="pass",
        file_sha="x",
        parent=None,
        signature=name,
    )
    return SearchHit(chunk=chunk, score=score)


def test_rerank_boosts_identifier_path_match() -> None:
    # Dense order puts an irrelevant chunk first; several real matches (path
    # tokens "mcp"/"server") follow. RRF should pull a real match to the top —
    # the reinforcing lexical hits sink the distractor's fused rank.
    hits = [
        _hit("CompiledPack", "packs/tagger.py", 0.70),   # dense #0, no lexical match
        _hit("main", "mcp_server.py", 0.69),             # dense #1, lexical match
        _hit("run_http", "mcp_server.py", 0.68),         # dense #2, lexical match
        _hit("search_code", "mcp_server.py", 0.67),      # dense #3, lexical match
    ]
    reranked = _rrf_rerank(hits, "how does the mcp server expose tools")
    assert reranked[0].chunk.file_relpath == "mcp_server.py"


def test_rerank_noop_without_lexical_signal() -> None:
    # No query term matches any name/path/body → dense order preserved.
    hits = [_hit("alpha", "a.py", 0.9), _hit("beta", "b.py", 0.8)]
    out = _rrf_rerank(hits, "zzz qqq vvvthing")
    assert [h.chunk.name for h in out] == ["alpha", "beta"]
