"""End-to-end analyze pipeline: walk → parse → chunk.

Holds the public surface that the CLI (and later, agents) will call.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .chunker import chunk_file
from .models import Chunk
from .parser import ParsedFile, ParserRegistry, parse_file
from .walker import iter_source_files


@dataclass
class FileResult:
    parsed: ParsedFile
    chunks: list[Chunk]


def analyze_repo(root: str | Path) -> Iterator[FileResult]:
    """Iterate over every supported source file under `root`, yielding the
    parsed file + its extracted chunks.

    Streaming — callers can write JSONL as it flows, without holding the
    whole repo in memory.
    """
    root_path = Path(root).resolve()
    registry = ParserRegistry()

    for file_path in iter_source_files(root_path):
        parsed = parse_file(file_path, repo_root=root_path, registry=registry)
        if parsed is None:
            continue
        chunks = chunk_file(parsed)
        yield FileResult(parsed=parsed, chunks=chunks)
