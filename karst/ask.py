"""Repo Q&A — question in, cited answer out.

Pipeline:
  question → embed → Qdrant top-k → assemble prompt → LLM → answer

Citation discipline (spec §33): the prompt forces the model to anchor every
claim to `file:start-end`. If no LLM is configured, callers can render the
retrieved hits directly — still useful, just no prose synthesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .embedder import DEFAULT_MODEL, Embedder
from .llm import LLM, LLMResponse, default_llm
from .store import DEFAULT_COLLECTION, ChunkStore, SearchHit


@dataclass
class _LabeledHit:
    """Internal: a SearchHit with a source label ('vector' or 'graph')."""
    hit: SearchHit
    source: str

# How long any single retrieved chunk is allowed to be inside the prompt.
# Beyond this we cut — the citation still points at the full file.
_MAX_CHUNK_CHARS = 2_000


@dataclass
class AskResult:
    question: str
    hits: list[SearchHit]
    answer: str | None
    llm: LLMResponse | None


SYSTEM_PROMPT = """\
You are an AI Staff Engineer answering questions about a specific code repository.

You are given the user's question and the top retrieved chunks of code from
the repo. Each chunk header looks like [N] path/to/file.ts:start-end. You must:

1. Answer concisely and concretely.
2. Cite every claim with a bracketed reference in the form [path/to/file.ts:start-end].
   Use the same path/range shown in the chunk header. Never invent files or line ranges.
3. If the retrieved chunks do not contain enough information, say so plainly and
   suggest what to look at next. Do not guess.
4. Prefer evidence from the retrieved chunks over background knowledge.
"""


def ask(
    question: str,
    *,
    storage_path: str | Path,
    collection: str = DEFAULT_COLLECTION,
    embedding_model: str = DEFAULT_MODEL,
    embedder_cache_dir: str | Path | None = None,
    top_k: int = 8,
    llm: LLM | None = None,
    use_llm: bool = True,
    graph_path: str | Path | None = None,
    graph_extra: int = 6,
    pack_ids: list[str] | None = None,
) -> AskResult:
    """Question → embed → Qdrant top-k → (optional graph expansion) → LLM.

    When `pack_ids` is provided, retrieval is scoped to chunks tagged with
    any of those packs (spec §22). This is the single largest token-cost
    lever in Phase 4 — 60-80% input reduction on big repos.
    """
    embedder = Embedder(
        embedding_model,
        cache_dir=str(embedder_cache_dir) if embedder_cache_dir else None,
    )
    store = ChunkStore(location=storage_path, collection=collection)
    try:
        (query_vec,) = embedder.embed_texts([question])
        seed_hits = store.search(query_vec, limit=top_k, pack_ids=pack_ids, query_text=question)

        if graph_path is not None:
            hits = _expand_with_graph(seed_hits, graph_path, store, extra=graph_extra)
        else:
            hits = seed_hits
    finally:
        store.close()

    if not use_llm:
        return AskResult(question=question, hits=hits, answer=None, llm=None)

    used_llm = llm or default_llm()
    user_prompt = _build_user_prompt(question, hits)
    resp = used_llm.generate(SYSTEM_PROMPT, user_prompt)
    return AskResult(question=question, hits=hits, answer=resp.text, llm=resp)


def _expand_with_graph(
    seed_hits: list[SearchHit],
    graph_path: str | Path,
    qdrant: ChunkStore,
    *,
    extra: int,
) -> list[SearchHit]:
    """Lazy import so plain `ask` doesn't pay for networkx unnecessarily."""
    from .graph.graphrag import expand_with_graph
    from .graph.store import GraphStore

    graph = GraphStore.load(graph_path)
    expanded = expand_with_graph(
        seed_hits,
        graph=graph,
        qdrant=qdrant,
        max_extra=extra,
    )
    # Collapse back into SearchHit list so the downstream prompt builder
    # doesn't need to learn a new type. Source label is encoded in the score
    # rank order; graph hits will already be lower-scored than seeds.
    return [SearchHit(chunk=h.chunk, score=h.score) for h in expanded]


def _build_user_prompt(question: str, hits: list[SearchHit]) -> str:
    if not hits:
        return (
            "No chunks were retrieved from the index for this question.\n\n"
            f"Question: {question}\n\n"
            "Tell the user the index is empty or the question matches nothing, "
            "and recommend re-running `karst index <path>` or rephrasing."
        )

    parts: list[str] = ["# Retrieved chunks", ""]
    for i, hit in enumerate(hits, start=1):
        c = hit.chunk
        code = c.code
        if len(code) > _MAX_CHUNK_CHARS:
            code = code[:_MAX_CHUNK_CHARS] + "\n… (truncated)"
        parts.append(
            f"[{i}] {c.citation}  "
            f"({c.kind.value} {c.qualified_name}, score={hit.score:.3f})"
        )
        parts.append(f"```{c.language}")
        parts.append(code)
        parts.append("```")
        parts.append("")
    parts.append("# User question")
    parts.append(question)
    return "\n".join(parts)
