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

def _resolve_pack_ids(args: argparse.Namespace, storage: Path) -> list[str] | None:
    """Active packs from state.json (pinned + attached) unless --all is set."""
    if args.all_packs:
        return None
    state = load_state(storage)
    active = state.all_active_packs()
    if active:
        print(
            f"Pack filter: {len(active)} active "
            f"({', '.join(active[:3])}{'…' if len(active) > 3 else ''})",
            file=sys.stderr,
        )
        return active
    return None


def _answer_once(
    args: argparse.Namespace,
    question: str,
    storage: Path,
    cache: Path,
    graph_path: Path | None,
) -> int:
    """Run one question end-to-end and print retrieval + answer. May raise
    LLMNotConfigured (callers decide whether to abort or keep going)."""
    pack_ids = _resolve_pack_ids(args, storage)
    result = ask(
        question,
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
        _print_token_meter(result.hits, question, model_hint=None)
        return 0

    print(result.answer)
    if result.llm is not None:
        print("", file=sys.stderr)
        print(f"(answered by {result.llm.provider}:{result.llm.model})", file=sys.stderr)
        _print_token_meter(result.hits, question, model_hint=result.llm.model)
    return 0


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

    # One-shot when a question is given; otherwise drop into an interactive loop.
    interactive = bool(args.interactive) or not args.question
    if not interactive:
        try:
            return _answer_once(args, args.question, storage, cache, graph_path)
        except LLMNotConfigured as e:
            print(f"error: {e}", file=sys.stderr)
            return 3

    print("karst interactive ask — ask anything about this repo.", file=sys.stderr)
    print(
        "Enter a question, or 'exit' / Ctrl-D to quit. Tip: start `ask` with "
        "--no-llm to explore cited chunks without an API key.",
        file=sys.stderr,
    )
    while True:
        try:
            q = input("\nask ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye 👋", file=sys.stderr)
            return 0
        if not q:
            continue
        if q.lower() in {"exit", "quit", ":q"}:
            print("bye 👋", file=sys.stderr)
            return 0
        try:
            _answer_once(args, q, storage, cache, graph_path)
        except LLMNotConfigured as e:
            print(
                f"error: {e}\n(tip: restart `ask` with --no-llm to see cited "
                "chunks without a key)",
                file=sys.stderr,
            )
        except Exception as e:  # keep the REPL alive on a single bad question
            print(f"error: {e}", file=sys.stderr)


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
# quickstart  (index + graph + packs in one go)
# --------------------------------------------------------------------------- #

def _cmd_quickstart(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    storage = Path(args.storage) if args.storage else _default_storage(root)

    print(
        f"\n▸ karst quickstart — getting '{root.resolve().name}' ready to explore\n",
        file=sys.stderr,
    )

    print("[1/3] Indexing code…", file=sys.stderr)
    rc = main(["index", str(root), "--storage", str(storage)] + (["--full"] if args.full else []))
    if rc:
        return rc

    print("\n[2/3] Building the call/import graph…", file=sys.stderr)
    rc = main(["graph-index", str(root), "--storage", str(storage)])
    if rc:
        return rc

    print("\n[3/3] Suggesting context packs…", file=sys.stderr)
    # Soft-fail: tiny repos may yield no packs; that shouldn't fail quickstart.
    main(["packs", "--storage", str(storage), "suggest", str(root), "--apply", "--retag"])

    graph_path = storage / "graph.pkl"
    s = str(storage)
    g = str(graph_path)
    print("\n✓ Ready! Your repo is indexed, graphed, and packed. Try:\n", file=sys.stderr)
    print(f'  karst ask "what does this project do?" --storage "{s}" --no-llm   # no API key needed')
    print(f'  karst ask -i --storage "{s}"                                      # interactive Q&A')
    print(f'  karst ask "how does X work?" --storage "{s}" --graph "{g}"        # GraphRAG')
    print(f'  karst impact --target <name> --graph-path "{g}"                   # blast radius')
    print(f'  karst packs --storage "{s}" list                                  # see context packs')
    print('  karst examples                                                     # more ideas')
    print(
        "\n(For LLM-written answers, set ANTHROPIC_API_KEY or OPENAI_API_KEY; "
        "otherwise use --no-llm to get cited chunks.)",
        file=sys.stderr,
    )
    return 0


# --------------------------------------------------------------------------- #
# examples  (a copy-paste cheatsheet)
# --------------------------------------------------------------------------- #

_EXAMPLES = """\
karst — things to try
=====================

One-time setup on any repo (index + graph + packs):
  karst quickstart ./my-repo

Explore (S = the storage path quickstart prints, e.g. ~/.karst/indexes/my-repo):
  karst ask "where is auth handled?" --storage S --no-llm   # cited chunks, no key
  karst ask "summarize the checkout flow" --storage S       # LLM answer (needs API key)
  karst ask -i --storage S                                  # interactive: ask many questions
  karst ask "..." --storage S --graph S/graph.pkl           # GraphRAG: pull in neighbors

Understand impact before you change something:
  karst impact --target chargeUser --graph-path S/graph.pkl
  karst impact --staged --graph-path S/graph.pkl            # what your staged diff touches

Scope retrieval with packs (fewer tokens, sharper answers):
  karst packs --storage S list
  karst packs --storage S attach billing      # next `ask` only searches the billing pack
  karst packs --storage S pin auth            # keep a pack active for every query

Review a diff with cited, severity-tagged findings:
  karst review --staged --storage S
  karst review --base main --storage S

Peek under the hood:
  karst analyze ./my-repo --stats             # chunk counts by language/kind, no storage
  karst index ./my-repo                       # re-index (incremental + cached; fast)

Use it from your IDE (no API key — your IDE supplies the model):
  add an MCP server `karst-mcp` to Claude Desktop / Cursor. See docs/MCP.md.

Tip: every command supports --help, e.g. `karst ask --help`.
"""


def _cmd_examples(args: argparse.Namespace) -> int:
    print(_EXAMPLES)
    return 0


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
    p_analyze.add_argument("path", nargs="?", default=".", help="Repo path (default: current folder).")
    p_analyze.add_argument("--jsonl", action="store_true")
    p_analyze.add_argument("--include-code", action="store_true")
    p_analyze.add_argument("--stats", action="store_true")
    p_analyze.set_defaults(func=_cmd_analyze)

    # index
    p_index = sub.add_parser(
        "index",
        help="Ingest a repo into the Qdrant vector store (walk -> parse -> chunk -> embed -> upsert).",
    )
    p_index.add_argument("path", nargs="?", default=".", help="Repo path (default: current folder).")
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
    p_ask = sub.add_parser(
        "ask",
        help="Ask a question against an indexed repo (omit the question for an interactive loop).",
    )
    p_ask.add_argument(
        "question",
        nargs="?",
        help="The question. Omit it (or pass -i) to enter interactive mode.",
    )
    p_ask.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Interactive REPL — ask many questions against the same index.",
    )
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

    # quickstart — index + graph + packs in one command
    p_qs = sub.add_parser(
        "quickstart",
        help="One command to get a repo ready: index + graph + suggested packs, then prints what to try.",
    )
    p_qs.add_argument("path", nargs="?", default=".", help="Repo path (default: current folder).")
    p_qs.add_argument("--storage", help="Storage path (default: ~/.karst/indexes/<repo>).")
    p_qs.add_argument("--full", action="store_true", help="Force a full re-index (ignore the SHA manifest).")
    p_qs.set_defaults(func=_cmd_quickstart)

    # examples — a copy-paste cheatsheet of things to try
    p_ex = sub.add_parser("examples", help="Print a cheatsheet of useful commands and questions to try.")
    p_ex.set_defaults(func=_cmd_examples)

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
