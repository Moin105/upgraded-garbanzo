"""Pack data model (spec §22).

A pack is a named, versioned, attachable slice of repo knowledge. The single
biggest cost lever we have: a pack-scoped retrieval reads ~200 chunks instead
of 5000, which is 60-80% input-token savings on most queries.

Fields are deliberately small. We do NOT inline the chunks themselves —
chunks live in Qdrant tagged with the pack id, so a pack is just a label
plus a scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Pack:
    id: str                     # slug — e.g. "pack_auth"
    label: str                  # human label — "Auth Module"
    scope: list[str] = field(default_factory=list)        # glob patterns
    summary: str | None = None
    token_estimate: int = 0
    chunk_count: int = 0
    refreshed_at: str = ""
    auto: bool = False          # auto-suggested vs user-created

    @classmethod
    def now_iso(cls) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def touch(self) -> None:
        self.refreshed_at = self.now_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "Pack":
        return cls(
            id=row[0],
            label=row[1],
            scope=json.loads(row[2]) if row[2] else [],
            summary=row[3],
            token_estimate=int(row[4] or 0),
            chunk_count=int(row[5] or 0),
            refreshed_at=row[6] or "",
            auto=bool(row[7]),
        )

    @classmethod
    def slug_from_label(cls, label: str) -> str:
        slug = "".join(
            c.lower() if c.isalnum() else "_" for c in label.strip()
        ).strip("_")
        # Collapse runs of underscores.
        while "__" in slug:
            slug = slug.replace("__", "_")
        return f"pack_{slug}" if not slug.startswith("pack_") else slug
