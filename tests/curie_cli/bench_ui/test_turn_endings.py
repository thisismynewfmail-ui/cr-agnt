"""A turn on the bench ends where the agent ends it, and says why.

Reported: in UI mode the model's response "stops at random times, usually
directly after a tool call or compaction", and the console looks stuck. Each
cause below was a way the console either hid why a turn ended, kept saying it
was compacting when it was not, or ran the model with less of the reader's
configuration than ``curie chat`` does.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from agent.conversation_compression import (  # noqa: E402
    COMPACTION_DONE_STATUS,
    COMPACTION_STATUS,
    PREFLIGHT_COMPRESSION_STATUS_TEMPLATE,
    is_compaction_progress_status,
)
from curie_cli.bench_ui.agent_bridge import (  # noqa: E402
    AgentBridge,
    TurnEvent,
    _configured_behaviour,
    _turn_fault,
)
from curie_cli.bench_ui.app import (  # noqa: E402
    BenchConsole,
    _not_yet_shown,
    _plain_status,
)
from curie_cli.bench_ui.indicators import SAVING  # noqa: E402

from tests.curie_cli.bench_ui.test_compaction import _OpenTurn, _settle, _wire  # noqa: E402
from tests.curie_cli.bench_ui.test_console import _transcript_text  # noqa: E402

#: The advisory the agent emits after a second compaction, and replays at the
#: start of the next turn.
ADVISORY = (
    "⚠️  Session compressed 2 times — accuracy may degrade. "
    "Consider /new to start fresh."
)


def _run(coro):
    return asyncio.run(coro)


# ── "Compacting" that was not compacting ─────────────────────────────────


def test_the_repeated_compression_advisory_is_not_compaction_progress():
    """Replayed at the next turn's start, it opened a hold nothing closed."""
    assert is_compaction_progress_status(ADVISORY) is False
    # The real progress lines still are.
    assert is_compaction_progress_status(COMPACTION_STATUS) is True
    assert is_compaction_progress_status(
        PREFLIGHT_COMPRESSION_STATUS_TEMPLATE.format(tokens=1, threshold=1)
    ) is True


def test_the_bridge_reports_the_advisory_as_a_notice_not_a_compaction():
    bridge = AgentBridge()
    agent = _wire(bridge)
    agent.status_callback("lifecycle", ADVISORY)
    assert [e.kind for e in bridge.drain()] == ["status"]


def test_output_ends_a_compaction_hold_its_done_edge_never_closed():
    """A turn that is producing tokens is not compacting.

    Compaction runs on the agent's thread between two model calls, so a
    thought, a tool call or an answer arriving means the pause is over. A
    hold left on past that point is a console reporting RECORDING over a
    model that is visibly working — and it stopped the next compaction from
    being written into the transcript at all.
    """

    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("carry on")
            await _settle(pilot)
            app._compaction_started(app._bench(), COMPACTION_STATUS)
            assert app._compacting is True
            app.bridge._events.put(TurnEvent("reasoning", "next step"))
            app._pump_agent()
            await _settle(pilot)
            assert app._compacting is False
            assert app.query_one("#activity")._state != SAVING
            # And the next real compaction is written in its own right.
            app._compaction_started(app._bench(), COMPACTION_STATUS)
            app._compaction_finished(app._bench(), COMPACTION_DONE_STATUS)
            await _settle(pilot)
            assert _transcript_text(app).count("Compacting context") == 2

    _run(scenario())


def test_a_finishing_tool_does_not_end_the_hold():
    """``tool_done`` is the tail of work already under way, not new output."""

    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("carry on")
            await _settle(pilot)
            app._compaction_started(app._bench(), COMPACTION_STATUS)
            app.bridge._events.put(TurnEvent("tool_done", "read_file"))
            app._pump_agent()
            await _settle(pilot)
            assert app._compacting is True

    _run(scenario())


# ── No emoji in the chat ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line,kept",
    [
        ("📦 Pre-API compression: ~61,937 tokens near the limit.", "Pre-API compression: ~61,937 tokens near the limit."),
        (COMPACTION_STATUS, "Compacting context — summarizing earlier conversation so I can continue..."),
        (COMPACTION_DONE_STATUS, "Context compaction complete — continuing turn..."),
        (ADVISORY, "Session compressed 2 times — accuracy may degrade. Consider /new to start fresh."),
        ("↻ Stream interrupted — using delivered content", "Stream interrupted — using delivered content"),
        # An arrow inside the sentence is a word, not a mark.
        ("🗜️ Compressed 18 → 14 messages, retrying...", "Compressed 18 → 14 messages, retrying..."),
    ],
)
def test_status_lines_lose_their_emoji_and_keep_their_words(line, kept):
    assert _plain_status(line) == kept


def test_compaction_lines_in_the_transcript_carry_no_emoji():
    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("go")
            await _settle(pilot)
            agent = _wire(app.bridge)
            agent.status_callback("lifecycle", COMPACTION_STATUS)
            agent.status_callback("compacted", COMPACTION_DONE_STATUS)
            app._pump_agent()
            await _settle(pilot)
            shown = _transcript_text(app)
            assert "Compacting context" in shown
            assert "Context compaction complete" in shown
            for pictogram in ("🗜", "✓", "📦", "⚠", "️"):
                assert pictogram not in shown, pictogram

    _run(scenario())


# ── Saying why a turn ended ──────────────────────────────────────────────


def test_a_failed_turn_is_a_fault_and_an_interrupted_one_is_not():
    assert _turn_fault({"failed": True, "error": "Context length exceeded."}) == (
        "Context length exceeded."
    )
    assert _turn_fault({"failed": True, "final_response": "No answer."}) == "No answer."
    assert _turn_fault({"failed": True, "interrupted": True, "error": "x"}) == ""
    assert _turn_fault({"final_response": "fine"}) == ""
    assert _turn_fault("not a dict") == ""


@pytest.mark.parametrize(
    "final,streamed,expected",
    [
        ("Done.", "\n\nDone.\n", False),
        ("Done.", "Let me look.  Done.", False),
        ("", "anything", False),
        ("Context length exceeded.", "Let me look at the file.", True),
    ],
)
def test_a_final_line_is_shown_only_when_the_stream_did_not_say_it(final, streamed, expected):
    assert _not_yet_shown(final, streamed) is expected


class _EndsAfterStreaming(AgentBridge):
    """A turn that streams a sentence, then fails with a reason of its own."""

    def ensure_agent(self) -> bool:
        self._agent = object()
        return True

    def describe(self) -> dict:
        return {"model": "m", "provider": "p", "context_length": 1000}

    def submit(self, message: str) -> bool:
        self._events.put(TurnEvent("delta", "Let me check the server's list_dir."))
        self._events.put(TurnEvent("error", "Context length exceeded: compaction could not fit it."))
        self._events.put(TurnEvent("done", "The model produced only reasoning."))
        return True


def test_why_a_turn_stopped_is_written_into_the_transcript():
    """It used to end on its last streamed sentence, with nothing saying why."""

    async def scenario():
        app = BenchConsole(bridge=_EndsAfterStreaming())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("go")
            await _settle(pilot)
            app._pump_agent()
            await _settle(pilot)
            shown = _transcript_text(app)
            assert "Let me check the server's list_dir." in shown
            assert "compaction could not fit it" in shown
            assert "The model produced only reasoning." in shown

    _run(scenario())


# ── The same agent as everywhere else ────────────────────────────────────


def test_the_console_builds_its_agent_with_the_readers_configuration():
    """Fallback chain, reasoning, routing, standing prompt — as the TUI does.

    Without the fallback chain, a provider error ``curie chat`` fails over
    from ended the console's turn instead.
    """
    import inspect

    from run_agent import AIAgent

    config = {
        "agent": {"reasoning_effort": "high", "service_tier": "fast",
                  "system_prompt": "Answer in French."},
        "provider_routing": {"only": ["anthropic"], "require_parameters": True},
        "fallback_providers": [{"provider": "openrouter", "model": "a/b"}],
        "checkpoints": {"enabled": True, "max_snapshots": 7},
    }
    built = _configured_behaviour(config, "model-x", {"command": "copilot", "args": ["--acp"]})
    assert built["fallback_model"][0]["model"] == "a/b"
    assert built["reasoning_config"]["effort"] == "high"
    assert built["service_tier"] == "priority"
    assert built["providers_allowed"] == ["anthropic"]
    assert built["provider_require_parameters"] is True
    assert "Answer in French." in built["ephemeral_system_prompt"]
    assert built["acp_command"] == "copilot" and built["acp_args"] == ["--acp"]
    assert built["checkpoints_enabled"] is True
    # Every one of them is a keyword the agent actually takes.
    accepted = set(inspect.signature(AIAgent.__init__).parameters)
    assert set(built) <= accepted
    # And an empty config adds nothing.
    assert _configured_behaviour({}, "m", {}) == {}


# ── A reply the agent set aside ──────────────────────────────────────────


def test_a_reply_set_aside_for_a_truncated_request_is_said_in_the_transcript():
    """Its reasoning has already streamed — "the user's message is empty" —
    so the line saying it was set aside belongs beside it, not only on a
    notice that is gone in eight seconds."""
    from agent.prompt_truncation import SET_ASIDE_NOTICE

    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._send("carry on")
            await _settle(pilot)
            app.bridge._events.put(TurnEvent("reasoning", "The user's message is empty."))
            app.bridge._events.put(TurnEvent(
                "warn", f"{SET_ASIDE_NOTICE}: it read 30,000 of the ~60,000 tokens sent."))
            app.bridge._events.put(TurnEvent("warn", "Memory flush failed."))
            app._pump_agent()
            await _settle(pilot)
            text = _transcript_text(app)
            assert SET_ASIDE_NOTICE in text
            assert "Memory flush failed." not in text

    _run(scenario())
