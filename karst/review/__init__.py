"""Code Review agent (spec §8)."""

from .findings import Category, Finding, Severity
from .diff import FileChange, Hunk, ParsedDiff, parse_diff

__all__ = [
    "Category",
    "FileChange",
    "Finding",
    "Hunk",
    "ParsedDiff",
    "Severity",
    "parse_diff",
]
