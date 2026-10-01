"""Session-store operations the console performs itself.

The logbook reads the session store; these write to it. One operation, used
from two places: LOGBOOK → DUPLICATE copies any past conversation, and
``/branch`` (``/fork``) copies the one on the bench. Both produce the same
thing — a new conversation, with its own id and its own row in the logbook,
holding a copy of the original's transcript — and both leave the original
exactly as it was.

The copy follows the TUI's ``session.branch``, which is the most recent of
Curie's fork paths and the one that settled what a copy should hold:

* **The visible transcript, not the model's working set.** After compaction
  the model history is a summary and a tail; the display projection still has
  every turn the reader saw. A copy taken from the former would lose every
  turn compacted away before it was taken, permanently.
* **Turns, not tool plumbing.** User and assistant messages that say
  something, with their reasoning fields and timeline markers intact. Tool
  calls and their results stay behind: copied without the process that
  answered them they are calls nobody will ever answer.
* **A branch marker.** ``_branched_from`` and the parent link are what tell
  the store this row *owns* its transcript rather than continuing its
  parent's — so a resume of the copy reads the copy, and nothing written to
  the original afterwards can appear in it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

#: What a copy is called beside its original, so the two rows of the
#: logbook can be told apart at a glance — an untitled original is listed
#: under its opening line, and a copy titled with that same line would be
#: its exact double.
COPY_SUFFIX = " (copy)"

#: The longest title a copy is given, before the suffix. The logbook's
#: subject column shows sixty-four characters.
TITLE_ROOM = 48

#: The fields of a message that survive into the copy — the TUI branch's
#: list, so a conversation copied here and one branched there are the same
#: shape in the store.
COPIED_FIELDS = (
    "role",
    "content",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "display_kind",
    "display_metadata",
    "timestamp",
)


class DuplicateError(Exception):
    """Why a conversation could not be copied, as a sentence for the reader."""


@dataclass(frozen=True)
class Duplicate:
    """A copy that was made."""

    session_id: str
    title: str
    source_id: str
    messages: int


def new_session_id(now: Optional[datetime] = None) -> str:
    """An id in the agent's own format: ``YYYYmmdd_HHMMSS_xxxxxx``."""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:6]}"


def message_text(content: Any) -> str:
    """The words in a message's content, whatever shape it arrived in."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, (list, tuple)):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "input_text", "output_text"):
                parts.append(str(part.get("text") or ""))
        return " ".join(p for p in parts if p)
    return str(content)


def visible_turns(messages: Iterable[Any]) -> list[dict]:
    """The messages a copy keeps: user and assistant turns that say something."""
    kept: list[dict] = []
    for message in messages or ():
        if not isinstance(message, dict):
            continue
        if message.get("role") not in ("user", "assistant"):
            continue
        if not message_text(message.get("content")).strip():
            continue
        kept.append(dict(message))
    return kept


def copy_title(original: str, opening: str = "") -> str:
    """The base of a copy's title: the original's, marked as a copy.

    A copy of a copy is not "(copy) (copy)", and the lineage number the store
    appends to tell copies apart (``#2``) is not carried forward either —
    both are stripped before the suffix goes back on.
    """
    base = " ".join(str(original or "").split()) or " ".join(str(opening or "").split())
    base = re.sub(r"(?: #\d+)+$", "", base)
    while base.endswith(COPY_SUFFIX):
        base = base[: -len(COPY_SUFFIX)].rstrip()
    base = re.sub(r"(?: #\d+)+$", "", base)
    if len(base) > TITLE_ROOM:
        base = base[: TITLE_ROOM - 1].rstrip() + "…"
    return (base or "conversation") + COPY_SUFFIX


def _open_db(db: Any = None) -> Any:
    if db is not None:
        return db
    try:
        from curie_state import SessionDB

        return SessionDB()
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        raise DuplicateError(f"the session store could not be opened ({exc})") from exc


def resolve_session_id(session_id: str, db: Any = None) -> str:
    """The id a conversation lives on now — its compression tip."""
    db = _open_db(db)
    resolver = getattr(db, "resolve_resume_session_id", None)
    if callable(resolver):
        try:
            return str(resolver(session_id) or session_id)
        except Exception:
            return session_id
    return session_id


def duplicate_session(
    source_id: str,
    *,
    title: str = "",
    source: str = "cli",
    db: Any = None,
) -> Duplicate:
    """Copy a conversation into a new one. The original is not touched.

    ``title`` names the copy; without one it is the original's title (or
    opening line) marked ``(copy)``, numbered by the store if that is taken.
    ``source`` is the surface the copy belongs to — the console's own
    conversations are ``cli`` sessions, and a copy is born on the console.

    Raises :class:`DuplicateError` with a sentence for the reader. A copy that
    fails part-way is removed rather than left as an empty row in the
    logbook.
    """
    source_id = str(source_id or "").strip()
    if not source_id:
        raise DuplicateError("no conversation is selected")
    db = _open_db(db)
    resolved = resolve_session_id(source_id, db)

    try:
        row = db.get_session(resolved)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        raise DuplicateError(f"the session store would not answer ({exc})") from exc
    if not row:
        raise DuplicateError(f"{source_id} is not in the session store")

    try:
        _model_history, display_history = db.get_resume_conversations(resolved)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        raise DuplicateError(f"its transcript could not be read ({exc})") from exc
    turns = visible_turns(display_history)
    if not turns:
        raise DuplicateError("that conversation has nothing in it to copy yet")

    if title.strip():
        wanted = " ".join(title.split())
    else:
        try:
            original = db.get_session_title(resolved) or ""
        except Exception:
            original = ""
        opening = next(
            (message_text(t.get("content")) for t in turns if t.get("role") == "user"),
            "",
        )
        wanted = copy_title(original, opening)
    try:
        wanted = db.get_next_title_in_lineage(wanted)
    except Exception:
        pass

    new_id = new_session_id()
    try:
        db.create_session(
            new_id,
            source=source or "cli",
            model=row.get("model"),
            model_config={"_branched_from": resolved},
            parent_session_id=resolved,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        raise DuplicateError(f"the new conversation could not be created ({exc})") from exc

    try:
        db.append_messages_batch(
            new_id,
            [{name: turn.get(name) for name in COPIED_FIELDS} for turn in turns],
            chunk_rows=500,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        # Half a copy is worse than none: it is a row in the logbook that
        # looks like a conversation and opens onto nothing.
        try:
            db.delete_session(new_id)
        except Exception:
            pass
        raise DuplicateError(f"the messages could not be copied ({exc})") from exc

    # The title is the last step and the only one allowed to fall short: the
    # copy is whole by now, and a conversation without the name it was meant
    # to have is still the conversation. Titles are unique in the store, so a
    # name taken between choosing it and writing it is tried once more under
    # the store's next number before giving up.
    named = ""
    for candidate in (wanted, None):
        try:
            if candidate is None:
                candidate = db.get_next_title_in_lineage(wanted)
            if db.set_session_title(new_id, candidate) is not False:
                named = candidate
                break
        except Exception:
            continue

    return Duplicate(
        session_id=new_id,
        title=named,
        source_id=resolved,
        messages=len(turns),
    )


def rename_session(session_id: str, title: str, db: Any = None) -> str:
    """Give a conversation a title. Returns "" or why it could not.

    Titles are unique in the store, and a clash is refused there without an
    error — which is reported here as the sentence it is, rather than as a
    rename that silently did nothing.
    """
    title = " ".join(str(title or "").split())
    if not title:
        return "a title needs at least one word"
    db = _open_db(db)
    try:
        ok = db.set_session_title(session_id, title)
    except ValueError as exc:
        # Raised for a title in use elsewhere and for one the store will not
        # hold (too long); its message is the reason, in words.
        return str(exc) or f"“{title}” could not be used"
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the store refused it ({exc})"
    if not ok:
        return (
            "this conversation has not been written to the store yet — "
            "send a message first"
        )
    return ""


def find_session(query: str, db: Any = None) -> Optional[str]:
    """A conversation named by its id, its title, or an unambiguous id prefix."""
    query = str(query or "").strip()
    if not query:
        return None
    db = _open_db(db)
    try:
        if db.get_session(query):
            return query
    except Exception:
        pass
    finder = getattr(db, "get_session_by_title", None)
    if callable(finder):
        try:
            found = finder(query)
        except Exception:
            found = None
        if isinstance(found, dict) and found.get("id"):
            return str(found["id"])
        if isinstance(found, str) and found:
            return found
    try:
        rows = db.list_sessions_rich(limit=500, order_by_last_active=True)
    except Exception:
        rows = []
    lowered = query.lower()
    by_prefix = [
        str(r.get("id")) for r in rows or ()
        if isinstance(r, dict) and str(r.get("id") or "").startswith(query)
    ]
    if len(by_prefix) == 1:
        return by_prefix[0]
    by_title = [
        str(r.get("id")) for r in rows or ()
        if isinstance(r, dict) and str(r.get("title") or "").strip().lower() == lowered
    ]
    if len(by_title) == 1:
        return by_title[0]
    return None


__all__ = [
    "COPY_SUFFIX",
    "Duplicate",
    "DuplicateError",
    "copy_title",
    "duplicate_session",
    "find_session",
    "message_text",
    "new_session_id",
    "rename_session",
    "resolve_session_id",
    "visible_turns",
]
