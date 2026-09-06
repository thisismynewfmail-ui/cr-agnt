"""Runs agent turns off the UI thread and reports progress back.

The agent loop is synchronous and long-running: it blocks on network I/O,
executes tools, and can take minutes. Calling it from Textual's event loop
would freeze the console — no repaints, no mouse, no way to interrupt. So a
turn runs on a worker thread and communicates through a queue of events the
app drains on its own clock.

The bridge owns exactly one thing: the boundary. It does not interpret the
agent's output, style it, or decide what the console shows.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class TurnEvent:
    """One thing that happened during a turn.

    ``kind`` is one of:

    ``delta``     a chunk of assistant text arrived (``text``)
    ``reasoning`` a chunk of reasoning/thinking arrived (``text``)
    ``wait``      the agent is waiting on the provider (``text`` says why)
    ``tool_gen``  the model has begun writing a call (``text`` is the tool
                  name); it has not run yet and may still be abandoned
    ``tool``      a tool call started running (``text`` is the tool name)
    ``tool_done`` a tool call finished (``text`` is the tool name)
    ``record``    something was written to the record — a session title, a
                  memory flush, a compression (``text`` says what)
    ``compacting`` the conversation is being compacted so the turn can carry
                  on (``text`` is the agent's own progress line); the turn is
                  *not* over and no answer will arrive until it ends
    ``compacted`` compaction finished and the turn is resuming (``text`` is
                  the agent's completion line)
    ``status``    a transient status line (``text``)
    ``warn``      a degraded side path reported a problem (``text``)
    ``done``      the turn finished (``text`` is the final response)
    ``error``     the turn failed (``text`` is the message)

    The last three of the first group exist so the console can tell four
    waits apart that a single "busy" flag cannot: waiting on the provider,
    reasoning, running a tool, and writing. Those look identical from the
    outside and are the whole content of a long turn.
    """

    kind: str
    text: str = ""
    at: float = field(default_factory=time.monotonic)


#: Every progress hook the bridge takes over for the length of a turn, and
#: puts back afterwards. Restoring matters: the agent outlives the turn, and
#: a hook still pointing at a finished turn's queue is a slow leak.
_HOOKS = (
    "reasoning_callback",
    "thinking_callback",
    "tool_gen_callback",
    "tool_start_callback",
    "tool_complete_callback",
    "status_callback",
    "_on_session_title",
    "event_callback",
)


#: The first argument ``AIAgent.status_callback`` is called with. It is a
#: *kind*, not the message — a surface that treats it as the text shows the
#: literal word "lifecycle" to the user for every status the agent emits.
#:
#: ``compacted`` is in this set because it is the one the agent emits from
#: ``_emit_compaction_done``, and leaving it out is not a missing feature but
#: a swallowed message: the two-argument call fell through to the
#: single-argument branch, which reads ``args[0]`` as the text — so the
#: console printed the bare word "compacted" and *dropped the sentence saying
#: compaction had finished*. That is the whole of "compaction seems not to
#: happen" as a reader experiences it: it does happen, and nothing says so.
_STATUS_KINDS = frozenset(
    {"lifecycle", "warn", "info", "status", "error", "compacted", "compacting"}
)

#: The longest a status *kind* can be. Kinds are bare identifiers the agent
#: chooses; messages are sentences. Anything short, lowercase and unspaced is
#: taken as a kind even when it is not one this version has heard of, so a
#: kind added to the agent later cannot silently start eating messages here
#: the way ``compacted`` did.
_STATUS_KIND_MAX = 24


def _is_human_turn(message: Any) -> bool:
    """Whether a history entry is something the person actually typed.

    Not every ``role="user"`` row is: the agent loop writes user-role
    scaffolding of its own — a nudge to finish a dropped tool call, a
    verification prompt, a compaction summary — and those come back in the
    history the turn returns.

    Going back one exchange has to skip them. Cutting at the newest user-role
    row instead landed on a nudge, which left the real request in place with
    a half-answered turn hanging off it: an assistant message whose tool
    calls had no results. Sent back to the model, that reads as work still
    to do, so it makes the same calls again — the console's own contribution
    to a turn that will not end.

    The agent's own reader is used rather than a copy of its list of
    markers, because a copy would be wrong the first time a marker is added.
    """
    if not isinstance(message, dict):
        return False
    try:
        from agent.conversation_compression import _is_real_user_message

        return bool(_is_real_user_message(message))
    except Exception:
        # The agent package is not importable (no provider extra, a partial
        # install). The plain reading is worse but it is not nothing.
        return message.get("role") == "user"


def is_compaction_progress(text: str) -> bool:
    """Whether a lifecycle line is auto-compaction reporting its progress.

    Asked of the agent, which owns the wording, rather than matched against a
    copy of it here — a copy is wrong the first time a template is reworded,
    and the console would go back to showing compaction as an eight-second
    notice with a stalled state figure behind it. The local fallback exists
    only for an install where the agent package is not importable at all, and
    is deliberately narrow.
    """
    try:
        from agent.conversation_compression import is_compaction_progress_status

        return bool(is_compaction_progress_status(text))
    except Exception:
        lowered = str(text or "").lower()
        if "compaction complete" in lowered:
            return False
        return "compacting" in lowered or "compression" in lowered


def _looks_like_status_kind(text: str) -> bool:
    """Whether a first argument is a *kind* rather than a message.

    A kind is a bare identifier the agent picked — ``lifecycle``, ``warn``,
    ``compacted``. A message is a sentence, and usually one with a pictogram
    and punctuation in it. Telling them apart by shape rather than by a fixed
    list is what stops the next kind the agent gains from being rendered as
    the whole status line while the real sentence is thrown away.
    """
    body = text.strip()
    if not body or len(body) > _STATUS_KIND_MAX:
        return False
    return body.replace("_", "").replace("-", "").isalpha() and body.islower()


def _status_parts(args: tuple) -> tuple[str, str]:
    """Normalise a status callback's arguments to ``(kind, message)``.

    The agent calls ``status_callback("lifecycle", message)``; older and
    simpler emitters call it with the message alone. Both have to arrive here
    as the same shape, and neither may be dropped.
    """
    if not args:
        return "lifecycle", ""
    first = str(args[0] or "")
    if len(args) >= 2:
        kind = first.strip().lower()
        if kind in _STATUS_KINDS or _looks_like_status_kind(first):
            return kind, str(args[1] or "")
    return "lifecycle", first


class AgentBridge:
    """Owns the agent instance and the worker thread that drives it."""

    def __init__(self, session_id: Optional[str] = None) -> None:
        self._agent: Any = None
        self._events: "queue.Queue[TurnEvent]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None
        self._chars_this_turn = 0
        # Reasoning and tool activity are counted apart from answer text so
        # the console's recorder can draw them as separate traces. One total
        # would answer "is anything happening", which the reader can already
        # see; three answer what the time actually went into.
        self._reasoning_chars_this_turn = 0
        self._tool_calls_this_turn = 0
        self._session_id = session_id
        # The conversation, as the model sees it. The bridge owns this
        # because ``run_conversation`` builds each turn's message list from
        # the history it is *handed* and never reads it off the agent — so a
        # surface that does not keep and pass it gets a first turn every
        # time. See :meth:`_run_turn`.
        self._history: list = []

    # ── Lifecycle ────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        worker = self._worker
        return worker is not None and worker.is_alive()

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def describe(self) -> dict:
        """Model/provider/context facts for the panel readouts.

        Returns ``{}`` before the agent exists — the console renders dashes
        rather than guessing, because a panel showing a plausible-looking
        wrong number is worse than one showing nothing.
        """
        agent = self._agent
        if agent is None:
            return {}
        out = {
            "model": str(getattr(agent, "model", "") or ""),
            "provider": str(getattr(agent, "provider", "") or ""),
            "base_url": str(getattr(agent, "base_url", "") or ""),
            "session_id": str(getattr(agent, "session_id", "") or ""),
        }
        for attr in ("context_length", "max_context_tokens"):
            value = getattr(agent, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                out["context_length"] = int(value)
                break
        # The bridge's own history, not the agent attribute: the attribute is
        # not what a turn is built from, so it is not what "turns" means here.
        out["turns"] = sum(
            1
            for m in self._history
            if isinstance(m, dict) and m.get("role") == "user"
        )
        return out

    @property
    def session_id(self) -> Optional[str]:
        """The conversation this bridge is writing to, once it has one."""
        agent = self._agent
        if agent is not None:
            return str(getattr(agent, "session_id", "") or "") or None
        return self._session_id

    def ensure_agent(self) -> bool:
        """Build the agent if it does not exist yet. Returns success.

        Construction reads config, resolves the provider, loads tools and
        skills, and can take a second or two — so callers run it from a
        worker, not from an event handler.
        """
        with self._lock:
            if self._agent is not None:
                return True
            try:
                self._agent = _build_agent(session_id=self._session_id)
                self._session_id = str(
                    getattr(self._agent, "session_id", "") or ""
                ) or None
                self._load_error = None
                return True
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._load_error = f"{type(exc).__name__}: {exc}"
                return False

    def new_session(self) -> bool:
        """Rebuild the agent against a fresh conversation. Returns success.

        The agent is replaced rather than reset. Its session id is read at
        construction time by the state-row creator, the recall engine and the
        approval session key, so clearing ``conversation_history`` in place
        would leave all three still pointing at the previous conversation —
        and the next turn would be appended to the one the reader thinks they
        just left.
        """
        if self.busy:
            return False
        with self._lock:
            try:
                agent = _build_agent()
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._load_error = f"{type(exc).__name__}: {exc}"
                return False
            self._agent = agent
            self._session_id = str(getattr(agent, "session_id", "") or "") or None
            self._load_error = None
            self._reset_counters()
            self._history = []
            # Anything the previous turn left queued belongs to a
            # conversation that is no longer on the bench.
            while True:
                try:
                    self._events.get_nowait()
                except queue.Empty:
                    break
            return True

    def load_session(self, session_id: str) -> "list[dict] | None":
        """Point the bridge at an existing conversation and return its transcript.

        Returns the display history (every message in the conversation's
        compression lineage, oldest first) so the caller can redraw it, or
        None when the session cannot be opened. Refuses while a turn is
        running: swapping the agent's history under a live turn would lose
        whichever of the two came second.

        The agent is rebuilt rather than mutated. Its session id is read at
        construction time by the state row creator, the recall engine and the
        approval session key, so reassigning the attribute afterwards leaves
        those three pointing at the old conversation.
        """
        session_id = str(session_id or "").strip()
        if not session_id or self.busy:
            return None
        with self._lock:
            try:
                from curie_state import SessionDB

                db = SessionDB()
                resolved = session_id
                resolver = getattr(db, "resolve_resume_session_id", None)
                if callable(resolver):
                    # A conversation that has been compressed lives on under
                    # the id of its latest continuation; resuming the root
                    # would write into a session nothing reads back.
                    resolved = str(resolver(session_id) or session_id)
                model_history, display_history = db.get_resume_conversations(resolved)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._load_error = f"{type(exc).__name__}: {exc}"
                return None

            try:
                agent = _build_agent(session_id=resolved)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._load_error = f"{type(exc).__name__}: {exc}"
                return None

            # The model's working history is the repaired projection, not the
            # display one: display history carries the whole lineage plus
            # markers, and replaying that at the model would double up turns
            # the compression already summarised.
            #
            # This is what the next turn is actually handed. Setting
            # ``agent.conversation_history`` alone did nothing: the turn
            # builder reads its history from the argument, not the attribute.
            self._history = list(model_history or [])
            try:
                agent.conversation_history = list(self._history)
            except Exception:
                pass
            self._agent = agent
            self._session_id = resolved
            self._load_error = None
            self._reset_counters()
            return list(display_history or [])

    # ── Going back ───────────────────────────────────────────────────────

    @property
    def last_prompt(self) -> Optional[str]:
        """The most recent thing the user asked, or None.

        A nudge the agent loop wrote to itself is not something the user
        asked, however it is filed — see :func:`_is_human_turn`.
        """
        for message in reversed(self._history):
            if _is_human_turn(message):
                text = message.get("content")
                if isinstance(text, str) and text.strip():
                    return text
                # Multimodal turns carry a list of parts; the text ones are
                # what can be asked again.
                if isinstance(text, list):
                    joined = " ".join(
                        str(part.get("text", ""))
                        for part in text
                        if isinstance(part, dict) and part.get("type") == "text"
                    ).strip()
                    if joined:
                        return joined
        return None

    def rewind(self) -> Optional[str]:
        """Drop the last exchange. Returns the prompt that was removed.

        "The last exchange" is the newest user message and everything the
        agent said after it, which is what going back one turn means to the
        person who typed it — the reply, the tool calls and the reasoning all
        belong to the question that prompted them.

        The store is rewound too, best-effort. Leaving it alone would mean
        the conversation the console shows and the conversation the logbook
        replays had diverged, and the divergence would only surface later,
        when the session was reopened somewhere else.
        """
        if self.busy:
            return None
        with self._lock:
            cut = None
            for index in range(len(self._history) - 1, -1, -1):
                if _is_human_turn(self._history[index]):
                    cut = index
                    break
            if cut is None:
                return None
            removed = self._history[cut]
            prompt = removed.get("content") if isinstance(removed, dict) else ""
            self._history = self._history[:cut]
            agent = self._agent
            if agent is not None:
                try:
                    agent.conversation_history = list(self._history)
                except Exception:
                    pass
            self._rewind_store()
            return prompt if isinstance(prompt, str) else ""

    def _rewind_store(self) -> None:
        """Soft-delete the last exchange in the session store, if it is there.

        Every failure mode here is acceptable and none of them is worth a
        message: no store, a session never written, a compression lock held,
        a turn lease from another process. The console's own history is
        already correct, and that is what the next turn is built from.
        """
        session_id = self.session_id
        if not session_id:
            return
        try:
            from curie_state import SessionDB

            db = SessionDB()
            rows = db.get_messages(session_id)
        except Exception:
            return
        target = None
        for row in reversed(rows or []):
            if isinstance(row, dict) and row.get("role") == "user":
                target = row.get("id")
                break
        if target is None:
            return
        try:
            db.rewind_to_message(session_id, int(target))
        except Exception:
            return

    def interrupt(self) -> bool:
        """Ask a running turn to stop. Returns True if there was one."""
        agent = self._agent
        if agent is None or not self.busy:
            return False
        try:
            agent._interrupt_requested = True
        except Exception:
            return False
        self._events.put(TurnEvent("status", "stop requested — unwinding the turn"))
        return True

    # ── Turns ────────────────────────────────────────────────────────────

    def submit(self, message: str) -> bool:
        """Start a turn on a worker thread. False if one is already running."""
        if self.busy:
            return False
        self._reset_counters()
        self._worker = threading.Thread(
            target=self._run_turn,
            args=(message,),
            name="bench-ui-turn",
            daemon=True,
        )
        self._worker.start()
        return True

    def drain(self, limit: int = 512) -> list[TurnEvent]:
        """Take up to ``limit`` pending events.

        Bounded so a burst of deltas cannot starve the repaint that is
        supposed to display them.
        """
        out: list[TurnEvent] = []
        for _ in range(limit):
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def _run_turn(self, message: str) -> None:
        if not self.ensure_agent():
            self._events.put(
                TurnEvent("error", self._load_error or "agent unavailable")
            )
            return
        agent = self._agent

        def on_delta(text: str) -> None:
            if text:
                self._chars_this_turn += len(text)
                self._events.put(TurnEvent("delta", text))

        # The agent's real hook names, taken from its constructor signature —
        # an earlier version invented ``reasoning_delta_callback`` and
        # ``tool_gen_started_callback``, which simply never fired.
        saved = {
            name: getattr(agent, name, None)
            for name in _HOOKS
        }
        try:
            self._wire_hooks(agent)
            agent._interrupt_requested = False
            # The attribute as well as the argument. ``run_conversation``
            # builds the turn from the argument, but compaction does not: it
            # runs *inside* the turn, reads and rewrites the agent's own
            # history, and decides from it whether a compaction is a no-op.
            # An agent whose attribute says the conversation is empty while
            # the argument carries fifty turns is a compaction that keeps
            # being asked for and keeps finding nothing to do — which is a
            # turn that pauses, compacts, and comes back no smaller.
            try:
                agent.conversation_history = list(self._history)
            except Exception:
                pass
            # NOT ``agent.chat()``. That forwards to ``run_conversation``
            # with ``conversation_history=None``, and the turn's message list
            # is built from that argument alone — never from
            # ``agent.conversation_history``. So every turn arrived at the
            # model as a first turn: a resumed conversation was silently
            # dropped, and within one session the model could not see what it
            # had just said. Hand it the history and take the updated one
            # back, exactly as the CLI does.
            result = agent.run_conversation(
                message,
                conversation_history=list(self._history),
                stream_callback=on_delta,
                task_id=self.session_id or None,
            )
            if isinstance(result, dict):
                messages = result.get("messages")
                if isinstance(messages, list) and messages:
                    self._history = list(messages)
                final = result.get("final_response", "") or ""
            else:
                final = str(result or "")
            self._events.put(TurnEvent("done", final))
        except InterruptedError:
            self._adopt_agent_history(agent)
            self._events.put(TurnEvent("status", "turn interrupted"))
            self._events.put(TurnEvent("done", ""))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._adopt_agent_history(agent)
            self._events.put(TurnEvent("error", f"{type(exc).__name__}: {exc}"))
            self._events.put(TurnEvent("done", ""))
        finally:
            self._adopt_rotated_session(agent)
            for name, value in saved.items():
                try:
                    setattr(agent, name, value)
                except Exception:
                    pass

    def _adopt_agent_history(self, agent: Any) -> None:
        """Keep whatever the agent got to before the turn came apart.

        A turn that fails *after* compacting is the case this exists for.
        ``run_conversation`` only returns its message list on the way out
        through the front door; raise, and the bridge is left holding the
        history it went in with — the pre-compaction one. The next turn then
        sends that same oversized conversation, the agent compacts it again,
        and the reader watches the console pause to compact on every single
        message while the conversation never gets any shorter. Compaction is
        working perfectly; it is being thrown away.
        """
        try:
            held = getattr(agent, "conversation_history", None)
        except Exception:
            return
        if isinstance(held, list) and held:
            self._history = list(held)

    def _adopt_rotated_session(self, agent: Any) -> None:
        """Follow the conversation if compaction moved it to a new session.

        Legacy compression does not shrink a session, it *rotates* it: the
        compacted transcript becomes a fresh child session and the agent
        re-points itself at it mid-turn. A bridge still holding the parent id
        writes the next turn's task context against a conversation nothing
        reads back, and the logbook flags the wrong row as the one on the
        bench — the conversation the reader is looking at appears to have
        stopped being recorded.
        """
        try:
            rotated = str(getattr(agent, "session_id", "") or "")
        except Exception:
            return
        if rotated and rotated != self._session_id:
            self._session_id = rotated

    def _wire_hooks(self, agent: Any) -> None:
        """Point every progress hook the agent has at the event queue.

        Six of them, because the console draws six different things and a
        turn that reports only "text arrived" cannot tell it what any of the
        silence in between was for:

        ``reasoning_callback``      scratch work, into the fold
        ``thinking_callback``       why the provider is taking its time
        ``tool_gen_callback``       the model has begun *writing* a call,
                                    while its arguments are still streaming
        ``tool_start_callback``     that same call actually running, with a
                                    stable id and its arguments
        ``tool_complete_callback``  a tool call finishing
        ``status_callback``         lifecycle notices and warnings
        ``_on_session_title``       the session being named — the one write
                                    to the record a reader can see happen
        ``event_callback``          structured events, of which compression
                                    is the one worth a lamp
        """

        def reasoning(text: str = "", *_a, **_k) -> None:
            if text:
                self._reasoning_chars_this_turn += len(str(text))
                self._events.put(TurnEvent("reasoning", str(text)))

        def waiting(text: str = "", *_a, **_k) -> None:
            # Empty is the agent clearing its own status line, not a message.
            if text and str(text).strip():
                self._events.put(TurnEvent("wait", str(text).strip()))

        def tool_generating(name: str = "", *_a, **_k) -> None:
            # Not a call yet — the model is still writing its arguments, and
            # a 45 KB ``write_file`` payload takes long enough that saying
            # nothing reads as a frozen console. It is deliberately *not*
            # counted or recorded: this hook and ``tool_start_callback``
            # below are two moments in the life of one call, not two
            # runtimes' ways of reporting it, so treating both as a call
            # wrote every tool into the fold twice and doubled the count in
            # the heading above it.
            if name:
                self._events.put(TurnEvent("tool_gen", str(name)))

        def tool_started(_call_id=None, name: str = "", *_a, **_k) -> None:
            # ``tool_start_callback`` leads with the call id; the name is
            # second. Reading it positionally the other way round put an
            # opaque identifier in the fold instead of the tool. This is the
            # authoritative one — it fires once per call that actually runs,
            # on every runtime that executes tools.
            if name:
                self._tool_calls_this_turn += 1
                self._events.put(TurnEvent("tool", str(name)))

        def tool_finished(_call_id=None, name: str = "", *_a, **_k) -> None:
            if name:
                self._events.put(TurnEvent("tool_done", str(name)))

        def status(*args, **_k) -> None:
            kind, message = _status_parts(args)
            if not message:
                return
            # Compaction is not a status line, it is a *phase of the turn*.
            # A long pause with a notice that expires after eight seconds is
            # indistinguishable from a hung console, which is what the reader
            # actually reported: the turn stops producing tokens, the state
            # figure sits on whatever it was doing, and nothing on the panel
            # says the agent is busy rewriting the conversation. So the two
            # edges are their own events and the console holds an indicator
            # between them.
            if kind == "compacted":
                self._events.put(TurnEvent("compacted", message))
                return
            # Asked of the kind first and the wording second, and a failure is
            # asked about at all. "Auxiliary compression failed" is a warning
            # that happens to be about compression, and reading it as
            # compaction *progress* would hold the state figure on a pause
            # that is not happening and swallow the one line saying the
            # side path fell over.
            if kind in {"warn", "error"}:
                self._events.put(TurnEvent("warn", message))
                return
            if kind == "compacting" or is_compaction_progress(message):
                self._events.put(TurnEvent("compacting", message))
                return
            self._events.put(TurnEvent("status", message))

        def titled(title: str = "", source: str = "", *_a, **_k) -> None:
            if title:
                self._events.put(
                    TurnEvent("record", f"session named “{title}”" if source != "model"
                              else f"session named “{title}” by the auxiliary model")
                )

        def structured(name: str = "", payload: Any = None, *_a, **_k) -> None:
            if str(name).startswith("session:compress"):
                self._events.put(TurnEvent("record", "conversation compressed"))

        agent.reasoning_callback = reasoning
        agent.thinking_callback = waiting
        agent.tool_gen_callback = tool_generating
        agent.tool_start_callback = tool_started
        agent.tool_complete_callback = tool_finished
        agent.status_callback = status
        agent._on_session_title = titled
        agent.event_callback = structured

    def _reset_counters(self) -> None:
        self._chars_this_turn = 0
        self._reasoning_chars_this_turn = 0
        self._tool_calls_this_turn = 0

    @property
    def chars_this_turn(self) -> int:
        """Characters of *answer* delivered this turn."""
        return self._chars_this_turn

    @property
    def reasoning_chars_this_turn(self) -> int:
        """Characters of reasoning delivered this turn."""
        return self._reasoning_chars_this_turn

    @property
    def tool_calls_this_turn(self) -> int:
        """Tool calls started this turn."""
        return self._tool_calls_this_turn


def _build_agent(session_id: Optional[str] = None) -> Any:
    """Construct a configured agent the way every other surface does.

    Deliberately routes through ``resolve_runtime_provider`` rather than
    reading ``model.default`` out of config directly. That resolver is where
    provider selection, credential lookup, base-URL and api_mode derivation,
    named custom providers and the ``providers.<name>.enabled`` gate all live.
    An earlier version of this function passed a bare ``model``/``provider``
    pair and would have produced an agent that could not authenticate — and,
    because it also invented a ``max_turns`` keyword the constructor does not
    take, one that could not be built at all.

    ``session_db`` is not optional decoration. Without it the agent's
    ``_flush_messages_to_session_db`` has nothing to flush to, so a
    conversation held on the bench was never written anywhere: it vanished
    when the console closed and never appeared in the logbook. Passing the
    store is what makes a bench conversation a conversation.
    """
    from curie_cli.config import load_config, resolve_turn_limit
    from curie_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    config = load_config()
    model_cfg = config.get("model") if isinstance(config, dict) else {}
    model_cfg = model_cfg if isinstance(model_cfg, dict) else {}
    agent_cfg = config.get("agent") if isinstance(config, dict) else {}
    agent_cfg = agent_cfg if isinstance(agent_cfg, dict) else {}

    model = str(
        os.getenv("CURIE_INFERENCE_MODEL", "").strip()
        or model_cfg.get("default")
        or model_cfg.get("model")
        or ""
    ).strip()
    provider = str(model_cfg.get("provider") or "auto").strip() or "auto"

    runtime = resolve_runtime_provider(
        requested=provider,
        target_model=model or None,
    )

    kwargs: dict[str, Any] = {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "api_mode": runtime.get("api_mode"),
        "model": model or runtime.get("model") or "",
        "platform": "cli",
        # The console draws its own transcript; the agent must not also print
        # to stdout, which is the alternate screen the console is occupying.
        "quiet_mode": True,
    }
    if session_id:
        kwargs["session_id"] = session_id
    session_db = _open_session_db()
    if session_db is not None:
        kwargs["session_db"] = session_db
    requested = runtime.get("requested_provider")
    if requested:
        kwargs["requested_provider"] = requested
    pool = runtime.get("credential_pool")
    if pool is not None:
        kwargs["credential_pool"] = pool

    # ``agent.max_turns`` is a turn cap in config; the constructor spells it
    # ``max_iterations``. resolve_turn_limit normalises the documented spellings
    # ("none", "unlimited", 0, -1) to the sentinel the agent expects.
    raw_turns = agent_cfg.get("max_turns")
    if raw_turns is not None:
        try:
            kwargs["max_iterations"] = resolve_turn_limit(raw_turns)
        except Exception:
            pass

    toolsets = _configured_toolsets(config)
    if toolsets:
        kwargs["enabled_toolsets"] = toolsets

    agent = AIAgent(**kwargs)
    # The console renders status itself; leave the agent's own writers unset so
    # nothing escapes onto the alternate screen.
    try:
        agent.suppress_status_output = True
    except Exception:
        pass
    return agent


def _open_session_db() -> Any:
    """The session store, or None when it cannot be opened.

    A console that cannot persist is still a usable console — the turn runs
    and the reply is readable — so a store that will not open degrades to an
    unrecorded session rather than refusing to start.
    """
    try:
        from curie_state import SessionDB

        return SessionDB()
    except Exception:
        return None


def _configured_toolsets(config: Any) -> "list[str] | None":
    """The toolsets this user has enabled for the CLI platform, if any."""
    try:
        from curie_cli.tools_config import _get_platform_tools

        enabled = _get_platform_tools(config, "cli")
    except Exception:
        return None
    return sorted(enabled) if enabled else None


__all__ = ["AgentBridge", "TurnEvent"]
