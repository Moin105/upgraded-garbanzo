"""AST-aware chunker.

Given a ParsedFile, walks the tree-sitter tree and emits Chunk objects, one
per function / class / method / interface / etc. Each chunk preserves its
exact byte range and line range so it doubles as a citation.

Design notes:
- The chunker is intentionally one-pass and stateless beyond the parent stack.
- We DO emit both a container (e.g. class) and its members (methods) — the
  container chunk gives architectural shape; the member chunks give the
  retrieval-friendly units the spec calls for ("each chunk is a complete
  function, class, or top-level statement"; spec §7).
- "decorated_definition" in Python wraps the real function/class. We treat
  the decorated form as the chunk and skip the inner duplicate.

API note:
- The tree-sitter Python bindings shipped with tree-sitter-language-pack on
  Windows expose Node attributes as methods (e.g. `node.kind()`,
  `node.child_count()`, `node.start_position().row`). The helpers in this
  module call those methods directly rather than the property-style API of
  upstream py-tree-sitter, so we work with whichever wheel is installed.
"""

from __future__ import annotations

from collections.abc import Iterator

from .languages import LanguageSpec, get_language
from .models import Chunk, ChunkKind
from .parser import ParsedFile


_SIGNATURE_MAX_BYTES = 240


def chunk_file(parsed: ParsedFile) -> list[Chunk]:
    """Extract AST-aware chunks from a parsed file."""
    lang = get_language(parsed.language)
    if lang is None or not lang.chunk_nodes:
        return []

    chunks: list[Chunk] = []
    root = parsed.tree.root_node()
    _walk(root, lang, parsed, parent_qname=None, out=chunks, skip_children_of=set())
    return chunks


def _iter_children(node) -> Iterator:
    count = node.child_count()
    for i in range(count):
        yield node.child(i)


def _walk(
    node,
    lang: LanguageSpec,
    parsed: ParsedFile,
    *,
    parent_qname: str | None,
    out: list[Chunk],
    skip_children_of: set[int],
) -> None:
    """Recursively walk the tree, emitting chunks for chunkable nodes.

    `skip_children_of` carries Python object ids of nodes whose chunkable
    descendants we've already processed via a wrapper (e.g.
    decorated_definition wraps function_definition; we don't want both).
    """
    for child in _iter_children(node):
        child_kind = child.kind()
        if id(child) in skip_children_of:
            continue

        chunk_kind = lang.chunk_nodes.get(child_kind)
        if chunk_kind is not None:
            chunk = _emit_chunk(child, chunk_kind, lang, parsed, parent_qname=parent_qname)
            next_parent = chunk.qualified_name if chunk is not None else parent_qname
            if chunk is not None:
                out.append(chunk)

            # Python decorated_definition wraps function_definition /
            # class_definition. Mark the inner node so we don't double-emit.
            if child_kind == "decorated_definition":
                for grand in _iter_children(child):
                    if grand.kind() in {"function_definition", "class_definition"}:
                        skip_children_of.add(id(grand))

            if child_kind in lang.container_nodes:
                _walk(
                    child,
                    lang,
                    parsed,
                    parent_qname=next_parent,
                    out=out,
                    skip_children_of=skip_children_of,
                )
        else:
            # Not a chunk node; keep descending — methods may be wrapped in a
            # class_body / declaration_list node we don't emit ourselves.
            _walk(
                child,
                lang,
                parsed,
                parent_qname=parent_qname,
                out=out,
                skip_children_of=skip_children_of,
            )


def _emit_chunk(
    node,
    kind: ChunkKind,
    lang: LanguageSpec,
    parsed: ParsedFile,
    *,
    parent_qname: str | None,
) -> Chunk | None:
    name = _extract_name(node, lang, parsed.source)
    if name is None:
        return None

    if kind == ChunkKind.FUNCTION and parent_qname is not None:
        kind = ChunkKind.METHOD

    qualified = f"{parent_qname}.{name}" if parent_qname else name

    start_byte = node.start_byte()
    end_byte = node.end_byte()
    code_bytes = parsed.source[start_byte:end_byte]
    code = code_bytes.decode("utf-8", errors="replace")

    start_point = node.start_position()
    end_point = node.end_position()

    return Chunk(
        file_relpath=parsed.relpath,
        language=parsed.language,
        kind=kind,
        name=name,
        qualified_name=qualified,
        start_line=start_point.row + 1,
        end_line=end_point.row + 1,
        start_byte=start_byte,
        end_byte=end_byte,
        code=code,
        file_sha=parsed.sha,
        parent=parent_qname,
        signature=_extract_signature(code),
    )


def _extract_name(node, lang: LanguageSpec, source: bytes) -> str | None:
    kind = node.kind()

    # Python decorated_definition: name lives on the wrapped function/class.
    if kind == "decorated_definition":
        for child in _iter_children(node):
            if child.kind() in {"function_definition", "class_definition"}:
                return _extract_name(child, lang, source)
        return None

    # Rust impl_item: prefer the "type" being implemented (or the trait).
    if kind == "impl_item":
        for fname in ("type", "trait"):
            named = node.child_by_field_name(fname)
            if named is not None:
                return _node_text(named, source)
        return None

    # Go type_declaration wraps one or more type_specs; take the first.
    if kind == "type_declaration":
        for child in _iter_children(node):
            if child.kind() == "type_spec":
                named = child.child_by_field_name("name")
                if named is not None:
                    return _node_text(named, source)
        return None

    named = node.child_by_field_name(lang.name_field)
    if named is not None:
        return _node_text(named, source)

    for child in _iter_children(node):
        if child.kind() in {"identifier", "property_identifier", "type_identifier"}:
            return _node_text(child, source)
    return None


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _extract_signature(code: str) -> str:
    for line in code.splitlines():
        stripped = line.strip()
        if stripped:
            if len(stripped) > _SIGNATURE_MAX_BYTES:
                return stripped[:_SIGNATURE_MAX_BYTES] + "…"
            return stripped
    return ""


def chunk_files(parsed_files: Iterator[ParsedFile]) -> Iterator[Chunk]:
    for parsed in parsed_files:
        yield from chunk_file(parsed)
