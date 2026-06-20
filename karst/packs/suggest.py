"""Auto-suggest packs from the file/chunk layout.

Heuristic: chunks are grouped by their top-1 or top-2 directory segments
(spec §22 says packs are scoped to file globs). A directory needs at least
MIN_CHUNKS_PER_PACK chunks to qualify — below that, the pack is too small
to be a useful retrieval scope and just adds menu noise.

We deliberately don't use ML clustering for v1: directory layout already
encodes intent in most repos (auth/, billing/, components/, api/), and a
deterministic suggestion is far easier to debug than a graph-clustering
black box. Users can run `packs suggest` repeatedly without surprises.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..models import Chunk
from .models import Pack

# Tunables — exposed so tests can shrink the threshold for fixtures.
DEFAULT_MIN_CHUNKS = 3
DEFAULT_MAX_PACKS = 40
# Rough chars-per-token heuristic for English/code. Within ~20% on most
# bge-style tokenizers; close enough for budget meter purposes.
TOKEN_PER_CHUNK_CHARS = 4


@dataclass
class PackCandidate:
    pack: Pack
    sample_files: list[str]
    sample_names: list[str]


def suggest_packs(
    chunks: Iterable[Chunk],
    *,
    min_chunks_per_pack: int = DEFAULT_MIN_CHUNKS,
    max_packs: int = DEFAULT_MAX_PACKS,
) -> list[PackCandidate]:
    """Produce ranked pack candidates."""
    # Bucket chunks by directory key. We try top-1 segment first; if a single
    # segment dominates (>200 chunks, like 'backend/'), we re-bucket those
    # chunks by top-2 segments so the result is granular enough to be useful.
    by_top1: dict[str, list[Chunk]] = defaultdict(list)
    for c in chunks:
        key = _top_segments(c.file_relpath, n=1)
        if key:
            by_top1[key].append(c)

    final_buckets: dict[str, list[Chunk]] = {}
    for top1, group in by_top1.items():
        if len(group) <= 200:
            final_buckets[top1] = group
            continue
        # Re-bucket this oversized top-1 into top-2 segments.
        by_top2: dict[str, list[Chunk]] = defaultdict(list)
        leftover: list[Chunk] = []
        for c in group:
            key2 = _top_segments(c.file_relpath, n=2)
            if key2 and "/" in key2:
                by_top2[key2].append(c)
            else:
                leftover.append(c)
        if leftover:
            by_top2[top1].extend(leftover)
        final_buckets.update(by_top2)

    candidates: list[PackCandidate] = []
    for key, group in final_buckets.items():
        if len(group) < min_chunks_per_pack:
            continue
        candidates.append(_make_candidate(key, group))

    # Rank by chunk_count desc, then by label asc; trim to max_packs.
    candidates.sort(key=lambda c: (-c.pack.chunk_count, c.pack.label))
    return candidates[:max_packs]


def _make_candidate(dir_key: str, group: list[Chunk]) -> PackCandidate:
    files_counter: Counter[str] = Counter()
    chars_total = 0
    name_counter: Counter[str] = Counter()
    for c in group:
        files_counter[c.file_relpath] += 1
        chars_total += len(c.code)
        name_counter[c.name] += 1

    label = _label_from_dir(dir_key)
    pack = Pack(
        id=Pack.slug_from_label(label),
        label=label,
        scope=[f"{dir_key}/**" if not dir_key.endswith("/**") else dir_key],
        summary=_default_summary(dir_key, group, files_counter),
        token_estimate=max(1, chars_total // TOKEN_PER_CHUNK_CHARS),
        chunk_count=len(group),
        refreshed_at=Pack.now_iso(),
        auto=True,
    )

    return PackCandidate(
        pack=pack,
        sample_files=[f for f, _ in files_counter.most_common(5)],
        sample_names=[n for n, _ in name_counter.most_common(5)],
    )


def _top_segments(relpath: str, *, n: int) -> str:
    parts = PurePosixPath(relpath.replace("\\", "/")).parts
    parts = [p for p in parts if p not in (".", "..")]
    if len(parts) < 2:
        return ""  # top-level file — no sensible directory to bucket on
    return "/".join(parts[:n])


def _label_from_dir(dir_key: str) -> str:
    parts = [p for p in dir_key.split("/") if p]
    pretty = " ".join(_humanize(p) for p in parts)
    return pretty or dir_key


def _humanize(token: str) -> str:
    # Strip common decorations and Pascal-case sensibly.
    raw = token.replace("-", " ").replace("_", " ")
    return " ".join(word.capitalize() for word in raw.split())


def _default_summary(
    dir_key: str, group: list[Chunk], files: Counter[str]
) -> str:
    langs = Counter(c.language for c in group)
    top_lang = langs.most_common(1)[0][0] if langs else "code"
    return (
        f"{len(group)} chunks across {len(files)} files in {dir_key}/ "
        f"({top_lang})."
    )
