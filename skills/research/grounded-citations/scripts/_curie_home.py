"""Resolve CURIE_HOME for standalone skill scripts.

Skill scripts may run outside the Curie process (system Python, nix env,
CI) where ``curie_constants`` is not importable.  This module provides the
same ``get_curie_home()`` contract without requiring it on ``sys.path``.

When ``curie_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from curie_constants import get_curie_home as get_curie_home
except (ModuleNotFoundError, ImportError):

    def get_curie_home() -> Path:
        """Return the Curie home directory (default: ``~/.curie``)."""
        val = os.environ.get("CURIE_HOME", "").strip()
        return Path(val) if val else Path.home() / ".curie"
