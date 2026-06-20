"""CLI handler for `coderchecker review`.

Kept in its own module to avoid bloating cli.py. The CLI dispatcher in
cli.py calls _cmd_review here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .embedder import DEFAULT_MODEL
from .llm import LLMNotConfigured
from .review.agent import render_findings_text, review_diff
from .review.diff import parse_diff
from .review.github import GhError, GhUnavailable, PRRef, fetch_pr_diff, post_review
from .store import DEFAULT_COLLECTION


def add_review_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "review",
        help="Review a diff against the indexed repo. Sources: --staged, --base, --diff, --pr.",
    )

    # Diff source — mutually exclusive
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--staged",
        action="store_true",
        help="Review the currently staged changes (git diff --cached).",
    )
    src.add_argument(
        "--base",
        metavar="REV",
        help="Review the diff from REV to HEAD (e.g. --base origin/main).",
    )
    src.add_argument(
        "--diff",
        metavar="PATH",
        help="Read a unified diff from a file ('-' for stdin).",
    )
    src.add_argument(
        "--pr",
        type=int,
        metavar="N",
        help="Fetch and review GitHub PR #N (uses gh CLI).",
    )

    p.add_argument("--repo", help='GitHub repo as "owner/name" (with --pr).')
    p.add_argument(
        "--repo-path",
        default=".",
        help="Local repo working directory (used by --staged/--base, default cwd).",
    )
    p.add_argument(
        "--storage",
        required=True,
        help="Qdrant index storage path (must match the path used by `index`).",
    )
    p.add_argument("--collection", default=DEFAULT_COLLECTION)
    p.add_argument("--embedding-model", default=DEFAULT_MODEL)
    p.add_argument("--embedder-cache")
    p.add_argument(
        "--no-neighbors",
        action="store_true",
        help="Skip semantic-neighbor lookup (faster, less context).",
    )
    p.add_argument(
        "--jsonl",
        action="store_true",
        help="Emit one finding per line as JSON.",
    )
    p.add_argument(
        "--post-to-pr",
        action="store_true",
        help="Post findings as inline comments on the PR (requires --pr).",
    )
    p.set_defaults(func=_cmd_review)


def _cmd_review(args: argparse.Namespace) -> int:
    diff_text = _load_diff(args)
    if not diff_text.strip():
        print("No diff content. Nothing to review.", file=sys.stderr)
        return 0

    parsed = parse_diff(diff_text)
    reviewable = parsed.reviewable_files()
    if not reviewable:
        print("Diff has no reviewable files (binary, removals, or empty).", file=sys.stderr)
        return 0

    print(
        f"Reviewing {len(reviewable)} file(s): "
        + ", ".join(f.path for f in reviewable[:5])
        + ("…" if len(reviewable) > 5 else ""),
        file=sys.stderr,
    )

    try:
        result = review_diff(
            parsed,
            storage_path=Path(args.storage),
            collection=args.collection,
            embedding_model=args.embedding_model,
            embedder_cache_dir=Path(args.embedder_cache) if args.embedder_cache else None,
            use_semantic_neighbors=not args.no_neighbors,
        )
    except LLMNotConfigured:
        print(
            "error: No LLM configured. The review agent needs to call an LLM "
            "to generate findings.\n"
            "       Set ANTHROPIC_API_KEY or OPENAI_API_KEY and re-run.",
            file=sys.stderr,
        )
        return 3

    print(
        f"Files reviewed: {result.files_reviewed}, "
        f"skipped: {result.files_skipped}, "
        f"findings: {len(result.findings)}",
        file=sys.stderr,
    )
    print("", file=sys.stderr)

    if args.jsonl:
        for f in result.findings:
            sys.stdout.write(json.dumps(f.to_dict(), ensure_ascii=False) + "\n")
    else:
        print(render_findings_text(result.findings))

    if args.post_to_pr:
        if not args.pr:
            print("error: --post-to-pr requires --pr.", file=sys.stderr)
            return 2
        try:
            post_review(PRRef(number=args.pr, repo=args.repo), result.findings)
            print(
                f"Posted {len(result.findings)} comment(s) to PR #{args.pr}.",
                file=sys.stderr,
            )
        except (GhUnavailable, GhError) as e:
            print(f"error posting to PR: {e}", file=sys.stderr)
            return 4

    return 0


def _load_diff(args: argparse.Namespace) -> str:
    if args.staged:
        return _git_diff(args.repo_path, ["--cached"])
    if args.base:
        return _git_diff(args.repo_path, [f"{args.base}...HEAD"])
    if args.diff:
        if args.diff == "-":
            return sys.stdin.read()
        return Path(args.diff).read_text(encoding="utf-8", errors="replace")
    if args.pr:
        try:
            return fetch_pr_diff(PRRef(number=args.pr, repo=args.repo))
        except GhUnavailable as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        except GhError as e:
            print(f"error fetching PR diff: {e}", file=sys.stderr)
            sys.exit(2)
    return ""


def _git_diff(cwd: str, extra_args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", cwd, "diff", "--no-color", "--unified=3", *extra_args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print(f"git diff failed: {proc.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return proc.stdout
