"""Admin CLI for the karst enterprise gateway.

    python -m enterprise.gateway.cli keys add --team acme --label "CI bot"
    python -m enterprise.gateway.cli keys list --team acme
    python -m enterprise.gateway.cli keys revoke <id>
    python -m enterprise.gateway.cli usage --team acme

State lives in a single sqlite file (``--db``, default ~/.karst/enterprise/gateway.db).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .keys import DEFAULT_SCOPES, KeyStore
from .usage import UsageLog


def _default_db() -> Path:
    return Path.home() / ".karst" / "enterprise" / "gateway.db"


def _ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="karst-enterprise", description="karst enterprise gateway admin.")
    parser.add_argument("--db", help="Gateway sqlite path (default ~/.karst/enterprise/gateway.db).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keys = sub.add_parser("keys", help="Manage API keys.")
    keys_sub = p_keys.add_subparsers(dest="keys_command", required=True)

    p_add = keys_sub.add_parser("add", help="Create a key (shown once).")
    p_add.add_argument("--team", required=True)
    p_add.add_argument("--label", default="")
    p_add.add_argument("--scopes", default=",".join(DEFAULT_SCOPES),
                       help="Comma-separated tool scopes, or '*' for all.")

    p_list = keys_sub.add_parser("list", help="List keys.")
    p_list.add_argument("--team")

    p_rev = keys_sub.add_parser("revoke", help="Revoke a key by id.")
    p_rev.add_argument("id", type=int)

    p_usage = sub.add_parser("usage", help="Show usage summary.")
    p_usage.add_argument("--team")
    p_usage.add_argument("--days", type=int, default=30)

    args = parser.parse_args(argv)
    db = Path(args.db) if args.db else _default_db()
    _ensure_parent(db)

    if args.command == "keys":
        store = KeyStore(db)
        try:
            if args.keys_command == "add":
                scopes = tuple(s.strip() for s in args.scopes.split(",") if s.strip())
                raw, kid = store.create_key(args.team, label=args.label, scopes=scopes)
                print(f"Created key #{kid} for team '{args.team}'.")
                print("\n  " + raw + "\n")
                print("Store it now — it will NOT be shown again.")
                return 0
            if args.keys_command == "list":
                rows = store.list_keys(args.team)
                if not rows:
                    print("No keys.")
                    return 0
                print(f"{'ID':>3}  {'TEAM':<16} {'PREFIX':<14} {'LABEL':<20} STATUS  SCOPES")
                for k in rows:
                    status = "revoked" if k.revoked_at else "active "
                    print(f"{k.id:>3}  {k.team_id:<16} {k.prefix:<14} {k.label:<20} {status} {','.join(k.scopes)}")
                return 0
            if args.keys_command == "revoke":
                ok = store.revoke(args.id)
                print("Revoked." if ok else "No active key with that id.")
                return 0 if ok else 1
        finally:
            store.close()

    if args.command == "usage":
        log = UsageLog(db)
        try:
            since = time.time() - args.days * 86400
            s = log.summary(team_id=args.team, since=since)
            scope = f"team '{args.team}'" if args.team else "all teams"
            print(f"Usage for {scope}, last {args.days}d:")
            print(f"  calls:      {s['calls']:,}")
            print(f"  tokens in:  {s['tokens_in']:,}")
            print(f"  tokens out: {s['tokens_out']:,}")
            print(f"  errors:     {s['errors']:,}")
            return 0
        finally:
            log.close()

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
