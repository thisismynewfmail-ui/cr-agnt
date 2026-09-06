"""The conversation is held off its own edges, top and bottom as well as the sides.

The reported fault only shows up once the transcript is *full*: with a short
conversation there is empty pane below it and nothing looks wrong. Fill it, and
the newest reply sits welded to the composer's rule while the oldest visible
one is welded to the title plate's — with two clean columns of margin either
side of both. The eye reads that as a rendering fault rather than as a full
column, because on every other edge the console holds its content off the
chrome.

Two columns and one row is what *even* means on a character grid: a terminal
cell is about twice as tall as it is wide, so equal counts would not read as
equal margins. One row is the same inset the sides have.

Padding rather than a margin on the entries, and that is the half a margin
cannot do: a margin is part of the scrolled content, so it rides up out of
sight the moment the reader scrolls — which is exactly when the gap is wanted.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 5) -> None:
    for _ in range(times):
        await pilot.pause()


def _console(mode: str) -> BenchConsole:
    app = BenchConsole(bridge=_StubBridge())
    app._display_mode = mode
    app._optics = dos.Optics(mode=mode)
    app.bench_palette = app._resolve_palette()
    return app


def _rows_of(app, region) -> list[str]:
    strips = app.screen._compositor.render_strips()
    rows = []
    for y in range(region.y, min(region.y + region.height, len(strips))):
        row = "".join(seg.text for seg in strips[y])
        rows.append(row[region.x : region.x + region.width])
    return rows


def _blank_edges(app) -> tuple[int, int]:
    """Blank rows at the top and the bottom of the transcript's own box."""
    transcript = app.query_one("#transcript")
    rows = _rows_of(app, transcript.region)
    top = 0
    for row in rows:
        if row.strip():
            break
        top += 1
    bottom = 0
    for row in reversed(rows):
        if row.strip():
            break
        bottom += 1
    return top, bottom


def _inset(app) -> dict:
    """The transcript's own inset, measured off its box rather than its content.

    Blank *rows* are a content question — where the scroll happens to have
    landed, whether an entry's own margin is showing — and they answer "is the
    text welded to the chrome", which is the fault. They cannot answer "are
    the four insets even", because a margin inside the content looks exactly
    like padding outside it. The box can: the difference between a widget's
    region and its content region is its padding, and nothing else.
    """
    transcript = app.query_one("#transcript")
    box, inner = transcript.region, transcript.content_region
    pane = app.query_one("#pane-bench")
    return {
        "top": inner.y - box.y,
        "bottom": (box.y + box.height) - (inner.y + inner.height),
        "left": inner.x - box.x,
        # From the *pane's* edge, which is the margin a reader actually sees
        # at the side: the pane holds the transcript off the frame, and the
        # transcript holds the text off itself.
        "from_pane": inner.x - pane.region.x,
    }


async def _fill(app, pilot) -> None:
    """More conversation than the pane can hold, so the edges are real edges."""
    pane = app._bench()
    for index in range(40):
        pane.write("reply", f"line {index} of the conversation body")
    await _settle(pilot)


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_a_full_transcript_still_has_a_row_of_air_at_each_end(mode):
    """The reported fault: it had none, at either end, in both modes."""

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 32)) as pilot:
            await _settle(pilot)
            await _fill(app, pilot)
            top, bottom = _blank_edges(app)
            assert top >= 1, "the oldest visible line is welded to the plate above it"
            assert bottom >= 1, "the newest line is welded to the composer's rule"

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_four_insets_are_the_same_inset(mode):
    """Even, not merely present. One end padded and the other not reads worse."""

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 32)) as pilot:
            await _settle(pilot)
            await _fill(app, pilot)
            inset = _inset(app)
            assert inset["top"] == inset["bottom"] == inset["left"] == 1, inset

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_one_row_answers_the_two_columns_at_the_sides(mode):
    """A cell is about twice as tall as it is wide, so this is what even means.

    Counted from the pane's own edge, which is where the reader's eye starts:
    the pane holds the transcript off the frame by one column and the
    transcript holds the text off itself by another, against one row at each
    end. Two by one, on a grid whose cells are two by one.
    """

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 32)) as pilot:
            await _settle(pilot)
            await _fill(app, pilot)
            inset = _inset(app)
            assert inset["from_pane"] == 2 * inset["top"], inset

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_gap_survives_scrolling(mode):
    """A margin would ride up out of sight; padding is part of the box.

    This is the case the gap exists for — the reader has scrolled back through
    a long conversation, and every edge is a mid-conversation edge.
    """

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 32)) as pilot:
            await _settle(pilot)
            await _fill(app, pilot)
            transcript = app.query_one("#transcript")
            transcript.scroll_to(y=transcript.max_scroll_y // 2, animate=False)
            await _settle(pilot)
            top, bottom = _blank_edges(app)
            assert top >= 1 and bottom >= 1, (top, bottom)

    _run(scenario())


def test_the_title_plate_is_not_welded_to_the_frame_above_it():
    """The DOS mode drew two heavy rules with nothing between them.

    Which reads as the pane having lost its top edge rather than as a plate
    inside a frame — and the bench palette had a row there all along, so the
    two modes disagreed about the same picture.
    """

    async def scenario():
        rows = {}
        for mode in (dos.MODE_BENCH, dos.MODE_DOS):
            app = _console(mode)
            async with app.run_test(size=(110, 32)) as pilot:
                await _settle(pilot)
                content = app.query_one("#content").region
                plate = app.query_one("#masthead").region
                rows[mode] = plate.y - content.y - (1 if mode == dos.MODE_DOS else 0)
        assert rows[dos.MODE_BENCH] == rows[dos.MODE_DOS] == 1, rows

    _run(scenario())
