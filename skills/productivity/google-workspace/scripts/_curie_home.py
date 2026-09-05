"""Resolve CURIE_HOME for standalone skill scripts.

Skill scripts may run outside the Curie process (e.g. system Python,
nix env, CI) where ``curie_constants`` is not importable.  This module
provides the same ``get_curie_home()`` and ``display_curie_home()``
contracts as ``curie_constants`` without requiring it on ``sys.path``.

When ``curie_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``curie_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``CURIE_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from curie_constants import display_curie_home as display_curie_home
    from curie_constants import get_curie_home as get_curie_home
except (ModuleNotFoundError, ImportError):

    def get_curie_home() -> Path:
        """Return the Curie home directory (default: ~/.curie).

        Mirrors ``curie_constants.get_curie_home()``."""
        val = os.environ.get("CURIE_HOME", "").strip()
        return Path(val) if val else Path.home() / ".curie"

    def display_curie_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``curie_constants.display_curie_home()``."""
        home = get_curie_home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(home)
