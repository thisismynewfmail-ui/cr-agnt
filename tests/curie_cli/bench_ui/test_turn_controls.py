"""F10, Ctrl+G and Ctrl+B: the chrome, and the two ways back over a turn.

F10 takes away everything that is not the conversation — the title plate, the
key line's lettering, the pane's top padding and notices — because all of it
is frame and all of it costs rows the reading column could have. The key
line's *bar* stays: it is the painted bottom edge of the window.

Ctrl+G asks the last question again from a clean slate; Ctrl+B takes it back
into the composer. Both remove the exchange first, because regenerating with
the previous answer still in the history asks a different question, and going
back in a way that leaves the old reply behind makes the transcript claim the
agent said it unprompted. Neither is a function key: F11, which the first
version used, is the fullscreen toggle in essentially every terminal
emulator and never reaches the application at all.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import Static  # noqa: E402

from curie_cli.bench_ui.agent_bridge import AgentBridge  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole, KeyCap  # noqa: E402
from curie_cli.bench_ui.panes import Composer  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_text,
)


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


async def _one_turn(app, pilot, message: str = "what changed") -> None:
    app.query_one("#composer", Composer).text = message
    await pilot.press("enter")
    app._pump_agent()
    await _settle(pilot)


# ── F10: title plate and key line together ───────────────────────────────

def test_f10_blanks_the_key_line_but_keeps_its_bar():
    """The lettering goes; the painted bar stays.

    Removing the bar itself left the composer sitting on the terminal's own
    background with nothing closing the bottom of the frame, which reads as
    the window having lost an edge rather than as a cleaner one.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            plate = app.query_one("#masthead", Static)
            keyline = app.query_one("#keyline")
            assert plate.display and keyline.display
            assert all(cap.visible for cap in app.query(KeyCap) if not cap.has_class("hidden"))

            await pilot.press("f10")
            await _settle(pilot)
            assert not plate.display, "F10 left the title plate up"
            assert keyline.display, "F10 took the key line's bar away"
            assert keyline.region.height == 1, "the bar lost its row"
            assert not any(cap.visible for cap in app.query(KeyCap)), (
                "the key line still shows its lettering"
            )

            await pilot.press("f10")
            await _settle(pilot)
            assert plate.display, "F10 did not bring the title back"
            assert any(cap.visible for cap in app.query(KeyCap)), (
                "F10 did not bring the lettering back"
            )
    _run(scenario())


def test_the_blank_key_line_keeps_its_colour_and_is_not_clickable():
    """A bar the reader cannot see must not still be a row of buttons."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            keyline = app.query_one("#keyline")
            cap = next(c for c in app.query(KeyCap) if c.cap == "F3")
            spot = (cap.region.x + 1, cap.region.y)

            def bar_colours():
                strips = app.screen._compositor.render_strips()
                row = strips[keyline.region.y]
                return {str(seg.style.bgcolor) for seg in row if seg.style}

            painted = bar_colours()

            await pilot.press("f10")
            await _settle(pilot)
            assert bar_colours() == painted, (
                "the blank bar lost the panel colour it is drawn in"
            )

            fired: list[str] = []
            original = app.run_keyline_action
            app.run_keyline_action = lambda a: (fired.append(a), original(a))[1]
            await pilot.click(offset=spot)
            await _settle(pilot)
            assert fired == [], f"the blank bar is still clickable: {fired}"
    _run(scenario())


def test_hiding_both_gives_their_rows_to_the_conversation():
    """The point of the key is the reading height, so prove it moved."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            before = app.query_one("#transcript").content_region.height
            await pilot.press("f10")
            # The one-per-session reminder is a widget in the same column and
            # would be counted as rows the transcript did not get. Expire it
            # the way the clock would, then measure.
            app._notice_until = 1e-6
            app._tick_clock()
            await _settle(pilot, 4)
            after = app.query_one("#transcript").content_region.height
            # Three rows from the plate, one from the key line.
            assert after >= before + 4, (
                f"the freed rows did not reach the transcript ({before} -> {after})"
            )
    _run(scenario())


def test_the_keys_still_work_with_the_key_line_hidden():
    """The bar is a reminder. Hiding it must not disarm anything."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await pilot.press("f10")
            await _settle(pilot)
            await pilot.press("f3")
            await _settle(pilot)
            assert app.active_pane == "logbook", (
                "a pane key stopped working once the key line was hidden"
            )
    _run(scenario())


def test_the_key_line_drops_tiers_rather_than_overhanging_its_row():
    """A row wider than the window takes the caps on its end off with it."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 30)) as pilot:
            await _settle(pilot)
            for width in (140, 122, 100, 74, 60, 46):
                await pilot.resize_terminal(width, 30)
                await _settle(pilot)
                painted = [
                    cap for cap in app.query(KeyCap) if not cap.has_class("hidden")
                ]
                used = sum(cap.keycap_width for cap in painted)
                note = str(app.query_one("#keyline-note", Static).render())
                assert used + len(note) <= width, (
                    f"the key line is {used + len(note)} columns wide in a "
                    f"{width}-column window"
                )
                # Help and the chrome toggle are the way out of any state the
                # reader did not mean to enter, so they never go.
                caps = {cap.cap for cap in painted}
                assert {"F1", "F10"} <= caps, f"lost an essential cap at {width}: {caps}"
    _run(scenario())


def test_both_turn_controls_are_on_the_key_line():
    """They were asked for as visible buttons, not only as keybindings."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 30)) as pilot:
            await _settle(pilot)
            caps = {
                cap.cap: cap.key_action
                for cap in app.query_one("#keyline").query(KeyCap)
            }
            assert caps.get("^G") == "regenerate", caps
            assert caps.get("^B") == "back", caps
    _run(scenario())


def test_no_key_the_terminal_eats_is_advertised_or_bound():
    """F11 is the fullscreen toggle in essentially every terminal emulator.

    It is intercepted by the window manager, so the application never sees
    it — a keycap painted with it is a button that cannot be pressed, and a
    binding on it is dead code that reads as a working feature.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 30)) as pilot:
            await _settle(pilot)
            assert not any(cap.cap == "F11" for cap in app.query(KeyCap)), (
                "F11 is back on the key line"
            )
            bound = {
                b.key for b in BenchConsole.BINDINGS if hasattr(b, "key")
            }
            assert "f11" not in bound, "F11 is still bound"
    _run(scenario())


# ── Ctrl+G: ask it again ─────────────────────────────────────────────────

def test_regenerate_sends_the_last_request_again():
    async def scenario():
        bridge = _StubBridge(reply="first answer")
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await _one_turn(app, pilot, "which commits are docs-only")
            assert bridge.submitted == ["which commits are docs-only"]

            await pilot.press("ctrl+g")
            await _settle(pilot, 4)
            app._pump_agent()
            await _settle(pilot)

            assert bridge.submitted == [
                "which commits are docs-only",
                "which commits are docs-only",
            ]
    _run(scenario())


def test_regenerate_replaces_the_old_exchange_rather_than_stacking_a_second():
    """Two copies of the question would make it a follow-up, not a retry."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge(reply="an answer"))
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await _one_turn(app, pilot, "ask me once")
            await pilot.press("ctrl+g")
            await _settle(pilot, 4)
            app._pump_agent()
            await _settle(pilot)

            rendered = _transcript_text(app)
            assert rendered.count("ask me once") == 1, rendered
            assert rendered.count("an answer") == 1, rendered
    _run(scenario())


def test_regenerate_drops_the_previous_answer_from_the_history_it_sends():
    """Regenerating with the old answer in context asks a different question."""
    class _Recording(_StubBridge):
        def __init__(self):
            super().__init__()
            self._history = [
                {"role": "user", "content": "ask me once"},
                {"role": "assistant", "content": "an answer"},
            ]

    async def scenario():
        bridge = _Recording()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write("user", "ask me once")
            app._bench().write("reply", "an answer")
            await pilot.pause()

            await pilot.press("ctrl+g")
            await _settle(pilot, 4)
            assert bridge._history == [], (
                f"the old exchange survived the retry: {bridge._history}"
            )
    _run(scenario())


def test_regenerate_with_nothing_to_repeat_says_so():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await pilot.press("ctrl+g")
            await _settle(pilot)
            assert bridge.submitted == []
            notice = app.query_one("#notice")
            assert not notice.has_class("hidden")
            assert "Nothing to send again" in str(notice.render())
    _run(scenario())


def test_regenerate_is_refused_while_a_turn_is_running():
    class _Busy(_StubBridge):
        @property
        def busy(self):
            return True

    async def scenario():
        bridge = _Busy()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write("user", "already asked")
            await pilot.press("ctrl+g")
            await _settle(pilot)
            assert bridge.submitted == []
            assert "A turn is running" in str(app.query_one("#notice").render())
    _run(scenario())


# ── Ctrl+B: take it back ─────────────────────────────────────────────────

def test_ctrl_b_removes_the_exchange_and_returns_the_words():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge(reply="an answer"))
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await _one_turn(app, pilot, "take this one back")
            assert "take this one back" in _transcript_text(app)

            await pilot.press("ctrl+b")
            await _settle(pilot, 4)

            rendered = _transcript_text(app)
            assert "take this one back" not in rendered, "the message is still shown"
            assert "an answer" not in rendered, "the reply to it was left behind"
            assert app.query_one("#composer", Composer).text == "take this one back"
    _run(scenario())


def test_ctrl_b_only_goes_back_one_message_at_a_time():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge(reply="ok"))
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await _one_turn(app, pilot, "the first question")
            await _one_turn(app, pilot, "the second question")

            await pilot.press("ctrl+b")
            await _settle(pilot, 4)
            rendered = _transcript_text(app)
            assert "the first question" in rendered, "it went back two messages"
            assert "the second question" not in rendered
    _run(scenario())


def test_ctrl_b_with_an_empty_bench_says_so():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            await pilot.press("ctrl+b")
            await _settle(pilot)
            assert "Nothing to take back" in str(app.query_one("#notice").render())
    _run(scenario())


def test_ctrl_b_is_refused_while_a_turn_is_running():
    class _Busy(_StubBridge):
        @property
        def busy(self):
            return True

    async def scenario():
        app = BenchConsole(bridge=_Busy())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write("user", "mid-flight")
            await pilot.press("ctrl+b")
            await _settle(pilot)
            assert "mid-flight" in _transcript_text(app)
    _run(scenario())


def test_the_composer_keeps_its_own_editing_keys():
    """Ctrl+B is the console's; every TextArea key it did not take stays."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            composer = app.query_one("#composer", Composer)
            composer.focus()
            await pilot.pause()
            for action in ("delete_word_left", "delete_to_start_of_line", "undo"):
                assert composer.check_action(action, ()) is True, (
                    f"the console took {action} away from the composer"
                )
    _run(scenario())


# ── The bridge's half of going back ──────────────────────────────────────

class TestBridgeRewind:
    """The history the next turn is built from has to go back too."""

    @staticmethod
    def _bridge(history):
        bridge = AgentBridge()
        bridge._history = list(history)
        return bridge

    def test_rewind_drops_the_last_user_message_and_everything_after(self):
        bridge = self._bridge([
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "second"},
        ])
        assert bridge.rewind() == "two"
        assert bridge._history == [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "first"},
        ]

    def test_rewind_on_an_empty_conversation_returns_nothing(self):
        assert self._bridge([]).rewind() is None

    def test_the_last_prompt_is_readable_without_removing_it(self):
        bridge = self._bridge([
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "first"},
        ])
        assert bridge.last_prompt == "one"
        assert len(bridge._history) == 2, "reading the prompt removed it"

    def test_a_multimodal_turn_yields_its_text_parts(self):
        bridge = self._bridge([
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "…"}},
                    {"type": "text", "text": "what is in this"},
                ],
            },
        ])
        assert bridge.last_prompt == "what is in this"

    def test_rewind_skips_the_nudges_the_agent_wrote_to_itself(self):
        """This is the console's own contribution to a turn that will not end.

        The agent loop writes user-role scaffolding of its own — here, the
        nudge it sends when a model announces a tool call and then drops it —
        and hands it back in the turn's history. Cutting at the newest
        user-role row landed on that nudge, leaving the real request in
        place with a half-answered turn hanging off it: an assistant message
        whose tool calls have no results. Sent back to the model, that reads
        as work still outstanding, so it makes the same calls again.
        """
        bridge = self._bridge([
            {"role": "user", "content": "build the site"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {
                "role": "user",
                "content": "[System: Your previous tool call was dropped.]",
                "_dropped_toolcall_nudge": True,
            },
            {"role": "assistant", "content": "done"},
        ])
        assert bridge.rewind() == "build the site"
        assert bridge._history == [], (
            "the rewind cut at a nudge and left a half-answered turn behind"
        )

    def test_the_last_prompt_is_the_last_one_a_person_typed(self):
        bridge = self._bridge([
            {"role": "user", "content": "build the site"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {
                "role": "user",
                "content": "[System: Your previous tool call was dropped.]",
                "_dropped_toolcall_nudge": True,
            },
        ])
        assert bridge.last_prompt == "build the site", (
            "Ctrl+G would have asked the agent to answer its own nudge"
        )

    def test_rewind_is_refused_while_a_turn_is_running(self):
        class _Busy(AgentBridge):
            @property
            def busy(self):
                return True

        bridge = _Busy()
        bridge._history = [{"role": "user", "content": "one"}]
        assert bridge.rewind() is None
        assert bridge._history, "a running turn's history was cut from under it"
