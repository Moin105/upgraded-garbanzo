"""Tree-sitter node access, normalised across binding flavours.

Upstream py-tree-sitter (the Linux/macOS wheels used in CI and by most users)
exposes node data as **properties** — `type`, `child_count`, `start_byte`,
`start_point`, `children`. Some `tree-sitter-language-pack` wheels seen on
Windows expose them as **methods**, and with a couple of different names
(`kind()`, `start_position()`). Code that assumes one flavour crashes on the
other (e.g. `'tree_sitter.Node' object is not callable`).

`wrap_root(tree)` returns a lightweight adapter that presents a single,
method-style node API (`.kind()`, `.child(i)`, `.start_byte()`, …) over whichever
binding is installed. Children and field lookups return adapters too, so a whole
walk stays normalised. Node byte offsets are byte offsets on both bindings, so
slicing the (bytes) source with them is consistent.
"""
from __future__ import annotations

_MISSING = object()


def _resolve(obj, *names):
    """Return the first present attribute among `names`, calling it if it is a
    zero-arg method (method-style binding) or returning it as-is (property)."""
    for name in names:
        v = getattr(obj, name, _MISSING)
        if v is _MISSING:
            continue
        return v() if callable(v) else v
    raise AttributeError(f"tree-sitter node exposes none of {names!r}")


def _raw_child(node, i):
    # Prefer the O(1) `Node.child(index)` method — present on BOTH standard
    # py-tree-sitter and the language-pack wheels. Do NOT use `node.children[i]`:
    # that rebuilds the entire children list on every access (O(n^2) over a walk),
    # and on a huge node (e.g. a 4000-statement function) the allocation churn
    # crashes the native binding with a segfault.
    child = getattr(node, "child", None)
    if callable(child):
        return child(i)
    children = getattr(node, "children", None)
    if children is not None and not callable(children):
        return children[i]
    raise AttributeError("tree-sitter node exposes neither child() nor children")


class TSNode:
    """Method-style adapter over a tree-sitter node from either binding."""

    __slots__ = ("_n",)

    def __init__(self, node) -> None:
        self._n = node

    def kind(self) -> str:
        return _resolve(self._n, "type", "kind")

    def child_count(self) -> int:
        return _resolve(self._n, "child_count")

    def child(self, i: int) -> "TSNode | None":
        c = _raw_child(self._n, i)
        return TSNode(c) if c is not None else None

    def child_by_field_name(self, name: str) -> "TSNode | None":
        c = self._n.child_by_field_name(name)
        return TSNode(c) if c is not None else None

    def start_byte(self) -> int:
        return _resolve(self._n, "start_byte")

    def end_byte(self) -> int:
        return _resolve(self._n, "end_byte")

    def start_position(self):
        return _resolve(self._n, "start_point", "start_position")

    def end_position(self):
        return _resolve(self._n, "end_point", "end_position")


def wrap_root(tree) -> TSNode:
    """Adapter around a parsed tree's root node (root_node is a property upstream,
    a method on some wheels)."""
    return TSNode(_resolve(tree, "root_node"))
