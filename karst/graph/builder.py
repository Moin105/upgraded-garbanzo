"""Graph builder.

Walks a repository, parses each file with tree-sitter, and populates a
GraphStore with:

- File nodes
- Function / Class / Method / Interface / Struct / Enum nodes (1:1 with chunks)
- CONTAINS edges (File → Class/Function, Class → Method)
- IMPORTS edges (File → File or File → Module)
- CALLS edges (Function → Function, best-effort name match)

Two-pass design: pass 1 adds nodes and queues raw imports + raw calls.
Pass 2 resolves imports against the known set of File nodes and resolves
calls against the name index in the store.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..chunker import chunk_file
from ..models import Chunk, ChunkKind
from ..parser import ParsedFile, ParserRegistry, parse_file
from ..walker import iter_source_files
from .calls import extract_call_names
from .imports import RawImport, extract_imports
from .store import EdgeKind, GraphStore, NodeKind, chunk_node_id, file_node_id, module_node_id


@dataclass
class BuildResult:
    files: int
    chunks: int
    nodes: int
    edges: int
    edge_counts: dict[str, int]
    storage_path: str


_CHUNK_KIND_TO_NODE_KIND: dict[ChunkKind, NodeKind] = {
    ChunkKind.FUNCTION: NodeKind.FUNCTION,
    ChunkKind.METHOD: NodeKind.METHOD,
    ChunkKind.CLASS: NodeKind.CLASS,
    ChunkKind.INTERFACE: NodeKind.INTERFACE,
    ChunkKind.STRUCT: NodeKind.STRUCT,
    ChunkKind.ENUM: NodeKind.ENUM,
}


def build_graph(
    root: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[GraphStore, BuildResult]:
    root_path = Path(root).resolve()
    registry = ParserRegistry()
    store = GraphStore()

    # Queued for pass 2.
    raw_imports: list[RawImport] = []
    raw_calls: list[tuple[str, list[str]]] = []   # (function node_id, [callee names])

    # File-path index for relative import resolution.
    file_relpaths: set[str] = set()

    files_seen = 0
    chunks_emitted = 0

    for file_path in iter_source_files(root_path):
        parsed = parse_file(file_path, repo_root=root_path, registry=registry)
        if parsed is None:
            continue
        files_seen += 1

        # File node
        f_id = file_node_id(parsed.relpath)
        store.add_node(
            f_id,
            kind=NodeKind.FILE,
            name=Path(parsed.relpath).name,
            qualified_name=parsed.relpath,
            language=parsed.language,
            sha=parsed.sha,
        )
        file_relpaths.add(parsed.relpath)

        # Chunks -> nodes + CONTAINS edges
        chunks = chunk_file(parsed)
        chunks_emitted += len(chunks)
        for chunk in chunks:
            node_kind = _CHUNK_KIND_TO_NODE_KIND.get(chunk.kind)
            if node_kind is None:
                continue
            cid = chunk_node_id(chunk.chunk_id)
            store.add_node(
                cid,
                kind=node_kind,
                name=chunk.name,
                qualified_name=f"{parsed.relpath}::{chunk.qualified_name}",
                language=chunk.language,
                file_relpath=chunk.file_relpath,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                signature=chunk.signature,
            )
            # File CONTAINS top-level chunks; parent class CONTAINS methods.
            parent_qname = (
                f"{parsed.relpath}::{chunk.parent}" if chunk.parent else None
            )
            parent_id = store.find_by_qname(parent_qname) if parent_qname else None
            store.add_edge(parent_id or f_id, cid, EdgeKind.CONTAINS)

            # Queue call extraction for functions/methods.
            if node_kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                callee_names = extract_call_names(
                    parsed, start_byte=chunk.start_byte, end_byte=chunk.end_byte
                )
                if callee_names:
                    raw_calls.append((cid, callee_names))

        # Imports
        raw_imports.extend(extract_imports(parsed))

        if progress is not None:
            progress(files_seen, chunks_emitted)

    # ---- pass 2: resolve imports
    for imp in raw_imports:
        src_id = file_node_id(imp.importer_relpath)
        if not store.has_node(src_id):
            continue
        resolved_id = _resolve_import(imp, file_relpaths)
        if resolved_id is not None:
            if not store.has_node(resolved_id):
                # Edge target may not exist yet for module nodes; create one.
                store.add_node(
                    resolved_id,
                    kind=NodeKind.MODULE,
                    name=imp.target,
                    qualified_name=imp.target,
                )
            store.add_edge(src_id, resolved_id, EdgeKind.IMPORTS, weight=0.5)
            continue
        # Fall back to a Module node so the edge isn't dropped.
        mod_id = module_node_id(imp.target)
        store.add_node(mod_id, kind=NodeKind.MODULE, name=imp.target, qualified_name=imp.target)
        store.add_edge(src_id, mod_id, EdgeKind.IMPORTS, weight=0.3)

    # ---- pass 2: resolve calls
    # Build a name index of function/method nodes for fast lookup.
    fn_name_index: dict[str, list[str]] = {}
    for node in store.iter_nodes():
        if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
            fn_name_index.setdefault(node.name, []).append(node.id)

    for caller_id, callees in raw_calls:
        for name in callees:
            for callee_id in fn_name_index.get(name, ()):
                if callee_id == caller_id:
                    continue
                store.add_edge(caller_id, callee_id, EdgeKind.CALLS, weight=1.0)

    result = BuildResult(
        files=files_seen,
        chunks=chunks_emitted,
        nodes=store.node_count,
        edges=store.edge_count,
        edge_counts=store.edge_counts_by_kind(),
        storage_path="",  # set by caller after save
    )
    return store, result


def build_and_save(
    root: str | Path,
    *,
    graph_path: str | Path,
    progress: Callable[[int, int], None] | None = None,
) -> BuildResult:
    store, result = build_graph(root, progress=progress)
    store.save(graph_path)
    result.storage_path = str(graph_path)
    return result


# ---------------------------------------------------------------- resolution

# Common JS/TS extensions and index-file fallbacks we try when resolving
# `./foo` against the on-disk file set.
_JS_TS_RESOLVE_SUFFIXES: tuple[str, ...] = (
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    "/index.ts", "/index.tsx", "/index.js", "/index.jsx",
)


def _resolve_import(imp: RawImport, file_relpaths: set[str]) -> str | None:
    """Try to map a raw import to a known File node id.

    Phase 3 covers the common cases:
    - Python `from foo.bar import x` -> foo/bar.py if present
    - Python relative `from .baz import x` -> sibling baz.py
    - JS/TS `./foo` -> foo + common extensions / index files
    """
    if imp.is_relative:
        return _resolve_relative(imp, file_relpaths)

    # Python absolute-ish import: try replacing dots with /.
    candidate = imp.target.replace(".", "/")
    for ext in (".py", ".pyi", "/__init__.py"):
        full = candidate + ext
        if full in file_relpaths:
            return file_node_id(full)
    return None


def _resolve_relative(imp: RawImport, file_relpaths: set[str]) -> str | None:
    base_dir = str(Path(imp.importer_relpath).parent).replace("\\", "/")
    target = imp.target
    if not target:
        return None

    # Python: leading dots count as levels.
    if target.startswith("."):
        dots = len(target) - len(target.lstrip("."))
        remainder = target[dots:]
        parts = base_dir.split("/") if base_dir not in {"", "."} else []
        # One dot stays in current dir; each extra dot goes up one level.
        levels_up = max(dots - 1, 0)
        if levels_up:
            parts = parts[: -levels_up] if levels_up <= len(parts) else []
        sub = remainder.replace(".", "/") if remainder else ""
        candidate = "/".join([p for p in parts + [sub] if p])
        for ext in (".py", ".pyi", "/__init__.py"):
            if (candidate + ext) in file_relpaths:
                return file_node_id(candidate + ext)
        return None

    # JS/TS: ./foo or ../bar
    parts = base_dir.split("/") if base_dir not in {"", "."} else []
    rel_parts = target.split("/")
    # Walk parts according to ./ vs ../
    for part in rel_parts:
        if part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    base = "/".join(parts)
    if base in file_relpaths:
        return file_node_id(base)
    for suffix in _JS_TS_RESOLVE_SUFFIXES:
        full = base + suffix
        if full in file_relpaths:
            return file_node_id(full)
    return None
