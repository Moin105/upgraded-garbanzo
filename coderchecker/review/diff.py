"""Unified-diff parser.

Wraps `unidiff` so the rest of the agent talks about FileChange/Hunk objects
with clean line-range semantics. Line numbers refer to the POST-image (the
"+++" file) because that's what the reviewer cites and what GitHub PR review
comments anchor to.

Renames, deletions, and binary files are surfaced but the reviewer will
mostly skip them: there's nothing to chunk-review on a delete or a binary
blob.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unidiff import PatchSet


@dataclass(frozen=True)
class Hunk:
    """A single contiguous range of changes inside a file."""

    new_start: int            # first line of the post-image hunk window
    new_length: int           # total lines in the post-image hunk window
    added_line_numbers: tuple[int, ...]    # post-image line numbers that were added
    removed_line_count: int                # how many lines were deleted
    # Header context (the bit after @@ ... @@) — useful as a section hint.
    section_header: str = ""
    # The full hunk body as it appeared in the diff (for prompt assembly).
    body: str = ""

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_length - 1, 0)

    @property
    def has_additions(self) -> bool:
        return bool(self.added_line_numbers)


@dataclass
class FileChange:
    path: str                          # the post-image path (b/foo.py)
    old_path: str | None               # pre-image path (None if added)
    is_added: bool = False
    is_removed: bool = False
    is_renamed: bool = False
    is_binary: bool = False
    language_hint: str | None = None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def is_reviewable(self) -> bool:
        if self.is_binary or self.is_removed:
            return False
        return bool(self.hunks)

    def added_line_ranges(self) -> list[tuple[int, int]]:
        """Collapse added lines into contiguous (start, end) ranges."""
        all_lines = sorted({n for h in self.hunks for n in h.added_line_numbers})
        if not all_lines:
            return []
        ranges: list[tuple[int, int]] = []
        start = prev = all_lines[0]
        for n in all_lines[1:]:
            if n == prev + 1:
                prev = n
                continue
            ranges.append((start, prev))
            start = prev = n
        ranges.append((start, prev))
        return ranges


@dataclass
class ParsedDiff:
    files: list[FileChange]

    def reviewable_files(self) -> list[FileChange]:
        return [f for f in self.files if f.is_reviewable]


def parse_diff(diff_text: str) -> ParsedDiff:
    if not diff_text.strip():
        return ParsedDiff(files=[])

    patch = PatchSet(diff_text)
    files: list[FileChange] = []

    for pf in patch:
        path = pf.target_file
        old = pf.source_file
        # unidiff prefixes with a/ and b/ when reading git diffs.
        path = _strip_prefix(path)
        old = _strip_prefix(old) if old else None
        if path == "/dev/null":
            path = old or ""

        fc = FileChange(
            path=path or "",
            old_path=old if old not in (None, "/dev/null") else None,
            is_added=pf.is_added_file,
            is_removed=pf.is_removed_file,
            is_renamed=pf.is_rename,
            is_binary=pf.is_binary_file,
        )

        for h in pf:
            added: list[int] = []
            removed = 0
            body_lines: list[str] = []
            body_lines.append(
                f"@@ -{h.source_start},{h.source_length} +{h.target_start},{h.target_length} @@"
                + (f" {h.section_header}" if h.section_header else "")
            )
            for line in h:
                # unidiff Line objects: .is_added/.is_removed/.is_context, .target_line_no
                if line.is_added:
                    if line.target_line_no is not None:
                        added.append(line.target_line_no)
                    body_lines.append(f"+{line.value.rstrip()}")
                elif line.is_removed:
                    removed += 1
                    body_lines.append(f"-{line.value.rstrip()}")
                else:
                    body_lines.append(f" {line.value.rstrip()}")

            fc.hunks.append(
                Hunk(
                    new_start=h.target_start,
                    new_length=h.target_length,
                    added_line_numbers=tuple(added),
                    removed_line_count=removed,
                    section_header=h.section_header or "",
                    body="\n".join(body_lines),
                )
            )

        files.append(fc)

    return ParsedDiff(files=files)


def _strip_prefix(p: str) -> str:
    if p.startswith("a/") or p.startswith("b/"):
        return p[2:]
    return p
