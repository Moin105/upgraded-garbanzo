"""Knowledge graph store (spec §17).

Backed by NetworkX so the agent runs without external infrastructure. The
public surface — NodeKind/EdgeKind enums plus add/find/traverse methods — is
intentionally narrow so a Neo4j backend can be swapped in later by
implementing the same interface.

The spec's Cypher example
    MATCH (f:Function {name:'getUser'})-[:CALLS*1..3]->(d) WHERE d:DBTable
    RETURN DISTINCT d.name
maps to: find_by_name("getUser") + bfs_outgoing(kinds={CALLS}, max_depth=3)
filtered by NodeKind.DB_TABLE on the receiving side.
"""

from __future__ import annotations

import pickle
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import networkx as nx


class NodeKind(str, Enum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    DB_TABLE = "db_table"          # reserved — populated in later phases
    ENDPOINT = "endpoint"          # reserved — populated in later phases


class EdgeKind(str, Enum):
    CONTAINS = "contains"          # File → Class/Function, Class → Method
    IMPORTS = "imports"            # File → File/Module
    CALLS = "calls"                # Function → Function (best-effort name match)
    DEFINES = "defines"            # alias of CONTAINS for Class → Method
    READS = "reads"                # reserved
    EXPOSED_BY = "exposed_by"      # reserved
    BACKS = "backs"                # reserved


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    attrs: dict[str, Any]


GRAPH_VERSION = 1


class GraphStore:
    """Thin wrapper over a NetworkX MultiDiGraph.

    Multi-graph because the same pair of nodes can be connected by edges of
    different kinds (e.g. a file IMPORTS another file AND the function inside
    one CALLS a function inside the other).
    """

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        # name → list of node ids, for fast call resolution and entity match.
        self._by_name: dict[str, list[str]] = {}
        self._by_qname: dict[str, str] = {}

    # ---------------------------------------------------------------- nodes

    def add_node(
        self,
        node_id: str,
        *,
        kind: NodeKind,
        name: str,
        qualified_name: str | None = None,
        **attrs: Any,
    ) -> None:
        qname = qualified_name or name
        if node_id in self._g:
            # Merge attrs on re-add; keep existing kind/name (first writer wins).
            self._g.nodes[node_id].update(attrs)
            return
        self._g.add_node(
            node_id, kind=kind.value, name=name, qualified_name=qname, **attrs
        )
        self._by_name.setdefault(name, []).append(node_id)
        # qname is a stronger key; we keep just one node per qname (last wins).
        self._by_qname[qname] = node_id

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    def get_node(self, node_id: str) -> GraphNode | None:
        if node_id not in self._g:
            return None
        data = self._g.nodes[node_id]
        return GraphNode(
            id=node_id,
            kind=NodeKind(data["kind"]),
            name=data.get("name", node_id),
            qualified_name=data.get("qualified_name", data.get("name", node_id)),
            attrs={k: v for k, v in data.items() if k not in {"kind", "name", "qualified_name"}},
        )

    def find_by_name(self, name: str) -> list[str]:
        return list(self._by_name.get(name, ()))

    def find_by_qname(self, qname: str) -> str | None:
        return self._by_qname.get(qname)

    def iter_nodes(self, *, kind: NodeKind | None = None) -> Iterator[GraphNode]:
        for nid, data in self._g.nodes(data=True):
            if kind is not None and data.get("kind") != kind.value:
                continue
            yield GraphNode(
                id=nid,
                kind=NodeKind(data["kind"]),
                name=data.get("name", nid),
                qualified_name=data.get("qualified_name", data.get("name", nid)),
                attrs={k: v for k, v in data.items() if k not in {"kind", "name", "qualified_name"}},
            )

    # ---------------------------------------------------------------- edges

    def add_edge(
        self,
        src: str,
        dst: str,
        kind: EdgeKind,
        *,
        weight: float = 1.0,
        **attrs: Any,
    ) -> None:
        if src == dst:
            return
        if src not in self._g or dst not in self._g:
            return
        # Multi-graph keys edges by (src, dst, key). Use kind.value as the key
        # so we never duplicate same-kind edges between the same pair.
        self._g.add_edge(src, dst, key=kind.value, kind=kind.value, weight=weight, **attrs)

    def out_edges(self, node_id: str, *, kinds: Iterable[EdgeKind] | None = None) -> list[tuple[str, EdgeKind, dict[str, Any]]]:
        return list(self._iter_edges(node_id, direction="out", kinds=kinds))

    def in_edges(self, node_id: str, *, kinds: Iterable[EdgeKind] | None = None) -> list[tuple[str, EdgeKind, dict[str, Any]]]:
        return list(self._iter_edges(node_id, direction="in", kinds=kinds))

    def _iter_edges(
        self,
        node_id: str,
        *,
        direction: str,
        kinds: Iterable[EdgeKind] | None,
    ) -> Iterator[tuple[str, EdgeKind, dict[str, Any]]]:
        if node_id not in self._g:
            return
        allow = {k.value for k in kinds} if kinds else None
        if direction == "out":
            it = self._g.out_edges(node_id, keys=True, data=True)
            for _, dst, key, data in it:
                if allow and key not in allow:
                    continue
                yield dst, EdgeKind(key), dict(data)
        else:
            it = self._g.in_edges(node_id, keys=True, data=True)
            for src, _, key, data in it:
                if allow and key not in allow:
                    continue
                yield src, EdgeKind(key), dict(data)

    # ------------------------------------------------------------ traversal

    def bfs(
        self,
        start: Iterable[str],
        *,
        direction: str = "in",  # "in" = walk callers/dependers, "out" = walk callees
        kinds: Iterable[EdgeKind] | None = None,
        max_depth: int = 3,
    ) -> dict[str, int]:
        """BFS from `start` along edges of the given kinds.

        Returns {node_id: depth}. Depth 0 = the starting node itself.
        """
        depths: dict[str, int] = {}
        q: deque[tuple[str, int]] = deque()
        for s in start:
            if s in self._g and s not in depths:
                depths[s] = 0
                q.append((s, 0))
        while q:
            node, d = q.popleft()
            if d >= max_depth:
                continue
            edges = (
                self._iter_edges(node, direction=direction, kinds=kinds)
            )
            for neighbor, _, _ in edges:
                if neighbor in depths:
                    continue
                depths[neighbor] = d + 1
                q.append((neighbor, d + 1))
        return depths

    # ---------------------------------------------------------------- stats

    @property
    def node_count(self) -> int:
        return self._g.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._g.number_of_edges()

    def counts_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, data in self._g.nodes(data=True):
            k = data.get("kind", "?")
            out[k] = out.get(k, 0) + 1
        return out

    def edge_counts_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, _, key in self._g.edges(keys=True):
            out[key] = out.get(key, 0) + 1
        return out

    # -------------------------------------------------------------- persist

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(
                {
                    "version": GRAPH_VERSION,
                    "graph": self._g,
                    "by_name": self._by_name,
                    "by_qname": self._by_qname,
                },
                fh,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    @classmethod
    def load(cls, path: str | Path) -> "GraphStore":
        with Path(path).open("rb") as fh:
            payload = pickle.load(fh)
        if payload.get("version") != GRAPH_VERSION:
            raise ValueError(
                f"Graph pickle version mismatch: got {payload.get('version')}, "
                f"expected {GRAPH_VERSION}. Rebuild with `graph-index --reset`."
            )
        store = cls()
        store._g = payload["graph"]
        store._by_name = payload["by_name"]
        store._by_qname = payload["by_qname"]
        return store


# ---------------------------------------------------------------- node IDs

def file_node_id(relpath: str) -> str:
    return f"file:{relpath}"


def module_node_id(name: str) -> str:
    return f"module:{name}"


def chunk_node_id(chunk_id: str) -> str:
    # The chunk_id is already deterministic (sha-derived in models.py); reuse
    # it so the graph nodes align 1:1 with Qdrant points.
    return chunk_id
