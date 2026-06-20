"""Per-repo state file.

Solves the cross-session amnesia problem: which packs the user attached or
pinned should survive shell restarts. Lives next to the Qdrant index and
the graph pickle so the lifetime ties to the index lifetime.

Pinned vs attached (spec §20):
- pinned   — included in every query against this repo until explicitly unpinned
- attached — included in the next query only, then auto-cleared
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path


STATE_FILENAME = "state.json"


@dataclass
class RepoState:
    pinned_packs: list[str] = field(default_factory=list)
    attached_packs: list[str] = field(default_factory=list)
    excluded_packs: list[str] = field(default_factory=list)
    default_top_k: int = 8

    def all_active_packs(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for p in (*self.pinned_packs, *self.attached_packs):
            if p in self.excluded_packs or p in seen:
                continue
            seen.add(p)
            out.append(p)
        return out


def state_path(storage_dir: str | Path) -> Path:
    return Path(storage_dir) / STATE_FILENAME


def load_state(storage_dir: str | Path) -> RepoState:
    path = state_path(storage_dir)
    if not path.is_file():
        return RepoState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RepoState()
    return RepoState(
        pinned_packs=list(raw.get("pinned_packs") or []),
        attached_packs=list(raw.get("attached_packs") or []),
        excluded_packs=list(raw.get("excluded_packs") or []),
        default_top_k=int(raw.get("default_top_k") or 8),
    )


def save_state(storage_dir: str | Path, state: RepoState) -> None:
    path = state_path(storage_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def attach(storage_dir: str | Path, pack_ids: list[str]) -> RepoState:
    state = load_state(storage_dir)
    for pid in pack_ids:
        if pid not in state.attached_packs:
            state.attached_packs.append(pid)
    save_state(storage_dir, state)
    return state


def detach(storage_dir: str | Path, pack_ids: list[str]) -> RepoState:
    state = load_state(storage_dir)
    keep = [p for p in state.attached_packs if p not in pack_ids]
    state.attached_packs = keep
    save_state(storage_dir, state)
    return state


def pin(storage_dir: str | Path, pack_ids: list[str]) -> RepoState:
    state = load_state(storage_dir)
    for pid in pack_ids:
        if pid not in state.pinned_packs:
            state.pinned_packs.append(pid)
    save_state(storage_dir, state)
    return state


def unpin(storage_dir: str | Path, pack_ids: list[str]) -> RepoState:
    state = load_state(storage_dir)
    state.pinned_packs = [p for p in state.pinned_packs if p not in pack_ids]
    save_state(storage_dir, state)
    return state


def clear_attached(storage_dir: str | Path) -> RepoState:
    state = load_state(storage_dir)
    state.attached_packs = []
    save_state(storage_dir, state)
    return state
