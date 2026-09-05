"""Single owner for the session system-prompt overlay.

The agent has exactly one personality, and it lives in ``SOUL.md``.  That file
is read fresh on every message, so editing it takes effect immediately with no
restart and no config round-trip.

This module owns the one *other* thing that can prepend text to a session: the
user-authored ``agent.system_prompt`` escape hatch (and the
``CURIE_EPHEMERAL_SYSTEM_PROMPT`` environment variable that callers check
first).  That is a manual override the user writes themselves — it is not a
personality, it ships with no presets, and nothing in the codebase generates
it.

History: ``curie_cli/personality.py`` used to sit here, shipping a table of
built-in character sheets ("kawaii", "pirate", "noir", …) selected by
``display.personality`` and extensible through ``agent.personalities``.  Two
independent sources of voice is one too many: whichever won, the other was
silently ignored, and the config-side one kept resurrecting itself out of
stale per-surface state.  The personality table, both config keys, and the
``/personality`` command are gone; ``SOUL.md`` is the only answer to "who is
this agent".  The v35 config migration strips the dead keys.

This module deliberately has no module-level imports from ``curie_cli.config``
(that module imports us), keeping the import direction acyclic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _get(cfg: Optional[Dict[str, Any]], *keys: str, default: Any = None) -> Any:
    """Nested dict lookup tolerant of None/non-dict intermediate nodes."""
    node: Any = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def prompt_text(value: Any) -> str:
    """Normalize config prompt values from YAML (str | list | None) to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def resolve_ephemeral_system_prompt(cfg: Optional[Dict[str, Any]]) -> str:
    """Resolve the session overlay from config.

    Returns the user-owned ``agent.system_prompt``, or ``""`` when unset — in
    which case ``SOUL.md`` alone describes the agent.  Callers should still
    prefer ``CURIE_EPHEMERAL_SYSTEM_PROMPT`` when that env var is set.
    """
    return prompt_text(_get(cfg, "agent", "system_prompt", default=""))


__all__ = ["prompt_text", "resolve_ephemeral_system_prompt"]
