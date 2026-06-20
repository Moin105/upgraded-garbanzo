"""Code Review agent (spec §8).

Reads a parsed diff, fetches per-hunk context from Qdrant + semantic
neighbors, asks the LLM for severity-tagged findings via structured output.

Per-file pass:
- We send one prompt per FileChange (not one per hunk). The LLM gets the
  whole file's diff and surrounding context together; that's how a human
  reviewer thinks. One-prompt-per-hunk would waste tokens repeating context.
- Findings are clamped to lines that actually changed: an LLM that flags a
  line nowhere near the diff is hallucinating, and we drop those silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..embedder import DEFAULT_MODEL, Embedder
from ..llm import LLM, default_llm
from ..store import DEFAULT_COLLECTION, ChunkStore
from .context import HunkContext, fetch_context
from .diff import FileChange, ParsedDiff
from .findings import FINDINGS_SCHEMA, Finding, parse_findings

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
You are a senior staff engineer doing a code review. You analyze the diff
below and report concrete, actionable findings.

Rules:
1. Report ONLY problems supported by the diff or by the retrieved context.
   No general advice, no taste commentary, no "consider X" boilerplate.
2. Every finding must anchor to a line that exists in the POST-image of the
   diff (a line that the diff adds or modifies). Use the file path exactly
   as it appears in the diff header.
3. Severity scale:
   - critical: will crash production or expose data.
   - high:     likely incorrect under normal load (logic bugs, races,
               broken contracts).
   - medium:   real smell or risk (resource leak, dead code, missing
               error handling on an important path).
   - low:      style, naming, micro-perf.
   - info:     refactor suggestion or FYI.
4. Categories: correctness, security, performance, design, style, testing, other.
5. If the diff is clean for the file, return an empty findings array. Empty
   is normal — do not invent issues to fill space.
6. When you suggest a fix, make it concrete (a sentence or a small snippet).
"""


@dataclass
class ReviewResult:
    findings: list[Finding] = field(default_factory=list)
    files_reviewed: int = 0
    files_skipped: int = 0


def review_diff(
    parsed: ParsedDiff,
    *,
    storage_path: str | Path,
    collection: str = DEFAULT_COLLECTION,
    embedding_model: str = DEFAULT_MODEL,
    embedder_cache_dir: str | Path | None = None,
    llm: LLM | None = None,
    neighbor_k: int = 3,
    use_semantic_neighbors: bool = True,
) -> ReviewResult:
    """Review every reviewable file in the diff."""
    used_llm = llm or default_llm()

    embedder: Embedder | None = None
    if use_semantic_neighbors:
        embedder = Embedder(
            embedding_model,
            cache_dir=str(embedder_cache_dir) if embedder_cache_dir else None,
        )

    store = ChunkStore(location=storage_path, collection=collection)

    result = ReviewResult()
    try:
        for file in parsed.files:
            if not file.is_reviewable:
                result.files_skipped += 1
                continue
            try:
                contexts = fetch_context(
                    file, store=store, embedder=embedder, neighbor_k=neighbor_k
                )
                file_findings = _review_file(file, contexts, used_llm)
                result.findings.extend(file_findings)
                result.files_reviewed += 1
            except Exception:  # pragma: no cover — never fail the whole run on one file
                log.exception("review failed for file %s", file.path)
                result.files_skipped += 1

        result.findings = _clamp_to_diff(result.findings, parsed)
        result.findings.sort(key=lambda f: (-f.severity.rank, f.file, f.line))
    finally:
        store.close()
    return result


def _review_file(
    file: FileChange, contexts: list[HunkContext], llm: LLM
) -> list[Finding]:
    user_prompt = _build_user_prompt(file, contexts)
    payload = llm.generate_structured(
        SYSTEM_PROMPT,
        user_prompt,
        schema=FINDINGS_SCHEMA,
        tool_name="report_findings",
        tool_description="Report code-review findings as a structured list.",
    )
    return parse_findings(payload)


def _build_user_prompt(file: FileChange, contexts: list[HunkContext]) -> str:
    parts: list[str] = []
    parts.append(f"# File under review\n{file.path}")
    if file.is_added:
        parts.append("(new file)")
    if file.is_renamed and file.old_path:
        parts.append(f"(renamed from {file.old_path})")
    parts.append("")

    parts.append("# Diff")
    for hunk in file.hunks:
        parts.append("```diff")
        parts.append(hunk.body)
        parts.append("```")
        parts.append("")

    for i, ctx in enumerate(contexts, start=1):
        if ctx.containing:
            parts.append(f"# Containing context for hunk {i} (lines {ctx.hunk.new_start}-{ctx.hunk.new_end})")
            for chunk in ctx.containing[:3]:
                parts.append(
                    f"## {chunk.citation}  ({chunk.kind.value} {chunk.qualified_name})"
                )
                parts.append(f"```{chunk.language}")
                code = chunk.code
                if len(code) > 1500:
                    code = code[:1500] + "\n… (truncated)"
                parts.append(code)
                parts.append("```")
                parts.append("")
        if ctx.neighbors:
            parts.append(f"# Similar code elsewhere (hunk {i})")
            for hit in ctx.neighbors[:3]:
                c = hit.chunk
                parts.append(
                    f"## {c.citation}  ({c.kind.value} {c.qualified_name}, score {hit.score:.2f})"
                )
                code = c.code
                if len(code) > 800:
                    code = code[:800] + "\n… (truncated)"
                parts.append(f"```{c.language}")
                parts.append(code)
                parts.append("```")
                parts.append("")

    parts.append(
        "# Task\nReview the diff above and call report_findings with any concrete "
        "issues. Only flag what you can defend with evidence from the diff or context."
    )
    return "\n".join(parts)


def _clamp_to_diff(findings: list[Finding], parsed: ParsedDiff) -> list[Finding]:
    """Drop findings that point outside the diff's added line ranges.

    A finding the LLM dreamed up on an unrelated line is a hallucination by
    definition — the reviewer only has authority over what the diff changed.
    A finding spans [line, end_line or line]; it must overlap at least one
    of the file's added-line ranges to survive.
    """
    ranges_by_file: dict[str, list[tuple[int, int]]] = {
        fc.path: fc.added_line_ranges() for fc in parsed.files
    }

    kept: list[Finding] = []
    for f in findings:
        ranges = ranges_by_file.get(f.file)
        if not ranges:
            continue
        f_start = f.line
        f_end = f.end_line if f.end_line is not None else f.line
        if any(r_start <= f_end and f_start <= r_end for (r_start, r_end) in ranges):
            kept.append(f)
    return kept


def render_findings_text(findings: list[Finding]) -> str:
    if not findings:
        return "No findings."
    lines: list[str] = []
    for f in findings:
        loc = f"{f.file}:{f.line}" + (f"-{f.end_line}" if f.end_line else "")
        lines.append(f"[{f.severity.value.upper():<8}] [{f.category.value:<11}] {loc}")
        lines.append(f"  {f.message}")
        if f.fix:
            lines.append(f"  fix: {f.fix}")
        lines.append("")
    return "\n".join(lines).rstrip()
