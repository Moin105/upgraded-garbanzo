"""Indexer (Phase 4).

Walks the repo, parses, chunks, embeds, upserts. With three big wins over
Phase 1's implementation:

1. **Incremental** — file-SHA manifest skips unchanged files entirely
   (the embedder is never called for them).
2. **Cached** — content-hash → vector cache means embedding work is never
   repeated for the same chunk text.
3. **Pack-tagged** — chunks land in Qdrant with a `packs` payload field, so
   downstream `ask` can filter by pack and shrink retrieval to 1/20th of
   the corpus.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .analyze import analyze_repo
from .chunker import chunk_file
from .embedder import DEFAULT_MODEL, EmbeddedChunk, Embedder, _chunk_to_text
from .embedding_cache import EmbeddingCache, cache_path
from .manifest import FileEntry, Manifest, file_sha, load_manifest, save_manifest
from .models import Chunk
from .packs.models import Pack
from .packs.store import PackStore
from .packs.tagger import compile_packs, tag_relpath
from .parser import ParserRegistry, parse_file
from .store import DEFAULT_COLLECTION, ChunkStore
from .walker import iter_source_files


@dataclass
class IndexResult:
    files: int                       # total files walked
    files_reused: int                # SHA matched manifest, skipped
    files_indexed: int               # actually re-embedded
    chunks: int                      # chunks now in Qdrant from this run
    embeddings_computed: int         # cache miss = real model call
    embeddings_cached: int           # cache hit
    collection: str
    storage_path: str
    embedding_model: str
    edge_effects: dict[str, int] = field(default_factory=dict)


def index_repo(
    root: str | Path,
    *,
    storage_path: str | Path,
    collection: str = DEFAULT_COLLECTION,
    embedding_model: str = DEFAULT_MODEL,
    embedder_cache_dir: str | Path | None = None,
    reset: bool = False,
    incremental: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> IndexResult:
    """Index a repository into Qdrant.

    progress(files_seen, chunks_emitted) is called once per file when set.
    """
    storage_path = Path(storage_path).resolve()
    storage_path.mkdir(parents=True, exist_ok=True)

    embedder = Embedder(
        embedding_model,
        cache_dir=str(embedder_cache_dir) if embedder_cache_dir else None,
    )
    store = ChunkStore(location=storage_path, collection=collection)
    cache = EmbeddingCache(cache_path(storage_path))
    pack_store = PackStore(storage_path / "packs.sqlite")
    compiled_packs = compile_packs(pack_store.list())

    def tagger(relpath: str) -> list[str]:
        return tag_relpath(compiled_packs, relpath)

    # Load (or skip) the manifest.
    if reset:
        manifest = Manifest(embedding_model=embedding_model)
        store.reset_collection(vector_size=embedder.dim)
    else:
        manifest = load_manifest(storage_path)
        if manifest.embedding_model and manifest.embedding_model != embedding_model:
            # Embedding model changed — old vectors are wrong-dim. Full reset.
            store.reset_collection(vector_size=embedder.dim)
            manifest = Manifest(embedding_model=embedding_model)
        else:
            store.ensure_collection(vector_size=embedder.dim)
            manifest.embedding_model = embedding_model

    result = IndexResult(
        files=0,
        files_reused=0,
        files_indexed=0,
        chunks=0,
        embeddings_computed=0,
        embeddings_cached=0,
        collection=collection,
        storage_path=str(storage_path),
        embedding_model=embedding_model,
    )

    root_path = Path(root).resolve()
    registry = ParserRegistry()
    seen_paths: set[str] = set()

    to_embed: list[tuple[Chunk, str]] = []  # (chunk, text_to_embed)

    for file_path in iter_source_files(root_path):
        result.files += 1
        try:
            relative = file_path.resolve().relative_to(root_path).as_posix()
        except ValueError:
            continue
        seen_paths.add(relative)

        sha = file_sha(file_path)
        prior = manifest.files.get(relative)
        if incremental and prior is not None and prior.sha == sha:
            # Unchanged — points already in Qdrant from a prior run.
            result.files_reused += 1
            result.chunks += prior.chunk_count
            if progress is not None:
                progress(result.files, result.chunks)
            continue

        parsed = parse_file(file_path, repo_root=root_path, registry=registry)
        if parsed is None:
            continue

        # If the file existed before, blow away its old chunks before
        # re-upserting (its line ranges may have shifted).
        if prior is not None:
            store.delete_by_file(relative)

        chunks = chunk_file(parsed)
        result.files_indexed += 1
        result.chunks += len(chunks)
        manifest.files[relative] = FileEntry(
            sha=sha,
            chunk_count=len(chunks),
            indexed_at=Pack.now_iso(),
        )

        for chunk in chunks:
            text = _chunk_to_text(chunk)
            to_embed.append((chunk, text))

        if progress is not None:
            progress(result.files, result.chunks)

    # Drop files that vanished since the last index.
    gone = [p for p in list(manifest.files.keys()) if p not in seen_paths]
    for p in gone:
        store.delete_by_file(p)
        manifest.files.pop(p, None)

    # ---- embed + upsert with cache ----
    if to_embed:
        shas = [cache.text_sha(t) for _, t in to_embed]
        cached = cache.get_many(embedding_model, shas)
        result.embeddings_cached = len(cached)

        new_items: list[tuple[Chunk, str, str]] = []  # (chunk, text, sha)
        embedded_iter: list[EmbeddedChunk] = []
        for (chunk, text), sha in zip(to_embed, shas, strict=True):
            vec = cached.get(sha)
            if vec is not None:
                embedded_iter.append(EmbeddedChunk(chunk=chunk, vector=vec))
            else:
                new_items.append((chunk, text, sha))

        if new_items:
            texts = [t for _, t, _ in new_items]
            vectors = embedder.embed_texts(texts)
            result.embeddings_computed = len(vectors)
            cache.put_many(
                embedding_model,
                ((sha, vec) for (_, _, sha), vec in zip(new_items, vectors, strict=True)),
            )
            for (chunk, _, _), vec in zip(new_items, vectors, strict=True):
                embedded_iter.append(EmbeddedChunk(chunk=chunk, vector=vec))

        upserted = store.upsert(embedded_iter, pack_tagger=tagger)
        result.edge_effects = {"upserted": upserted}

    save_manifest(storage_path, manifest)
    store.close()
    return result
