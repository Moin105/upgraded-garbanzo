"""Enables `python -m karst …` as a PATH-independent entry point.

Equivalent to the `karst` console script, but always works even when the
interpreter's Scripts/bin directory isn't on PATH (common with Microsoft Store
Python and `pip install --user`).
"""
import sys

from karst.cli import main

if __name__ == "__main__":
    sys.exit(main())
