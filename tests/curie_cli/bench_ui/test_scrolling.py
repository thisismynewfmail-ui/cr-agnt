"""The transcript has to stay on the newest line, including inside a fold.

The old rule — compare the scroll offset with ``max_scroll_y`` immediately
before mounting — measured geometry that was one layout pass old. A fast
reply outran it and following stopped for the rest of the turn; expanding a
fold broke it harder still, because that adds several screens in one pass
with no scroll event at all. These pin the behaviour, not the mechanism: new
content brings the view with it, a reader who scrolls away is left alone, and
coming back to the bottom starts following again.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import BenchPane, Transcript  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


PARAGRAPH = (
    "A paragraph with enough words in it to occupy several rows of the "
    "transcript at any width this console runs at, so that a handful of "
    "them overflows the pane and there is something to scroll. "
)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


def _at_bottom(log: Transcript) -> bool:
    """Whether the newest row is actually on screen."""
    return log.scroll_offset.y >= log.max_scroll_y - 1


async def _fill(app, pilot, blocks: int = 12) -> BenchPane:
    pane = app.query_one("#pane-bench", BenchPane)
    for index in range(blocks):
        pane.write("reply", f"{index}. {PARAGRAPH}")
    await _settle(pilot)
    return pane


# ── Following the tail ───────────────────────────────────────────────────

def test_new_entries_bring_the_view_with_them():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            pane = await _fill(app, pilot)
            log = app.query_one("#transcript", Transcript)
            assert log.max_scroll_y > 0, "the fixture did not overflow the pane"
            assert _at_bottom(log), "the transcript did not follow the newest entry"

            pane.write("reply", "the last word")
            await _settle(pilot)
            assert _at_bottom(log), "a later entry was not followed"
    _run(scenario())


def test_a_streamed_reply_is_followed_to_its_last_line():
    """The failure mode this exists for: following stops mid-stream.

    Streaming grows one widget rather than mounting many, so the stale
    measurement went wrong here first — the offset fell behind a maximum that
    had already moved and the comparison stopped being true.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            pane = await _fill(app, pilot, blocks=6)
            log = app.query_one("#transcript", Transcript)

            pane.begin_reply()
            for index in range(20):
                pane.append_reply(f"chunk {index} of a long answer. ")
                await pilot.pause()
            await _settle(pilot)
            assert _at_bottom(log), (
                "the view fell behind a streaming reply — "
                f"at {log.scroll_offset.y} of {log.max_scroll_y}"
            )
    _run(scenario())


def test_scrolling_up_stops_the_following_and_coming_back_restarts_it():
    """Reading back through a live conversation must not be yanked away."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            pane = await _fill(app, pilot)
            log = app.query_one("#transcript", Transcript)

            log.scroll_to(y=0, animate=False, immediate=True)
            await _settle(pilot)
            assert not log.following, "scrolling to the top kept the anchor"

            pane.write("reply", "arrived while the reader was elsewhere")
            await _settle(pilot)
            assert log.scroll_offset.y == 0, "the reader was dragged back down"

            log.scroll_end(animate=False, immediate=True)
            await _settle(pilot)
            assert log.following, "returning to the bottom did not resume following"

            pane.write("reply", "and this one is followed again")
            await _settle(pilot)
            assert _at_bottom(log)
    _run(scenario())


# ── Folds ────────────────────────────────────────────────────────────────

def test_expanding_the_workings_keeps_the_newest_line_in_view():
    """The reported bug: open the thinking, then have to scroll down."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            pane = await _fill(app, pilot, blocks=6)
            log = app.query_one("#transcript", Transcript)

            fold = pane.fold()
            for index in range(30):
                fold.add_reasoning(f"reasoning line {index}, at some length. ")
            pane.write("reply", "the answer, which must stay visible")
            await _settle(pilot)
            assert _at_bottom(log)

            fold.collapsed = False
            await _settle(pilot, 6)
            assert _at_bottom(log), (
                "expanding the workings pushed the newest line off screen — "
                f"at {log.scroll_offset.y} of {log.max_scroll_y}"
            )
    _run(scenario())


def test_reasoning_streaming_into_an_open_fold_is_followed():
    """An expanded fold grows without any entry being mounted."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            pane = await _fill(app, pilot, blocks=4)
            log = app.query_one("#transcript", Transcript)

            fold = pane.fold()
            fold.collapsed = False
            await _settle(pilot)
            for index in range(40):
                fold.add_reasoning(f"line {index} of scratch work, at length. ")
                await pilot.pause()
            await _settle(pilot)
            assert _at_bottom(log), (
                "an open fold outgrew the view — "
                f"at {log.scroll_offset.y} of {log.max_scroll_y}"
            )
    _run(scenario())


def test_collapsing_the_workings_also_re_aims_the_view():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            pane = await _fill(app, pilot, blocks=6)
            log = app.query_one("#transcript", Transcript)
            fold = pane.fold()
            for index in range(30):
                fold.add_reasoning(f"reasoning line {index}. ")
            fold.collapsed = False
            await _settle(pilot, 6)
            fold.collapsed = True
            await _settle(pilot, 6)
            assert _at_bottom(log)
    _run(scenario())


# ── Through the real event path ──────────────────────────────────────────

def test_a_whole_turn_ends_with_the_answer_on_screen():
    """The end-to-end shape: reasoning, a tool, an answer, all followed."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(100, 24)) as pilot:
            await _settle(pilot)
            await _fill(app, pilot, blocks=5)
            log = app.query_one("#transcript", Transcript)

            for index in range(12):
                app.bridge._events.put(
                    TurnEvent("reasoning", f"considering option {index}. ")
                )
            app.bridge._events.put(TurnEvent("tool", "run_command"))
            app._pump_agent()
            await _settle(pilot)

            for index in range(12):
                app.bridge._events.put(TurnEvent("delta", f"sentence {index}. "))
            app._pump_agent()
            await _settle(pilot)

            app.bridge._events.put(TurnEvent("done", ""))
            app._pump_agent()
            await _settle(pilot)

            assert _at_bottom(log), "the finished answer was not on screen"
    _run(scenario())


def test_a_resize_keeps_the_newest_line_in_view():
    """A rewrap moves every row above the tail, so the tail moves too."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 24)) as pilot:
            await _settle(pilot)
            await _fill(app, pilot)
            log = app.query_one("#transcript", Transcript)
            for width in (70, 130, 90):
                await pilot.resize_terminal(width, 24)
                await _settle(pilot, 5)
                assert _at_bottom(log), f"lost the tail at {width} columns"
    _run(scenario())
