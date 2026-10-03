"""Taking part of a conversation back out, and what has to go with it.

Two things remove content the model has already been shown: compaction,
which summarises old turns away, and a rewind — ``/undo``, ``/retry``, the
bench console's Ctrl+B and Ctrl+G — which archives the newest turns.

After either, anything that remembers *having shown* the model something is
wrong. Several tools keep exactly that memory, per task, to save tokens on a
repeat: ``read_file`` answers a re-read of an unchanged file with a "file
unchanged since last read" stub, and ``skill_view`` answers a re-view with
"Skill content unchanged since it was loaded earlier in this conversation —
refer to the earlier skill_view result". Each stub points at a tool result
that is supposed to be further up the transcript. Once that result has been
summarised or taken back out, the stub points at nothing: the model is told
it has a copy it does not have, and goes looking for an earlier conversation
that, for all it can see, never happened.

Compaction already cleared those caches. A rewind did not, on any surface —
so a skill loaded in a turn that was then taken back could never be loaded
again in that conversation. :func:`forget_served_content` is the one place
that knows every such cache, and every path that removes content calls it.

A rewind takes back more than content, and :func:`forget_rewound_turns` and
:func:`restore_todo_list` are the rest of it. The read and search tools
count identical calls *in a row* to stop a model re-reading in a loop; the
reads of a taken-back turn are not in any row the model can see, but they
kept counting, so going back and asking again a few times got the re-read
BLOCKED with "You already have this information". And the todo list lives
on the agent, not in the transcript: a plan written in a taken-back turn
outlived it, came back on the model's next todo read, and was re-injected
after compaction as the task list to carry on with — so the model went back
to work the person had just taken away.

:func:`rewind_user_turn` is the verified rewind itself, shared so a surface
does not need a copy of it: it takes back one person-authored turn and
everything after it, keeps a compaction handoff that rides in the same row
(it is the only remaining copy of the turns it summarised), and — when the
conversation is stored — archives the same rows in the session store,
refusing rather than guessing when the store no longer matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Dict, List, Optional


def forget_served_content(task_id: Optional[str] = None) -> None:
    """Forget what the tools remember having shown the model under ``task_id``.

    Call it whenever content leaves the transcript the model is sent —
    compaction and every kind of rewind. ``task_id`` is the task id the turns
    ran under (each surface passes its own: the CLI and the bench console
    their session id, the TUI its session key). ``None`` or an empty id
    clears every task, which is never wrong — the caches only save tokens —
    and is what compaction did before this existed.

    Total: a tool module that will not import has nothing to forget.
    """
    key = str(task_id) if task_id else None
    try:
        from tools.file_tools import reset_file_dedup

        reset_file_dedup(key)
    except Exception:
        pass
    try:
        from tools.skills_tool import reset_skill_view_dedup

        reset_skill_view_dedup(key)
    except Exception:
        pass


def forget_rewound_turns(task_id: Optional[str] = None) -> None:
    """Forget what the tools remember of turns a rewind took back.

    :func:`forget_served_content`, and the read/search streaks and
    patch-failure counts as well — compaction keeps those, because the calls
    it summarises really were made in a row; a rewind takes them back.
    ``task_id`` as for :func:`forget_served_content`.

    Total: a tool module that will not import has nothing to forget.
    """
    forget_served_content(task_id)
    key = str(task_id) if task_id else None
    try:
        from tools.file_tools import reset_read_loop_tracking

        reset_read_loop_tracking(key)
    except Exception:
        pass


def restore_todo_list(agent: Any, history: Optional[List[Dict[str, Any]]]) -> None:
    """Put the agent's todo list back to what ``history`` last recorded.

    The list lives on the agent, so a rewind that only shortens the
    transcript leaves the taken-back turns' plan in place. It is emptied and
    re-read from the newest todo result still in ``history`` — the same
    reading a resumed session starts from — or left empty when there is none.
    Total: an agent without a todo list has nothing to restore.
    """
    store = getattr(agent, "_todo_store", None) if agent is not None else None
    if store is None:
        return
    try:
        store.restore([], revision=0)
    except Exception:
        return
    hydrate = getattr(agent, "_hydrate_todo_store", None)
    if callable(hydrate) and history:
        try:
            hydrate(list(history))
        except Exception:
            pass


def without_ephemeral_scaffolding(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The durable transcript shape: copies, minus transient recovery rows."""
    from run_agent import _is_ephemeral_scaffolding

    return [
        message.copy()
        for message in history or []
        if isinstance(message, dict) and not _is_ephemeral_scaffolding(message)
    ]


def user_turn_count(history: List[Dict[str, Any]]) -> int:
    """How many person-authored turns ``history`` holds, by the rewind's count."""
    from agent.context_compressor import user_originated_turn_view

    return sum(
        1
        for message in without_ephemeral_scaffolding(history)
        if user_originated_turn_view(message) is not None
    )


@dataclass(frozen=True)
class RewoundTurn:
    """What :func:`rewind_user_turn` took back, and what is left."""

    #: The retained prefix, as fresh copies, ready to install.
    history: List[Dict[str, Any]]
    #: The taken-back turn's canonical live projection — the person's own
    #: words, without any compaction handoff that shared their row.
    live_view: Dict[str, Any]
    #: Rows archived in the store, or messages dropped when not stored.
    rewound_count: int
    #: Whether the session store was rewound as well.
    persisted: bool


def _comparison_content(message: Dict[str, Any]) -> Any:
    """A message's content as the store would hold it, for matching."""
    from agent.memory_manager import sanitize_context
    from agent.tool_dispatch_helpers import (
        _is_multimodal_tool_result,
        _multimodal_text_summary,
    )

    content = message.get("content")
    if _is_multimodal_tool_result(content):
        content = _multimodal_text_summary(content)
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") in {
                "image",
                "image_url",
                "input_image",
            }:
                text_parts.append("[screenshot]")
        content = "\n".join(text_parts) if text_parts else None
    if message.get("role") in {"user", "assistant"} and isinstance(content, str):
        return sanitize_context(content).strip()
    return content


def rewind_user_turn(
    history: List[Dict[str, Any]],
    user_ordinal: int,
    *,
    session_id: str = "",
    db_scope: Optional[Callable[[], ContextManager[Any]]] = None,
    require_retryable: bool = False,
) -> RewoundTurn:
    """Take back person-authored turn ``user_ordinal`` and everything after it.

    ``history`` is not modified; the retained prefix comes back on the
    result for the caller to install. With a ``session_id`` and a
    ``db_scope`` (a callable returning a context manager that yields the
    ``SessionDB`` holding the session, or None when there is none) the store
    is rewound in the same step: the target row is bound to the in-memory
    turn by ordinal *and* content, and the archive is conditional on the
    store's active rows being the ones just read. Anything that does not line
    up raises ``RuntimeError`` and nothing is changed — a rewind that guessed
    would archive the wrong rows. An out-of-range ordinal raises
    ``ValueError`` before the store is touched.
    """
    from agent.context_compressor import (
        history_before_user_originated_turn,
        retryable_user_text,
        split_user_originated_turn,
        user_originated_turn_view,
    )

    history = without_ephemeral_scaffolding(history)
    user_indices = [
        index
        for index, message in enumerate(history)
        if user_originated_turn_view(message) is not None
    ]
    if user_ordinal < 0 or user_ordinal >= len(user_indices):
        raise ValueError("target user message is no longer in session history")
    target_index = user_indices[user_ordinal]
    installed, live_view = history_before_user_originated_turn(history, target_index)
    rewound_count = len(history) - target_index

    session_id = str(session_id or "").strip()
    persisted = False
    if session_id and db_scope is not None:
        with db_scope() as db:
            if db is None:
                raise RuntimeError("session database is unavailable")
            expected_active_ids = db.get_active_message_ids(session_id)
            durable = db.get_messages_as_conversation(
                session_id,
                include_row_ids=True,
            )
            durable_user_indices = [
                index
                for index, message in enumerate(durable)
                if user_originated_turn_view(message) is not None
            ]
            if len(durable_user_indices) != len(user_indices):
                raise RuntimeError(
                    "session history changed before the rewind could be persisted"
                )
            durable_target_index = durable_user_indices[user_ordinal]
            durable_target = durable[durable_target_index]
            durable_prefix, durable_live_view = history_before_user_originated_turn(
                durable, durable_target_index
            )
            if _comparison_content(durable_live_view) != _comparison_content(live_view):
                raise RuntimeError(
                    "session history changed before the rewind could be persisted"
                )
            target_row_id = durable_target.get("_row_id")
            if not isinstance(target_row_id, int):
                raise RuntimeError("rewind target has no durable row identity")
            if require_retryable:
                retryable_user_text(durable_live_view.get("content"))
            scaffold, _ = split_user_originated_turn(durable_target)
            result = db.rewind_to_message(
                session_id,
                target_row_id,
                preserve_compaction_handoff=scaffold is not None,
                expected_active_ids=expected_active_ids,
                expected_target_content=durable_live_view.get("content"),
            )
            if scaffold is not None:
                replacement_id = result.get("replacement_message_id")
                if not isinstance(replacement_id, int):
                    raise RuntimeError(
                        "rewind commit did not return the replacement scaffold id"
                    )
                durable_prefix[-1]["_row_id"] = replacement_id
                durable_prefix[-1]["_db_persisted"] = True
                installed[-1] = durable_prefix[-1]
            # Current clients address destructive follow-ups by durable row id.
            # Preserve the richer warm content (for example image parts), but
            # copy row identities when the retained warm/durable shapes align.
            if len(installed) == len(durable_prefix) and all(
                warm.get("role") == durable_message.get("role")
                and bool(warm.get("display_kind"))
                == bool(durable_message.get("display_kind"))
                and _comparison_content(warm) == _comparison_content(durable_message)
                for warm, durable_message in zip(installed, durable_prefix)
            ):
                for warm, durable_message in zip(installed, durable_prefix):
                    row_id = durable_message.get("_row_id")
                    if isinstance(row_id, int):
                        warm["_row_id"] = row_id
            live_view = durable_live_view
            rewound_count = int(result.get("rewound_count", 0))
            persisted = True
    elif require_retryable:
        retryable_user_text(live_view.get("content"))

    installed = [message.copy() for message in installed]
    return RewoundTurn(
        history=installed,
        live_view=live_view,
        rewound_count=rewound_count,
        persisted=persisted,
    )


def install_rewound_history(
    agent: Any,
    history: List[Dict[str, Any]],
    *,
    persisted: bool,
    task_id: Optional[str] = None,
) -> None:
    """Point a live agent at a rewound history, and forget the turns it lost.

    The agent's live mirror and its store-flush cursor follow the shorter
    history (a cursor left past the end would skip the next turn's rows), its
    todo list goes back to what the history last recorded (see
    :func:`restore_todo_list`), and what the tools remember of the
    taken-back turns under ``task_id`` is forgotten (see
    :func:`forget_rewound_turns`). The cached system prompt is deliberately
    left alone: nothing in it came from the turns taken back, and rebuilding
    it would throw away the prompt cache for every turn that remains.
    """
    if agent is not None:
        try:
            agent._session_messages = history
            if hasattr(agent, "_last_flushed_db_idx"):
                agent._last_flushed_db_idx = len(history) if persisted else 0
            if hasattr(agent, "_db_flush_scan_prefix"):
                agent._db_flush_scan_prefix = history[:] if persisted else None
        except Exception:
            pass
        restore_todo_list(agent, history)
    if task_id:
        forget_rewound_turns(task_id)


__all__ = [
    "RewoundTurn",
    "forget_rewound_turns",
    "forget_served_content",
    "install_rewound_history",
    "restore_todo_list",
    "rewind_user_turn",
    "user_turn_count",
    "without_ephemeral_scaffolding",
]
