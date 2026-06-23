"""Impact analysis (spec §10).

Given one or more "changed" nodes, walk INCOMING edges in the knowledge
graph to find what depends on them — callers, importers, containers — and
score by distance + edge type.

The spec scoring includes test coverage and historical co-change frequency;
those need extra signals (git log, coverage XML) and are deferred. Phase 3
ships the structural piece, which is the foundation everything else builds
on.

Resolution: a target can be specified three ways:
  - node_id (precise)
  - qualified_name like "src/foo.py::Bar.baz"
  - bare name "baz" (returns matches across the repo)
Plus: from a parsed diff, every chunk whose line range overlaps a changed
hunk is treated as a target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..review.diff import ParsedDiff
from .store import EdgeKind, GraphStore, NodeKind, file_node_id


# Per-edge-kind weight when scoring an incoming dependency. Higher means
# "more important — likely to break". Calls and methods that contain the
# code are the strongest signals; imports are softer.
_EDGE_WEIGHT: dict[EdgeKind, float] = {
    EdgeKind.CALLS: 1.0,
    EdgeKind.IMPLEMENTS: 0.7,  # changing a base/interface strongly affects subtypes
    EdgeKind.CONTAINS: 0.6,
    EdgeKind.DEFINES: 0.6,
    EdgeKind.IMPORTS: 0.5,
}


@dataclass
class AffectedNode:
    node_id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file_relpath: str | None
    start_line: int | None
    end_line: int | None
    depth: int
    score: float
    via_edges: list[EdgeKind] = field(default_factory=list)

    @property
    def citation(self) -> str | None:
        if self.file_relpath and self.start_line and self.end_line:
            return f"{self.file_relpath}:{self.start_line}-{self.end_line}"
        return self.file_relpath


@dataclass
class ImpactReport:
    targets: list[str]                 # the resolved starting node ids
    affected: list[AffectedNode]       # ranked, highest-score first
    risk: str                          # low / medium / high / critical
    max_depth: int

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.affected:
            out[a.kind.value] = out.get(a.kind.value, 0) + 1
        return out


def analyze_impact(
    store: GraphStore,
    *,
    targets: Iterable[str],
    max_depth: int = 3,
    kinds: tuple[EdgeKind, ...] = (
        EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.CONTAINS, EdgeKind.DEFINES, EdgeKind.IMPLEMENTS,
    ),
) -> ImpactReport:
    """Run impact analysis from a set of node ids.

    For each target, BFS along incoming edges of the given kinds up to
    `max_depth` and score each reached node.
    """
    resolved = [t for t in targets if store.has_node(t)]
    if not resolved:
        return ImpactReport(targets=[], affected=[], risk="none", max_depth=max_depth)

    # depths[node_id] = (min depth across all paths, list of edge kinds traversed)
    depths: dict[str, int] = {}
    via: dict[str, list[EdgeKind]] = {}

    from collections import deque

    q: deque[tuple[str, int]] = deque()
    for t in resolved:
        depths[t] = 0
        q.append((t, 0))

    while q:
        node, d = q.popleft()
        if d >= max_depth:
            continue
        for src_id, kind, _ in store.in_edges(node, kinds=kinds):
            new_d = d + 1
            if src_id in depths and depths[src_id] <= new_d:
                # Already recorded a shorter or equal path; just remember the edge.
                if kind not in via.setdefault(src_id, []):
                    via[src_id].append(kind)
                continue
            depths[src_id] = new_d
            via.setdefault(src_id, []).append(kind)
            q.append((src_id, new_d))

    affected: list[AffectedNode] = []
    for node_id, depth in depths.items():
        if node_id in resolved:
            continue  # don't list the targets themselves
        node = store.get_node(node_id)
        if node is None:
            continue
        kinds_in = via.get(node_id, [])
        # Score: best edge weight / depth, bumped slightly if node is a File
        # or top-level function (proxies for public-API exposure).
        best_edge_w = max((_EDGE_WEIGHT.get(k, 0.4) for k in kinds_in), default=0.4)
        exposure = 1.15 if node.kind in (NodeKind.FILE, NodeKind.FUNCTION) else 1.0
        score = best_edge_w * exposure / max(depth, 1)
        affected.append(
            AffectedNode(
                node_id=node_id,
                kind=node.kind,
                name=node.name,
                qualified_name=node.qualified_name,
                file_relpath=node.attrs.get("file_relpath") or (
                    node.qualified_name if node.kind == NodeKind.FILE else None
                ),
                start_line=node.attrs.get("start_line"),
                end_line=node.attrs.get("end_line"),
                depth=depth,
                score=round(score, 4),
                via_edges=kinds_in,
            )
        )

    affected.sort(key=lambda a: (-a.score, a.depth, a.qualified_name))
    risk = _risk_label(affected)
    return ImpactReport(targets=resolved, affected=affected, risk=risk, max_depth=max_depth)


def resolve_targets(
    store: GraphStore,
    *,
    names: Iterable[str] = (),
    qnames: Iterable[str] = (),
    files: Iterable[str] = (),
    node_ids: Iterable[str] = (),
) -> list[str]:
    """Map user input (names, qualified names, file paths, raw ids) to node ids."""
    out: list[str] = []
    seen: set[str] = set()

    def add(nid: str) -> None:
        if nid and nid not in seen and store.has_node(nid):
            seen.add(nid)
            out.append(nid)

    for nid in node_ids:
        add(nid)
    for qn in qnames:
        nid = store.find_by_qname(qn)
        if nid:
            add(nid)
    for name in names:
        for nid in store.find_by_name(name):
            add(nid)
    for path in files:
        add(file_node_id(path))
    return out


def resolve_targets_from_diff(store: GraphStore, parsed: ParsedDiff) -> list[str]:
    """Treat every chunk whose line range overlaps a changed hunk as a target,
    plus the File nodes for the changed files themselves.
    """
    out: list[str] = []
    seen: set[str] = set()
    for fc in parsed.files:
        if not fc.is_reviewable:
            continue
        # Always include the File node.
        fid = file_node_id(fc.path)
        if store.has_node(fid) and fid not in seen:
            seen.add(fid)
            out.append(fid)
        ranges = fc.added_line_ranges()
        if not ranges:
            continue
        for node in store.iter_nodes():
            if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                continue
            if node.attrs.get("file_relpath") != fc.path:
                continue
            ns = node.attrs.get("start_line") or 0
            ne = node.attrs.get("end_line") or 0
            if any(rs <= ne and ns <= re for (rs, re) in ranges) and node.id not in seen:
                seen.add(node.id)
                out.append(node.id)
    return out


def _risk_label(affected: list[AffectedNode]) -> str:
    if not affected:
        return "none"
    n = len(affected)
    direct = sum(1 for a in affected if a.depth == 1)
    if direct >= 10 or n >= 40:
        return "critical"
    if direct >= 4 or n >= 15:
        return "high"
    if direct >= 1 or n >= 5:
        return "medium"
    return "low"
