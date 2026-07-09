"""Import-edge extractor.

Walks the tree-sitter tree of a parsed file and yields RawImport records:
the importing file, the imported module name (as written in source), and a
hint at whether it's a relative path.

Resolution to File/Module nodes happens later in the builder, because that
needs the full set of indexed files to decide if `./foo` matched a real
`./foo.ts` on disk.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .._tsapi import wrap_root
from ..parser import ParsedFile


@dataclass(frozen=True)
class RawImport:
    importer_relpath: str
    target: str            # exactly as written in source ("./foo", "foo.bar", "react")
    is_relative: bool      # starts with . or ./ or ../


def extract_imports(parsed: ParsedFile) -> list[RawImport]:
    if parsed.language == "python":
        return list(_python_imports(parsed))
    if parsed.language in ("javascript", "typescript"):
        return list(_js_ts_imports(parsed))
    return []


# ---------------------------------------------------------------- Python

def _python_imports(parsed: ParsedFile) -> Iterator[RawImport]:
    root = wrap_root(parsed.tree)
    src = parsed.source
    yield from _python_walk(root, parsed.relpath, src)


def _python_walk(node, importer: str, src: bytes) -> Iterator[RawImport]:
    kind = node.kind()
    if kind == "import_statement":
        # import foo, foo.bar  -> dotted_name children
        for child in _children(node):
            if child.kind() == "dotted_name":
                name = _text(child, src)
                yield RawImport(importer, name, is_relative=False)
            elif child.kind() == "aliased_import":
                inner = child.child_by_field_name("name")
                if inner is not None:
                    name = _text(inner, src)
                    yield RawImport(importer, name, is_relative=False)
        return
    if kind == "import_from_statement":
        # from X import a, b   X may be `relative_import` (dots) or `dotted_name`
        module_field = node.child_by_field_name("module_name")
        if module_field is not None:
            name = _text(module_field, src)
            # Leading dots ('.', '..') mark relative imports.
            yield RawImport(importer, name, is_relative=name.startswith("."))
        else:
            # No module field on `from . import x` — emit one bare relative.
            for child in _children(node):
                if child.kind() == "relative_import":
                    name = _text(child, src).strip()
                    yield RawImport(importer, name or ".", is_relative=True)
                    break
        return

    for child in _children(node):
        yield from _python_walk(child, importer, src)


# ---------------------------------------------------------------- JS/TS

def _js_ts_imports(parsed: ParsedFile) -> Iterator[RawImport]:
    root = wrap_root(parsed.tree)
    src = parsed.source
    yield from _js_ts_walk(root, parsed.relpath, src)


def _js_ts_walk(node, importer: str, src: bytes) -> Iterator[RawImport]:
    kind = node.kind()
    # ES module imports
    if kind == "import_statement":
        for child in _children(node):
            if child.kind() == "string":
                target = _strip_quotes(_text(child, src))
                if target:
                    yield RawImport(importer, target, is_relative=_is_relative(target))
        return
    # CommonJS require()
    if kind == "call_expression":
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if (
            fn is not None
            and _text(fn, src) == "require"
            and args is not None
        ):
            for child in _children(args):
                if child.kind() == "string":
                    target = _strip_quotes(_text(child, src))
                    if target:
                        yield RawImport(importer, target, is_relative=_is_relative(target))
        # Keep walking — calls may be nested under expression statements etc.

    for child in _children(node):
        yield from _js_ts_walk(child, importer, src)


# ---------------------------------------------------------------- helpers

def _children(node) -> Iterator:
    for i in range(node.child_count()):
        yield node.child(i)


def _text(node, src: bytes) -> str:
    return src[node.start_byte() : node.end_byte()].decode("utf-8", errors="replace")


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] in {'"', "'", "`"} and s[-1] == s[0]:
        return s[1:-1]
    return s


def _is_relative(target: str) -> bool:
    return target.startswith("./") or target.startswith("../") or target == "." or target == ".."
