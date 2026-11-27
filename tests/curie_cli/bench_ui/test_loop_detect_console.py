"""Loop Detect on the bench: the PANEL block, the stop, and the resume.

Asked for: a LOOP DETECT block on the PANEL with an on/off switch and the
settings that matter — REPEATS above all, three by default. When a passage
comes round that many times the console stops the turn exactly as Ctrl+C
would, waits for the model to have fully stopped, and sends "Loop detected,
break the loop and continue exactly as you were" so the work carries on. Off,
the console must behave as though the feature did not exist.

The turns here run through the real :class:`AgentBridge` — its worker
thread, its hooks, its interrupt and its ``done`` edge — against a fake agent
that loops in its reasoning until it is interrupted, the way the reported
model did.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import AgentBridge, TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.loop_detect import DEFAULT_MESSAGE  # noqa: E402
from curie_cli.bench_ui.panes import PanelPane, ToggleSwitch  # noqa: E402
from curie_cli.bench_ui.settings import read_settings, write_setting  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402

PARAGRAPH = (
    "Let me verify the geometry one more time: the test walks to the enemy flag "
    "at (62, 0) for the blue player, which is the red flag's pad."
)
ANSWER = "Carrying on with the task from where I was."
INTERRUPTED = "Operation interrupted: waiting for model response (2.0s elapsed)."


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


async def _until(pilot, condition, timeout: float = 15.0) -> None:
    """Let the console run — its timers on the real clock — until ``condition``."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the console")
        await pilot.pause(0.05)


class _LoopingAgent:
    """A model that loops in its reasoning until someone stops it.

    ``plan`` says, call by call, whether that turn loops. A looping turn
    writes :data:`PARAGRAPH` over and over and checks the interrupt between
    paragraphs, as the real agent checks it between streamed chunks; left
    alone it gives up after ``cap`` paragraphs.
    """

    def __init__(self, plan=(True, False), cap: int = 40) -> None:
        self.plan = list(plan)
        self.cap = cap
        self._interrupt_requested = False
        self.session_id = "loop-test"
        self.conversation_history: list = []
        self.calls: list[str] = []
        self.interrupted: list[bool] = []
        self.paragraphs: list[int] = []
        self.histories: list[list] = []
        self.alive_at_call: list[int] = []
        self.reasoning_callback = None

    def run_conversation(self, user_message, conversation_history=None,
                         stream_callback=None, task_id=None, **_kw):
        self.calls.append(user_message)
        self.histories.append(list(conversation_history or []))
        self.alive_at_call.append(
            sum(1 for t in threading.enumerate() if t.name == "bench-ui-turn")
        )
        history = list(conversation_history or []) + [
            {"role": "user", "content": user_message}
        ]
        index = len(self.calls) - 1
        loops = self.plan[index] if index < len(self.plan) else False
        if not loops:
            stream_callback(ANSWER)
            self.interrupted.append(False)
            self.paragraphs.append(0)
            return {
                "final_response": ANSWER,
                "messages": history + [{"role": "assistant", "content": ANSWER}],
            }
        for count in range(self.cap):
            if self._interrupt_requested:
                self.interrupted.append(True)
                self.paragraphs.append(count)
                return {
                    "final_response": INTERRUPTED,
                    "messages": history,
                    "interrupted": True,
                }
            self.reasoning_callback(PARAGRAPH + "\n\n")
            time.sleep(0.02)
        self.interrupted.append(False)
        self.paragraphs.append(self.cap)
        return {
            "final_response": "gave up",
            "messages": history + [{"role": "assistant", "content": "gave up"}],
        }


class _LoopBridge(AgentBridge):
    """The real bridge, holding the fake agent instead of building one."""

    def __init__(self, agent: _LoopingAgent) -> None:
        super().__init__()
        self.fake = agent

    def ensure_agent(self) -> bool:
        self._agent = self.fake
        return True

    def describe(self) -> dict:
        return {"model": "test/model", "provider": "test", "context_length": 200_000}


def _entries(app, kind=None):
    pane = app._bench()
    return [(k, t) for k, t, _w in pane._entries if kind is None or k == kind]


def _configure(**values) -> None:
    for key, value in values.items():
        assert write_setting(f"ui.loop_detect.{key}", value) == ""


# ── The PANEL block ──────────────────────────────────────────────────────


def test_the_panel_has_a_loop_detect_switch_on_by_default_and_it_saves():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app.show_pane("panel")
            await pilot.pause()
            switch = app.query_one("#switch-loop-detect", ToggleSwitch)
            assert switch.is_on is True
            switch.scroll_visible(animate=False)
            await _settle(pilot)
            await pilot.click("#switch-loop-detect")
            await _settle(pilot)
            assert switch.is_on is False
            assert app._loop_settings.enabled is False
            assert read_settings().loop_detect.enabled is False
            # The notice line opening moves the pane; click where it is now.
            switch.scroll_visible(animate=False)
            await _settle(pilot)
            await pilot.click("#switch-loop-detect")
            await _settle(pilot)
            assert switch.is_on is True
            assert read_settings().loop_detect.enabled is True

    _run(scenario())


def test_the_panel_opens_where_config_left_it():
    async def scenario():
        _configure(enabled=False, repeats=5)
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            assert app.query_one("#switch-loop-detect", ToggleSwitch).is_on is False
            readout = str(app.query_one("#loop-repeats-readout").render()).strip()
            assert readout.startswith("5 ·")
            assert str(app.query_one("#loop-note").render()).strip().startswith("Off")

    _run(scenario())


def test_the_dials_step_save_and_say_what_they_are_set_to():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app.show_pane("panel")
            await pilot.pause()
            readout = app.query_one("#loop-repeats-readout")
            assert str(readout.render()).strip().startswith("3 ·")
            app.run_keyline_action("loop-repeats-up")
            await pilot.pause()
            assert app._loop_settings.repeats == 4
            assert read_settings().loop_detect.repeats == 4
            assert str(readout.render()).strip().startswith("4 ·")
            app.run_keyline_action("loop-words-down")
            app.run_keyline_action("loop-resumes-down")
            await pilot.pause()
            saved = read_settings().loop_detect
            assert saved.min_words == 7 and saved.resumes == 2
            # The detector itself follows the dials.
            assert app._loop_watch.thinking.repeats == 4
            assert app._loop_watch.answer.min_words == 7

    _run(scenario())


def test_a_dial_stops_at_the_end_of_its_range():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            for _ in range(20):
                app.run_keyline_action("loop-repeats-down")
            await pilot.pause()
            assert app._loop_settings.repeats == 2
            assert "lowest" in str(app.query_one("#notice").render())

    _run(scenario())


def test_a_change_made_in_another_window_is_adopted():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app.show_pane("panel")
            await pilot.pause()
            _configure(enabled=False, repeats=5)
            app._adopt_external_settings()
            await pilot.pause()
            assert app._loop_settings.enabled is False
            assert app._loop_settings.repeats == 5
            assert app.query_one("#switch-loop-detect", ToggleSwitch).is_on is False

    _run(scenario())


# ── Stop, wait, resume ───────────────────────────────────────────────────


def test_a_loop_is_stopped_like_ctrl_c_and_the_model_is_sent_on():
    async def scenario():
        agent = _LoopingAgent(plan=(True, False))
        app = BenchConsole(bridge=_LoopBridge(agent))
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            await _until(pilot, lambda: len(agent.calls) == 2 and not app.bridge.busy
                         and app._activity == "ready")
            # Stopped by the interrupt Ctrl+C uses, on the third paragraph —
            # not left to run to the model's own cap.
            assert agent.interrupted[0] is True
            assert agent.paragraphs[0] < agent.cap
            # The resume went out once the stopped turn was over: its worker
            # had finished, so the second turn's thread was the only one.
            assert agent.calls == ["fix the flag pickup", DEFAULT_MESSAGE]
            assert agent.alive_at_call[1] == 1
            # ...and built on the conversation as the stopped turn left it.
            assert agent.histories[1] == [
                {"role": "user", "content": "fix the flag pickup"}
            ]
            kinds = [k for k, _t in _entries(app)]
            assert kinds == ["user", "fold", "loop", "auto", "reply"]
            loop_text = _entries(app, "loop")[0][1]
            assert "came round 3 times in the thinking" in loop_text
            assert PARAGRAPH[:40] in loop_text
            assert _entries(app, "auto") == [("auto", DEFAULT_MESSAGE)]
            assert _entries(app, "reply") == [("reply", ANSWER)]
            # The agent's "interrupted" line is not passed off as a reply.
            assert all(INTERRUPTED not in t for _k, t in _entries(app))
            assert app._loop_resume == "" and app._loop_stopping is None

    _run(scenario())


def test_off_the_console_behaves_as_if_there_were_no_loop_detect():
    async def scenario():
        _configure(enabled=False)
        agent = _LoopingAgent(plan=(True,), cap=8)
        app = BenchConsole(bridge=_LoopBridge(agent))
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            await _until(pilot, lambda: agent.paragraphs and not app.bridge.busy
                         and app._activity == "ready")
            await pilot.pause(0.3)
            # Never interrupted, never sent on: the model ran to its own end.
            assert agent.interrupted == [False]
            assert agent.paragraphs == [8]
            assert agent.calls == ["fix the flag pickup"]
            assert [k for k, _t in _entries(app)] == ["user", "fold", "reply"]
            assert _entries(app, "reply") == [("reply", "gave up")]

    _run(scenario())


def test_off_the_stream_is_never_handed_to_the_detector():
    async def scenario():
        _configure(enabled=False)
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            fed = []
            app._loop_watch.feed = lambda *a: fed.append(a)
            app._loop_turn_started(auto=False)
            for kind, text in (
                ("reasoning", (PARAGRAPH + "\n") * 5),
                ("delta", (PARAGRAPH + "\n") * 5),
                ("done", ""),
            ):
                app.bridge._events.put(TurnEvent(kind, text))
            app._pump_agent()
            assert fed == []
            assert [k for k, _t in _entries(app)] == ["fold", "reply"]

    _run(scenario())


def test_resumes_stop_at_their_limit_and_leave_the_next_move_to_the_reader():
    async def scenario():
        _configure(resumes=1)
        agent = _LoopingAgent(plan=(True, True, True))
        app = BenchConsole(bridge=_LoopBridge(agent))
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            await _until(pilot, lambda: len(agent.interrupted) == 2
                         and not app.bridge.busy and app._activity == "ready")
            await pilot.pause(0.3)
            assert agent.calls == ["fix the flag pickup", DEFAULT_MESSAGE]
            assert agent.interrupted == [True, True]
            loops = _entries(app, "loop")
            assert len(loops) == 2
            assert "next move is yours" in loops[1][1]
            # The reader speaking starts the count again.
            app._send("keep going")
            await _until(pilot, lambda: len(agent.calls) == 4 and not app.bridge.busy
                         and app._activity == "ready")
            assert agent.calls[2:] == ["keep going", DEFAULT_MESSAGE]

    _run(scenario())


def test_resumes_zero_stops_and_sends_nothing():
    async def scenario():
        _configure(resumes=0)
        agent = _LoopingAgent(plan=(True,))
        app = BenchConsole(bridge=_LoopBridge(agent))
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            await _until(pilot, lambda: agent.interrupted and not app.bridge.busy
                         and app._activity == "ready")
            await pilot.pause(0.3)
            assert agent.calls == ["fix the flag pickup"]
            assert "RESUMES is 0" in _entries(app, "loop")[0][1]

    _run(scenario())


def test_a_tool_call_starts_the_count_again():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._loop_turn_started(auto=False)
            for kind, text in (
                ("reasoning", (PARAGRAPH + "\n") * 2),
                ("tool", "read_file"),
                ("reasoning", (PARAGRAPH + "\n") * 2),
            ):
                app.bridge._events.put(TurnEvent(kind, text))
                app._pump_agent()
            assert app._loop_stopping is None
            assert not _entries(app, "loop")

    _run(scenario())


# ── Waiting for the full stop, and the reader's own say ─────────────────


class _Unwinding(_StubBridge):
    """A bridge whose turn is still finishing until the test says it is done."""

    def __init__(self) -> None:
        super().__init__()
        self.running = False
        self.interrupts = 0

    @property
    def busy(self) -> bool:
        return self.running

    def interrupt(self) -> bool:
        self.interrupts += 1
        return self.running

    def submit(self, message: str) -> bool:
        self.submitted.append(message)
        self.running = True
        return True


def _loop_now(app) -> None:
    app.bridge._events.put(TurnEvent("reasoning", (PARAGRAPH + "\n") * 3))
    app._pump_agent()


def test_the_resume_waits_until_the_turn_has_fully_stopped():
    async def scenario():
        app = BenchConsole(bridge=_Unwinding())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            _loop_now(app)
            assert app.bridge.interrupts == 1
            assert app._loop_resume == "planned"
            # The rest of the loop, still in flight, is not drawn.
            app.bridge._events.put(TurnEvent("reasoning", "more of the same\n"))
            app.bridge._events.put(TurnEvent("done", INTERRUPTED))
            app._pump_agent()
            assert app._loop_resume == "due"
            # The done edge is in, but the worker is still on its way out.
            app._pump_agent()
            assert app.bridge.submitted == ["fix the flag pickup"]
            app.bridge.running = False
            app._pump_agent()
            assert app.bridge.submitted == ["fix the flag pickup", DEFAULT_MESSAGE]
            fold_text = app._bench()._entries[1][2].plain_text()
            assert "more of the same" not in fold_text

    _run(scenario())


def test_the_readers_ctrl_c_calls_off_the_resume():
    async def scenario():
        app = BenchConsole(bridge=_Unwinding())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            _loop_now(app)
            app.run_keyline_action("stop")
            app.bridge._events.put(TurnEvent("done", INTERRUPTED))
            app.bridge.running = False
            app._pump_agent()
            app._pump_agent()
            assert app.bridge.submitted == ["fix the flag pickup"]
            assert app._loop_resume == ""

    _run(scenario())


def test_switching_it_off_mid_stop_calls_off_the_resume():
    async def scenario():
        app = BenchConsole(bridge=_Unwinding())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            _loop_now(app)
            app.run_keyline_action("loop-detect")
            app.bridge._events.put(TurnEvent("done", INTERRUPTED))
            app.bridge.running = False
            app._pump_agent()
            app._pump_agent()
            assert app.bridge.submitted == ["fix the flag pickup"]

    _run(scenario())


def test_going_back_takes_the_resume_with_it():
    async def scenario():
        app = BenchConsole(bridge=_Unwinding())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._send("fix the flag pickup")
            _loop_now(app)
            app.bridge._events.put(TurnEvent("done", INTERRUPTED))
            app._pump_agent()
            assert app._loop_resume == "due"
            app.bridge.running = False
            app._take_back_last_exchange()
            app._pump_agent()
            assert app.bridge.submitted == ["fix the flag pickup"]

    _run(scenario())


# ── How it reads ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("dos_mode", [False, True])
def test_loop_and_auto_entries_are_marked_in_either_skin(dos_mode):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            if dos_mode:
                app.run_keyline_action("dos-mode")
                await pilot.pause()
            app._write("loop", "The same passage came round 3 times in the thinking.")
            app._write("auto", DEFAULT_MESSAGE)
            await pilot.pause()
            pane = app._bench()
            gutters = [
                str(widget._gutter.render()) for kind, _t, widget in pane._entries
            ]
            assert gutters[0].endswith("LOOP") and gutters[1].endswith("AUTO")
            copied = pane.transcript_text()
            assert "LOOP: The same passage" in copied
            assert f"AUTO: {DEFAULT_MESSAGE}" in copied
            # The message sent on the reader's behalf opens an exchange, as
            # it does in the history: going back takes it back.
            assert pane.last_prompt() == DEFAULT_MESSAGE
            assert pane.drop_last_exchange() == DEFAULT_MESSAGE

    _run(scenario())


def test_a_reloaded_conversation_shows_the_resume_as_auto():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 50)) as pilot:
            await pilot.pause()
            app._replay([
                {"role": "user", "content": "fix the flag pickup"},
                {"role": "user", "content": DEFAULT_MESSAGE},
                {"role": "assistant", "content": ANSWER},
            ])
            assert [k for k, _t in _entries(app)] == ["user", "auto", "reply"]

    _run(scenario())


def test_the_settings_live_under_ui_loop_detect():
    _configure(enabled=False, repeats=4, min_words=10, resumes=0)
    settings = read_settings().loop_detect
    assert (settings.enabled, settings.repeats, settings.min_words, settings.resumes) == (
        False, 4, 10, 0,
    )
    assert os.path.exists(os.path.join(os.environ["CURIE_HOME"], "config.yaml"))
