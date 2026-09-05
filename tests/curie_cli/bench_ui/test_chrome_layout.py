"""What the panel does *not* say, and the rows that buys back.

Every item here was a label naming something the reader could already see —
a title over a gauge, a word beside the swatch that is the thing it names, a
counter restating a figure that was already moving. In a 26-column
instrument stack and a terminal that is often 24 rows tall, each of those is
a row or a third of a row spent on a caption.

The rest is F10. It frees rows, and the rows have to actually arrive: a
blank pane padding, a leftover entry margin, or a notice landing a second
later each take back what it just gave.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import Static  # noqa: E402

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge, _flatten  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


def _screen(app) -> list[str]:
    strips = app.screen._compositor.render_strips()
    return ["".join(s.text for s in row) for row in strips]


def _stack_text(app) -> str:
    """Everything the instrument column is currently rendering."""
    return "\n".join(
        _flatten(node.render())
        for node in app.query_one("#instruments").query("*")
        if hasattr(node, "render")
    )


# ── Wording removed from the meters ──────────────────────────────────────

@pytest.mark.parametrize(
    "gone",
    ["CONTEXT", "TURN", "used", "tok", "answer", "think", "tool", "idle"],
)
def test_the_instrument_stack_no_longer_carries_that_word(gone):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            app._tick_instruments()
            await _settle(pilot)
            stack = _stack_text(app)
            assert gone not in stack, f"{gone!r} is back: {stack!r}"
    _run(scenario())


@pytest.mark.parametrize("kept", ["STATE", "OUTPUT", "elapsed"])
def test_the_instruments_that_still_need_naming_keep_their_names(kept):
    """Not a blanket strip: a figure and a bar with no name at all are mute."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            assert kept in _stack_text(app)
    _run(scenario())


def test_the_state_figure_carries_no_running_count():
    """The figure says what; the recorder below says how much."""
    async def scenario():
        from curie_cli.bench_ui.indicators import ActivityMonitor

        class _Counting(_StubBridge):
            def submit(self, message):
                self.submitted.append(message)
                return True

            @property
            def chars_this_turn(self):
                return 4321

            @property
            def reasoning_chars_this_turn(self):
                return 8765

        app = BenchConsole(bridge=_Counting())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            app._send("go")
            app.bridge._events.put(TurnEvent("delta", "answering"))
            app._pump_agent()
            app._tick_instruments()
            await _settle(pilot)
            caption = str(app.query_one("#activity", ActivityMonitor).render())
            assert "4,321" not in caption and "8,765" not in caption, caption
            assert " ch" not in caption, caption
    _run(scenario())


def test_a_tool_name_and_a_wait_reason_still_reach_the_figure():
    """Removing the counters must not silence the state's own detail."""
    async def scenario():
        from curie_cli.bench_ui.indicators import ActivityMonitor

        class _Quiet(_StubBridge):
            def submit(self, message):
                self.submitted.append(message)
                return True

        app = BenchConsole(bridge=_Quiet())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            app._send("go")
            app.bridge._events.put(TurnEvent("tool", "run_command"))
            app._pump_agent()
            await _settle(pilot)
            assert "run_command" in str(app.query_one("#activity", ActivityMonitor).render())
    _run(scenario())


@pytest.mark.parametrize("pictogram", ["⏳", "⌛", "⚠"])
def test_the_status_pictograms_do_not_reach_the_figure(pictogram):
    """The panel has a lamp, a state name and a moving figure for this.

    A pictogram in front of them is a fourth thing saying what three already
    said — and it arrives inside text the agent wrote for a plain terminal.
    """
    from curie_cli.bench_ui.app import _shorten

    assert not _shorten(f"{pictogram} waiting on the provider", 40).startswith(
        pictogram
    )


def test_the_title_bar_carries_no_turn_counter():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 30)) as pilot:
            await _settle(pilot)
            app._set_subject(
                {"model": "omnibrain-curie", "provider": "custom", "turns": 7}
            )
            await pilot.pause()
            subject = _flatten(app.query_one("#titlebar-subject", Static).render())
            assert "turn" not in subject.lower(), subject
            assert "omnibrain-curie" in subject, "the model name went with it"
    _run(scenario())


# ── The composer ─────────────────────────────────────────────────────────

def test_the_composer_no_longer_explains_its_own_keys():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 30)) as pilot:
            await _settle(pilot)
            painted = "\n".join(_screen(app))
            for gone in ("Enter sends", "Shift+Enter", "Ctrl+J newline"):
                assert gone not in painted, f"{gone!r} is back above the composer"
    _run(scenario())


def test_the_row_the_hint_occupied_is_still_there():
    """Without it the input sits hard against the rule above it."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 30)) as pilot:
            await _settle(pilot)
            gap = app.query_one("#composer-gap", Static)
            assert gap.region.height == 1, "the blank row above the input is gone"
            rule = app.query_one("#composer-frame").region.y
            caret = app.query_one("#composer-caret").region.y
            assert caret - rule >= 1, (
                f"the input is flush against the composer rule ({rule} -> {caret})"
            )
    _run(scenario())


# ── F10 and the rows it frees ────────────────────────────────────────────

def test_hiding_the_chrome_puts_the_first_message_at_the_top():
    """Two blank rows stood above it: the pane's padding and an entry margin.

    Both are right while the title plate is there — the plate needs air under
    it — and both are wasted the moment it is not.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(110, 26)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "Hi, how are you?")
            await _settle(pilot)

            transcript = app.query_one("#transcript")
            before = transcript.content_region.y

            await pilot.press("f10")
            app._notice_until = 1e-6
            app._tick_clock()
            await _settle(pilot)

            after = transcript.content_region.y
            assert after < before, (
                f"the transcript did not move up ({before} -> {after})"
            )
            # And the message is on the transcript's very first row.
            rows = _screen(app)
            assert "Hi, how are you?" in rows[after], (
                f"row {after} is {rows[after]!r}, not the first message"
            )
    _run(scenario())


def test_the_ordinary_layout_keeps_its_air_under_the_plate():
    """The tightening is F10's, not a change to the default look."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(110, 26)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "Hi, how are you?")
            await _settle(pilot)
            plate = app.query_one("#masthead")
            rows = _screen(app)
            first = next(i for i, r in enumerate(rows) if "Hi, how are you?" in r)
            assert first > plate.region.y + plate.region.height, (
                "the first message is flush against the title plate"
            )
    _run(scenario())


def test_notices_are_refused_while_the_chrome_is_hidden():
    """A banner landing a second later takes back the rows F10 just freed."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            notice = app.query_one("#notice", Static)

            app._notify_panel("shown while the chrome is up")
            await pilot.pause()
            assert not notice.has_class("hidden")

            await pilot.press("f10")
            await _settle(pilot)
            app._notice_until = 1e-6
            app._tick_clock()
            await _settle(pilot)

            app._notify_panel("arriving after F10")
            await pilot.pause()
            assert notice.has_class("hidden"), "a notice took the freed rows back"

            # A wait notice from the agent takes the same route.
            app.bridge._events.put(TurnEvent("wait", "provider is slow"))
            app._pump_agent()
            await _settle(pilot)
            assert notice.has_class("hidden"), "an agent status broke through"

            await pilot.press("f10")
            await _settle(pilot)
            app._notify_panel("and back when the chrome returns")
            await pilot.pause()
            assert not notice.has_class("hidden"), (
                "notices did not come back with the chrome"
            )
    _run(scenario())


def test_a_notice_already_up_is_taken_down_by_f10():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            notice = app.query_one("#notice", Static)
            app._notify_panel("up before the key was pressed")
            await pilot.pause()
            assert not notice.has_class("hidden")

            await pilot.press("f10")
            await _settle(pilot)
            # The one-shot reminder is the exception: it explains the state
            # that suppresses everything else, so it is allowed through once.
            app._notice_until = 1e-6
            app._tick_clock()
            await _settle(pilot)
            assert notice.has_class("hidden")
    _run(scenario())


@pytest.mark.parametrize("size", [(46, 20), (80, 24), (110, 26), (160, 50)])
def test_the_tightened_layout_still_scales(size):
    """The pane's padding goes; nothing else about the reflow changes."""
    width, height = size

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(width, height)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "a message long enough to wrap at the narrow sizes")
            await _settle(pilot)
            await pilot.press("f10")
            await _settle(pilot)

            transcript = app.query_one("#transcript")
            composer = app.query_one("#composer")
            assert transcript.display and transcript.size.width > 0
            assert composer.display
            assert not transcript.show_horizontal_scrollbar
            # The freed rows go to the transcript, never off the screen.
            assert transcript.region.y + transcript.region.height <= height
    _run(scenario())
