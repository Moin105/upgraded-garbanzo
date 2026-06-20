"""CLI for `karst packs` — spec §22/§23 made textual.

Verbs:
    packs list                — show every pack in this repo's index
    packs suggest <repo>      — derive packs from the repo's chunks
    packs show <id>           — pack details + sample files
    packs create <label>      — manually create a pack with a glob scope
    packs delete <id>         — remove
    packs attach <id ...>     — make pack active for the next query
    packs detach <id ...>     — drop from active
    packs pin <id ...>        — make pack active for every query
    packs unpin <id ...>      — remove from pinned
    packs status              — what's attached/pinned right now
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze_repo
from .packs.models import Pack
from .packs.store import PackStore
from .packs.suggest import suggest_packs
from .packs.tagger import compile_packs
from .state import (
    attach,
    clear_attached,
    detach,
    load_state,
    pin,
    save_state,
    unpin,
)
from .store import DEFAULT_COLLECTION, ChunkStore


def add_packs_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "packs",
        help="Manage context packs (list/suggest/show/attach/detach/pin/unpin/status).",
    )
    p.add_argument(
        "--storage",
        required=True,
        help="Index storage path (same as used by `index`).",
    )

    verbs = p.add_subparsers(dest="verb", required=True)

    verbs.add_parser("list", help="List packs in this repo's index.")
    verbs.add_parser("status", help="Show currently attached/pinned packs.")

    s_show = verbs.add_parser("show", help="Show one pack's details.")
    s_show.add_argument("pack_id")

    s_del = verbs.add_parser("delete", help="Remove a pack from the registry.")
    s_del.add_argument("pack_id")

    s_suggest = verbs.add_parser(
        "suggest",
        help="Derive packs from a repo's directory structure.",
    )
    s_suggest.add_argument("path", help="Path to the same repo that was indexed.")
    s_suggest.add_argument(
        "--apply",
        action="store_true",
        help="Save the suggested packs (default just previews).",
    )
    s_suggest.add_argument(
        "--retag",
        action="store_true",
        help="After apply, rewrite the `packs` payload on every existing chunk.",
    )
    s_suggest.add_argument(
        "--collection", default=DEFAULT_COLLECTION,
        help="Qdrant collection (used by --retag).",
    )
    s_suggest.add_argument(
        "--min-chunks", type=int, default=3,
        help="Minimum chunks for a pack to be suggested (default 3).",
    )

    s_create = verbs.add_parser("create", help="Create a pack with a glob scope.")
    s_create.add_argument("label")
    s_create.add_argument("--scope", action="append", required=True,
                          help="Glob pattern (repeatable).")
    s_create.add_argument("--summary")

    for verb in ("attach", "detach", "pin", "unpin"):
        s = verbs.add_parser(verb, help=f"{verb.capitalize()} one or more packs.")
        s.add_argument("pack_ids", nargs="+")

    p.set_defaults(func=_cmd_packs)


def _cmd_packs(args: argparse.Namespace) -> int:
    storage = Path(args.storage)
    if not storage.exists():
        print(f"error: storage path does not exist: {storage}", file=sys.stderr)
        return 2
    pack_store = PackStore(storage / "packs.sqlite")

    verb = args.verb
    if verb == "list":
        return _list_packs(pack_store)
    if verb == "status":
        return _show_status(storage, pack_store)
    if verb == "show":
        return _show_pack(pack_store, args.pack_id)
    if verb == "delete":
        ok = pack_store.delete(args.pack_id)
        print("deleted" if ok else "no such pack", file=sys.stderr)
        return 0 if ok else 1
    if verb == "suggest":
        return _suggest(args, storage, pack_store)
    if verb == "create":
        return _create(args, pack_store)
    if verb in ("attach", "detach", "pin", "unpin"):
        return _toggle(args, storage, pack_store, verb)
    print(f"error: unknown verb '{verb}'", file=sys.stderr)
    return 2


# ------------------------------------------------------------------ verbs

def _list_packs(pack_store: PackStore) -> int:
    packs = pack_store.list()
    if not packs:
        print("No packs yet. Run `packs suggest <repo> --apply` to create some.",
              file=sys.stderr)
        return 0
    print(f"{'ID':<32} {'LABEL':<30} {'CHUNKS':>7} {'TOKENS':>8} {'AUTO':<4}")
    for p in packs:
        print(
            f"{p.id:<32} {p.label[:30]:<30} "
            f"{p.chunk_count:>7} {p.token_estimate:>8} "
            f"{'yes' if p.auto else 'no':<4}"
        )
    return 0


def _show_pack(pack_store: PackStore, pack_id: str) -> int:
    pack = pack_store.get(pack_id)
    if pack is None:
        print(f"no such pack: {pack_id}", file=sys.stderr)
        return 1
    print(f"id:             {pack.id}")
    print(f"label:          {pack.label}")
    print(f"scope:          {pack.scope}")
    print(f"summary:        {pack.summary or '(none)'}")
    print(f"chunks:         {pack.chunk_count}")
    print(f"token_estimate: {pack.token_estimate}")
    print(f"auto:           {pack.auto}")
    print(f"refreshed_at:   {pack.refreshed_at}")
    return 0


def _show_status(storage: Path, pack_store: PackStore) -> int:
    state = load_state(storage)
    print(f"Pinned:   {state.pinned_packs or '(none)'}")
    print(f"Attached: {state.attached_packs or '(none)'}")
    print(f"Excluded: {state.excluded_packs or '(none)'}")
    active = state.all_active_packs()
    if active:
        print(f"\nActive packs ({len(active)}):", file=sys.stderr)
        for pid in active:
            pack = pack_store.get(pid)
            if pack is None:
                print(f"  {pid} (missing — orphaned)", file=sys.stderr)
            else:
                print(f"  {pid:<32} {pack.label:<30}  chunks={pack.chunk_count}",
                      file=sys.stderr)
    return 0


def _suggest(args: argparse.Namespace, storage: Path, pack_store: PackStore) -> int:
    root = Path(args.path)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    print(f"Analyzing {root}…", file=sys.stderr)
    all_chunks = []
    for result in analyze_repo(root):
        all_chunks.extend(result.chunks)
    print(f"  scanned {len(all_chunks)} chunks", file=sys.stderr)

    candidates = suggest_packs(all_chunks, min_chunks_per_pack=args.min_chunks)
    if not candidates:
        print("No packs suggested (repo too small or all top-level files).",
              file=sys.stderr)
        return 0

    print(f"\nSuggested {len(candidates)} packs:")
    for cand in candidates:
        p = cand.pack
        print(f"  - {p.id:<32} {p.label:<28}  chunks={p.chunk_count:<4}  "
              f"tokens~={p.token_estimate:<7}")
        if cand.sample_files:
            print(f"      files: {', '.join(cand.sample_files[:3])}"
                  + (" …" if len(cand.sample_files) > 3 else ""))

    if not args.apply:
        print("\n(preview only — pass --apply to save these packs)", file=sys.stderr)
        return 0

    pack_store.delete_auto()
    n = pack_store.upsert_many(c.pack for c in candidates)
    print(f"\nSaved {n} auto-suggested packs.", file=sys.stderr)

    if args.retag:
        print("Re-tagging existing chunks with new pack scopes…", file=sys.stderr)
        compiled = compile_packs(pack_store.list())

        def tagger(relpath: str) -> list[str]:
            from .packs.tagger import tag_relpath
            return tag_relpath(compiled, relpath)

        chunk_store = ChunkStore(location=storage, collection=args.collection)
        try:
            updated = chunk_store.retag_with_packs(tagger)
        finally:
            chunk_store.close()
        print(f"Re-tagged {updated} chunks.", file=sys.stderr)
    return 0


def _create(args: argparse.Namespace, pack_store: PackStore) -> int:
    pack = Pack(
        id=Pack.slug_from_label(args.label),
        label=args.label,
        scope=args.scope,
        summary=args.summary,
        auto=False,
    )
    pack.touch()
    pack_store.upsert(pack)
    print(f"Created pack {pack.id}", file=sys.stderr)
    print(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False))
    return 0


def _toggle(
    args: argparse.Namespace,
    storage: Path,
    pack_store: PackStore,
    verb: str,
) -> int:
    # Validate that each pack id exists, but allow attach for orphaned ids
    # (the user may run `packs suggest --apply` later).
    unknown = [p for p in args.pack_ids if pack_store.get(p) is None]
    if unknown:
        print(
            f"warning: unknown pack id(s): {unknown}. Operation will still "
            "proceed; the ids will resolve once the packs are created.",
            file=sys.stderr,
        )

    if verb == "attach":
        state = attach(storage, args.pack_ids)
    elif verb == "detach":
        state = detach(storage, args.pack_ids)
    elif verb == "pin":
        state = pin(storage, args.pack_ids)
    elif verb == "unpin":
        state = unpin(storage, args.pack_ids)
    else:  # unreachable per argparse
        return 2

    print(f"Pinned:   {state.pinned_packs}", file=sys.stderr)
    print(f"Attached: {state.attached_packs}", file=sys.stderr)
    return 0
