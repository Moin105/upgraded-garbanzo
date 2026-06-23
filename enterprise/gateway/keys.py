"""Per-team API keys for the karst enterprise gateway.

Multi-tenant: each key belongs to a `team_id` and carries `scopes` (which MCP
tools it may call). Keys are shown **once** at creation — like every real API
platform — and stored only as a SHA-256 hash, so a DB leak never reveals a
usable key. Backed by sqlite (zero extra deps); the schema ports cleanly to
Postgres for production.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .db import Db


def _csv(s: str | None) -> tuple[str, ...]:
    return tuple(x for x in (s or "").split(",") if x)

# Human-recognisable prefix (think `sk-...`). The first chars are also stored in
# clear as `prefix` so admins can identify a key in a list without seeing it.
KEY_PREFIX = "kst_sk_"

DEFAULT_SCOPES: tuple[str, ...] = ("search_code", "find_impact", "list_packs", "index_status")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller behind a request."""

    key_id: int
    team_id: str
    label: str
    scopes: tuple[str, ...]
    repos: tuple[str, ...] = ("*",)   # which repos this principal may query; "*" = all

    def may(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes

    def may_access_repo(self, repo_path: str | None) -> bool:
        """Whether this principal may target `repo_path`. Matches by the repo's
        folder name or its full normalised path; "*" allows any."""
        if "*" in self.repos:
            return True
        if not repo_path:
            return False  # a repo-scoped principal must name an allowed repo
        norm = repo_path.replace("\\", "/").rstrip("/")
        name = norm.rsplit("/", 1)[-1]
        return name in self.repos or norm in self.repos


@dataclass(frozen=True)
class KeyInfo:
    """A key as shown to an admin (never includes the secret)."""

    id: int
    team_id: str
    label: str
    prefix: str
    scopes: tuple[str, ...]
    repos: tuple[str, ...]
    created_at: float
    revoked_at: float | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key_hash   TEXT NOT NULL UNIQUE,
  prefix     TEXT NOT NULL,
  team_id    TEXT NOT NULL,
  label      TEXT NOT NULL DEFAULT '',
  scopes     TEXT NOT NULL DEFAULT '',
  repos      TEXT NOT NULL DEFAULT '*',
  created_at REAL NOT NULL,
  revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_team ON api_keys(team_id);
"""


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class KeyStore:
    def __init__(self, path: str | Path) -> None:
        self.db = Db(path)
        self.db.executescript(_SCHEMA)
        # Best-effort migration for DBs created before the repos column existed.
        try:
            self.db.run("ALTER TABLE api_keys ADD COLUMN repos TEXT NOT NULL DEFAULT '*'")
        except Exception:
            pass  # column already present

    # -- write ---------------------------------------------------------------
    def create_key(
        self,
        team_id: str,
        *,
        label: str = "",
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
        repos: tuple[str, ...] = ("*",),
    ) -> tuple[str, int]:
        """Create a key. Returns ``(plaintext_key, key_id)``. The plaintext is
        the ONLY time the secret exists — store the hash, show the user once.

        ``repos`` limits which repos the key may query ("*" = all). Set it to
        the team's repo folder names for per-team isolation on a shared host."""
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        kid = self.db.insert(
            "INSERT INTO api_keys (key_hash, prefix, team_id, label, scopes, repos, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_hash(raw), raw[: len(KEY_PREFIX) + 6], team_id, label,
             ",".join(scopes), ",".join(repos) or "*", time.time()),
        )
        return raw, kid

    def revoke(self, key_id: int) -> bool:
        res = self.db.run(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (time.time(), key_id),
        )
        return res.rowcount > 0

    # -- read ----------------------------------------------------------------
    def verify(self, presented: str | None) -> Principal | None:
        """Resolve a presented key to a Principal, or None if invalid/revoked.

        Lookup is by hash, so the secret is never compared in clear and a
        revoked key never authenticates."""
        if not presented:
            return None
        row = self.db.run(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
            (_hash(presented),),
        ).one()
        if row is None:
            return None
        return Principal(
            key_id=row["id"],
            team_id=row["team_id"],
            label=row["label"],
            scopes=_csv(row["scopes"]),
            repos=_csv(row["repos"]) or ("*",),
        )

    def list_keys(self, team_id: str | None = None) -> list[KeyInfo]:
        if team_id:
            rows = self.db.run(
                "SELECT * FROM api_keys WHERE team_id = ? ORDER BY created_at DESC", (team_id,)
            ).all()
        else:
            rows = self.db.run("SELECT * FROM api_keys ORDER BY created_at DESC").all()
        return [
            KeyInfo(
                id=r["id"],
                team_id=r["team_id"],
                label=r["label"],
                prefix=r["prefix"],
                scopes=_csv(r["scopes"]),
                repos=_csv(r["repos"]) or ("*",),
                created_at=r["created_at"],
                revoked_at=r["revoked_at"],
            )
            for r in rows
        ]

    def close(self) -> None:
        self.db.close()
