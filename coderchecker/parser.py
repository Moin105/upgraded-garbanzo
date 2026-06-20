"""Tree-sitter parser wrapper.

Lazily loads one tree-sitter Parser per language via tree-sitter-language-pack.
Parsers are cached on the registry so subsequent files of the same language
reuse the same Parser instance — tree-sitter parsers are thread-safe for
sequential use within a single thread, which is what we do here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .languages import LanguageSpec, detect_language

if TYPE_CHECKING:
    from tree_sitter import Parser, Tree


@dataclass
class ParsedFile:
    relpath: str
    language: str
    source: bytes
    tree: "Tree"
    sha: str


class ParserRegistry:
    """Lazy cache of tree-sitter parsers, keyed by language name."""

    def __init__(self) -> None:
        self._parsers: dict[str, "Parser"] = {}

    def get(self, lang: LanguageSpec) -> "Parser":
        parser = self._parsers.get(lang.name)
        if parser is None:
            # Imported lazily so import errors surface at first parse, not at
            # package import time.
            from tree_sitter_language_pack import get_parser

            parser = get_parser(lang.name)
            self._parsers[lang.name] = parser
        return parser


def parse_file(
    path: Path,
    *,
    repo_root: Path,
    registry: ParserRegistry,
) -> ParsedFile | None:
    """Parse a single file. Returns None if the language is unsupported or the
    file can't be read.
    """
    lang = detect_language(path)
    if lang is None:
        return None
    try:
        source = path.read_bytes()
    except OSError:
        return None
    if not source:
        return None

    parser = registry.get(lang)
    # The tree-sitter-language-pack Parser exposed on Windows wheels accepts
    # str (not bytes). Tree-sitter still reports byte offsets, so we keep
    # `source` as bytes for slicing and pass the decoded text to parse().
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        text = source.decode("utf-8", errors="replace")
    tree = parser.parse(text)
    sha = hashlib.sha1(source).hexdigest()
    relpath = path.resolve().relative_to(repo_root.resolve()).as_posix()
    return ParsedFile(
        relpath=relpath,
        language=lang.name,
        source=source,
        tree=tree,
        sha=sha,
    )
