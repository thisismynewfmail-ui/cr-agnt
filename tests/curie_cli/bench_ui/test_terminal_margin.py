"""The sliver of terminal under the key line.

Reported as a gap between the bottom of the console and the bottom of the
window, through which the terminal's own background showed. Two different
things produce that picture and both are covered here:

* **Rows the console never claims.** Fixed earlier (stale ``COLUMNS`` and
  ``LINES``); held here as an invariant at several window sizes — the key
  line is always the last row and nothing below it is left unpainted.
* **The terminal's margin.** A terminal lays its grid out in whole cells and
  paints whatever the window has left over below the last row in *its* own
  default background. No cell can reach that strip; the only thing a program
  can change about it is the terminal's default background (OSC 11), which
  the console sets to the colour of its bottom edge and hands back (OSC 111)
  on the way out and while suspended.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.app import (  # noqa: E402
    OSC_RESET_BACKGROUND,
    BenchConsole,
    osc_default_background,
)
from curie_cli.bench_ui.settings import KEY_FILL_MARGIN, write_setting  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """A config file nobody else is writing to."""
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


def _recording(app) -> list:
    """Capture every control sequence the console sends the terminal."""
    written: list = []

    def record(sequence: str) -> bool:
        written.append(sequence)
        return True

    app._write_terminal = record
    return written


def _key_line_colour(app) -> str:
    return app.query_one("#keyline").styles.background.hex.upper()


def _paints(written: list) -> list:
    return [s for s in written if s.startswith("\x1b]11;")]


# ── The sequence ─────────────────────────────────────────────────────────

def test_the_sequence_is_spelled_the_way_xparsecolor_reads_it():
    assert osc_default_background("#EDE8DE") == "\x1b]11;rgb:ed/e8/de\x07"
    assert osc_default_background("3e2604") == "\x1b]11;rgb:3e/26/04\x07"


@pytest.mark.parametrize("bad", ["", None, "red", "#12345", "#1234567", "rgb(1,2,3)"])
def test_a_colour_it_cannot_spell_sends_nothing(bad):
    """A malformed OSC is something some terminals print rather than ignore."""
    assert osc_default_background(bad) == ""


# ── Painting and handing back ────────────────────────────────────────────

def test_the_margin_takes_the_colour_of_the_key_line_beside_it():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        written = _recording(app)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            assert _paints(written), "the margin was never painted"
            assert written[-1] == osc_default_background(_key_line_colour(app))
    _run(scenario())


def test_the_margin_follows_the_display_mode():
    """The DOS key line is the glass, not the panel; the margin goes with it."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        written = _recording(app)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            before = written[-1]
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            assert app.bench_palette.dos
            assert written[-1] == osc_default_background(_key_line_colour(app))
            assert written[-1] != before
    _run(scenario())


def test_an_unchanged_edge_is_not_painted_again():
    """Repaints are frequent; the terminal hears about the colour once."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        written = _recording(app)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            count = len(_paints(written))
            app._apply_display_mode()
            app._apply_display_mode()
            await _settle(pilot)
            assert len(_paints(written)) == count
    _run(scenario())


def test_the_terminal_gets_its_own_colour_back_on_the_way_out():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        written = _recording(app)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
        assert written[-1] == OSC_RESET_BACKGROUND
        assert app._margin_painted == ""
    _run(scenario())


def test_a_suspend_hands_the_colour_back_and_a_resume_takes_it_again():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        written = _recording(app)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.app_suspend_signal.publish(app)
            assert written[-1] == OSC_RESET_BACKGROUND
            app.app_resume_signal.publish(app)
            await _settle(pilot)
            assert written[-1] == osc_default_background(_key_line_colour(app))
    _run(scenario())


def test_switched_off_it_leaves_the_terminal_alone():
    write_setting(KEY_FILL_MARGIN, False)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        written = _recording(app)
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
        assert written == [], written
    _run(scenario())


# ── Every row claimed ────────────────────────────────────────────────────

@pytest.mark.parametrize("size", [(80, 24), (120, 40), (200, 60), (60, 18)])
def test_the_key_line_is_the_last_row_at_every_size(size):
    """Nothing below the key line is left for the terminal to show through."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        _recording(app)
        async with app.run_test(size=size) as pilot:
            await _settle(pilot)
            keyline = app.query_one("#keyline")
            assert keyline.region.bottom == size[1], (keyline.region, size)
            assert keyline.region.width == size[0]
            strips = app.screen._compositor.render_strips()
            assert len(strips) == size[1]
            assert "STOP" in "".join(seg.text for seg in strips[-1])
    _run(scenario())
