"""The indicator sets: what they draw, and that the choice reaches everything.

Two halves. The kits themselves are pure functions of (state, tick, box) and
are tested as such — every set draws every state, at every size, in glyphs and
palette roles rather than colours. The console half is that choosing a set on
the PANEL pane changes every animated indicator at once and survives a
restart, and that the state instrument actually tracks the turn.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import indicators  # noqa: E402
from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.indicators import (  # noqa: E402
    IDLE,
    READY,
    SAVING,
    STATES,
    STREAMING,
    THINKING,
    TOOL,
    WAITING,
    ActivityMonitor,
    KitPreview,
    Pen,
    get_kit,
    kit_catalogue,
    kit_names,
)

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


ALL_KITS = kit_names()


class _SilentBridge(_StubBridge):
    """A bridge that accepts a turn and then says nothing.

    The shared stub answers the instant it is asked, which is right for
    testing that a reply lands and wrong for testing the states *before* one
    does: the console's own pump timer drains the answer inside the first
    ``pause`` and the wait is over before it can be looked at.
    """

    def submit(self, message: str) -> bool:
        self.submitted.append(message)
        return True


# ── The kits as drawings ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", ALL_KITS)
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("size", [(1, 1), (4, 1), (18, 1), (26, 5), (34, 7), (120, 3)])
def test_every_kit_fills_every_box_for_every_state(name, state, size):
    """A frame is exactly the box it was asked for, whatever is in it.

    This is the whole scaling contract. The indicators are mounted in a
    one-row strip beside a fold and in a seven-row panel in the instrument
    stack, and both of those change width with the window — a kit that
    returns a short row overhangs or under-fills, and neither is recoverable
    from the outside.
    """
    width, height = size
    frame = get_kit(name).frame(state, 11, width, height)
    assert len(frame) == height, f"{name}/{state} drew {len(frame)} rows, not {height}"
    for row in frame:
        assert len(row) == width, f"{name}/{state} drew {len(row)} cells, not {width}"
        for glyph, role in row:
            assert len(glyph) == 1, f"{name}/{state} emitted {glyph!r}"
            assert isinstance(role, str) and role, f"{name}/{state} emitted a bare glyph"


@pytest.mark.parametrize("name", ALL_KITS)
def test_every_kit_names_a_palette_role_and_never_a_colour(name):
    """Colour is late-bound, or a skin change cannot reach the indicators."""
    kit = get_kit(name)
    seen = set()
    for state in STATES:
        for row in kit.frame(state, 7, 20, 4):
            seen.update(role for _glyph, role in row)
    assert seen, "a kit painted nothing at all"
    for role in seen:
        assert not role.startswith("#"), f"{name} baked in a colour: {role}"
        assert role in {
            "background", "panel", "foreground", "primary", "secondary",
            "accent", "success", "warning", "error", "border", "dim",
            "rule", "selection",
        }, f"{name} used a role no palette defines: {role}"


@pytest.mark.parametrize("name", ALL_KITS)
def test_idle_is_a_parked_flat_trace_in_every_kit(name):
    """A finished indicator has to mean the same thing in every set."""
    row = get_kit(name).frame(IDLE, 9, 14, 1)[0]
    assert {glyph for glyph, _role in row} == {"▁"}, (
        f"{name} does not park flat: {''.join(g for g, _ in row)!r}"
    )


@pytest.mark.parametrize("name", ALL_KITS)
def test_idle_does_not_move_and_the_live_states_do(name):
    """Motion is the signal, so it has to be present exactly where it means
    something. A parked figure that shimmers says work is happening."""
    kit = get_kit(name)

    def frames(state, ticks):
        return {
            "".join(g for g, _ in kit.frame(state, tick, 24, 3)[0]) for tick in ticks
        }

    assert len(frames(IDLE, range(0, 40, 3))) == 1, f"{name}: idle is animating"
    for state in (WAITING, THINKING, TOOL, STREAMING, SAVING):
        assert len(frames(state, range(0, 60, 4))) > 1, (
            f"{name}: {state} does not animate"
        )


@pytest.mark.parametrize("name", ALL_KITS)
def test_the_live_states_do_not_all_draw_the_same_picture(name):
    """A set whose states differ only in colour is a set with one figure."""
    kit = get_kit(name)
    pictures = {
        state: "\n".join(
            "".join(g for g, _ in row) for row in kit.frame(state, 13, 30, 5)
        )
        for state in (WAITING, THINKING, TOOL, STREAMING)
    }
    assert len(set(pictures.values())) == len(pictures), (
        f"{name} draws the same figure for more than one state"
    )


def test_the_sets_are_distinct_from_one_another():
    """Six names for one drawing would be a menu, not a choice."""
    drawings = {}
    for name in ALL_KITS:
        drawings[name] = "\n".join(
            "".join(g for g, _ in row)
            for row in get_kit(name).frame(THINKING, 13, 30, 5)
        )
    assert len(set(drawings.values())) == len(drawings), (
        f"two sets draw identically: {drawings}"
    )


def test_the_catalogue_describes_every_set():
    catalogue = kit_catalogue()
    assert [name for name, _t, _b in catalogue] == ALL_KITS
    for name, title, blurb in catalogue:
        assert title and title.isupper(), f"{name} has no stencilled title"
        assert len(blurb) > 20, f"{name} has no description worth reading"


def test_an_unknown_set_falls_back_rather_than_raising():
    """A hand-edited config must not be able to stop the console starting."""
    assert get_kit("nonsense").name == indicators.DEFAULT_KIT
    assert get_kit("").name == indicators.DEFAULT_KIT
    assert get_kit(None).name == indicators.DEFAULT_KIT


def test_each_kit_instance_carries_its_own_state():
    """Two indicators on screen must not share an automaton's generation."""
    first, second = get_kit("cells"), get_kit("cells")
    assert first is not second
    for tick in range(0, 40, 3):
        first.frame(THINKING, tick, 20, 4)
    a = first.frame(THINKING, 40, 20, 4)
    b = second.frame(THINKING, 40, 20, 4)
    assert a == b, "the same tick drew two different generations"


# ── The console half ─────────────────────────────────────────────────────

def test_the_state_instrument_follows_the_turn():
    """Every distinct thing a turn does gets a distinct state."""
    async def scenario():
        app = BenchConsole(bridge=_SilentBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monitor = app.query_one("#activity", ActivityMonitor)
            assert monitor.state == READY

            app._send("go")
            await pilot.pause()
            assert monitor.state == WAITING, "a sent request is a wait"

            app.bridge._events.put(TurnEvent("reasoning", "thinking about it"))
            app._pump_agent()
            await pilot.pause()
            assert monitor.state == THINKING

            app.bridge._events.put(TurnEvent("tool", "run_command"))
            app._pump_agent()
            await pilot.pause()
            assert monitor.state == TOOL
            assert "run_command" in str(monitor.render())

            app.bridge._events.put(TurnEvent("delta", "the answer"))
            app._pump_agent()
            await pilot.pause()
            assert monitor.state == STREAMING

            app.bridge._events.put(TurnEvent("done", ""))
            app._pump_agent()
            await pilot.pause()
            assert monitor.state == READY
    _run(scenario())


def test_a_write_to_the_record_pulses_and_then_gives_the_instrument_back():
    """It is an event, not a condition — it must not stick."""
    async def scenario():
        app = BenchConsole(bridge=_SilentBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._send("go")
            app.bridge._events.put(TurnEvent("reasoning", "…"))
            app._pump_agent()
            await pilot.pause()
            assert app.query_one("#activity", ActivityMonitor).state == THINKING

            app.bridge._events.put(TurnEvent("record", "session named “rewrap”"))
            app._pump_agent()
            await pilot.pause()
            monitor = app.query_one("#activity", ActivityMonitor)
            assert monitor.state == SAVING, "a write to the record was not shown"

            # Expire the overlay the way the clock would.
            app._activity_overlay = (SAVING, 0.0)
            app._refresh_activity()
            await pilot.pause()
            assert monitor.state == THINKING, "the pulse stuck on the instrument"
    _run(scenario())


def test_a_write_to_the_record_flashes_the_lamp():
    async def scenario():
        from curie_cli.bench_ui.instruments import PanelLamps

        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            lamps = app.query_one("#titlebar-lamps", PanelLamps)
            assert dict(lamps.lamps)["REC"] == "off"
            app.bridge._events.put(TurnEvent("record", "conversation compressed"))
            app._pump_agent()
            await pilot.pause()
            assert dict(lamps.lamps)["REC"] == "pulse"
            # And it fades rather than latching on.
            assert "REC" in lamps._pulses
    _run(scenario())


def test_choosing_a_set_changes_every_indicator_at_once():
    async def scenario():
        app = BenchConsole(bridge=_SilentBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("bench")
            app.bridge._events.put(TurnEvent("reasoning", "…"))
            app._pump_agent()
            await pilot.pause()

            app._apply_kit("scope")
            await pilot.pause()
            assert app.query_one("#activity", ActivityMonitor).kit.name == "scope"
            assert app.query_one(Pen).kit.name == "scope"
    _run(scenario())


def test_the_settings_pane_lists_every_set_and_previews_them_live():
    async def scenario():
        from textual.widgets import DataTable

        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("panel")
            for _ in range(3):
                await pilot.pause()
            table = app.query_one("#indicator-table", DataTable)
            listed = [
                str(table.get_row_at(i)[0]).replace("▶", "").strip()
                for i in range(table.row_count)
            ]
            assert listed == ALL_KITS
            preview = app.query_one("#indicator-preview", KitPreview)
            frames = set()
            for _ in range(6):
                await pilot.pause(0.1)
                frames.add(str(preview.render()))
            assert len(frames) > 1, "the preview is not live"
    _run(scenario())


def test_selecting_a_set_applies_it_and_writes_it_down():
    async def scenario():
        from textual.widgets import DataTable

        saved: list[tuple[str, str]] = []
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("panel")
            for _ in range(3):
                await pilot.pause()
            import curie_cli.bench_ui.app as app_module

            original = app_module.write_setting
            app_module.write_setting = lambda key, value: (
                saved.append((key, value)) or ""
            )
            try:
                table = app.query_one("#indicator-table", DataTable)
                table.move_cursor(row=ALL_KITS.index("cells"))
                await pilot.pause()
                table.post_message(
                    DataTable.RowSelected(
                        table, ALL_KITS.index("cells"), table.coordinate_to_cell_key(
                            table.cursor_coordinate
                        ).row_key
                    )
                )
                for _ in range(3):
                    await pilot.pause()
            finally:
                app_module.write_setting = original

            assert app._kit_name == "cells"
            assert app.query_one("#activity", ActivityMonitor).kit.name == "cells"
            assert saved == [("ui.indicators", "cells")], saved
    _run(scenario())


def test_an_indicator_nobody_can_see_does_not_run_a_frame_clock():
    """Every pane is mounted at start-up, thrown switch or not.

    Six kit-driven widgets exist from the first frame and only one or two of
    them are ever on screen. Repainting the rest is a frame clock spent on
    nothing, at twelve frames a second, for the life of the console.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            preview = app.query_one("#indicator-preview", KitPreview)
            assert not preview.on_screen, "the hidden settings pane was arranged"

            painted: list[int] = []
            preview.refresh = lambda *a, **k: painted.append(1)
            for _ in range(6):
                preview._advance()
            assert painted == [], "a hidden indicator repainted anyway"

            app.show_pane("panel")
            for _ in range(3):
                await pilot.pause()
            assert preview.on_screen
            preview._advance()
            assert painted, "a visible indicator stopped repainting"
    _run(scenario())


def test_hiding_the_meters_stops_the_state_instrument_repainting():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monitor = app.query_one("#activity", ActivityMonitor)
            assert monitor.on_screen

            app.run_keyline_action("instruments_toggle")
            for _ in range(3):
                await pilot.pause()
            assert not monitor.on_screen

            # After the state change, not before: changing state marks the
            # widget dirty on purpose, so it is correct when it comes back.
            monitor.set_state(THINKING)
            painted: list[int] = []
            monitor.refresh = lambda *a, **k: painted.append(1)
            for _ in range(12):
                monitor._advance()
            assert painted == [], "the state instrument ran with the meters hidden"
    _run(scenario())


def test_a_late_tool_completion_does_not_restart_a_parked_pen():
    """The workings end when the answer begins, whatever arrives after."""
    async def scenario():
        app = BenchConsole(bridge=_SilentBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._send("go")
            app.bridge._events.put(TurnEvent("tool", "run_command"))
            app._pump_agent()
            await pilot.pause()
            pen = app.query_one(Pen)
            assert pen.running

            app.bridge._events.put(TurnEvent("delta", "the answer"))
            app._pump_agent()
            await pilot.pause()
            assert not pen.running

            app.bridge._events.put(TurnEvent("tool_done", "run_command"))
            app._pump_agent()
            await pilot.pause()
            assert not pen.running, "a late completion restarted the parked pen"
            assert app.query_one("#activity", ActivityMonitor).state == STREAMING
    _run(scenario())


def test_the_indicators_repaint_in_the_new_skin():
    """A set is glyphs and roles; the skin decides the ink."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monitor = app.query_one("#activity", ActivityMonitor)
            monitor.set_state(THINKING)
            before = {str(span.style) for span in monitor.render().spans}

            from curie_cli.bench_ui.theme import resolve_palette
            from curie_cli.skin_engine import load_skin

            app.bench_palette = resolve_palette(load_skin("curie-vga"), dark=True)
            after = {str(span.style) for span in monitor.render().spans}
            assert before and after
            assert before != after, "the state instrument ignored the skin change"
    _run(scenario())
