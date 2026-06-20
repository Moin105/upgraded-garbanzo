"""PackStore: sqlite-backed pack registry, one db per repo.

Sqlite is the right primitive here: we want serializable updates (so two
CLI calls don't trample each other), per-repo isolation (so two indexes
don't conflict), and zero infrastructure (no Docker, no server). The db
lives inside the same storage directory as the Qdrant index.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from .models import Pack

SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    scope_json      TEXT NOT NULL,
    summary         TEXT,
    token_estimate  INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    refreshed_at    TEXT,
    auto            INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_packs_label ON packs(label);
"""


class PackStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- CRUD --------------------------------------------------------------

    def upsert(self, pack: Pack) -> None:
        if not pack.refreshed_at:
            pack.touch()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO packs (id, label, scope_json, summary, token_estimate,
                                   chunk_count, refreshed_at, auto)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label = excluded.label,
                    scope_json = excluded.scope_json,
                    summary = excluded.summary,
                    token_estimate = excluded.token_estimate,
                    chunk_count = excluded.chunk_count,
                    refreshed_at = excluded.refreshed_at,
                    auto = excluded.auto
                """,
                (
                    pack.id,
                    pack.label,
                    json.dumps(pack.scope),
                    pack.summary,
                    pack.token_estimate,
                    pack.chunk_count,
                    pack.refreshed_at,
                    1 if pack.auto else 0,
                ),
            )

    def upsert_many(self, packs: Iterable[Pack]) -> int:
        n = 0
        for p in packs:
            self.upsert(p)
            n += 1
        return n

    def get(self, pack_id: str) -> Pack | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, label, scope_json, summary, token_estimate, "
                "chunk_count, refreshed_at, auto FROM packs WHERE id = ?",
                (pack_id,),
            ).fetchone()
        return Pack.from_row(row) if row else None

    def list(self) -> list[Pack]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, label, scope_json, summary, token_estimate, "
                "chunk_count, refreshed_at, auto FROM packs ORDER BY label"
            ).fetchall()
        return [Pack.from_row(r) for r in rows]

    def delete(self, pack_id: str) -> bool:
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
            return cur.rowcount > 0

    def delete_auto(self) -> int:
        """Drop all auto-suggested packs. Used before re-running suggest."""
        with self.transaction() as conn:
            cur = conn.execute("DELETE FROM packs WHERE auto = 1")
            return cur.rowcount
