"""The composer is one row at rest, with one blank row under it.

It used to be three rows at rest. With the pane's own bottom padding beneath
them that put *three* blank rows between the caret and the key line, which
reads as the input having lost its bottom rather than as room to type in —
and those rows came out of the transcript, which is the one measurement in
this console that should be generous.

Two things have to stay true together, and the second is why the first is not
just a number change:

* one blank row under the caret, in both display modes — the bench palette
  gets it from the pane's bottom padding, the DOS one from the composer frame,
  because that mode spends its vertical padding on the double frame instead;
* the box still grows with what is typed into it, one row per line, up to the
  cap, and every row it gives up goes to the conversation above it.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import Composer  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402

#: The tallest the composer grows before it scrolls its own contents.
COMPOSER_MAX_ROWS = 8


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


def _blank_rows_below_the_caret(app) -> int:
    """How many empty rows the reader sees under the input, before the keys.

    Measured off the compositor rather than off any widget's height: the row
    under the caret belongs to a different widget in each mode, so a
    per-widget assertion would pass while the thing the reader looks at
    changed. What is being pinned is the picture.
    """
    strips = app.screen._compositor.render_strips()
    composer = app.query_one("#composer").region
    keyline = app.query_one("#keyline").region
    caret_row = composer.y + composer.height - 1
    blank = 0
    for y in range(caret_row + 1, min(keyline.y, len(strips))):
        # The columns the composer occupies — the instrument stack and the
        # rail draw on the same screen rows and are not this question.
        row = strips[y].text[composer.x : composer.x + composer.width]
        if row.strip():
            break
        blank += 1
    return blank


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_one_blank_row_sits_under_the_input(mode):
    """The reported fault: it was three."""
    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            app._apply_display_mode()
            await _settle(pilot)
            assert _blank_rows_below_the_caret(app) == 1

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_input_is_one_row_at_rest(mode):
    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            app._apply_display_mode()
            await _settle(pilot)
            composer = app.query_one("#composer", Composer)
            assert composer.outer_size.height == 1
            # The caret shares the row rather than sitting above or below it.
            caret = app.query_one("#composer-caret")
            assert caret.region.y == composer.region.y
            assert caret.outer_size.height == composer.outer_size.height

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_input_grows_a_row_per_line_and_then_stops(mode):
    """Shrinking the resting height must not cost the box its growth."""
    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            app._apply_display_mode()
            await _settle(pilot)
            composer = app.query_one("#composer", Composer)
            caret = app.query_one("#composer-caret")

            for lines in (1, 2, 3, 5, COMPOSER_MAX_ROWS):
                composer.text = "\n".join(f"line {i}" for i in range(lines))
                await _settle(pilot)
                assert composer.outer_size.height == lines, (
                    f"{lines} line(s) drew {composer.outer_size.height} rows"
                )
                # The gutter tracks the box, so the mark stays beside the text.
                assert caret.outer_size.height == lines

            # Past the cap it scrolls its own contents instead of eating the
            # window: a composer that grows without limit is a transcript.
            for lines in (COMPOSER_MAX_ROWS + 4, 40):
                composer.text = "\n".join(f"line {i}" for i in range(lines))
                await _settle(pilot)
                assert composer.outer_size.height == COMPOSER_MAX_ROWS

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_a_wrapped_single_line_counts_toward_the_height(mode):
    """Soft-wrapped text is still text; the box has to make room for it."""
    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            app._apply_display_mode()
            await _settle(pilot)
            composer = app.query_one("#composer", Composer)
            at_rest = composer.outer_size.height
            composer.text = "wrap " * 200
            await _settle(pilot)
            assert composer.outer_size.height > at_rest

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_rows_the_composer_gives_up_go_to_the_conversation(mode):
    """Which is the point of taking them: the composer is docked at the bottom."""
    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            app._apply_display_mode()
            await _settle(pilot)
            composer = app.query_one("#composer", Composer)
            transcript = app.query_one("#transcript")

            tall_before = transcript.region.height
            composer.text = "\n".join(f"line {i}" for i in range(5))
            await _settle(pilot)
            grown = transcript.region.height
            assert grown == tall_before - 4, (
                "the transcript did not give back exactly what the composer took"
            )

            composer.text = ""
            await _settle(pilot)
            assert transcript.region.height == tall_before

    _run(scenario())


def test_the_stylesheet_does_not_carry_the_old_minimum():
    """A guard for the number itself, so a merge cannot quietly restore it."""
    from curie_cli.bench_ui.styles import BENCH_CSS

    composer_rules = [
        block
        for block in BENCH_CSS.split("}")
        if "#composer {" in block or "#composer-row {" in block
    ]
    assert len(composer_rules) == 2, composer_rules
    for block in composer_rules:
        assert "min-height: 1;" in block, block
        assert f"max-height: {COMPOSER_MAX_ROWS};" in block, block
