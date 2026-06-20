"""Repo walker.

Walks a directory tree, skips common noise directories, honors .gitignore at
the repo root, and yields files whose extension matches a supported language.

Kept deliberately small: gitignore handling is best-effort at the root; nested
.gitignore files are not unioned in v1. Good enough for MVP.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pathspec

from .languages import detect_language, supported_extensions

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",  # rust
        ".next",
        ".nuxt",
        ".turbo",
        ".cache",
        "vendor",
    }
)

# Hard ceiling on a single file. Beyond this size we assume it's vendored,
# minified, or generated — tree-sitter would still parse it but the chunk
# would be useless for retrieval.
MAX_FILE_BYTES: int = 1_500_000


def _load_gitignore(repo_root: Path) -> pathspec.PathSpec | None:
    gi = repo_root / ".gitignore"
    if not gi.is_file():
        return None
    try:
        lines = gi.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def iter_source_files(
    root: str | Path,
    *,
    extra_skip_dirs: frozenset[str] = frozenset(),
    follow_symlinks: bool = False,
) -> Iterator[Path]:
    """Yield paths to source files under `root` that match a supported language.

    Skips DEFAULT_SKIP_DIRS plus any extras. Honors the repo-root .gitignore
    if present. Skips files larger than MAX_FILE_BYTES.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {root_path}")

    skip = DEFAULT_SKIP_DIRS | extra_skip_dirs
    exts = supported_extensions()
    gitignore = _load_gitignore(root_path)

    stack: list[Path] = [root_path]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue

        for entry in entries:
            if entry.is_symlink() and not follow_symlinks:
                continue
            if entry.is_dir():
                if entry.name in skip or entry.name.startswith("."):
                    # Hidden dirs are skipped by default; .git etc. are in `skip`
                    # anyway. Users who want hidden config dirs scanned can
                    # rename them or pass extra_skip_dirs accordingly.
                    continue
                if gitignore is not None:
                    rel = entry.relative_to(root_path).as_posix() + "/"
                    if gitignore.match_file(rel):
                        continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in exts:
                continue
            if detect_language(entry) is None:
                continue
            if gitignore is not None:
                rel = entry.relative_to(root_path).as_posix()
                if gitignore.match_file(rel):
                    continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            if size == 0 or size > MAX_FILE_BYTES:
                continue
            yield entry
