"""GraphRAG retrieval (spec §24).

Plain vector RAG retrieves chunks by similarity. GraphRAG augments that with
the dependency-aware neighborhood: for each top vector hit, walk a few
outgoing edges (callers, callees, containing class) and pull those chunks
into the result set too.

For code, this is the right move — "what calls getUser?" is a graph
question, not an embedding question.

Public API:
  expand_with_graph(seed_hits, graph, qdrant) -> list[GraphHit]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..models import Chunk
from ..store import ChunkStore, SearchHit
from .store import EdgeKind, GraphStore, NodeKind


# Edges to follow when expanding a vector hit. CONTAINS gives architectural
# context (the class around the method); CALLS surfaces the dependency
# neighborhood; IMPLEMENTS pulls in the interface/base a class declares.
_DEFAULT_EXPAND_KINDS: tuple[EdgeKind, ...] = (
    EdgeKind.CALLS,
    EdgeKind.CONTAINS,
    EdgeKind.IMPLEMENTS,
)


@dataclass
class GraphHit:
    chunk: Chunk
    score: float
    source: str                  # "vector" or "graph"
    via: str | None = None       # the seed chunk_id that led us here (graph only)
    edge: EdgeKind | None = None
    depth: int = 0


def expand_with_graph(
    seed_hits: list[SearchHit],
    *,
    graph: GraphStore,
    qdrant: ChunkStore,
    expand_kinds: Iterable[EdgeKind] = _DEFAULT_EXPAND_KINDS,
    max_extra: int = 8,
    max_depth: int = 1,
) -> list[GraphHit]:
    """Take vector hits and add the most relevant graph-adjacent chunks.

    Graph-added hit scores: parent seed score * 0.7^depth. We don't re-embed
    the new chunks — that would defeat the speed advantage of GraphRAG over
    just running a second vector search.
    """
    out: list[GraphHit] = [
        GraphHit(chunk=h.chunk, score=h.score, source="vector") for h in seed_hits
    ]
    seen: set[str] = {h.chunk.chunk_id for h in seed_hits}

    # Collect (node_id, depth, parent_seed_id, edge_kind) for each unique extra.
    additions: dict[str, tuple[int, str, EdgeKind | None]] = {}
    expand_kinds_tuple = tuple(expand_kinds)

    for seed in seed_hits:
        seed_id = seed.chunk.chunk_id
        if not graph.has_node(seed_id):
            continue
        depths = graph.bfs(
            [seed_id],
            direction="out",
            kinds=expand_kinds_tuple,
            max_depth=max_depth,
        )
        for node_id, depth in depths.items():
            if depth == 0 or node_id in seen:
                continue
            node = graph.get_node(node_id)
            if node is None or node.kind in (NodeKind.FILE, NodeKind.MODULE):
                continue
            prior = additions.get(node_id)
            if prior is not None and prior[0] <= depth:
                continue
            edge_kind = _first_edge_kind(graph, seed_id, node_id, expand_kinds_tuple)
            additions[node_id] = (depth, seed_id, edge_kind)

    if not additions:
        return out

    chunks = _fetch_chunks_by_id(qdrant, additions.keys())
    chunk_by_id = {c.chunk_id: c for c in chunks}

    seed_score_by_id = {h.chunk.chunk_id: h.score for h in seed_hits}
    extras: list[GraphHit] = []
    for node_id, (depth, parent_id, edge_kind) in additions.items():
        chunk = chunk_by_id.get(node_id)
        if chunk is None:
            continue
        parent_score = seed_score_by_id.get(parent_id, 0.5)
        score = round(parent_score * (0.7 ** depth), 4)
        extras.append(
            GraphHit(
                chunk=chunk,
                score=score,
                source="graph",
                via=parent_id,
                edge=edge_kind,
                depth=depth,
            )
        )

    extras.sort(key=lambda h: -h.score)
    out.extend(extras[:max_extra])
    return out


def _first_edge_kind(
    graph: GraphStore, src: str, dst: str, kinds: tuple[EdgeKind, ...]
) -> EdgeKind | None:
    for d, k, _ in graph.out_edges(src, kinds=kinds):
        if d == dst:
            return k
    return None


def _fetch_chunks_by_id(qdrant: ChunkStore, chunk_ids: Iterable[str]) -> list[Chunk]:
    """Pull chunk payloads from Qdrant by chunk_id (stored on each payload)."""
    from qdrant_client.http import models as qm

    from ..store import _chunk_from_payload  # module-internal, intentional

    ids = list(chunk_ids)
    if not ids:
        return []
    flt = qm.Filter(
        must=[qm.FieldCondition(key="chunk_id", match=qm.MatchAny(any=ids))]
    )
    points, _ = qdrant._client.scroll(
        collection_name=qdrant.collection,
        scroll_filter=flt,
        with_payload=True,
        limit=max(len(ids), 32),
    )
    out: list[Chunk] = []
    for p in points:
        chunk = _chunk_from_payload(p.payload or {})
        if chunk is not None:
            out.append(chunk)
    return out
