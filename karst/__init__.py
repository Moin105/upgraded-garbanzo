"""karst — code context for AI dev tools."""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: the version recorded at install time from
    # pyproject.toml. This is what an installed wheel reports, so it can never
    # drift from the published package again.
    __version__ = _pkg_version("karst")
except PackageNotFoundError:  # running from a source tree with no install metadata
    __version__ = "0.2.6"

