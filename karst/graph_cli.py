"""CLI handlers for `graph-index` and `impact` (spec §10, §17)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .graph.builder import build_and_save
from .graph.impact import (
    analyze_impact,
    resolve_targets,
    resolve_targets_from_diff,
)
from .graph.store import EdgeKind, GraphStore
from .review.diff import parse_diff


def default_graph_path(storage_dir: Path) -> Path:
    return storage_dir / "graph.pkl"


# ---------------------------------------------------------------- graph-index

def add_graph_index_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "graph-index",
        help="Build the knowledge graph for a repo (File/Function nodes, IMPORTS/CALLS/CONTAINS edges).",
    )
    p.add_argument("path", nargs="?", default=".", help="Repo path (default: current folder).")
    p.add_argument(
        "--storage",
        help="Where to write the graph (default ~/.karst/indexes/<repo>/graph.pkl).",
    )
    p.set_defaults(func=_cmd_graph_index)


def _cmd_graph_index(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    if args.storage:
        graph_path = Path(args.storage)
        if graph_path.is_dir():
            graph_path = graph_path / "graph.pkl"
    else:
        # Mirror the vector-index default layout.
        base = Path.home() / ".karst" / "indexes" / (root.resolve().name or "root")
        graph_path = default_graph_path(base)

    print(f"Indexing graph: {root.resolve()}", file=sys.stderr)
    print(f"Output:         {graph_path}", file=sys.stderr)
    print("", file=sys.stderr)

    last = 0.0

    def progress(files: int, chunks: int) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last > 0.5:
            print(f"  scanned {files} files, {chunks} chunks…", file=sys.stderr)
            last = now

    start = time.monotonic()
    result = build_and_save(root, graph_path=graph_path, progress=progress)
    elapsed = time.monotonic() - start

    print("", file=sys.stderr)
    print(
        f"Built graph: {result.nodes} nodes, {result.edges} edges "
        f"from {result.files} files / {result.chunks} chunks in {elapsed:.1f}s",
        file=sys.stderr,
    )
    if result.edge_counts:
        print("Edges by kind:", file=sys.stderr)
        for kind, n in sorted(result.edge_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {kind:10} {n}", file=sys.stderr)
    print(f"Saved to {graph_path}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- impact

def add_impact_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "impact",
        help="Predict blast radius of a change (spec section 10).",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--target", action="append", default=[], metavar="NAME",
                     help="Target by bare name (repeatable; matches all nodes with that name).")
    src.add_argument("--qname", action="append", default=[], metavar="QNAME",
                     help="Target by qualified name like 'src/foo.py::Bar.baz'.")
    src.add_argument("--file", action="append", default=[], metavar="PATH",
                     help="Target the entire file by repo-relative path.")
    src.add_argument("--diff", metavar="PATH",
                     help="Read a unified diff and target every overlapping chunk ('-' for stdin).")
    src.add_argument("--staged", action="store_true",
                     help="Target chunks overlapping currently staged changes.")
    src.add_argument("--base", metavar="REV",
                     help="Target chunks overlapping the diff from REV to HEAD.")

    p.add_argument("--graph-path", help="Path to the graph pickle (default mirrors graph-index).")
    p.add_argument("--repo-path", default=".", help="Local repo dir for --staged/--base.")
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--limit", type=int, default=25, help="Cap the rendered output.")
    p.add_argument("--jsonl", action="store_true")
    p.set_defaults(func=_cmd_impact)


def _cmd_impact(args: argparse.Namespace) -> int:
    if not args.graph_path:
        print("error: --graph-path is required.", file=sys.stderr)
        return 2
    graph_path = Path(args.graph_path)
    if not graph_path.exists():
        print(f"error: graph not found at {graph_path}. Run `graph-index` first.", file=sys.stderr)
        return 2

    store = GraphStore.load(graph_path)

    if args.diff or args.staged or args.base:
        if args.diff:
            text = sys.stdin.read() if args.diff == "-" else Path(args.diff).read_text(encoding="utf-8", errors="replace")
        elif args.staged:
            text = _git_diff(args.repo_path, ["--cached"])
        else:
            text = _git_diff(args.repo_path, [f"{args.base}...HEAD"])
        parsed = parse_diff(text)
        targets = resolve_targets_from_diff(store, parsed)
    else:
        targets = resolve_targets(
            store,
            names=args.target,
            qnames=args.qname,
            files=args.file,
        )

    if not targets:
        print("No targets matched in the graph.", file=sys.stderr)
        return 1

    report = analyze_impact(
        store,
        targets=targets,
        max_depth=args.max_depth,
        kinds=(EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.CONTAINS, EdgeKind.DEFINES),
    )

    print(f"Targets ({len(report.targets)}):", file=sys.stderr)
    for t in report.targets[:5]:
        node = store.get_node(t)
        if node:
            print(f"  - {node.qualified_name}", file=sys.stderr)
    if len(report.targets) > 5:
        print(f"  … and {len(report.targets) - 5} more", file=sys.stderr)
    print(f"Affected: {len(report.affected)}  Risk: {report.risk.upper()}", file=sys.stderr)
    print("", file=sys.stderr)

    if args.jsonl:
        for a in report.affected[: args.limit]:
            payload = {
                "node_id": a.node_id,
                "kind": a.kind.value,
                "qualified_name": a.qualified_name,
                "citation": a.citation,
                "depth": a.depth,
                "score": a.score,
                "via": [e.value for e in a.via_edges],
            }
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        for a in report.affected[: args.limit]:
            via = ",".join(e.value for e in a.via_edges)
            cite = a.citation or "(no source)"
            print(
                f"  [{a.kind.value:9}]  depth {a.depth}  score {a.score:.3f}  via {via:18}  "
                f"{a.qualified_name}  ({cite})"
            )
        if len(report.affected) > args.limit:
            print(f"  … and {len(report.affected) - args.limit} more.", file=sys.stderr)
    return 0


def _git_diff(cwd: str, extra: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", cwd, "diff", "--no-color", "--unified=3", *extra],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        print(f"git diff failed: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return proc.stdout
