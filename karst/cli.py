"""CLI entry point.

Subcommands:
    karst analyze <path>          # walk + parse + chunk (no storage)
    karst index <path>            # full ingestion → Qdrant
    karst ask <question>          # Q&A over an indexed repo
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import __version__
from .analyze import analyze_repo
from .ask import ask
from .embedder import DEFAULT_MODEL
from .graph_cli import add_graph_index_subparser, add_impact_subparser
from .indexer import index_repo
from .llm import DEFAULT_ANTHROPIC_MODEL, DEFAULT_OPENAI_MODEL, LLMNotConfigured
from .packs_cli import add_packs_subparser
from .review_cli import add_review_subparser
from .state import clear_attached, load_state
from .store import DEFAULT_COLLECTION
from .tokens import estimate_cost


# Default per-user storage path. Each repo gets its own subdirectory so two
# projects don't share an index. Phase 1 keeps this in the home dir; in
# production §34 calls for per-tenant Qdrant collections.
def _default_storage(path: Path) -> Path:
    base = Path.home() / ".karst" / "indexes"
    slug = path.resolve().name or "root"
    return base / slug


def _default_cache_dir() -> Path:
    return Path.home() / ".karst" / "models"


# --------------------------------------------------------------------------- #
# analyze
# --------------------------------------------------------------------------- #

def _cmd_analyze(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        return 2

    file_count = 0
    chunk_count = 0
    by_lang: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()

    for result in analyze_repo(root):
        file_count += 1
        chunk_count += len(result.chunks)
        by_lang[result.parsed.language] += len(result.chunks)
        for chunk in result.chunks:
            by_kind[chunk.kind.value] += 1
            if args.jsonl:
                payload = chunk.to_dict()
                if not args.include_code:
                    payload.pop("code", None)
                sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            elif not args.stats:
                marker = f"[{chunk.kind.value}]"
                print(f"{chunk.citation}  {marker:10} {chunk.qualified_name}")

    if args.jsonl:
        return 0

    print("", file=sys.stderr)
    print(f"Files indexed:   {file_count}", file=sys.stderr)
    print(f"Chunks emitted:  {chunk_count}", file=sys.stderr)
    if by_lang:
        print("By language:", file=sys.stderr)
        for lang, n in by_lang.most_common():
            print(f"  {lang:14} {n}", file=sys.stderr)
    if by_kind:
        print("By kind:", file=sys.stderr)
        for kind, n in by_kind.most_common():
            print(f"  {kind:14} {n}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #

def _cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    storage = Path(args.storage) if args.storage else _default_storage(root)
    cache = Path(args.embedder_cache) if args.embedder_cache else _default_cache_dir()

    print(f"Indexing:        {root.resolve()}", file=sys.stderr)
    print(f"Storage:         {storage}", file=sys.stderr)
    print(f"Collection:      {args.collection}", file=sys.stderr)
    print(f"Embedding model: {args.embedding_model}", file=sys.stderr)
    print(f"Reset:           {args.reset}", file=sys.stderr)
    print("", file=sys.stderr)

    last_print = 0.0

    def progress(files: int, chunks: int) -> None:
        nonlocal last_print
        now = time.monotonic()
        if now - last_print > 0.5:
            print(f"  scanned {files} files, {chunks} chunks…", file=sys.stderr)
            last_print = now

    start = time.monotonic()
    result = index_repo(
        root,
        storage_path=storage,
        collection=args.collection,
        embedding_model=args.embedding_model,
        embedder_cache_dir=cache,
        reset=args.reset,
        incremental=not args.full,
        progress=progress,
    )
    elapsed = time.monotonic() - start

    print("", file=sys.stderr)
    print(
        f"Files: {result.files} total = "
        f"{result.files_indexed} indexed + {result.files_reused} reused",
        file=sys.stderr,
    )
    print(
        f"Embeddings: {result.embeddings_computed} computed + "
        f"{result.embeddings_cached} from cache",
        file=sys.stderr,
    )
    print(
        f"Chunks in collection: {result.chunks}  ({elapsed:.1f}s)",
        file=sys.stderr,
    )
    print(
        f"Collection '{result.collection}' at {result.storage_path}",
        file=sys.stderr,
    )
    return 0


# --------------------------------------------------------------------------- #
# ask
# --------------------------------------------------------------------------- #

def _cmd_ask(args: argparse.Namespace) -> int:
    if not args.storage:
        print(
            "error: --storage is required for `ask` (point at the same path used "
            "when indexing, or set it explicitly).",
            file=sys.stderr,
        )
        return 2
    storage = Path(args.storage)
    if not storage.exists():
        print(f"error: storage path does not exist: {storage}", file=sys.stderr)
        return 2

    cache = Path(args.embedder_cache) if args.embedder_cache else _default_cache_dir()

    graph_path = Path(args.graph) if args.graph else None
    if graph_path is not None and not graph_path.exists():
        print(f"error: --graph path does not exist: {graph_path}", file=sys.stderr)
        return 2

    # Pack-scoped retrieval. Active packs come from state.json (pinned +
    # attached) unless --all is set.
    pack_ids: list[str] | None = None
    if not args.all_packs:
        state = load_state(storage)
        active = state.all_active_packs()
        if active:
            pack_ids = active
            print(
                f"Pack filter: {len(active)} active "
                f"({', '.join(active[:3])}{'…' if len(active) > 3 else ''})",
                file=sys.stderr,
            )

    try:
        result = ask(
            args.question,
            storage_path=storage,
            collection=args.collection,
            embedding_model=args.embedding_model,
            embedder_cache_dir=cache,
            top_k=args.top_k,
            use_llm=not args.no_llm,
            graph_path=graph_path,
            graph_extra=args.graph_extra,
            pack_ids=pack_ids,
        )
    except LLMNotConfigured as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # One-shot attached packs are consumed by this call — clear them so the
    # next `ask` starts clean. Pinned packs survive.
    if not args.all_packs:
        try:
            clear_attached(storage)
        except Exception:
            pass

    # Always show retrieval first — these are the citations the answer rests on.
    print("Retrieved chunks:", file=sys.stderr)
    for i, hit in enumerate(result.hits, start=1):
        c = hit.chunk
        print(
            f"  [{i}] {c.citation:40}  "
            f"{c.kind.value:9} {c.qualified_name}  (score {hit.score:.3f})",
            file=sys.stderr,
        )
    print("", file=sys.stderr)

    if result.answer is None:
        # No-LLM mode: dump the top hits' code as the "answer".
        for i, hit in enumerate(result.hits, start=1):
            c = hit.chunk
            print(f"# [{i}] {c.citation} - {c.kind.value} {c.qualified_name}")
            print(c.code)
            print()
        # Even in no-LLM mode, show what a real call would cost — so users
        # understand the token bill they'd save.
        _print_token_meter(result.hits, args.question, model_hint=None)
        return 0

    print(result.answer)
    if result.llm is not None:
        print("", file=sys.stderr)
        print(f"(answered by {result.llm.provider}:{result.llm.model})", file=sys.stderr)
        _print_token_meter(result.hits, args.question, model_hint=result.llm.model)
    return 0


def _print_token_meter(hits, question, *, model_hint: str | None) -> None:
    """Show input-token estimate + dollar cost for the assembled prompt.

    Mirrors ask._build_user_prompt's approximate size; the meter is a
    budget tool, not a billing-accurate counter.
    """
    from .tokens import DEFAULT_CHARS_PER_TOKEN

    chars = sum(len(h.chunk.code) for h in hits) + len(question) + 800  # system prompt
    in_tok = max(1, chars // DEFAULT_CHARS_PER_TOKEN)

    if model_hint and "claude" in model_hint:
        cost = estimate_cost(provider="anthropic", model=model_hint, input_tokens=in_tok)
    elif model_hint and "gpt" in model_hint:
        cost = estimate_cost(provider="openai", model=model_hint, input_tokens=in_tok)
    else:
        cost = estimate_cost(
            provider="anthropic", model=DEFAULT_ANTHROPIC_MODEL, input_tokens=in_tok
        )
    if cost is None:
        print(f"~{in_tok:,} input tokens (pricing unknown for {model_hint})", file=sys.stderr)
        return
    print(cost.render(), file=sys.stderr)


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="karst",
        description="AI Staff Engineer Agent — Phase 1 (ingest + index + ask).",
    )
    parser.add_argument(
        "--version", action="version", version=f"karst {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # analyze
    p_analyze = sub.add_parser(
        "analyze", help="Walk a repo and emit AST-aware chunks (no storage)."
    )
    p_analyze.add_argument("path")
    p_analyze.add_argument("--jsonl", action="store_true")
    p_analyze.add_argument("--include-code", action="store_true")
    p_analyze.add_argument("--stats", action="store_true")
    p_analyze.set_defaults(func=_cmd_analyze)

    # index
    p_index = sub.add_parser(
        "index",
        help="Ingest a repo into the Qdrant vector store (walk -> parse -> chunk -> embed -> upsert).",
    )
    p_index.add_argument("path")
    p_index.add_argument("--storage", help="Qdrant local-storage path (default: ~/.karst/indexes/<repo>).")
    p_index.add_argument("--collection", default=DEFAULT_COLLECTION)
    p_index.add_argument("--embedding-model", default=DEFAULT_MODEL)
    p_index.add_argument("--embedder-cache", help="Where to cache the embedding model weights.")
    p_index.add_argument("--reset", action="store_true", help="Drop and recreate the collection first.")
    p_index.add_argument(
        "--full",
        action="store_true",
        help="Force re-embed every file, ignoring the SHA manifest.",
    )
    p_index.set_defaults(func=_cmd_index)

    # ask
    p_ask = sub.add_parser("ask", help="Ask a question against an indexed repo.")
    p_ask.add_argument("question")
    p_ask.add_argument("--storage", required=False, help="Index storage path (must match the one used for `index`).")
    p_ask.add_argument("--collection", default=DEFAULT_COLLECTION)
    p_ask.add_argument("--embedding-model", default=DEFAULT_MODEL)
    p_ask.add_argument("--embedder-cache")
    p_ask.add_argument("--top-k", type=int, default=8)
    p_ask.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM synthesis; print the top-k retrieved chunks instead.",
    )
    p_ask.add_argument(
        "--graph",
        metavar="PATH",
        help="Use GraphRAG: expand vector hits with graph neighbors from this graph pickle.",
    )
    p_ask.add_argument(
        "--graph-extra",
        type=int,
        default=6,
        help="Max number of graph-added neighbor chunks (default 6).",
    )
    p_ask.add_argument(
        "--all-packs",
        "--all",
        action="store_true",
        help="Bypass attached/pinned pack filter and search the whole index.",
    )
    p_ask.set_defaults(func=_cmd_ask)

    # review
    add_review_subparser(sub)

    # graph-index + impact
    add_graph_index_subparser(sub)
    add_impact_subparser(sub)

    # packs
    add_packs_subparser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
