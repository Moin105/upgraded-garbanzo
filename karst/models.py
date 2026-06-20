from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class ChunkKind(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    MODULE = "module"
    FILE = "file"


@dataclass
class SourceFile:
    path: Path
    relpath: str
    language: str
    size_bytes: int
    sha: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = str(self.path)
        return d


@dataclass
class Chunk:
    """A single AST-aware unit of code — a function, class, method, etc.

    Carries enough metadata to act as a citation: file + line range + sha.
    """

    file_relpath: str
    language: str
    kind: ChunkKind
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    code: str
    file_sha: str
    parent: str | None = None
    signature: str | None = None
    chunk_id: str = field(init=False)

    def __post_init__(self) -> None:
        h = hashlib.sha1(
            f"{self.file_relpath}:{self.start_byte}:{self.end_byte}:{self.file_sha}".encode()
        ).hexdigest()[:16]
        self.chunk_id = f"chunk_{h}"

    @property
    def citation(self) -> str:
        return f"{self.file_relpath}:{self.start_line}-{self.end_line}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d
