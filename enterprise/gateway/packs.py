"""Team pack libraries — the enterprise wedge.

karst's free core lets one developer curate "packs" (named bundles of related
code, scoped by glob). The pain at team scale: everyone re-curates the same
packs. This registry lets a team **publish** a pack definition once and have
every member **pull** it — so curation effort is spent once, not per-dev.

A pack *definition* is shareable (name + glob scope + intent); each member
applies it to their own local index (`karst packs create …`). We version every
publish so a team can evolve packs without breaking pinned members. sqlite now;
Postgres-portable schema.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class TeamPack:
    id: int
    team_id: str
    name: str
    version: int
    globs: tuple[str, ...]
    description: str
    created_at: float
    created_by: int | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_packs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  version     INTEGER NOT NULL,
  globs       TEXT NOT NULL DEFAULT '[]',
  description TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  created_by  INTEGER,
  UNIQUE(team_id, name, version)
);
CREATE INDEX IF NOT EXISTS idx_team_packs_team_name ON team_packs(team_id, name, version);
"""


def _row(r: sqlite3.Row) -> TeamPack:
    return TeamPack(
        id=r["id"],
        team_id=r["team_id"],
        name=r["name"],
        version=r["version"],
        globs=tuple(json.loads(r["globs"])),
        description=r["description"],
        created_at=r["created_at"],
        created_by=r["created_by"],
    )


class PackRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def publish(
        self,
        team_id: str,
        name: str,
        globs: list[str] | tuple[str, ...],
        *,
        description: str = "",
        created_by: int | None = None,
    ) -> TeamPack:
        """Publish a new version of a pack. Version auto-increments per
        (team, name), so publishing is always non-destructive."""
        if not name.strip():
            raise ValueError("pack name is required")
        if not globs:
            raise ValueError("a pack needs at least one glob scope")
        latest = self._conn.execute(
            "SELECT MAX(version) AS v FROM team_packs WHERE team_id = ? AND name = ?",
            (team_id, name),
        ).fetchone()
        version = int((latest["v"] or 0)) + 1
        cur = self._conn.execute(
            "INSERT INTO team_packs (team_id, name, version, globs, description, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team_id, name, version, json.dumps(list(globs)), description, time.time(), created_by),
        )
        self._conn.commit()
        return self.get(team_id, name, version=version)  # type: ignore[return-value]

    def get(self, team_id: str, name: str, *, version: int | None = None) -> TeamPack | None:
        """Get a pack — the latest version unless one is pinned."""
        if version is None:
            r = self._conn.execute(
                "SELECT * FROM team_packs WHERE team_id = ? AND name = ?"
                " ORDER BY version DESC LIMIT 1",
                (team_id, name),
            ).fetchone()
        else:
            r = self._conn.execute(
                "SELECT * FROM team_packs WHERE team_id = ? AND name = ? AND version = ?",
                (team_id, name, version),
            ).fetchone()
        return _row(r) if r else None

    def list_packs(self, team_id: str) -> list[TeamPack]:
        """Latest version of every pack in the team's library."""
        rows = self._conn.execute(
            "SELECT * FROM team_packs t WHERE team_id = ? AND version ="
            " (SELECT MAX(version) FROM team_packs WHERE team_id = t.team_id AND name = t.name)"
            " ORDER BY name ASC",
            (team_id,),
        ).fetchall()
        return [_row(r) for r in rows]

    def history(self, team_id: str, name: str) -> list[TeamPack]:
        rows = self._conn.execute(
            "SELECT * FROM team_packs WHERE team_id = ? AND name = ? ORDER BY version DESC",
            (team_id, name),
        ).fetchall()
        return [_row(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
