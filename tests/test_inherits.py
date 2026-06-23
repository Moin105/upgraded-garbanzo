"""IMPLEMENTS edges: a class that extends a base / implements an interface is
linked to it, so impact analysis on the base/interface surfaces its subtypes.
"""

from __future__ import annotations

from pathlib import Path

from karst.graph.builder import build_graph
from karst.graph.impact import analyze_impact, resolve_targets
from karst.graph.store import EdgeKind, NodeKind

PY = '''\
from abc import ABC


class Repository(ABC):
    def get(self, id):
        ...


class UserRepository(Repository):
    def get(self, id):
        return id


class AdminUser(UserRepository):
    pass
'''

TS = '''\
export interface PaymentGateway {
  charge(amount: number): void;
}

export class StripeGateway implements PaymentGateway {
  charge(amount: number): void {}
}

class Base {}
class Derived extends Base {}
'''


def _build(tmp_path: Path):
    (tmp_path / "models.py").write_text(PY, encoding="utf-8")
    (tmp_path / "payments.ts").write_text(TS, encoding="utf-8")
    store, _ = build_graph(tmp_path)
    return store


def _node_id(store, name: str, kinds) -> str | None:
    for nid in store.find_by_name(name):
        n = store.get_node(nid)
        if n and n.kind in kinds:
            return nid
    return None


def test_python_subclass_creates_implements_edge(tmp_path: Path) -> None:
    store = _build(tmp_path)
    repo = _node_id(store, "Repository", {NodeKind.CLASS})
    assert repo is not None
    subs = {
        store.get_node(src).name
        for src, _, _ in store.in_edges(repo, kinds=[EdgeKind.IMPLEMENTS])
        if store.get_node(src) is not None
    }
    assert "UserRepository" in subs


def test_ts_implements_interface_creates_edge(tmp_path: Path) -> None:
    store = _build(tmp_path)
    gw = _node_id(store, "PaymentGateway", {NodeKind.INTERFACE})
    assert gw is not None, "interface node should exist"
    impls = {
        store.get_node(src).name
        for src, _, _ in store.in_edges(gw, kinds=[EdgeKind.IMPLEMENTS])
        if store.get_node(src) is not None
    }
    assert "StripeGateway" in impls


def test_ts_extends_creates_edge(tmp_path: Path) -> None:
    store = _build(tmp_path)
    base = _node_id(store, "Base", {NodeKind.CLASS})
    assert base is not None
    derived = {
        store.get_node(src).name
        for src, _, _ in store.in_edges(base, kinds=[EdgeKind.IMPLEMENTS])
        if store.get_node(src) is not None
    }
    assert "Derived" in derived


def test_impact_on_interface_surfaces_implementer(tmp_path: Path) -> None:
    store = _build(tmp_path)
    targets = resolve_targets(store, names=["PaymentGateway"])
    assert targets
    report = analyze_impact(store, targets=targets, max_depth=3)
    affected = {a.name for a in report.affected}
    assert "StripeGateway" in affected, (
        f"impact on the interface should list its implementer, got {affected}"
    )


def test_impact_on_base_class_is_transitive(tmp_path: Path) -> None:
    # Repository <- UserRepository <- AdminUser : changing Repository should
    # reach AdminUser too (depth 2) via chained IMPLEMENTS edges.
    store = _build(tmp_path)
    targets = resolve_targets(store, names=["Repository"])
    report = analyze_impact(store, targets=targets, max_depth=3)
    affected = {a.name for a in report.affected}
    assert {"UserRepository", "AdminUser"}.issubset(affected), affected
