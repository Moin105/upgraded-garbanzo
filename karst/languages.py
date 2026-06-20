"""Language detection and tree-sitter node-kind mapping.

Each supported language declares which AST node types correspond to chunkable
units (functions, classes, methods) and how to extract the identifier name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import ChunkKind


@dataclass(frozen=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    # node_type -> ChunkKind
    chunk_nodes: dict[str, ChunkKind] = field(default_factory=dict)
    # Field name that holds the identifier on a chunk node. Most languages use
    # "name". A few (Go methods, Rust impl items) need custom extraction.
    name_field: str = "name"
    # Nodes whose body should be descended into to find nested chunks (e.g.
    # methods inside a class).
    container_nodes: frozenset[str] = frozenset()


PYTHON = LanguageSpec(
    name="python",
    extensions=(".py", ".pyi"),
    chunk_nodes={
        "function_definition": ChunkKind.FUNCTION,
        "class_definition": ChunkKind.CLASS,
        "decorated_definition": ChunkKind.FUNCTION,
    },
    container_nodes=frozenset({"class_definition", "decorated_definition"}),
)

JAVASCRIPT = LanguageSpec(
    name="javascript",
    extensions=(".js", ".jsx", ".mjs", ".cjs"),
    chunk_nodes={
        "function_declaration": ChunkKind.FUNCTION,
        "method_definition": ChunkKind.METHOD,
        "class_declaration": ChunkKind.CLASS,
        "generator_function_declaration": ChunkKind.FUNCTION,
    },
    container_nodes=frozenset({"class_declaration", "class_body"}),
)

TYPESCRIPT = LanguageSpec(
    name="typescript",
    extensions=(".ts", ".tsx"),
    chunk_nodes={
        "function_declaration": ChunkKind.FUNCTION,
        "method_definition": ChunkKind.METHOD,
        "method_signature": ChunkKind.METHOD,
        "class_declaration": ChunkKind.CLASS,
        "interface_declaration": ChunkKind.INTERFACE,
        "enum_declaration": ChunkKind.ENUM,
        "abstract_class_declaration": ChunkKind.CLASS,
        "abstract_method_signature": ChunkKind.METHOD,
    },
    container_nodes=frozenset(
        {"class_declaration", "abstract_class_declaration", "class_body", "interface_body"}
    ),
)

GO = LanguageSpec(
    name="go",
    extensions=(".go",),
    chunk_nodes={
        "function_declaration": ChunkKind.FUNCTION,
        "method_declaration": ChunkKind.METHOD,
        "type_declaration": ChunkKind.STRUCT,
    },
)

RUST = LanguageSpec(
    name="rust",
    extensions=(".rs",),
    chunk_nodes={
        "function_item": ChunkKind.FUNCTION,
        "struct_item": ChunkKind.STRUCT,
        "enum_item": ChunkKind.ENUM,
        "trait_item": ChunkKind.INTERFACE,
        "impl_item": ChunkKind.CLASS,
    },
    container_nodes=frozenset({"impl_item", "trait_item", "declaration_list"}),
)

JAVA = LanguageSpec(
    name="java",
    extensions=(".java",),
    chunk_nodes={
        "method_declaration": ChunkKind.METHOD,
        "constructor_declaration": ChunkKind.METHOD,
        "class_declaration": ChunkKind.CLASS,
        "interface_declaration": ChunkKind.INTERFACE,
        "enum_declaration": ChunkKind.ENUM,
    },
    container_nodes=frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration", "class_body"}
    ),
)


ALL_LANGUAGES: tuple[LanguageSpec, ...] = (
    PYTHON,
    JAVASCRIPT,
    TYPESCRIPT,
    GO,
    RUST,
    JAVA,
)

_EXT_INDEX: dict[str, LanguageSpec] = {
    ext: spec for spec in ALL_LANGUAGES for ext in spec.extensions
}

_NAME_INDEX: dict[str, LanguageSpec] = {spec.name: spec for spec in ALL_LANGUAGES}


def detect_language(path: Path) -> LanguageSpec | None:
    return _EXT_INDEX.get(path.suffix.lower())


def get_language(name: str) -> LanguageSpec | None:
    return _NAME_INDEX.get(name)


def supported_extensions() -> frozenset[str]:
    return frozenset(_EXT_INDEX.keys())
