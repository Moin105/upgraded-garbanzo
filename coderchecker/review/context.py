"""Per-hunk context fetching.

For each changed hunk in a diff, we want two flavors of surrounding code:

1. Containing chunks — the function/class that physically contains the new
   lines. These come from Qdrant via payload filtering on file_relpath and
   line-range overlap.
2. Semantic neighbors — chunks elsewhere in the repo that look similar to
   the new code. These come from vector search on the hunk body.

The reviewer needs both: containing context to judge correctness in-place,
semantic neighbors to spot inconsistency with how the same thing is done
elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import Chunk
from ..store import ChunkStore, SearchHit
from .diff import FileChange, Hunk

if TYPE_CHECKING:
    from ..embedder import Embedder


@dataclass
class HunkContext:
    file: FileChange
    hunk: Hunk
    containing: list[Chunk] = field(default_factory=list)
    neighbors: list[SearchHit] = field(default_factory=list)


def fetch_context(
    file: FileChange,
    *,
    store: ChunkStore,
    embedder: "Embedder | None" = None,
    neighbor_k: int = 4,
) -> list[HunkContext]:
    out: list[HunkContext] = []
    for hunk in file.hunks:
        ctx = HunkContext(file=file, hunk=hunk)
        ctx.containing = _containing_chunks(store, file.path, hunk)
        if embedder is not None and hunk.body:
            ctx.neighbors = _semantic_neighbors(
                store, embedder, hunk.body, exclude_file=file.path, k=neighbor_k
            )
        out.append(ctx)
    return out


def _containing_chunks(
    store: ChunkStore, file_path: str, hunk: Hunk
) -> list[Chunk]:
    """Pull chunks whose [start_line, end_line] overlaps the hunk window."""
    from qdrant_client.http import models as qm

    client = store._client  # internal access — store wraps this for us
    flt = qm.Filter(
        must=[
            qm.FieldCondition(
                key="file_relpath", match=qm.MatchValue(value=file_path)
            ),
            qm.FieldCondition(
                key="start_line", range=qm.Range(lte=hunk.new_end)
            ),
            qm.FieldCondition(
                key="end_line", range=qm.Range(gte=hunk.new_start)
            ),
        ]
    )
    # scroll returns (points, next_offset)
    points, _ = client.scroll(
        collection_name=store.collection,
        scroll_filter=flt,
        with_payload=True,
        limit=16,
    )

    from ..store import _chunk_from_payload  # local import — module-private helper

    out: list[Chunk] = []
    for p in points:
        chunk = _chunk_from_payload(p.payload or {})
        if chunk is not None:
            out.append(chunk)
    # Inner chunks first (methods before classes) — most-specific context wins.
    out.sort(key=lambda c: (c.end_line - c.start_line, c.start_line))
    return out


def _semantic_neighbors(
    store: ChunkStore,
    embedder: "Embedder",
    query_text: str,
    *,
    exclude_file: str,
    k: int = 4,
) -> list[SearchHit]:
    (vec,) = embedder.embed_texts([query_text])
    # Fetch a few extra so we can filter out same-file matches without going
    # under k.
    hits = store.search(vec, limit=k * 2)
    return [h for h in hits if h.chunk.file_relpath != exclude_file][:k]
