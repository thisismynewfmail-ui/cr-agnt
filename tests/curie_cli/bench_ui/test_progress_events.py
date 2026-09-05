"""The console can only draw what the bridge is told, so it wires every hook.

A turn spends most of its time doing one of four things — waiting on the
provider, reasoning, running a tool, writing an answer — and from the outside
they are indistinguishable unless each one is reported. These pin the wiring
to the agent's real hook names and argument shapes, which is the part that
silently stops working: a callback with the wrong signature is not an error,
it is an indicator that never moves.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import (  # noqa: E402
    _HOOKS,
    AgentBridge,
    _status_parts,
)
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.instruments import ChartLegend, StripChart  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _Agent:
    """An object with the hook attributes and nothing else."""


def _wired() -> tuple[AgentBridge, _Agent]:
    bridge = AgentBridge()
    agent = _Agent()
    bridge._wire_hooks(agent)
    return bridge, agent


def _kinds(bridge) -> list[tuple[str, str]]:
    return [(event.kind, event.text) for event in bridge.drain()]


# ── The status callback's arguments ──────────────────────────────────────

def test_a_status_callback_carries_a_kind_and_then_the_message():
    """The bug this exists for: the console showed the word "lifecycle".

    ``AIAgent`` calls ``status_callback("lifecycle", message)``. Reading the
    first argument as the text put that literal word on the notice line for
    every status the agent emitted, and threw the message away.
    """
    assert _status_parts(("lifecycle", "switched to the fallback")) == (
        "lifecycle", "switched to the fallback",
    )
    assert _status_parts(("warn", "auxiliary title generation failed")) == (
        "warn", "auxiliary title generation failed",
    )


def test_a_single_argument_status_is_still_the_message():
    """Simpler emitters pass the message alone; it must not be read as a kind."""
    assert _status_parts(("the provider is slow",)) == (
        "lifecycle", "the provider is slow",
    )
    assert _status_parts(()) == ("lifecycle", "")


def test_a_status_reaches_the_console_as_its_message():
    bridge, agent = _wired()
    agent.status_callback("lifecycle", "switched to the fallback")
    assert _kinds(bridge) == [("status", "switched to the fallback")]


def test_a_warning_is_kept_apart_from_an_ordinary_status():
    bridge, agent = _wired()
    agent.status_callback("warn", "auxiliary compression failed")
    assert _kinds(bridge) == [("warn", "auxiliary compression failed")]


# ── Every hook ───────────────────────────────────────────────────────────

def test_every_hook_the_bridge_takes_over_is_restored_after_the_turn():
    """The agent outlives the turn; a stale hook is a leak into a dead queue."""
    bridge = AgentBridge()
    agent = _Agent()
    for name in _HOOKS:
        setattr(agent, name, f"original {name}")
    saved = {name: getattr(agent, name, None) for name in _HOOKS}
    bridge._wire_hooks(agent)
    assert all(getattr(agent, name) != saved[name] for name in _HOOKS)
    for name, value in saved.items():
        setattr(agent, name, value)
    assert {name: getattr(agent, name) for name in _HOOKS} == saved


def test_reasoning_is_counted_and_reported():
    bridge, agent = _wired()
    agent.reasoning_callback("weighing the options. ")
    assert _kinds(bridge) == [("reasoning", "weighing the options. ")]
    assert bridge.reasoning_chars_this_turn == len("weighing the options. ")


def test_a_wait_notice_is_its_own_kind_and_an_empty_one_is_dropped():
    """An empty thinking callback is the agent clearing its status line."""
    bridge, agent = _wired()
    agent.thinking_callback("waiting on the provider — no first byte yet")
    agent.thinking_callback("")
    assert _kinds(bridge) == [
        ("wait", "waiting on the provider — no first byte yet")
    ]


def test_a_tool_start_reports_the_tool_and_not_the_call_id():
    """``tool_start_callback`` leads with the call id; the name is second."""
    bridge, agent = _wired()
    agent.tool_start_callback("call_abc123", "run_command", {"cmd": "git log"})
    assert _kinds(bridge) == [("tool", "run_command")]
    assert bridge.tool_calls_this_turn == 1


def test_the_generating_hook_is_a_call_being_written_not_a_call():
    """``tool_gen_callback`` fires while the arguments are still streaming.

    It is worth reporting — composing a 45 KB ``write_file`` payload is a
    long silence — but it is not a call yet, so it gets its own kind rather
    than being counted.
    """
    bridge, agent = _wired()
    agent.tool_gen_callback("read_file")
    assert _kinds(bridge) == [("tool_gen", "read_file")]
    assert bridge.tool_calls_this_turn == 0


def test_one_call_is_reported_once_even_though_two_hooks_see_it():
    """This is the bug behind every tool appearing twice in the fold.

    ``tool_gen_callback`` and ``tool_start_callback`` are two moments in the
    life of one call — the model writing it, then the executor running it —
    not two runtimes' ways of reporting it. Treating both as a call wrote
    every tool into the workings twice and doubled the count in the heading,
    so a turn that made four calls claimed eight.
    """
    bridge, agent = _wired()
    agent.tool_gen_callback("terminal")
    agent.tool_start_callback("call_1", "terminal", {"cmd": "ls"})
    agent.tool_complete_callback("call_1", "terminal", {}, "…")

    events = _kinds(bridge)
    assert [kind for kind, _ in events] == ["tool_gen", "tool", "tool_done"]
    assert bridge.tool_calls_this_turn == 1, (
        f"one call was counted {bridge.tool_calls_this_turn} times"
    )


def test_a_finished_tool_is_reported_so_the_state_can_go_back():
    bridge, agent = _wired()
    agent.tool_complete_callback("call_abc123", "run_command", {}, "…")
    assert _kinds(bridge) == [("tool_done", "run_command")]


def test_a_session_being_named_is_a_write_to_the_record():
    """The auxiliary title generation, which is otherwise invisible."""
    bridge, agent = _wired()
    agent._on_session_title("rewrapping the transcript", "derived")
    kinds = _kinds(bridge)
    assert kinds[0][0] == "record"
    assert "rewrapping the transcript" in kinds[0][1]


def test_a_model_written_title_says_so():
    bridge, agent = _wired()
    agent._on_session_title("rewrapping the transcript", "model")
    assert "auxiliary model" in _kinds(bridge)[0][1]


def test_a_compression_is_a_write_to_the_record_too():
    bridge, agent = _wired()
    agent.event_callback("session:compress", {"turns": 40})
    assert _kinds(bridge) == [("record", "conversation compressed")]


def test_an_unrelated_structured_event_is_ignored():
    bridge, agent = _wired()
    agent.event_callback("session:something-else", {})
    assert _kinds(bridge) == []


def test_the_counters_reset_between_turns():
    bridge, agent = _wired()
    agent.reasoning_callback("some thinking")
    agent.tool_start_callback("call_1", "read_file", {})
    assert bridge.reasoning_chars_this_turn and bridge.tool_calls_this_turn
    bridge._reset_counters()
    assert bridge.reasoning_chars_this_turn == 0
    assert bridge.tool_calls_this_turn == 0


# ── The output recorder ──────────────────────────────────────────────────

def test_the_recorder_keeps_the_three_channels_apart():
    chart = StripChart("chars/s")
    chart.push(100, channel="text")
    chart.push(80, channel="think")
    chart.push(40, channel="tool")
    assert chart.channels_seen() == ("text", "think", "tool")


def test_an_unknown_channel_lands_on_the_default_rather_than_being_lost():
    chart = StripChart("chars/s")
    chart.push(10, channel="nonsense")
    assert chart.channels_seen() == ("text",)


def test_the_channels_share_one_scale():
    """Two bursts of the same size must draw the same height."""
    chart = StripChart("chars/s")
    chart.push(100, channel="think")
    peak = chart.peak
    chart.push(100, channel="text")
    assert chart.peak == peak, "a second channel rescaled the chart under the first"


def test_each_channel_has_its_own_glyph_for_a_monochrome_terminal():
    glyphs = {glyph for _role, glyph in StripChart.CHANNELS.values()}
    assert len(glyphs) == len(StripChart.CHANNELS), (
        "two channels are told apart by colour alone"
    )


def test_the_legend_shows_a_swatch_for_every_channel():
    """Swatches, not words — the chart above already draws these marks.

    Each channel still has to be *there*, and in its own glyph, or a
    monochrome terminal loses the distinction the recorder exists to make.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            legend = str(app.query_one("#chart-legend", ChartLegend).render())
            for channel in ChartLegend.CHANNELS:
                _role, glyph = StripChart.CHANNELS[channel]
                assert glyph in legend, f"{channel} has no swatch in the legend"
            # The words are gone; only the marks remain.
            assert not any(c.isalpha() for c in legend), (
                f"the legend still carries wording: {legend!r}"
            )
    _run(scenario())


def test_reasoning_and_answer_text_reach_different_channels():
    """The recorder's whole job: say what the last twenty seconds went into."""
    class _Counting(_StubBridge):
        def __init__(self):
            super().__init__()
            self.reasoning = 0
            self.answer = 0

        def submit(self, message):
            self.submitted.append(message)
            return True

        @property
        def reasoning_chars_this_turn(self):
            return self.reasoning

        @property
        def chars_this_turn(self):
            return self.answer

    async def scenario():
        bridge = _Counting()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chart = app.query_one("#chart-output", StripChart)
            chart.clear()

            bridge.reasoning = 400
            app._push_output_sample()
            bridge.answer = 900
            app._push_output_sample()

            assert set(chart.channels_seen()) >= {"think", "text"}, (
                f"channels: {chart.channels_seen()}"
            )
    _run(scenario())


def test_a_tool_call_marks_the_trace_without_resetting_its_scale():
    class _Tools(_StubBridge):
        def __init__(self):
            super().__init__()
            self.tools = 0

        @property
        def tool_calls_this_turn(self):
            return self.tools

    async def scenario():
        bridge = _Tools()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            chart = app.query_one("#chart-output", StripChart)
            chart.clear()
            chart.push(1000, channel="text")
            app._last_chars = 0
            peak = chart.peak

            bridge.tools = 1
            app._push_output_sample()
            assert "tool" in chart.channels_seen()
            assert chart.peak == peak, "a tool call rescaled the recorder"
    _run(scenario())


# ── The two tool hooks are two moments, not two dialects ─────────────────

def _agent_source(name: str) -> str:
    """One of the agent's modules, read off disk.

    Read rather than imported: these modules pull in provider SDKs that the
    ``ui`` extra does not install, and this is a question about what the code
    says, not about what it does.
    """
    root = pathlib.Path(__file__).resolve().parents[3]
    return (root / "agent" / name).read_text(encoding="utf-8")


def test_the_agent_fires_both_tool_hooks_for_a_single_call():
    """The claim the bridge's de-duplication rests on, checked at the source.

    ``tool_gen_callback`` fires from the streaming reader while the model is
    still emitting the call's arguments; ``tool_start_callback`` fires from
    the tool executor when that same call runs. Any streaming provider goes
    through both. If this ever stopped being true — if one of them became
    the only hook a runtime fires — the console would start missing calls
    rather than double-counting them, so it is worth pinning where it can
    be read.
    """
    streaming = _agent_source("chat_completion_helpers.py")
    assert "_fire_tool_gen_started" in streaming, (
        "the streaming reader no longer announces a call being written"
    )
    executing = _agent_source("tool_executor.py")
    assert "agent.tool_start_callback(" in executing, (
        "the tool executor no longer announces a call running"
    )
    # And the executor is the one with the call id, which is what makes it
    # the authority on how many calls there were.
    assert "tool_call_id, function_name" in executing


def test_a_blocked_call_never_reports_finishing():
    """Why the console cannot treat ``tool_done`` as guaranteed.

    The executor skips ``tool_complete_callback`` for a call it blocked. A
    console that waited for one before leaving the tool state would sit on
    TOOL, naming a tool that had already been refused, for the rest of the
    turn.
    """
    source = _agent_source("tool_executor.py")
    assert "blocked and agent.tool_complete_callback" in source, (
        "the executor's completion hook is no longer gated on the call "
        "having been allowed — the console's recovery may be unnecessary"
    )
