"""Reasoning and tool calls belong in a fold, shut, behind an indicator.

Left inline they bury the reply they belong to: a minute of thinking is
several screens the reader has to scroll past to find one paragraph. The fold
keeps them one keystroke away.

What the shut fold reports is deliberately thin. The scratch work is the
model talking to itself and a running character count says only that a number
is going up — neither is addressed to the reader. So the heading counts tool
calls, and a sweeping pen says whether work is happening.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import Fold  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_lines,
    _transcript_text,
)


def _run(coro):
    return asyncio.run(coro)


def _feed(app, *events: TurnEvent) -> None:
    for event in events:
        app.bridge._events.put(event)
    app._pump_agent()


REASONING = "Weighing the two candidate routes before committing. " * 8


def test_reasoning_is_folded_and_starts_collapsed():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("reasoning", REASONING))
            await pilot.pause()

            folds = list(app.query(Fold))
            assert len(folds) == 1, f"expected one fold, got {len(folds)}"
            assert folds[0].collapsed is True, "the fold must start shut"

            # Shut means the body is genuinely not painted.
            painted = "\n".join(_transcript_lines(app))
            assert "Weighing the two candidate routes" not in painted, (
                "the reasoning is visible with the fold shut"
            )
    _run(scenario())


def test_the_collapsed_heading_says_how_much_work_not_what_it_said():
    """The heading counts tool calls. It does not quote the scratch work.

    Reasoning text is the model talking to itself, and a running character
    count says only that a number is going up — neither is addressed to the
    reader, and both invited them to follow along with something they had
    not opened.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", REASONING),
                TurnEvent("tool", "read_file"),
            )
            await pilot.pause()

            # The whole heading, exactly. A count of tool calls and nothing
            # else — no reasoning text, no measurement of it, no preview.
            assert str(app.query_one(Fold).title) == "WORKINGS · 1 tool call"
    _run(scenario())


def test_a_turn_that_only_thinks_gets_a_bare_heading():
    """With no tools there is nothing to count, so the heading says nothing."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("reasoning", REASONING))
            await pilot.pause()
            assert str(app.query_one(Fold).title) == "WORKINGS"
    _run(scenario())


def test_the_pen_sweeps_while_thinking_and_parks_when_it_stops():
    """The indicator is the only thing reporting that work is happening."""
    from curie_cli.bench_ui.panes import Pen

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("reasoning", REASONING))
            for _ in range(2):
                await pilot.pause()
            pen = app.query_one(Pen)
            assert pen.running, "the pen is not sweeping while reasoning arrives"

            frames = set()
            for _ in range(8):
                await pilot.pause(0.2)
                frames.add(str(pen.render()))
            assert len(frames) > 1, "the pen is not animating"

            # The first token of the answer means thinking is over.
            _feed(app, TurnEvent("delta", "the answer"))
            for _ in range(2):
                await pilot.pause()
            assert not pen.running, "the pen kept sweeping after the reply began"
            parked = str(pen.render())
            assert set(parked) == {"▁"}, f"the pen did not park flat: {parked!r}"
    _run(scenario())


def test_opening_the_fold_shows_the_whole_run_in_order():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", "First I check the config."),
                TurnEvent("tool", "read_file"),
                TurnEvent("reasoning", "Then I write the patch."),
                TurnEvent("tool", "write_file"),
            )
            await pilot.pause()

            fold = app.query_one(Fold)
            fold.collapsed = False
            for _ in range(2):
                await pilot.pause()

            body = "\n".join(_transcript_lines(app))
            for fragment in (
                "First I check the config.",
                "read_file",
                "Then I write the patch.",
                "write_file",
            ):
                assert fragment in body, f"{fragment!r} missing from the fold"
            assert body.index("First I check") < body.index("Then I write"), (
                "the run is out of order"
            )
    _run(scenario())


def test_the_reply_itself_is_never_folded():
    """The answer is the thing being read; it stays in the open."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", REASONING),
                TurnEvent("delta", "The residue assayed clean."),
                TurnEvent("done", ""),
            )
            await pilot.pause()

            painted = "\n".join(_transcript_lines(app))
            assert "The residue assayed clean." in painted, (
                "the reply is not visible without opening a fold"
            )
    _run(scenario())


def test_one_fold_per_turn_not_one_per_chunk():
    """A fold per reasoning delta would be hundreds of controls per turn."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, *[TurnEvent("reasoning", f"step {i}. ") for i in range(40)])
            await pilot.pause()
            assert len(list(app.query(Fold))) == 1
    _run(scenario())


def test_a_second_turn_gets_its_own_fold():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", "turn one thinking"),
                TurnEvent("delta", "one"),
                TurnEvent("done", "one"),
            )
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", "turn two thinking"),
                TurnEvent("delta", "two"),
                TurnEvent("done", "two"),
            )
            await pilot.pause()
            assert len(list(app.query(Fold))) == 2, (
                "the second turn's workings landed in the first turn's fold"
            )
    _run(scenario())


def test_a_turn_with_no_workings_leaves_no_empty_fold():
    """An empty control the reader can open onto nothing is worse than none."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("delta", "no tools needed for that."),
                TurnEvent("done", "no tools needed for that."),
            )
            for _ in range(2):
                await pilot.pause()
            assert list(app.query(Fold)) == []
    _run(scenario())


def test_clearing_the_transcript_removes_the_folds():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("reasoning", REASONING))
            await pilot.pause()
            assert list(app.query(Fold))

            app.run_keyline_action("clear")
            for _ in range(2):
                await pilot.pause()
            assert list(app.query(Fold)) == [], "a fold survived Ctrl+L"
    _run(scenario())


@pytest.mark.parametrize("width", [120, 90, 70, 56, 46])
def test_a_fold_never_overhangs_the_transcript(width):
    """A long preview must be clipped by the view, not scroll it sideways."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", REASONING),
                TurnEvent("tool", "a_tool_with_a_deliberately_long_name"),
            )
            for _ in range(3):
                await pilot.pause()

            log = app.query_one("#transcript")
            assert not log.show_horizontal_scrollbar, (
                f"the fold forced a horizontal scrollbar at {width} columns"
            )
            view = log.content_region.width
            for row in _transcript_lines(app):
                assert len(row.rstrip()) <= view, (
                    f"a fold row overhangs a {view}-column view: {row!r}"
                )
    _run(scenario())


def test_an_error_is_not_folded_away():
    """An error the reader has to open a drawer to find is a hidden error."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", REASONING),
                TurnEvent("error", "the provider refused the request"),
                TurnEvent("done", ""),
            )
            await pilot.pause()
            painted = "\n".join(_transcript_lines(app))
            assert "the provider refused the request" in painted
    _run(scenario())


def test_the_fold_gutter_mark_comes_from_the_skin():
    """Chrome is the skin's to decide, never this module's."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("tool", "read_file"))
            await pilot.pause()
            fold = app.query_one(Fold)
            fold.collapsed = False
            for _ in range(2):
                await pilot.pause()
            assert app.bench_palette.tool_prefix in _transcript_text(app)
    _run(scenario())
