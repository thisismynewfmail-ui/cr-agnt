"""Compaction has to be visible, and the turn has to carry on after it.

Auto-compaction is the longest silence a turn can contain: a second model call
over the whole conversation, producing no tokens while it runs. Everything in
here is about the difference between that and a hung console, which from the
outside look identical unless the console says which one it is.

The reported fault was "compaction seems not to happen, or once it does the
agent doesn't continue", and it had three separate causes, each tested below:

* the agent's completion message was **swallowed** — the console read the
  ``compacted`` kind as the message and printed the bare word;
* the state instrument was **never held** on compaction, so any other event
  arriving mid-pause took the figure back and the reader saw a turn sitting on
  whatever it had been doing;
* a turn that failed *after* compacting **threw the compacted history away**,
  so the next turn re-sent the oversized conversation and compacted again —
  compaction working perfectly, forever, with nothing to show for it.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from agent.conversation_compression import (  # noqa: E402
    COMPACTION_DONE_STATUS,
    COMPACTION_STATUS,
    PREFLIGHT_COMPRESSION_STATUS_TEMPLATE,
)
from curie_cli.bench_ui.agent_bridge import (  # noqa: E402
    AgentBridge,
    TurnEvent,
    _status_parts,
)
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.indicators import SAVING, WAITING  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_text,
)


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


class _OpenTurn(_StubBridge):
    """A bridge whose turn stays open, the way a compacting one does."""

    def submit(self, message: str) -> bool:
        self.submitted.append(message)
        return True

    @property
    def busy(self) -> bool:
        return True


class _Agent:
    """Something a bridge can hang its hooks on."""


# ── The swallowed message ────────────────────────────────────────────────


def test_the_completion_message_is_not_eaten_by_its_own_kind():
    """``status_callback("compacted", msg)`` must arrive as ``(kind, msg)``.

    Left out of the known-kinds set, the two-argument call fell through to the
    single-argument branch, which reads ``args[0]`` as the text — so the
    console printed the bare word "compacted" and dropped the sentence saying
    compaction had finished.
    """
    assert _status_parts(("compacted", COMPACTION_DONE_STATUS)) == (
        "compacted",
        COMPACTION_DONE_STATUS,
    )


def test_a_kind_this_version_has_never_heard_of_still_keeps_its_message():
    """So the next kind the agent gains cannot repeat the same fault."""
    kind, message = _status_parts(("somenewkind", "the sentence that matters"))
    assert kind == "somenewkind"
    assert message == "the sentence that matters"


def test_a_one_argument_status_is_still_the_message():
    """Simpler emitters call the hook with the message alone."""
    assert _status_parts(("just a sentence, with spaces",)) == (
        "lifecycle",
        "just a sentence, with spaces",
    )


def test_a_message_that_looks_like_nothing_but_a_word_is_still_a_message():
    kind, message = _status_parts(("📦 Pre-API compression: ~1 tokens",))
    assert kind == "lifecycle"
    assert message.startswith("📦")


# ── The two edges ────────────────────────────────────────────────────────


def _wire(bridge: AgentBridge) -> _Agent:
    agent = _Agent()
    bridge._wire_hooks(agent)
    return agent


@pytest.mark.parametrize(
    "line",
    [
        COMPACTION_STATUS,
        PREFLIGHT_COMPRESSION_STATUS_TEMPLATE.format(tokens=120000, threshold=100000),
    ],
)
def test_progress_lines_become_a_compaction_event(line):
    bridge = AgentBridge()
    agent = _wire(bridge)
    agent.status_callback("lifecycle", line)
    assert [e.kind for e in bridge.drain()] == ["compacting"]


def test_the_done_edge_becomes_its_own_event():
    bridge = AgentBridge()
    agent = _wire(bridge)
    agent.status_callback("compacted", COMPACTION_DONE_STATUS)
    events = bridge.drain()
    assert [e.kind for e in events] == ["compacted"]
    assert events[0].text == COMPACTION_DONE_STATUS


def test_an_ordinary_status_is_still_an_ordinary_status():
    bridge = AgentBridge()
    agent = _wire(bridge)
    agent.status_callback("lifecycle", "Resolving the provider…")
    assert [e.kind for e in bridge.drain()] == ["status"]


# ── What the console does with them ──────────────────────────────────────


def test_the_console_holds_the_instrument_for_the_whole_pause():
    """A pause nothing explains is the fault. It has to be held, not pulsed."""

    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("summarise everything so far")
            await _settle(pilot)
            assert app._activity == WAITING

            agent = _wire(app.bridge)
            agent.status_callback("lifecycle", COMPACTION_STATUS)
            app._pump_agent()
            await _settle(pilot)

            monitor = app.query_one("#activity")
            assert app._compacting is True
            assert monitor._state == SAVING
            assert monitor._detail == "compacting"

            # A tool finishing mid-pause must not take the figure back.
            app.bridge._events.put(TurnEvent("tool_done", "read_file"))
            app._pump_agent()
            await _settle(pilot)
            assert monitor._state == SAVING, (
                "an unrelated event took the instrument off compaction"
            )

            agent.status_callback("compacted", COMPACTION_DONE_STATUS)
            app._pump_agent()
            await _settle(pilot)
            assert app._compacting is False
            assert monitor._state != SAVING

    _run(scenario())


def test_both_edges_are_written_where_they_survive():
    """A notice expires after eight seconds; a compaction can take longer."""

    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("summarise everything so far")
            await _settle(pilot)
            agent = _wire(app.bridge)
            agent.status_callback("lifecycle", COMPACTION_STATUS)
            agent.status_callback("compacted", COMPACTION_DONE_STATUS)
            app._pump_agent()
            await _settle(pilot)

            shown = _transcript_text(app)
            assert "Compacting context" in shown
            assert "compaction complete" in shown.lower()

    _run(scenario())


def test_several_progress_lines_are_one_compaction_in_the_transcript():
    """The agent emits several; they are one event to the person reading."""

    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("go")
            await _settle(pilot)
            agent = _wire(app.bridge)
            for _ in range(3):
                agent.status_callback("lifecycle", COMPACTION_STATUS)
            app._pump_agent()
            await _settle(pilot)
            shown = _transcript_text(app)
            assert shown.count("Compacting context") == 1

    _run(scenario())


def test_a_turn_that_ends_mid_compaction_does_not_leave_the_hold_on():
    """Otherwise the instrument sits on RECORDING with no turn behind it."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._compacting = True
            app._compaction_detail = "compacting"
            app._finish_turn()
            await _settle(pilot)
            assert app._compacting is False

    _run(scenario())


# ── Not losing the compacted conversation ────────────────────────────────


class _FailsAfterCompacting:
    """An agent that compacts its history and then dies partway through.

    The real case is a provider error or an interrupt landing after a
    compaction has already rewritten the conversation — the compaction is
    real and durable, and the turn never returns to hand it back.
    """

    session_id = "s1"
    conversation_history: list = []

    def __init__(self):
        self.conversation_history = []

    def run_conversation(self, message, conversation_history=None, **kwargs):
        # Compaction, as the loop does it: the long history becomes a short one.
        self.conversation_history = [
            {"role": "user", "content": "[summary of everything before]"},
            {"role": "user", "content": message},
        ]
        raise RuntimeError("the provider dropped the connection")


def test_a_turn_that_fails_after_compacting_keeps_the_compacted_history():
    """Otherwise the next turn re-sends the old conversation and compacts again.

    That is the loop the reader sees as "compaction happens and nothing gets
    better": it is working every time, and being thrown away every time.
    """
    bridge = AgentBridge()
    bridge._agent = _FailsAfterCompacting()
    bridge._history = [{"role": "user", "content": f"turn {i}"} for i in range(50)]
    bridge.ensure_agent = lambda: True  # type: ignore[method-assign]

    bridge._run_turn("and now summarise it")

    assert len(bridge._history) == 2, (
        f"the pre-compaction history came back: {len(bridge._history)} messages"
    )
    assert bridge._history[0]["content"].startswith("[summary")


class _RotatesTheSession:
    """An agent whose compaction moves the conversation to a child session."""

    def __init__(self):
        self.session_id = "root"
        self.conversation_history: list = []

    def run_conversation(self, message, conversation_history=None, **kwargs):
        # Legacy compression rotates rather than shrinks: the compacted
        # transcript becomes a fresh child session, mid-turn.
        self.session_id = "root_c1"
        return {"final_response": "done", "messages": [{"role": "user", "content": message}]}


def test_the_bridge_follows_a_session_compaction_rotated():
    """A bridge left on the parent id writes the next turn where nothing reads."""
    bridge = AgentBridge(session_id="root")
    bridge._agent = _RotatesTheSession()
    bridge.ensure_agent = lambda: True  # type: ignore[method-assign]

    bridge._run_turn("hello")

    assert bridge.session_id == "root_c1"
    assert bridge._session_id == "root_c1"


class _ReadsTheAttribute:
    """An agent that decides from ``conversation_history``, as compaction does."""

    session_id = "s1"

    def __init__(self):
        self.conversation_history: list = []
        self.seen_at_entry: list | None = None

    def run_conversation(self, message, conversation_history=None, **kwargs):
        self.seen_at_entry = list(self.conversation_history)
        return {"final_response": "ok", "messages": list(conversation_history or [])}


def test_the_agent_attribute_carries_the_history_too():
    """Compaction reads the attribute, not the argument, when deciding.

    An agent whose attribute says the conversation is empty while the argument
    carries fifty turns is a compaction that keeps being asked for and keeps
    finding nothing to do.
    """
    bridge = AgentBridge()
    agent = _ReadsTheAttribute()
    bridge._agent = agent
    bridge._history = [{"role": "user", "content": f"turn {i}"} for i in range(5)]
    bridge.ensure_agent = lambda: True  # type: ignore[method-assign]

    bridge._run_turn("next")

    assert agent.seen_at_entry is not None
    assert len(agent.seen_at_entry) == 5
