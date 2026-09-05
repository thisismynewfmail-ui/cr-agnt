"""The bench console has to mount, reflow, and run a turn.

These are the invariants a visual rework can silently break: chrome that
paints over other chrome, a layout that stops fitting a narrow window, and a
chat surface that looks right but no longer sends anything.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import AgentBridge, TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import SWITCHES, BenchConsole  # noqa: E402


class _StubBridge(AgentBridge):
    """An AgentBridge that answers without touching a provider."""

    def __init__(self, reply="acknowledged.", fail=False):
        super().__init__()
        self.reply = reply
        self.fail = fail
        self.submitted: list[str] = []

    def ensure_agent(self) -> bool:
        if self.fail:
            self._load_error = "no model configured"
            return False
        self._agent = object()
        return True

    def describe(self) -> dict:
        if self._agent is None:
            return {}
        return {
            "model": "test/model",
            "provider": "test",
            "context_length": 200_000,
            "turns": len(self.submitted),
        }

    def submit(self, message: str) -> bool:
        self.submitted.append(message)
        self._events.put(TurnEvent("delta", self.reply))
        self._events.put(TurnEvent("done", self.reply))
        return True


def _run(coro):
    return asyncio.run(coro)


def _transcript_text(app) -> str:
    """Everything the transcript is currently showing, as plain text.

    The transcript holds widgets rather than pre-wrapped log lines, so this
    walks its children and flattens whatever each is rendering — including a
    fold's collapsed title, which is content in its own right.
    """
    from textual.widgets import Collapsible, Static

    from curie_cli.bench_ui.panes import Fold

    parts: list[str] = []
    for node in app.query_one("#transcript").query("*"):
        if isinstance(node, Fold):
            parts.append(str(node.title))
        elif isinstance(node, Collapsible):
            continue
        elif isinstance(node, Static):
            parts.append(_flatten(node.render()))
    return "\n".join(parts)


def _flatten(renderable) -> str:
    """Render anything Rich can draw down to its text."""
    from rich.console import Console

    console = Console(width=200, no_color=True, legacy_windows=False)
    with console.capture() as capture:
        console.print(renderable, end="")
    return capture.get()


def _rendered_row_count(app) -> int:
    """How many rows of the transcript hold anything.

    The measure of a reflow is that the same text occupies fewer rows once
    there is more width for it.
    """
    return sum(1 for row in _transcript_lines(app) if row.strip())


def _transcript_lines(app) -> list[str]:
    """The transcript's visible rows exactly as the console paints them.

    Read off the compositor and clipped to the transcript's own region, so
    what a test measures is what a reader would see — a widget re-rendered at
    some width of the test's choosing would prove nothing about wrapping.
    """
    region = app.query_one("#transcript").content_region
    screen = app.screen._compositor.render_strips()
    rows: list[str] = []
    for y in range(region.y, min(region.y + region.height, len(screen))):
        row = "".join(seg.text for seg in screen[y])
        rows.append(row[region.x : region.x + region.width])
    return rows


# ── Mounting and chrome ──────────────────────────────────────────────────

def test_console_mounts_and_shows_the_bench_first():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.active_pane == "bench"
            assert app.query_one("#pane-bench").display
    _run(scenario())


def test_titlebar_is_not_painted_over_by_the_side_columns():
    """Regression: the rail and instruments used to claim the full height.

    Docked to the left and right of the *screen*, both took rows 0-N and
    painted straight over the top-docked title bar — docks at different edges
    do not subtract from one another. The columns live inside a body row now,
    so the title bar keeps the top and nothing overlaps it.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            titlebar = app.query_one("#titlebar").region
            for selector in ("#rail", "#instruments", "#content"):
                region = app.query_one(selector).region
                assert not titlebar.overlaps(region), (
                    f"{selector} at {region} overlaps the title bar at {titlebar}"
                )
            keyline = app.query_one("#keyline").region
            for selector in ("#rail", "#instruments", "#content"):
                assert not keyline.overlaps(app.query_one(selector).region)
    _run(scenario())


def test_every_switch_reveals_exactly_one_pane():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for key, _label, _glyph, _cls in SWITCHES:
                app.show_pane(key)
                await pilot.pause()
                visible = [
                    k for k, *_ in SWITCHES if app.query_one(f"#pane-{k}").display
                ]
                assert visible == [key], f"{key} -> {visible}"
                assert app.query_one(f"#switch-{key}").has_class("-active")
    _run(scenario())


# ── Responsive layout ────────────────────────────────────────────────────

@pytest.mark.parametrize("width", [200, 140, 120, 100, 84, 70, 58, 50, 46])
def test_layout_holds_at_every_width(width):
    """The transcript and composer are never collapsed — they are the point."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(width, 32)) as pilot:
            await pilot.pause()
            app._apply_responsive_layout(width)
            await pilot.pause()
            transcript = app.query_one("#transcript")
            composer = app.query_one("#composer")
            assert transcript.display and transcript.size.width > 0
            assert composer.display
    _run(scenario())


def test_chrome_collapses_in_a_fixed_order():
    """Meters go before the rail's labels, which go before the rail."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(200, 32)) as pilot:
            await pilot.pause()

            def state(width):
                app._apply_responsive_layout(width)
                rail = app.query_one("#rail")
                instruments = app.query_one("#instruments")
                return (
                    rail.display,
                    rail.has_class("narrow"),
                    not instruments.has_class("hidden"),
                )

            assert state(200) == (True, False, True)
            assert state(90) == (True, False, False), "meters hide first"
            assert state(70) == (True, True, False), "then the rail narrows"
            assert state(50) == (False, True, False), "then the rail hides"
    _run(scenario())


def test_narrow_window_advertises_the_function_keys():
    """With the rail hidden, the keys are the only way to switch panes."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(50, 30)) as pilot:
            await pilot.pause()
            app._apply_responsive_layout(50)
            await pilot.pause()
            note = app.query_one("#keyline-note")
            assert "F2" in str(note.render())
    _run(scenario())


# ── The chat surface ─────────────────────────────────────────────────────

def test_enter_sends_the_composed_message():
    async def scenario():
        bridge = _StubBridge(reply="two commits, both docs.")
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer").text = "summarise the last three commits"
            await pilot.press("enter")
            await pilot.pause()
            assert bridge.submitted == ["summarise the last three commits"]
            assert app.query_one("#composer").text == "", "composer must clear"
    _run(scenario())


def test_empty_composer_sends_nothing():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer").text = "   \n  "
            await pilot.press("enter")
            await pilot.pause()
            assert bridge.submitted == []
    _run(scenario())


def test_streamed_deltas_reach_the_transcript():
    async def scenario():
        bridge = _StubBridge(reply="the assay came back clean.")
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer").text = "how did it go"
            await pilot.press("enter")
            app._pump_agent()
            await pilot.pause()
            rendered = _transcript_text(app)
            assert "how did it go" in rendered
            assert "the assay came back clean." in rendered
    _run(scenario())


def test_a_failed_agent_says_so_instead_of_looking_idle():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge(fail=True))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._agent_ready(False)
            await pilot.pause()
            notice = app.query_one("#notice")
            assert not notice.has_class("hidden")
            assert "curie model" in str(notice.render())
    _run(scenario())


# ── Notebook pages ───────────────────────────────────────────────────────

def test_a_notebook_keyword_opens_a_page_instead_of_sending_a_turn():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer").text = "notebook"
            await pilot.press("enter")
            await pilot.pause()
            assert bridge.submitted == [], "must not spend a turn on this"
            assert "NOTEBOOK" in _transcript_text(app)
    _run(scenario())


def test_an_ordinary_request_is_not_mistaken_for_a_notebook_keyword():
    """Only the bare keyword opens a page; a sentence containing it does not."""
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer").text = "add a notebook section to the docs"
            await pilot.press("enter")
            await pilot.pause()
            assert bridge.submitted == ["add a notebook section to the docs"]
    _run(scenario())


class TestTranscriptRewrapping:
    """The transcript must never need sideways scrolling.

    RichLog stores lines already wrapped to the width they were written at, so
    it cannot rewrap itself: a reply composed in a 100-column window kept
    100-column lines when the window shrank to 50, and the reader got a
    horizontal scrollbar instead of readable text — a bar that also ate a row
    of an already-short pane.
    """

    @staticmethod
    def _long_reply() -> str:
        # Several sentences rather than one, so the row count is well clear
        # of a wrap boundary at every width these tests use. A reply that
        # happens to fit exactly at one width measures the fixture, not the
        # rewrap — and the reading column moved when the speaker's name went
        # into a gutter, which is exactly the kind of change that turns a
        # boundary-sitting fixture into a failure with nothing wrong.
        return (
            "A reply long enough to need wrapping at every width used here, "
            "and an unbreakable token behind it: "
            "https://example.com/a/very/long/path/that/cannot/be/broken\n"
        )

    @pytest.mark.parametrize("width", [120, 100, 80, 60, 52, 46])
    def test_no_horizontal_scrollbar_at_any_width(self, width):
        async def scenario():
            app = BenchConsole(bridge=_StubBridge())
            async with app.run_test(size=(120, 24)) as pilot:
                await pilot.pause()
                app.bridge._events.put(
                    TurnEvent("delta", TestTranscriptRewrapping._long_reply())
                )
                app._pump_agent()
                await pilot.pause()

                await pilot.resize_terminal(width, 24)
                for _ in range(3):
                    await pilot.pause()

                log = app.query_one("#transcript")
                assert not log.show_horizontal_scrollbar, (
                    f"horizontal scrollbar at {width} columns"
                )
                assert log.max_scroll_x <= 2, (
                    f"content overhangs the view at {width} columns by "
                    f"{log.max_scroll_x}"
                )
        _run(scenario())

    def test_shrinking_rewraps_rather_than_clipping(self):
        """The content must still be there after the rewrap, just narrower."""
        async def scenario():
            app = BenchConsole(bridge=_StubBridge())
            async with app.run_test(size=(120, 24)) as pilot:
                await pilot.pause()
                app.bridge._events.put(
                    TurnEvent("delta", TestTranscriptRewrapping._long_reply())
                )
                app._pump_agent()
                await pilot.pause()

                await pilot.resize_terminal(50, 24)
                for _ in range(3):
                    await pilot.pause()

                rendered = _transcript_text(app)
                assert "unbreakable token" in rendered, "content lost in rewrap"
                assert "example.com" in rendered, "the long token was dropped"
        _run(scenario())

    def test_growing_back_rewraps_too(self):
        """Widening must reflow, not leave the narrow wrapping in place."""
        async def scenario():
            app = BenchConsole(bridge=_StubBridge())
            async with app.run_test(size=(50, 24)) as pilot:
                await pilot.pause()
                app.bridge._events.put(
                    TurnEvent("delta", TestTranscriptRewrapping._long_reply())
                )
                app._pump_agent()
                await pilot.pause()
                narrow = _rendered_row_count(app)

                await pilot.resize_terminal(120, 24)
                for _ in range(3):
                    await pilot.pause()
                wide = _rendered_row_count(app)

                assert wide < narrow, (
                    f"widening did not reflow: {narrow} -> {wide}"
                )
        _run(scenario())

    def test_the_header_plate_is_width_adaptive(self):
        """A hand-measured plate is right at one width and ragged at others."""
        async def scenario():
            app = BenchConsole(bridge=_StubBridge())
            async with app.run_test(size=(120, 24)) as pilot:
                for _ in range(3):
                    await pilot.pause()
                for width in (120, 70, 50):
                    await pilot.resize_terminal(width, 24)
                    for _ in range(3):
                        await pilot.pause()
                    view = app.query_one("#transcript").content_region.width
                    for row in _transcript_lines(app):
                        assert len(row.rstrip()) <= view, (
                            f"line overhangs a {view}-column view at "
                            f"{width} cols: {row!r}"
                        )
        _run(scenario())
