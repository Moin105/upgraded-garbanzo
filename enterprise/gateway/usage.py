"""Usage metering + audit log for the karst enterprise gateway.

Every authenticated call is recorded: who (key/team), what (tool/repo), cost
(tokens), latency, and success. This is the data behind the two enterprise
must-haves — **billing/usage** ("how many tokens did team X spend") and
**audit** ("who accessed which repo, when"). sqlite now; Postgres-portable.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          REAL NOT NULL,
  key_id      INTEGER,
  team_id     TEXT,
  tool        TEXT,
  repo        TEXT,
  tokens_in   INTEGER NOT NULL DEFAULT 0,
  tokens_out  INTEGER NOT NULL DEFAULT 0,
  latency_ms  INTEGER NOT NULL DEFAULT 0,
  ok          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_usage_team_ts ON usage_events(team_id, ts);
"""


class UsageLog:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def log(
        self,
        *,
        key_id: int | None = None,
        team_id: str | None = None,
        tool: str | None = None,
        repo: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        ok: bool = True,
    ) -> None:
        self._conn.execute(
            "INSERT INTO usage_events"
            " (ts, key_id, team_id, tool, repo, tokens_in, tokens_out, latency_ms, ok)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), key_id, team_id, tool, repo, tokens_in, tokens_out, latency_ms, 1 if ok else 0),
        )
        self._conn.commit()

    def summary(self, *, team_id: str | None = None, since: float | None = None) -> dict:
        where: list[str] = []
        args: list[object] = []
        if team_id:
            where.append("team_id = ?")
            args.append(team_id)
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        row = self._conn.execute(
            "SELECT COUNT(*) AS calls,"
            " COALESCE(SUM(tokens_in), 0) AS tokens_in,"
            " COALESCE(SUM(tokens_out), 0) AS tokens_out,"
            " COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), 0) AS errors"
            f" FROM usage_events{clause}",
            args,
        ).fetchone()
        return {
            "calls": row["calls"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "errors": row["errors"],
        }

    def recent(self, *, limit: int = 50, team_id: str | None = None) -> list[dict]:
        if team_id:
            rows = self._conn.execute(
                "SELECT * FROM usage_events WHERE team_id = ? ORDER BY ts DESC LIMIT ?",
                (team_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM usage_events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
