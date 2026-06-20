from __future__ import annotations

from pathlib import Path

from coderchecker.state import (
    attach,
    clear_attached,
    detach,
    load_state,
    pin,
    save_state,
    unpin,
)


def test_attach_persists(tmp_path: Path) -> None:
    state = attach(tmp_path, ["pack_a", "pack_b"])
    assert state.attached_packs == ["pack_a", "pack_b"]
    reloaded = load_state(tmp_path)
    assert reloaded.attached_packs == ["pack_a", "pack_b"]


def test_attach_idempotent(tmp_path: Path) -> None:
    attach(tmp_path, ["pack_a"])
    state = attach(tmp_path, ["pack_a", "pack_a"])
    assert state.attached_packs == ["pack_a"]


def test_detach_removes(tmp_path: Path) -> None:
    attach(tmp_path, ["pack_a", "pack_b", "pack_c"])
    state = detach(tmp_path, ["pack_b"])
    assert state.attached_packs == ["pack_a", "pack_c"]


def test_pin_and_attach_combine(tmp_path: Path) -> None:
    pin(tmp_path, ["pack_pinned"])
    attach(tmp_path, ["pack_attached"])
    state = load_state(tmp_path)
    assert state.all_active_packs() == ["pack_pinned", "pack_attached"]


def test_excluded_overrides(tmp_path: Path) -> None:
    state = load_state(tmp_path)
    state.pinned_packs = ["pack_a", "pack_b"]
    state.excluded_packs = ["pack_b"]
    save_state(tmp_path, state)
    reloaded = load_state(tmp_path)
    assert reloaded.all_active_packs() == ["pack_a"]


def test_clear_attached(tmp_path: Path) -> None:
    pin(tmp_path, ["pack_p"])
    attach(tmp_path, ["pack_a"])
    clear_attached(tmp_path)
    state = load_state(tmp_path)
    # Pinned must survive a clear_attached.
    assert state.pinned_packs == ["pack_p"]
    assert state.attached_packs == []


def test_load_state_handles_missing(tmp_path: Path) -> None:
    state = load_state(tmp_path / "nope")
    assert state.attached_packs == []
    assert state.pinned_packs == []
