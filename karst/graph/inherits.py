"""Inheritance-edge extractor.

Pulls the supertypes a class/interface declares — the classes it `extends`
and the interfaces it `implements` (Python base classes, JS/TS `extends`/
`implements`, TS `interface ... extends`). The builder wires each supertype
name to the known type node of that name as an IMPLEMENTS edge, so impact
analysis can answer "what implements this interface / extends this base?".

Same name-only philosophy as calls.py: no type resolution, no generics
machinery. We read only the heritage clause of the chunk's own declaration —
never its body — so nested declarations don't leak their bases upward.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..parser import ParsedFile

# Universal bases that are never repo-defined types worth an edge.
_NOISE_BASES: frozenset[str] = frozenset({"object", "Object"})

_NAME_KINDS = {"identifier", "type_identifier", "property_identifier"}
_TYPE_REF_KINDS = _NAME_KINDS | {
    "member_expression",
    "nested_type_identifier",
    "generic_type",
    "instantiation_expression",
}

_PY_TYPE_KINDS = {"class_definition"}
_JS_TS_TYPE_KINDS = {
    "class_declaration",
    "class",
    "abstract_class_declaration",
    "interface_declaration",
}


def extract_supertypes(parsed: ParsedFile, *, start_byte: int, end_byte: int) -> list[str]:
    """Return the deduplicated supertype names of the chunk's declaration."""
    root = parsed.tree.root_node()
    src = parsed.source
    lang = parsed.language

    if lang == "python":
        node = _first_type_node(root, start_byte, end_byte, _PY_TYPE_KINDS)
        names = _python_supertypes(node, src) if node is not None else []
    elif lang in ("javascript", "typescript"):
        node = _first_type_node(root, start_byte, end_byte, _JS_TS_TYPE_KINDS)
        names = _js_ts_supertypes(node, src) if node is not None else []
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        if not n or n in _NOISE_BASES or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


# --------------------------------------------------------------- node finding

def _first_type_node(node, lo: int, hi: int, kinds: set[str]):
    """Pre-order search for the first node overlapping [lo, hi] whose kind is a
    type declaration. Because the chunk's own declaration is the ancestor of any
    nested ones, it is always found first."""
    if not _overlaps(node, lo, hi):
        return None
    if node.kind() in kinds:
        return node
    for child in _children(node):
        found = _first_type_node(child, lo, hi, kinds)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------- Python

def _python_supertypes(class_node, src: bytes) -> list[str]:
    sup = class_node.child_by_field_name("superclasses")  # argument_list | None
    if sup is None:
        return []
    names: list[str] = []
    for child in _children(sup):
        ck = child.kind()
        if ck == "keyword_argument":  # metaclass=..., skip
            continue
        if ck == "identifier":
            names.append(_text(child, src))
        elif ck == "attribute":  # abc.ABC -> ABC
            attr = child.child_by_field_name("attribute")
            if attr is not None:
                names.append(_text(attr, src))
    return names


# ---------------------------------------------------------------- JS / TS

def _js_ts_supertypes(type_node, src: bytes) -> list[str]:
    names: list[str] = []
    for child in _children(type_node):
        ck = child.kind()
        if ck in ("class_body", "object_type"):
            break  # heritage always precedes the body; stop before it
        if ("heritage" in ck) or ("extends" in ck) or ("implements" in ck):
            for ref in _collect_type_refs(child):
                nm = _type_ref_name(ref, src)
                if nm:
                    names.append(nm)
    return names


def _collect_type_refs(node) -> Iterator:
    """Yield top-level type-reference nodes within a heritage clause without
    descending into generic arguments (so `IFoo<Bar>` yields IFoo, not Bar)."""
    for child in _children(node):
        ck = child.kind()
        if ck in ("type_arguments", "type_parameters"):
            continue
        if ck in _TYPE_REF_KINDS:
            yield child
        else:
            yield from _collect_type_refs(child)


def _type_ref_name(node, src: bytes) -> str | None:
    k = node.kind()
    if k in _NAME_KINDS:
        return _text(node, src)
    if k in ("member_expression", "nested_type_identifier"):  # a.b.C -> C
        prop = node.child_by_field_name("property") or node.child_by_field_name("name")
        if prop is not None:
            return _text(prop, src)
        last = None
        for c in _children(node):
            if c.kind() in _NAME_KINDS:
                last = c
        return _text(last, src) if last is not None else None
    if k in ("generic_type", "instantiation_expression"):  # IFoo<Bar> -> IFoo
        nm = node.child_by_field_name("name")
        if nm is not None:
            return _type_ref_name(nm, src)
        for c in _children(node):
            if c.kind() in _TYPE_REF_KINDS:
                return _type_ref_name(c, src)
    return None


# ---------------------------------------------------------------- helpers

def _children(node) -> Iterator:
    for i in range(node.child_count()):
        yield node.child(i)


def _text(node, src: bytes) -> str:
    return src[node.start_byte() : node.end_byte()].decode("utf-8", errors="replace")


def _overlaps(node, lo: int, hi: int) -> bool:
    return node.start_byte() < hi and node.end_byte() > lo
