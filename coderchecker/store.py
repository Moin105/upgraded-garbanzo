"""Qdrant vector store wrapper.

Spec §18 calls for Qdrant with cosine distance and per-chunk metadata. Phase 1
uses Qdrant's embedded local-file mode so the agent runs without Docker;
swapping to a Qdrant cluster later is a single URL change.

Point IDs are derived from chunk_id (deterministic per file+span+sha), so
re-indexing the same commit upserts in place rather than duplicating.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .embedder import EmbeddedChunk
from .models import Chunk, ChunkKind

PackTagger = Callable[[str], list[str]]
"""Maps a file_relpath to the list of pack ids that include it."""

DEFAULT_COLLECTION = "code_chunks"
DEFAULT_BATCH = 64


@dataclass
class SearchHit:
    chunk: Chunk
    score: float


class ChunkStore:
    def __init__(
        self,
        *,
        location: str | Path,
        collection: str = DEFAULT_COLLECTION,
    ) -> None:
        from qdrant_client import QdrantClient

        self._collection = collection
        self._location = str(location)
        # Local-file persistent mode. ":memory:" works too but we want survival
        # across CLI invocations.
        self._client = QdrantClient(path=self._location)

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self, *, vector_size: int) -> None:
        from qdrant_client.http import models as qm

        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection in existing:
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )

    def reset_collection(self, *, vector_size: int) -> None:
        """Drop everything in the collection.

        Note: qdrant-client's local-file mode doesn't fully wipe disk segments
        on delete_collection + create_collection (the recreated collection
        still sees the old points). We work around that by deleting all points
        via an empty filter, which is fully honored in local mode.
        """
        from qdrant_client.http import models as qm

        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            self.ensure_collection(vector_size=vector_size)
            return
        self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(filter=qm.Filter()),
        )

    def upsert(
        self,
        embedded: Iterable[EmbeddedChunk],
        *,
        batch_size: int = DEFAULT_BATCH,
        pack_tagger: "PackTagger | None" = None,
    ) -> int:
        from qdrant_client.http import models as qm

        batch: list[qm.PointStruct] = []
        total = 0
        for ec in embedded:
            payload = _chunk_payload(ec.chunk)
            if pack_tagger is not None:
                payload["packs"] = pack_tagger(ec.chunk.file_relpath)
            else:
                payload["packs"] = []
            batch.append(
                qm.PointStruct(
                    id=_chunk_point_id(ec.chunk.chunk_id),
                    vector=ec.vector,
                    payload=payload,
                )
            )
            if len(batch) >= batch_size:
                self._client.upsert(collection_name=self._collection, points=batch)
                total += len(batch)
                batch = []
        if batch:
            self._client.upsert(collection_name=self._collection, points=batch)
            total += len(batch)
        return total

    def delete_by_file(self, relpath: str) -> int:
        """Drop every chunk whose payload.file_relpath matches.

        Used by incremental indexing: when a file's content SHA changes we
        delete its old chunks before upserting the new ones, so stale chunks
        don't linger in the index.
        """
        from qdrant_client.http import models as qm

        before = self.count()
        self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="file_relpath", match=qm.MatchValue(value=relpath)
                        )
                    ]
                )
            ),
        )
        return before - self.count()

    def retag_with_packs(self, pack_tagger: "PackTagger") -> int:
        """Recompute the `packs` payload for every point. Used after the
        user changes their pack scope (suggest/create/delete).

        Implementation note: Qdrant local mode supports `set_payload` but
        only per-point. For Phase 4 we scroll the collection in batches,
        compute the new tag list, and set it back.
        """
        from qdrant_client.http import models as qm

        scroll_offset = None
        updated = 0
        while True:
            points, scroll_offset = self._client.scroll(
                collection_name=self._collection,
                limit=256,
                offset=scroll_offset,
                with_payload=True,
            )
            if not points:
                break
            for p in points:
                relpath = (p.payload or {}).get("file_relpath", "")
                tags = pack_tagger(relpath) if relpath else []
                self._client.set_payload(
                    collection_name=self._collection,
                    payload={"packs": tags},
                    points=[p.id],
                )
                updated += 1
            if scroll_offset is None:
                break
        return updated

    def search(
        self,
        vector: list[float],
        *,
        limit: int = 8,
        pack_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        from qdrant_client.http import models as qm

        query_filter: qm.Filter | None = None
        if pack_ids:
            # MatchAny over the `packs` array field: a chunk matches if its
            # packs list contains ANY of the requested ids. Filter is applied
            # before scoring — this is where the token-cost savings come from.
            query_filter = qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="packs", match=qm.MatchAny(any=list(pack_ids))
                    )
                ]
            )

        try:
            res = self._client.query_points(
                collection_name=self._collection,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            points = res.points
        except AttributeError:
            points = self._client.search(
                collection_name=self._collection,
                query_vector=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )

        hits: list[SearchHit] = []
        for p in points:
            chunk = _chunk_from_payload(p.payload or {})
            if chunk is None:
                continue
            hits.append(SearchHit(chunk=chunk, score=float(p.score)))
        return hits

    def count(self) -> int:
        return self._client.count(self._collection, exact=True).count

    def close(self) -> None:
        # qdrant-client local mode holds a file lock; release it explicitly so
        # the next CLI invocation can re-open the same path.
        try:
            self._client.close()
        except Exception:
            pass


def _chunk_point_id(chunk_id: str) -> str:
    """Qdrant requires point IDs to be unsigned ints or UUIDs. Our chunk_id is
    `chunk_<sha1-prefix>` — map deterministically to a UUID5 so re-indexes
    overwrite rather than duplicate.
    """
    digest = hashlib.sha1(chunk_id.encode("utf-8")).digest()[:16]
    return str(uuid.UUID(bytes=digest))


def _chunk_payload(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "file_relpath": chunk.file_relpath,
        "language": chunk.language,
        "kind": chunk.kind.value,
        "name": chunk.name,
        "qualified_name": chunk.qualified_name,
        "parent": chunk.parent,
        "signature": chunk.signature,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "start_byte": chunk.start_byte,
        "end_byte": chunk.end_byte,
        "file_sha": chunk.file_sha,
        "code": chunk.code,
    }


def _chunk_from_payload(payload: dict) -> Chunk | None:
    try:
        return Chunk(
            file_relpath=payload["file_relpath"],
            language=payload["language"],
            kind=ChunkKind(payload["kind"]),
            name=payload["name"],
            qualified_name=payload["qualified_name"],
            start_line=int(payload["start_line"]),
            end_line=int(payload["end_line"]),
            start_byte=int(payload["start_byte"]),
            end_byte=int(payload["end_byte"]),
            code=payload.get("code", ""),
            file_sha=payload["file_sha"],
            parent=payload.get("parent"),
            signature=payload.get("signature"),
        )
    except (KeyError, ValueError):
        return None
