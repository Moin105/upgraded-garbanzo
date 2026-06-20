"""karst MCP server.

Exposes karst's retrieval + analysis engine to any MCP host — Claude Desktop,
Cursor, Continue, Cline, or a custom agent — over stdio.

Design principle (important): the MCP server returns **structured, cited
context**. It does NOT call an LLM itself. The host already has the model;
karst's job is to feed it the *right* slice of the repo, scoped and cited, so
the host model reasons over 60% fewer tokens. That also means users never give
karst an API key.

Handle lifetime: the embedder, vector store, graph and pack store for a repo
are opened ONCE and reused for the life of the server process. We never
open/close the Qdrant store per tool call — on Windows the local-mode file
lock does not always release cleanly between an open/close pair inside one
long-lived process, which would hang the second call. Caching the handles
sidesteps that entirely. Access is serialized with a lock (a local server is
single-user; correctness beats concurrency here).

Tools exposed:
  - search_code        retrieve the most relevant code chunks for a question,
                       each anchored to file:line (optionally pack-scoped)
  - find_impact        blast radius of changing a symbol — what depends on it
  - list_packs         the named context packs available for a repo
  - index_status       whether a repo is indexed and how big the index is
  - index_repository   build / refresh the index + graph for a repo

Run it:  karst-mcp           (console script)
   or:    python -m karst.mcp_server
"""

from __future__ import annotations

import atexit
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:  # pragma: no cover
    from .embedder import Embedder
    from .graph.store import GraphStore
    from .packs.store import PackStore
    from .store import ChunkStore

mcp = FastMCP("karst")


# --------------------------------------------------------------------------- #
# Path conventions — mirror the CLI so the MCP server and `karst index` share
# the exact same on-disk index location.
# --------------------------------------------------------------------------- #

def _storage_for(repo_path: str) -> Path:
    root = Path(repo_path).expanduser().resolve()
    slug = root.name or "root"
    return Path.home() / ".karst" / "indexes" / slug


def _cache_dir() -> Path:
    return Path.home() / ".karst" / "models"


def _graph_path(storage: Path) -> Path:
    return storage / "graph.pkl"


def _is_indexed(storage: Path) -> bool:
    return storage.exists() and (storage / "manifest.json").exists()


_NOT_INDEXED_HINT = (
    "This repo isn't indexed yet. Run `index_repository` once (or `karst index "
    "<path>` on the command line), then try again."
)


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------- #
# Cached handles — opened once per repo, reused for the process lifetime.
# --------------------------------------------------------------------------- #

_lock = threading.RLock()
_embedder: "Embedder | None" = None
_stores: dict[str, "ChunkStore"] = {}       # keyed by str(storage)
_graphs: dict[str, "GraphStore"] = {}       # keyed by str(graph.pkl)
_packs: dict[str, "PackStore"] = {}         # keyed by str(packs.sqlite)


def _get_embedder() -> "Embedder":
    global _embedder
    if _embedder is None:
        from .embedder import DEFAULT_MODEL, Embedder

        _embedder = Embedder(DEFAULT_MODEL, cache_dir=str(_cache_dir()))
    return _embedder


def _get_store(storage: Path) -> "ChunkStore":
    key = str(storage)
    store = _stores.get(key)
    if store is None:
        from .store import DEFAULT_COLLECTION, ChunkStore

        store = ChunkStore(location=storage, collection=DEFAULT_COLLECTION)
        _stores[key] = store
    return store


def _get_graph(storage: Path) -> "GraphStore | None":
    gp = _graph_path(storage)
    if not gp.exists():
        return None
    key = str(gp)
    graph = _graphs.get(key)
    if graph is None:
        from .graph.store import GraphStore

        graph = GraphStore.load(gp)
        _graphs[key] = graph
    return graph


def _get_packstore(storage: Path) -> "PackStore":
    key = str(storage / "packs.sqlite")
    ps = _packs.get(key)
    if ps is None:
        from .packs.store import PackStore

        ps = PackStore(storage / "packs.sqlite")
        _packs[key] = ps
    return ps


def _evict_repo(storage: Path) -> None:
    """Close and drop every cached handle for a repo.

    Used before re-indexing so the indexer can take the Qdrant write lock that
    our cached read handle would otherwise be holding.
    """
    key = str(storage)
    store = _stores.pop(key, None)
    if store is not None:
        try:
            store.close()
        except Exception:
            pass
    _graphs.pop(str(_graph_path(storage)), None)
    _packs.pop(str(storage / "packs.sqlite"), None)


@atexit.register
def _close_all() -> None:
    for store in list(_stores.values()):
        try:
            store.close()
        except Exception:
            pass
    _stores.clear()


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@mcp.tool()
def search_code(
    query: str,
    repo_path: str,
    packs: list[str] | None = None,
    limit: int = 8,
) -> str:
    """Find the most relevant code in a repository for a question or task.

    Returns ranked code chunks, each anchored to an exact file:line citation,
    so you can reason over only the relevant slice instead of the whole repo.
    Prefer this over reading many files when you need to understand how
    something works or where a behavior lives.

    Args:
        query: A natural-language question or description, e.g.
            "how does checkout charge the user" or "JWT refresh logic".
        repo_path: Absolute path to the repository (must be indexed first).
        packs: Optional list of pack ids to scope the search to (see
            list_packs). Scoping cuts tokens further. Omit to search all.
        limit: Max number of chunks to return (default 8).
    """
    storage = _storage_for(repo_path)
    if not _is_indexed(storage):
        return _NOT_INDEXED_HINT

    with _lock:
        embedder = _get_embedder()
        store = _get_store(storage)
        (vec,) = embedder.embed_texts([query])
        hits = store.search(vec, limit=limit, pack_ids=packs or None)

    if not hits:
        scope = f" in packs {packs}" if packs else ""
        return f"No code matched '{query}'{scope}. Try a broader query or omit packs."

    parts: list[str] = []
    for i, hit in enumerate(hits, start=1):
        c = hit.chunk
        parts.append(
            f"[{i}] {c.citation}  ({c.kind.value} {c.qualified_name}, score {hit.score:.3f})"
        )
        parts.append(f"```{c.language}")
        parts.append(c.code.rstrip())
        parts.append("```")
        parts.append("")

    header = (
        f"Top {len(hits)} results for: {query}\n"
        f"(~{_est_tokens(''.join(h.chunk.code for h in hits)):,} tokens of scoped context"
        + (f", packs: {', '.join(packs)}" if packs else "")
        + ")\n"
    )
    return header + "\n" + "\n".join(parts).rstrip()


@mcp.tool()
def find_impact(symbol: str, repo_path: str, max_depth: int = 3) -> str:
    """Predict the blast radius of changing a function, method, class, or file.

    Walks the call/import graph to find everything that depends on the target,
    ranked by how directly. Use this before editing a symbol to know what else
    might break.

    Args:
        symbol: A bare name ("getUser"), a qualified name
            ("src/auth/users.ts::UserService.get"), or a file path.
        repo_path: Absolute path to the repository (must be indexed first).
        max_depth: How many dependency hops to walk (default 3).
    """
    storage = _storage_for(repo_path)

    with _lock:
        graph = _get_graph(storage)
        if graph is None:
            return (
                "No dependency graph for this repo yet. Run `index_repository` "
                "(it builds the graph too), then try again."
            )

        from .graph.impact import analyze_impact, resolve_targets

        targets = resolve_targets(
            graph, names=[symbol], qnames=[symbol], files=[symbol]
        )
        if not targets:
            return f"'{symbol}' was not found in the graph. Check the spelling or try a file path."

        report = analyze_impact(graph, targets=targets, max_depth=max_depth)
        target_names = [
            n.qualified_name
            for t in report.targets[:6]
            if (n := graph.get_node(t)) is not None
        ]

    lines = [
        f"Impact of changing '{symbol}'",
        f"Resolved targets: {', '.join(target_names) or symbol}",
        f"Affected: {len(report.affected)}   Risk: {report.risk.upper()}",
        "",
    ]
    if not report.affected:
        lines.append("Nothing depends on this — safe to change in isolation.")
        return "\n".join(lines)

    for a in report.affected[:25]:
        via = ",".join(e.value for e in a.via_edges) or "—"
        cite = a.citation or "(no source)"
        lines.append(
            f"  [{a.kind.value:9}] depth {a.depth} score {a.score:.3f} via {via:14} "
            f"{a.qualified_name}  ({cite})"
        )
    if len(report.affected) > 25:
        lines.append(f"  … and {len(report.affected) - 25} more.")
    return "\n".join(lines)


@mcp.tool()
def list_packs(repo_path: str) -> str:
    """List the named context packs available for a repository.

    A pack is a curated slice of the codebase (e.g. "auth", "billing"). Pass
    pack ids to search_code's `packs` argument to scope a search and cut
    tokens further.

    Args:
        repo_path: Absolute path to the repository (must be indexed first).
    """
    storage = _storage_for(repo_path)
    if not storage.exists():
        return _NOT_INDEXED_HINT

    with _lock:
        packs = _get_packstore(storage).list()

    if not packs:
        return (
            "No packs defined yet. Run "
            "`karst packs --storage <storage> suggest <repo> --apply --retag` "
            "to auto-generate them."
        )

    lines = [f"{len(packs)} packs:"]
    for p in packs:
        lines.append(
            f"  {p.id:32} {p.label:28} chunks={p.chunk_count:<5} ~{p.token_estimate:,} tok"
        )
    return "\n".join(lines)


@mcp.tool()
def index_status(repo_path: str) -> str:
    """Report whether a repository is indexed and how large the index is.

    Use this first if you're unsure whether search_code / find_impact will
    work for a repo.

    Args:
        repo_path: Absolute path to the repository.
    """
    storage = _storage_for(repo_path)
    if not _is_indexed(storage):
        return (
            f"Not indexed: {repo_path}\n"
            f"Expected index at {storage}\n"
            f"{_NOT_INDEXED_HINT}"
        )

    with _lock:
        chunk_count = _get_store(storage).count()
        try:
            n_packs = len(_get_packstore(storage).list())
        except Exception:
            n_packs = 0

    graph = "yes" if _graph_path(storage).exists() else "no"
    return (
        f"Indexed: {repo_path}\n"
        f"  storage:  {storage}\n"
        f"  chunks:   {chunk_count}\n"
        f"  graph:    {graph}\n"
        f"  packs:    {n_packs}"
    )


@mcp.tool()
def index_repository(repo_path: str, reset: bool = False) -> str:
    """Index a repository so search_code and find_impact can work on it.

    Builds the vector index (for search) AND the dependency graph (for impact).
    The first run on a large repo can take a few minutes; subsequent runs are
    near-instant because unchanged files are skipped. For very large repos,
    prefer running `karst index <path>` on the command line.

    Args:
        repo_path: Absolute path to the repository to index.
        reset: If true, rebuild the index from scratch.
    """
    root = Path(repo_path).expanduser().resolve()
    if not root.is_dir():
        return f"Not a directory: {repo_path}"

    storage = _storage_for(repo_path)

    from .graph.builder import build_and_save
    from .indexer import index_repo

    with _lock:
        # Release our cached read handle so the indexer can take the write lock.
        _evict_repo(storage)
        result = index_repo(
            root,
            storage_path=storage,
            embedder_cache_dir=_cache_dir(),
            reset=reset,
        )
        graph = build_and_save(root, graph_path=_graph_path(storage))
        # Drop the (now stale) graph cache so the next find_impact reloads it.
        _graphs.pop(str(_graph_path(storage)), None)

    return (
        f"Indexed {repo_path}\n"
        f"  files:       {result.files} ({result.files_indexed} new, {result.files_reused} reused)\n"
        f"  chunks:      {result.chunks}\n"
        f"  embeddings:  {result.embeddings_computed} computed, {result.embeddings_cached} cached\n"
        f"  graph:       {graph.nodes} nodes, {graph.edges} edges\n"
        f"  storage:     {storage}\n"
        f"Ready — call search_code or find_impact against this repo."
    )


def main() -> None:
    """Console entry point. Serves over stdio for MCP hosts."""
    mcp.run()


if __name__ == "__main__":
    main()
