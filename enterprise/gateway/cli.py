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

from .db import is_postgres, resolve_url
from .keys import DEFAULT_SCOPES, KeyStore
from .usage import UsageLog


def _default_db() -> Path:
    return Path.home() / ".karst" / "enterprise" / "gateway.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="karst-enterprise", description="karst enterprise gateway admin.")
    parser.add_argument(
        "--db",
        help="Gateway store: a sqlite path or a postgres:// URL. Defaults to "
        "$DATABASE_URL, else ~/.karst/enterprise/gateway.db.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_keys = sub.add_parser("keys", help="Manage API keys.")
    keys_sub = p_keys.add_subparsers(dest="keys_command", required=True)

    p_add = keys_sub.add_parser("add", help="Create a key (shown once).")
    p_add.add_argument("--team", required=True)
    p_add.add_argument("--label", default="")
    p_add.add_argument("--scopes", default=",".join(DEFAULT_SCOPES),
                       help="Comma-separated tool scopes, or '*' for all.")
    p_add.add_argument("--repos", default="*",
                       help="Comma-separated repo names this key may query, or "
                            "'*' for all. Scope a team to its repos for isolation "
                            "on a shared host, e.g. --repos acme-app,acme-api.")

    p_list = keys_sub.add_parser("list", help="List keys.")
    p_list.add_argument("--team")

    p_rev = keys_sub.add_parser("revoke", help="Revoke a key by id.")
    p_rev.add_argument("id", type=int)

    p_usage = sub.add_parser("usage", help="Show usage summary.")
    p_usage.add_argument("--team")
    p_usage.add_argument("--days", type=int, default=30)

    # team pack library
    p_packs = sub.add_parser("packs", help="Manage the team's shared pack library.")
    packs_sub = p_packs.add_subparsers(dest="packs_command", required=True)
    pp_pub = packs_sub.add_parser("publish", help="Publish a pack definition (auto-versioned).")
    pp_pub.add_argument("--team", required=True)
    pp_pub.add_argument("--name", required=True)
    pp_pub.add_argument("--glob", action="append", required=True, dest="globs",
                        help="Glob scope (repeatable), e.g. --glob 'src/auth/**'.")
    pp_pub.add_argument("--desc", default="")
    pp_list = packs_sub.add_parser("list", help="List the team's packs.")
    pp_list.add_argument("--team", required=True)
    pp_pull = packs_sub.add_parser("pull", help="Show how to recreate a team pack locally.")
    pp_pull.add_argument("--team", required=True)
    pp_pull.add_argument("--name", required=True)
    pp_pull.add_argument("--version", type=int)

    # serve the gateway
    p_serve = sub.add_parser("serve", help="Run the gateway (karst MCP + per-key auth + metering).")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8080)

    args = parser.parse_args(argv)
    # Explicit --db wins; else $DATABASE_URL (production Postgres); else the
    # local sqlite default.
    db = args.db if args.db else resolve_url(str(_default_db()))
    if not is_postgres(db):
        Path(db).parent.mkdir(parents=True, exist_ok=True)

    if args.command == "keys":
        store = KeyStore(db)
        try:
            if args.keys_command == "add":
                scopes = tuple(s.strip() for s in args.scopes.split(",") if s.strip())
                repos = tuple(s.strip() for s in args.repos.split(",") if s.strip()) or ("*",)
                raw, kid = store.create_key(args.team, label=args.label, scopes=scopes, repos=repos)
                print(f"Created key #{kid} for team '{args.team}'  (repos: {','.join(repos)}).")
                print("\n  " + raw + "\n")
                print("Store it now — it will NOT be shown again.")
                return 0
            if args.keys_command == "list":
                rows = store.list_keys(args.team)
                if not rows:
                    print("No keys.")
                    return 0
                print(f"{'ID':>3}  {'TEAM':<16} {'PREFIX':<14} {'LABEL':<16} STATUS  {'REPOS':<18} SCOPES")
                for k in rows:
                    status = "revoked" if k.revoked_at else "active "
                    print(f"{k.id:>3}  {k.team_id:<16} {k.prefix:<14} {k.label:<16} {status} "
                          f"{','.join(k.repos):<18} {','.join(k.scopes)}")
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

    if args.command == "packs":
        from .packs import PackRegistry

        reg = PackRegistry(db)
        try:
            if args.packs_command == "publish":
                p = reg.publish(args.team, args.name, args.globs, description=args.desc)
                print(f"Published '{p.name}' v{p.version} for team '{p.team_id}'  (globs: {', '.join(p.globs)})")
                return 0
            if args.packs_command == "list":
                rows = reg.list_packs(args.team)
                if not rows:
                    print("No packs in this team's library yet.")
                    return 0
                print(f"{'NAME':<24} {'VER':>3}  {'GLOBS':<40} DESCRIPTION")
                for p in rows:
                    print(f"{p.name:<24} {p.version:>3}  {','.join(p.globs):<40} {p.description}")
                return 0
            if args.packs_command == "pull":
                p = reg.get(args.team, args.name, version=args.version)
                if p is None:
                    print("No such pack.")
                    return 1
                print(f"# Team pack '{p.name}' v{p.version}. Recreate it on your local index:")
                glob_args = " ".join(f"--glob '{g}'" for g in p.globs)
                print(f"karst packs create {p.name} {glob_args}")
                return 0
        finally:
            reg.close()

    if args.command == "serve":
        from .serve import serve as _serve

        _serve(host=args.host, port=args.port, db=db)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
