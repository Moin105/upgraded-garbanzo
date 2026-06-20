"""Map chunks to packs by matching their file path against pack scope globs.

Used at two points:
1. Auto-suggest — when we propose packs, we tag chunks immediately.
2. Indexing — when a chunk is upserted to Qdrant we attach its pack ids as
   a payload field, so later searches can filter on `packs CONTAINS ?`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass

from .models import Pack


@dataclass(frozen=True)
class CompiledPack:
    id: str
    patterns: tuple[str, ...]

    def matches(self, relpath: str) -> bool:
        # Match against forward-slash form for cross-platform consistency.
        rp = relpath.replace("\\", "/")
        for pat in self.patterns:
            if fnmatch.fnmatchcase(rp, pat):
                return True
            # Also try matching against any path segment for convenience:
            # `auth/**` should match `backend/auth/login.ts`.
            if "/" in pat and not pat.startswith("**/"):
                if fnmatch.fnmatchcase(rp, f"**/{pat}"):
                    return True
        return False


def compile_packs(packs: Iterable[Pack]) -> list[CompiledPack]:
    return [CompiledPack(id=p.id, patterns=tuple(p.scope)) for p in packs]


def tag_relpath(compiled: list[CompiledPack], relpath: str) -> list[str]:
    return [cp.id for cp in compiled if cp.matches(relpath)]
