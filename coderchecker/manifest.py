"""Index manifest — tracks file SHAs so we can skip unchanged files on re-index.

This delivers the biggest time saving in Phase 4: re-indexing Byfoods
goes from 30s to under 2s when nothing has changed, because the embedder
is never invoked.

Schema: JSON dict from repo-relative path to the file SHA we last indexed.
Persisted next to the Qdrant collection in the storage directory.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1


@dataclass
class FileEntry:
    sha: str
    chunk_count: int = 0
    indexed_at: str = ""


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    embedding_model: str | None = None
    files: dict[str, FileEntry] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.files is None:
            self.files = {}


def manifest_path(storage_dir: str | Path) -> Path:
    return Path(storage_dir) / MANIFEST_FILENAME


def load_manifest(storage_dir: str | Path) -> Manifest:
    path = manifest_path(storage_dir)
    if not path.is_file():
        return Manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Manifest()
    if int(raw.get("version", 0)) != MANIFEST_VERSION:
        # Schema version mismatch — caller will rebuild.
        return Manifest()
    files = {
        path_: FileEntry(
            sha=str(entry.get("sha", "")),
            chunk_count=int(entry.get("chunk_count", 0)),
            indexed_at=str(entry.get("indexed_at", "")),
        )
        for path_, entry in (raw.get("files") or {}).items()
    }
    return Manifest(
        version=int(raw["version"]),
        embedding_model=raw.get("embedding_model"),
        files=files,
    )


def save_manifest(storage_dir: str | Path, manifest: Manifest) -> None:
    path = manifest_path(storage_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": manifest.version,
        "embedding_model": manifest.embedding_model,
        "files": {p: asdict(e) for p, e in manifest.files.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def file_sha(path: str | Path) -> str:
    """Compute a stable SHA-1 of a file's bytes. SHA-1 is plenty for change
    detection; we don't need cryptographic strength here.
    """
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
