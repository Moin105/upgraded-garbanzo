"""Chunk embedder.

Wraps FastEmbed (ONNX-backed sentence embedders) so we don't drag torch into
the dependency tree. Embedding model is configurable; default is
BAAI/bge-small-en-v1.5 (384 dims) — small, fast, decent on code.

Spec §18 specifies text-embedding-3-large (3072 dims) for production. We use a
smaller local model in Phase 1 so the agent runs offline; the swap point is a
single constant.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .models import Chunk

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_BATCH = 32

# Code chunks can be long. Most embedding models truncate at ~512 tokens; we
# cap input bytes so the truncation falls in a predictable place (and so we
# don't waste cycles encoding minified bundles that slipped through).
MAX_EMBED_BYTES = 8_000


@dataclass
class EmbeddedChunk:
    chunk: Chunk
    vector: list[float]


class Embedder:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: str | None = None,
    ) -> None:
        # Imported lazily so test runs that don't touch embeddings skip the
        # ONNX warmup entirely.
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self._dim: int | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        if self._dim is None:
            # FastEmbed exposes dim via the first embedding; encode a single
            # token to learn it without loading a giant corpus.
            self._dim = len(next(iter(self._model.embed(["x"]))))
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [list(v) for v in self._model.embed(texts)]

    def embed_chunks(
        self,
        chunks: Iterable[Chunk],
        *,
        batch_size: int = DEFAULT_BATCH,
    ) -> Iterator[EmbeddedChunk]:
        batch: list[Chunk] = []
        for chunk in chunks:
            batch.append(chunk)
            if len(batch) >= batch_size:
                yield from self._flush(batch)
                batch = []
        if batch:
            yield from self._flush(batch)

    def _flush(self, batch: list[Chunk]) -> Iterator[EmbeddedChunk]:
        texts = [_chunk_to_text(c) for c in batch]
        vectors = self.embed_texts(texts)
        for chunk, vector in zip(batch, vectors, strict=True):
            yield EmbeddedChunk(chunk=chunk, vector=vector)


def _chunk_to_text(chunk: Chunk) -> str:
    """Build the string we actually embed.

    Header line gives the model strong identifier signal (per spec §18 hybrid
    note: identifiers are the user's actual handle on the system). The body
    is truncated by bytes, not characters — UTF-8 multibyte is fine because
    embedders handle malformed tails gracefully.
    """
    header = f"{chunk.language} {chunk.kind.value} {chunk.qualified_name}"
    if chunk.signature and chunk.signature != chunk.code.strip():
        header = f"{header}\n{chunk.signature}"
    body = chunk.code
    if len(body.encode("utf-8", errors="ignore")) > MAX_EMBED_BYTES:
        body = body[:MAX_EMBED_BYTES]
    return f"{header}\n{body}"
