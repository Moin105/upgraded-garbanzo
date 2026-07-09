"""Call-edge extractor.

Walks the tree-sitter tree of a chunk's body and yields the names of
functions being called. We deliberately take a conservative, name-only
approach: no scope resolution, no type inference. The builder later wires
each callee name to every known function with that name.

This overcounts in shared-name cases (every codebase has a `get` or `add`).
For impact analysis that's the right side to err on — false positives are
cheaper than false negatives when you're about to ship a change.
"""

from __future__ import annotations

from collections.abc import Iterator

from .._tsapi import wrap_root
from ..parser import ParsedFile

# Names we never want to flag as cross-references. Pure-builtin noise.
_NOISE: frozenset[str] = frozenset(
    {
        # Python builtins
        "print", "len", "range", "list", "dict", "set", "tuple", "str", "int",
        "float", "bool", "isinstance", "issubclass", "getattr", "setattr",
        "hasattr", "type", "super", "iter", "next", "enumerate", "zip", "map",
        "filter", "sorted", "reversed", "min", "max", "sum", "abs", "any", "all",
        "open", "format", "repr", "id", "hash",
        # JS/TS very common
        "console", "log", "warn", "error", "info", "debug",
        "JSON", "Object", "Array", "String", "Number", "Boolean",
        "parseInt", "parseFloat", "Math", "Date", "Promise", "Map", "Set",
        "setTimeout", "setInterval", "fetch", "require",
    }
)


def extract_call_names(parsed: ParsedFile, *, start_byte: int, end_byte: int) -> list[str]:
    """Return a deduplicated, ordered list of callee names within the byte range."""
    root = wrap_root(parsed.tree)
    src = parsed.source
    found: list[str] = []
    seen: set[str] = set()

    walker = _walker_for(parsed.language)
    if walker is None:
        return []

    for name in walker(root, src, start_byte, end_byte):
        if name in _NOISE or name in seen or not name:
            continue
        # Drop dunder/init noise.
        if name.startswith("_") and name.endswith("__") and len(name) > 4:
            continue
        seen.add(name)
        found.append(name)
    return found


def _walker_for(language: str):
    if language == "python":
        return _python_calls
    if language in ("javascript", "typescript"):
        return _js_ts_calls
    return None


# ---------------------------------------------------------------- Python

def _python_calls(node, src: bytes, lo: int, hi: int) -> Iterator[str]:
    if not _overlaps(node, lo, hi):
        return
    if node.kind() == "call":
        fn = node.child_by_field_name("function")
        if fn is not None:
            name = _call_name(fn, src)
            if name:
                yield name
    for child in _children(node):
        yield from _python_calls(child, src, lo, hi)


# ---------------------------------------------------------------- JS/TS

def _js_ts_calls(node, src: bytes, lo: int, hi: int) -> Iterator[str]:
    if not _overlaps(node, lo, hi):
        return
    kind = node.kind()
    if kind in ("call_expression", "new_expression"):
        fn = node.child_by_field_name("function") or node.child_by_field_name("constructor")
        # `new Foo()` exposes the class via "constructor" field on some grammars
        # and inside the children on others — fall back to the first child for
        # new_expression.
        if fn is None and kind == "new_expression":
            for child in _children(node):
                if child.kind() in ("identifier", "member_expression"):
                    fn = child
                    break
        if fn is not None:
            name = _call_name(fn, src)
            if name:
                yield name
    for child in _children(node):
        yield from _js_ts_calls(child, src, lo, hi)


# ---------------------------------------------------------------- helpers

def _call_name(fn_node, src: bytes) -> str | None:
    """Reduce a callable expression to a bare name.

    - identifier -> the name
    - attribute / member_expression -> the final property name
    - everything else -> None (we don't try to resolve calls on arrays,
      ternaries, IIFEs, etc.)
    """
    k = fn_node.kind()
    if k in ("identifier", "property_identifier", "type_identifier"):
        return _text(fn_node, src)
    if k == "attribute":  # Python: obj.method
        attr = fn_node.child_by_field_name("attribute")
        if attr is not None:
            return _text(attr, src)
    if k == "member_expression":  # JS/TS: obj.method
        prop = fn_node.child_by_field_name("property")
        if prop is not None:
            return _text(prop, src)
    return None


def _children(node) -> Iterator:
    for i in range(node.child_count()):
        yield node.child(i)


def _text(node, src: bytes) -> str:
    return src[node.start_byte() : node.end_byte()].decode("utf-8", errors="replace")


def _overlaps(node, lo: int, hi: int) -> bool:
    return node.start_byte() < hi and node.end_byte() > lo
