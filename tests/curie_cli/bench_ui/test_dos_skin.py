"""The DOS display mode: a second skin, and nothing lost in the swap.

A skin that changes how the console *looks* is easy. A skin that changes what
the console *is* — its title bar, its rail, its key bar, its speaker marks,
its folds — without losing a single feature on the way is not, and every test
here is one of the ways that goes wrong:

* a key that reached its action on the panel and stops reaching it on the
  phosphor, because the mode changed what a keycap draws and something
  measured the drawing rather than the binding;
* a conversation that stays in the previous palette because a mode change
  repainted the chrome and not the transcript;
* a setting that is applied and never written down, which is the fault this
  console has already had once and shipped a whole commit to fix;
* a palette that is beautiful at one end of the brightness control and
  unreadable at the other;
* an animated figure that keeps drawing the panel's picture on the tube.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui.app import (  # noqa: E402
    PANE_KEYS,
    SWITCHES,
    BenchConsole,
    KeyCap,
    Switch,
)
from curie_cli.bench_ui.contrast import AA_LARGE, AA_TEXT, contrast_ratio  # noqa: E402
from curie_cli.bench_ui.indicators import (  # noqa: E402
    IDLE,
    STATES,
    ActivityMonitor,
    get_kit,
    kit_names,
)
from curie_cli.bench_ui.panes import (  # noqa: E402
    Composer,
    Entry,
    Fold,
    PanelPane,
    PhosphorPreview,
    ToggleSwitch,
)
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_DOS_BLOCK_CURSOR,
    KEY_DOS_GLOW,
    KEY_DOS_PHOSPHOR,
    KEY_DOS_SCANLINES,
    KEY_INDICATORS,
    KEY_SKIN_MODE,
    read_settings,
)
from curie_cli.bench_ui.theme import resolve_palette  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_lines,
)


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture
def curie_home(tmp_path, monkeypatch):
    """An isolated CURIE_HOME with a config the console can write to."""
    home = tmp_path / ".curie"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  default: test/model\n", encoding="utf-8"
    )
    monkeypatch.setenv("CURIE_HOME", str(home))
    return home


def _console(mode: str = dos.MODE_DOS, **optics) -> BenchConsole:
    """A console forced into one mode, without going through config."""
    app = BenchConsole(bridge=_StubBridge())
    app._display_mode = mode
    app._optics = dos.Optics(mode=mode, **optics)
    app.bench_palette = app._resolve_palette()
    return app


def _screen_text(app) -> str:
    return "\n".join(
        strip.text for strip in app.screen._compositor.render_strips()
    )


# ── The palette ──────────────────────────────────────────────────────────

ROLES = (
    "background", "panel", "foreground", "primary", "secondary", "accent",
    "success", "warning", "error", "border", "dim", "rule", "selection",
    "band", "ink", "inkdim",
)


@pytest.mark.parametrize("phosphor", dos.PHOSPHOR_NAMES)
@pytest.mark.parametrize("glow", range(dos.GLOW_MIN, dos.GLOW_MAX + 1))
def test_every_role_resolves_on_every_tube_at_every_brightness(phosphor, glow):
    palette = dos.resolve_palette(phosphor=phosphor, glow=glow)
    for role in ROLES:
        value = palette[role]
        assert value.startswith("#") and len(value) == 7, f"{role} = {value!r}"


@pytest.mark.parametrize("phosphor", dos.PHOSPHOR_NAMES)
@pytest.mark.parametrize("glow", range(dos.GLOW_MIN, dos.GLOW_MAX + 1))
def test_the_glass_stays_readable_at_full_drive(phosphor, glow):
    """Glow lifts the ground and the ink together, so the floor is not optional.

    Winding the brightness up mixes the phosphor into the glass *and* the
    foreground toward white: both ends of the contrast move the same way, and
    without the floor at the bottom of ``resolve_palette`` the top of the
    control would be a picture nobody can read.
    """
    palette = dos.resolve_palette(phosphor=phosphor, glow=glow)
    ground = palette["background"]
    assert contrast_ratio(palette["foreground"], ground) >= AA_TEXT
    for role in ("primary", "error", "warning", "success", "border", "rule"):
        assert contrast_ratio(palette[role], ground) >= AA_LARGE, role
    # Inverse video is the mode's other emphasis, so it is held to the same
    # floor the right way round: the glass is the ink on a lit band.
    assert contrast_ratio(palette["ink"], palette["band"]) >= AA_TEXT
    assert contrast_ratio(palette["inkdim"], palette["band"]) >= AA_LARGE


def test_brightness_actually_moves_both_halves_of_the_picture():
    """The setting has to do the two things it claims, not one of them."""
    from curie_cli.bench_ui.contrast import relative_luminance

    off = dos.resolve_palette(glow=dos.GLOW_MIN)
    burn = dos.resolve_palette(glow=dos.GLOW_MAX)
    assert relative_luminance(burn["background"]) > relative_luminance(
        off["background"]
    ), "the haze does not lift the glass"
    assert relative_luminance(burn["primary"]) > relative_luminance(
        off["primary"]
    ), "the bloom does not lift the strokes"


def test_the_dos_palette_says_which_mode_painted_it():
    palette = dos.resolve_palette(phosphor="green", glow=3, scanlines=False)
    assert palette.dos and palette.mode == dos.MODE_DOS
    optics = dos.optics_of(palette)
    assert (optics.phosphor, optics.glow, optics.scanlines) == ("green", 3, False)


def test_the_bench_palette_is_not_the_dos_one():
    palette = resolve_palette()
    assert not palette.dos and palette.mode == dos.MODE_BENCH
    # ...and still defines the band roles, because the stylesheet is one
    # document and a rule naming $bench-band has to resolve under it.
    assert all(palette[role].startswith("#") for role in ("band", "ink", "inkdim"))


@pytest.mark.parametrize(
    "value", ["", None, "nonsense", 17, -4, 2.6, object()]
)
def test_a_stored_setting_of_any_shape_resolves_rather_than_raising(value):
    """These come out of a hand-edited YAML file, so any of them can arrive."""
    assert dos.normalise_mode(value) in dos.MODES
    assert dos.get_phosphor(value).name in dos.PHOSPHOR_NAMES
    assert dos.GLOW_MIN <= dos.clamp_glow(value) <= dos.GLOW_MAX


# ── Reading and writing the settings ─────────────────────────────────────

def test_every_dos_setting_is_read_back(curie_home):
    curie_home.joinpath("config.yaml").write_text(
        "ui:\n"
        "  skin_mode: dos\n"
        "  indicators: raster\n"
        "  dos:\n"
        "    phosphor: green\n"
        "    glow: 4\n"
        "    scanlines: false\n"
        "    block_cursor: false\n",
        encoding="utf-8",
    )
    settings = read_settings()
    assert settings.skin_mode == dos.MODE_DOS and settings.dos_mode
    assert settings.dos_phosphor == "green"
    assert settings.dos_glow == 4
    assert settings.dos_scanlines is False
    assert settings.dos_block_cursor is False
    assert settings.indicators == "raster"


def test_the_documented_defaults_are_what_an_empty_config_gives(curie_home):
    settings = read_settings()
    assert settings.skin_mode == dos.MODE_BENCH, "the mode must be opt-in"
    assert settings.dos_phosphor == dos.DEFAULT_PHOSPHOR
    assert settings.dos_glow == dos.DEFAULT_GLOW
    # Both default ON: reading a missing scanline preference as "off" would
    # open the mode with the one effect its name promises already disabled.
    assert settings.dos_scanlines is True
    assert settings.dos_block_cursor is True


def test_a_malformed_dos_block_does_not_stop_the_console(curie_home):
    curie_home.joinpath("config.yaml").write_text(
        "ui:\n"
        "  skin_mode: [not, a, mode]\n"
        "  dos:\n"
        "    phosphor: 12\n"
        "    glow: brightest\n"
        "    scanlines: maybe\n",
        encoding="utf-8",
    )
    settings = read_settings()
    assert settings.skin_mode == dos.MODE_BENCH
    assert settings.dos_phosphor == dos.DEFAULT_PHOSPHOR
    assert settings.dos_glow == dos.DEFAULT_GLOW
    assert settings.dos_scanlines is True


def test_a_default_indicator_set_is_told_from_a_chosen_one(curie_home):
    """The mode offers its own figure to the first and never to the second.

    "Chosen" cannot mean "present in config.yaml": the config layer merges
    ``DEFAULT_CONFIG`` under every read, so a key nobody has touched comes
    back looking exactly like one somebody set to the same value. It means
    "differs from the set Curie ships with", which is the only signal that
    survives that merge — and it is the right one, because the shipped set is
    the panel's own and the swap makes it stop being the value.
    """
    assert read_settings().indicators_explicit is False
    curie_home.joinpath("config.yaml").write_text(
        "ui:\n  indicators: sweep\n", encoding="utf-8"
    )
    chosen = read_settings()
    assert chosen.indicators == "sweep" and chosen.indicators_explicit is True

    curie_home.joinpath("config.yaml").write_text(
        "ui:\n  indicators: not-a-set\n", encoding="utf-8"
    )
    unusable = read_settings()
    assert unusable.indicators == "bench" and unusable.indicators_explicit is False


def test_the_settings_keys_are_the_ones_the_cli_uses():
    """One store, not two. `curie config set ui.dos.glow 4` must work."""
    assert KEY_SKIN_MODE == "ui.skin_mode"
    assert KEY_DOS_PHOSPHOR == "ui.dos.phosphor"
    assert KEY_DOS_GLOW == "ui.dos.glow"
    assert KEY_DOS_SCANLINES == "ui.dos.scanlines"
    assert KEY_DOS_BLOCK_CURSOR == "ui.dos.block_cursor"

    from curie_cli.config_defaults import DEFAULT_CONFIG

    ui = DEFAULT_CONFIG["ui"]
    assert ui["skin_mode"] == dos.MODE_BENCH
    block = ui["dos"]
    assert block["phosphor"] == dos.DEFAULT_PHOSPHOR
    assert block["glow"] == dos.DEFAULT_GLOW
    assert block["scanlines"] is True
    assert block["block_cursor"] is True


def test_the_console_opens_in_the_saved_mode(curie_home):
    """End to end: the mode the console paints in comes from config."""
    curie_home.joinpath("config.yaml").write_text(
        "ui:\n  skin_mode: dos\n  dos:\n    phosphor: white\n    glow: 1\n",
        encoding="utf-8",
    )
    app = BenchConsole(bridge=_StubBridge())
    assert app.display_mode == dos.MODE_DOS
    assert app.bench_palette.dos
    assert app.optics.phosphor == "white" and app.optics.glow == 1


# ── Every setting is written down ────────────────────────────────────────

def _saving(app_module, saved: list) -> None:
    app_module.write_setting = lambda key, value: (saved.append((key, value)) or "")


async def _panel(app, pilot):
    await _settle(pilot)
    app.show_pane("panel")
    await _settle(pilot)
    return app.query_one("#pane-panel", PanelPane)


@pytest.mark.parametrize(
    "action,key,expected",
    [
        ("dos-mode", KEY_SKIN_MODE, dos.MODE_DOS),
        ("dos-scanlines", KEY_DOS_SCANLINES, False),
        ("dos-cursor", KEY_DOS_BLOCK_CURSOR, False),
        ("dos-glow-up", KEY_DOS_GLOW, dos.DEFAULT_GLOW + 1),
        ("dos-glow-down", KEY_DOS_GLOW, dos.DEFAULT_GLOW - 1),
    ],
)
def test_every_display_control_is_written_down(action, key, expected):
    """Applied *and* saved. Applied alone lasts exactly one session."""
    import curie_cli.bench_ui.app as app_module

    saved: list[tuple[str, object]] = []

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _panel(app, pilot)
            original = app_module.write_setting
            _saving(app_module, saved)
            try:
                app.run_keyline_action(action)
                await _settle(pilot)
            finally:
                app_module.write_setting = original

    _run(scenario())
    assert (key, expected) in saved, f"{action} was not remembered: {saved}"


def test_choosing_a_tube_is_written_down():
    from textual.widgets import DataTable

    import curie_cli.bench_ui.app as app_module

    saved: list[tuple[str, object]] = []

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _panel(app, pilot)
            table = app.query_one("#phosphor-table", DataTable)
            target = next(
                index
                for index in range(table.row_count)
                if str(table.get_row_at(index)[0]).replace("▶", "").strip() == "green"
            )
            table.move_cursor(row=target)
            await _settle(pilot)
            original = app_module.write_setting
            _saving(app_module, saved)
            try:
                table.post_message(
                    DataTable.RowSelected(
                        table,
                        target,
                        table.coordinate_to_cell_key(table.cursor_coordinate).row_key,
                    )
                )
                await _settle(pilot)
            finally:
                app_module.write_setting = original
            assert app.optics.phosphor == "green"

    _run(scenario())
    assert (KEY_DOS_PHOSPHOR, "green") in saved, saved


def test_the_brightness_control_stops_at_both_ends():
    async def scenario():
        app = _console(glow=dos.GLOW_MAX)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("dos-glow-up")
            await _settle(pilot)
            assert app.optics.glow == dos.GLOW_MAX
            for _ in range(dos.GLOW_MAX + 3):
                app.run_keyline_action("dos-glow-down")
                await pilot.pause()
            assert app.optics.glow == dos.GLOW_MIN

    _run(scenario())


def test_the_switches_show_what_is_stored(curie_home):
    curie_home.joinpath("config.yaml").write_text(
        "ui:\n"
        "  skin_mode: dos\n"
        "  dos:\n"
        "    scanlines: false\n"
        "    block_cursor: true\n",
        encoding="utf-8",
    )

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 44)) as pilot:
            await _panel(app, pilot)
            thrown = {
                switch.switch_key: switch.is_on
                for switch in app.query(ToggleSwitch)
            }
            assert thrown["dos-mode"] is True
            assert thrown["dos-scanlines"] is False
            assert thrown["dos-cursor"] is True

    _run(scenario())


# ── Nothing is lost in the swap ──────────────────────────────────────────

KEYLINE_KEYS = [
    ("f1", "help"),
    ("f2", "bench"),
    ("f3", "logbook"),
    ("f4", "instruments"),
    ("f5", "supply"),
    ("f6", "panel"),
    ("f7", "rail_toggle"),
    ("f8", "instruments_toggle"),
    ("f9", "diagnostics"),
    ("f10", "masthead_toggle"),
    ("f12", "new_session"),
    ("ctrl+g", "regenerate"),
    ("ctrl+b", "back"),
    ("ctrl+l", "clear"),
    ("ctrl+r", "reload_logbook"),
]


@pytest.mark.parametrize("key,action", KEYLINE_KEYS)
def test_every_key_still_works_on_the_phosphor(key, action):
    """The mode changes what a keycap *draws*, never what a key *does*."""
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            assert app.bench_palette.dos, "mode precondition"
            seen: list[str] = []
            original = app.run_keyline_action
            app.run_keyline_action = lambda a: (seen.append(a), original(a))[1]

            app.query_one("#composer", Composer).focus()
            await pilot.pause()
            assert isinstance(app.focused, Composer), "focus precondition"

            await pilot.press(key)
            await pilot.pause()
            assert seen == [action], (
                f"{key} did not reach the console in DOS mode (got {seen!r})"
            )

    _run(scenario())


def test_every_pane_still_mounts_and_switches_on_the_phosphor():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            for key, _label, _glyph, _cls in SWITCHES:
                app.show_pane(key)
                await _settle(pilot)
                assert app.query_one(f"#pane-{key}").display, key
                assert app.active_pane == key

    _run(scenario())


def test_a_turn_runs_end_to_end_on_the_phosphor():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            composer = app.query_one("#composer", Composer)
            composer.text = "what changed?"
            app._submit_turn()
            await _settle(pilot, 6)
            shown = "\n".join(_transcript_lines(app))
            assert "what changed?" in shown, shown
            assert "acknowledged." in shown, shown
            assert composer.text == "", "the composer was not cleared"

    _run(scenario())


def test_tool_calls_still_collapse_into_a_fold_on_the_phosphor():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "read the file")
            pane.fold().add_tool("read_file")
            pane.fold().add_tool("terminal")
            pane.fold().add_reasoning("weighing the diff")
            await _settle(pilot)
            fold = app.query_one(Fold)
            assert fold.collapsed, "the workings must be shut by default"
            assert "2" in fold.title and "TOOL CALLS" in fold.title, fold.title
            shown = "\n".join(_transcript_lines(app))
            assert dos.FOLD_COLLAPSED in shown, shown
            assert "weighing the diff" not in shown, "reasoning leaked out of the fold"

    _run(scenario())


@pytest.mark.parametrize("action", ["rail_toggle", "instruments_toggle", "masthead_toggle"])
def test_the_chrome_toggles_still_toggle_on_the_phosphor(action):
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            before = {
                "rail_toggle": lambda: app.query_one("#rail").display,
                "instruments_toggle": lambda: not app.query_one(
                    "#instruments"
                ).has_class("hidden"),
                "masthead_toggle": lambda: app.query_one("#masthead").display,
            }[action]
            was = before()
            app.run_keyline_action(action)
            await _settle(pilot)
            assert before() is not was, f"{action} did nothing"

    _run(scenario())


def test_the_frame_gives_its_rows_back_when_the_chrome_is_hidden():
    """F10 asks for reading room, and the DOS frame is chrome like the rest."""
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            framed = app.query_one("#transcript").content_region.height
            app.run_keyline_action("masthead_toggle")
            await _settle(pilot)
            assert app.query_one("#transcript").content_region.height > framed

    _run(scenario())


# ── The chrome the mode draws itself ─────────────────────────────────────

def test_the_menu_numbers_are_the_keys_that_throw_them():
    """A menu line painted with a key that does not work is worse than none.

    The menu's gutter started as a column of digits, and there were exactly as
    many single digits as there were panes. SCHEDULE is the pane that arrived
    after that, so it carries a control key — F1 to F10 are spoken for, F12
    starts a new conversation, and F11 is the window manager's fullscreen
    toggle in essentially every terminal. What the menu prints is therefore a
    key *label*, not a number, and this holds every one of them against the
    binding it claims.
    """
    bindings = {
        binding.action: key
        for key, binding in (
            (binding.key, binding) for binding in BenchConsole.BINDINGS
        )
    }
    for pane, key in PANE_KEYS.items():
        assert bindings.get(f"keyline('{pane}')") == key, (
            f"the menu offers {key} for {pane}, which is not its key"
        )
    assert set(PANE_KEYS) == {key for key, *_rest in SWITCHES}


def test_the_key_bar_is_drawn_the_way_a_text_mode_program_drew_it():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            caps = {cap.cap: cap.render().plain for cap in app.query(KeyCap)}
            # The F is dropped from a function key: the bar is what says they
            # are function keys, and ten F's is ten columns not spent on words.
            assert caps["F1"] == "1HELP", caps
            assert caps["F10"] == "10CHROME", caps
            # A control key keeps its caret — it is not found by counting
            # along the row.
            assert caps["^G"] == "^GAGAIN", caps

    _run(scenario())


def test_the_rail_is_a_numbered_menu_with_the_thrown_line_inverted():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            drawn = {s.switch_key: s.render().plain for s in app.query(Switch)}
            assert drawn["bench"].strip().startswith("2)"), drawn
            assert drawn["diagnostics"].strip().startswith("9)"), drawn
            # The one control key in the menu, printed with its caret and
            # right-aligned into the same gutter as the digits.
            assert drawn["schedule"].strip().startswith("^T)"), drawn
            palette = app.bench_palette
            active = next(s for s in app.query(Switch) if s.switch_key == "bench")
            styles = {str(span.style) for span in active.render().spans}
            assert any(palette["band"] in style for style in styles), (
                f"the thrown line is not inverse video: {styles}"
            )

    _run(scenario())


def test_the_title_plate_is_square_at_every_width():
    """The title is set into the top rule, so the two runs of rule must add up."""
    for width in (24, 40, 61, 80, 132, 200):
        lines = dos.masthead(dos.resolve_palette(), width, "test/model").plain.split("\n")
        assert len(lines) == 3, lines
        assert {len(line) for line in lines} == {width}, (
            f"the plate is ragged at {width}: {[len(l) for l in lines]}"
        )
        assert lines[0].startswith(dos.BOX_TL) and lines[0].endswith(dos.BOX_TR)
        assert lines[2].startswith(dos.BOX_BL) and lines[2].endswith(dos.BOX_BR)


def test_the_plate_is_redrawn_when_the_window_changes_width():
    """It is drawn to a measured width, so it is the one thing a resize breaks."""
    from textual.widgets import Static

    async def scenario():
        app = _console()
        # Both widths sit above every collapse threshold, so the reading
        # column narrows by the amount the window does and nothing else moves
        # — a resize across a threshold gives the transcript the instrument
        # stack's columns back and can land on the same width by coincidence.
        async with app.run_test(size=(152, 40)) as pilot:
            await _settle(pilot)
            plate = app.query_one("#masthead", Static)
            wide = {len(line) for line in plate.visual.plain.split("\n")}
            assert len(wide) == 1, f"the plate started ragged: {wide}"
            await pilot.resize_terminal(118, 40)
            await _settle(pilot, 5)
            narrow = {len(line) for line in plate.visual.plain.split("\n")}
            assert len(narrow) == 1, f"the plate went ragged on resize: {narrow}"
            assert narrow != wide, "the plate did not follow the window"
            assert narrow == {plate.content_size.width}

    _run(scenario())


def test_the_title_bar_carries_the_date_as_a_dos_title_bar_did():
    from datetime import datetime

    from textual.widgets import Static

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            shown = app.query_one("#titlebar-clock", Static).visual.plain
            assert datetime.now().strftime("%Y") in shown, shown
            assert ":" in shown, "the time went with the date"

    _run(scenario())


def test_the_wordmark_names_the_installed_version_and_not_a_made_up_one():
    from curie_cli import __version__

    drawn = dos.wordmark(dos.resolve_palette()).plain
    assert "CURIE AGENT" in drawn
    assert __version__ in drawn, drawn


# ── The conversation follows the mode ────────────────────────────────────

def test_the_marks_are_the_ones_the_period_could_print():
    bench = Entry.marks_for(resolve_palette())
    phosphor = Entry.marks_for(dos.resolve_palette())
    assert bench is Entry.MARKS
    assert phosphor is dos.ENTRY_MARKS
    # ✗ (U+2717) is not in code page 437, so the fault mark cannot be it.
    assert "✗" in bench["error"][0]
    assert "✗" not in phosphor["error"][0]


def test_a_conversation_already_on_screen_changes_with_the_mode():
    """The fault this guards is a chrome-only repaint leaving the chat behind."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "before the switch")
            pane.write("reply", "answered before the switch")
            pane.fold().add_tool("read_file")
            await _settle(pilot)
            assert "▶ YOU" in "\n".join(_transcript_lines(app))

            app.show_pane("panel")
            await _settle(pilot)
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            app.show_pane("bench")
            await _settle(pilot)

            shown = "\n".join(_transcript_lines(app))
            assert "► YOU" in shown, shown
            assert "■ CURIE" in shown, shown
            assert "▶ YOU" not in shown, "an entry kept the previous mode's mark"
            assert "TOOL CALL" in shown, "the fold kept the previous heading"
            assert "before the switch" in shown, "the words did not survive"

    _run(scenario())


def test_the_fold_marker_follows_the_mode_and_the_state():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.fold().add_tool("terminal")
            await _settle(pilot)
            fold = app.query_one(Fold)
            assert dos.FOLD_COLLAPSED in "\n".join(_transcript_lines(app))
            fold.collapsed = False
            await _settle(pilot)
            assert dos.FOLD_EXPANDED in "\n".join(_transcript_lines(app))

    _run(scenario())


def test_the_composer_takes_the_mode_prompt_and_the_cursor_preference():
    from textual.widgets import Static

    async def scenario():
        app = _console(block_cursor=False)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            caret = app.query_one("#composer-caret", Static)
            assert dos.COMPOSER_CARET in caret.visual.plain
            assert app.query_one("#composer", Composer).cursor_blink is False

            app.run_keyline_action("dos-cursor")
            await _settle(pilot)
            assert app.query_one("#composer", Composer).cursor_blink is True

    _run(scenario())


def test_turning_the_mode_off_puts_the_bench_panel_back():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "written on the phosphor")
            await _settle(pilot)
            assert "► YOU" in "\n".join(_transcript_lines(app))

            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            assert app.display_mode == dos.MODE_BENCH
            assert not app.bench_palette.dos
            assert not app.screen.has_class("-dos")
            shown = "\n".join(_transcript_lines(app))
            assert "▶ YOU" in shown, shown
            assert "written on the phosphor" in shown

    _run(scenario())


def test_a_skin_chosen_while_the_mode_is_on_does_not_drop_the_mode():
    """The skin dresses the CLI and the TUI; the console keeps its phosphor."""
    from textual.widgets import DataTable

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 44)) as pilot:
            await _panel(app, pilot)
            table = app.query_one("#panel-table", DataTable)
            target = next(
                (
                    index
                    for index in range(table.row_count)
                    if str(table.get_row_at(index)[0]).replace("▶", "").strip()
                    not in ("", "—", "default")
                ),
                None,
            )
            assert target is not None, "no second skin to select"
            table.move_cursor(row=target)
            await _settle(pilot)
            table.post_message(
                DataTable.RowSelected(
                    table,
                    target,
                    table.coordinate_to_cell_key(table.cursor_coordinate).row_key,
                )
            )
            await _settle(pilot)
            assert app.bench_palette.dos, "the skin knocked the console out of the mode"
            assert app.display_mode == dos.MODE_DOS
            switch = app.query_one("#switch-dos-mode", ToggleSwitch)
            assert switch.is_on, "the switch and the console disagree"

    _run(scenario())


# ── The animated figures ─────────────────────────────────────────────────

def test_the_optics_pass_is_the_identity_under_the_bench_mode():
    """Every existing set has to be byte-identical when no tube is showing it."""
    for name in kit_names():
        plain = get_kit(name)
        with_optics = get_kit(name)
        with_optics.optics = dos.Optics(mode=dos.MODE_BENCH).as_dict()
        for state in STATES:
            assert plain.frame(state, 9, 24, 5) == with_optics.frame(state, 9, 24, 5), (
                f"{name}/{state} changed under the bench mode"
            )


def test_bloom_lifts_a_lit_cell_and_never_a_blank():
    kit = get_kit("bench")
    kit.optics = dos.Optics(mode=dos.MODE_DOS, glow=dos.GLOW_MAX, scanlines=False).as_dict()
    bloomed = kit.frame("streaming", 9, 40, 4)
    plain = get_kit("bench").frame("streaming", 9, 40, 4)
    from curie_cli.bench_ui.indicators import SHADE

    lifted = 0
    for bright_row, plain_row in zip(bloomed, plain):
        for (bright, _r1), (dull, _r2) in zip(bright_row, plain_row):
            if dull == " ":
                assert bright == " ", "bloom lit a cell that had nothing on it"
            elif dull in SHADE and bright in SHADE:
                assert SHADE.index(bright) >= SHADE.index(dull)
                lifted += SHADE.index(bright) > SHADE.index(dull)
    assert lifted, "full drive changed nothing"


def test_scanlines_darken_alternate_rows_and_leave_a_single_row_alone():
    kit = get_kit("bench")
    kit.optics = dos.Optics(
        mode=dos.MODE_DOS, glow=dos.GLOW_MIN, scanlines=True
    ).as_dict()
    rows = kit.frame("thinking", 9, 30, 6)
    for index, row in enumerate(rows):
        roles = {role for _glyph, role in row}
        if index % 2:
            assert roles == {"dim"}, f"row {index} is not a scanline gap: {roles}"
        else:
            assert roles != {"dim"}, f"row {index} lost its own colour"
    # A one-row strip beside a fold has no raster to speak of.
    strip = kit.frame("thinking", 9, 30, 1)
    assert {role for _glyph, role in strip[0]} != {"dim"}

    kit.optics = dos.Optics(
        mode=dos.MODE_DOS, glow=dos.GLOW_MIN, scanlines=False
    ).as_dict()
    off = kit.frame("thinking", 9, 30, 6)
    assert off != rows, "turning scanlines off changed nothing"


def test_the_raster_set_draws_every_state():
    kit = get_kit("raster")
    assert kit.name == "raster" and "raster" in kit_names()
    for state in STATES:
        rows = kit.frame(state, 9, 32, 4)
        assert len(rows) == 4 and all(len(row) == 32 for row in rows)
        drawn = {glyph for row in rows for glyph, _role in row}
        if state != IDLE:
            assert drawn - {" "}, f"{state} drew nothing"


def test_the_mode_offers_its_own_figure_only_where_none_was_chosen():
    import curie_cli.bench_ui.app as app_module

    async def scenario(chosen: bool):
        saved: list[tuple[str, object]] = []
        app = BenchConsole(bridge=_StubBridge())
        app._kit_chosen = chosen
        app._apply_kit("bench")
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            original = app_module.write_setting
            _saving(app_module, saved)
            try:
                app.run_keyline_action("dos-mode")
                await _settle(pilot)
            finally:
                app_module.write_setting = original
            return app.query_one("#activity", ActivityMonitor).kit.name, saved

    picked, saved = _run(scenario(chosen=False))
    assert picked == "raster", "the mode did not adopt its own figure"
    assert (KEY_INDICATORS, "raster") in saved, "and did not write it down"

    kept, saved = _run(scenario(chosen=True))
    assert kept == "bench", "the mode overruled a set the reader had chosen"
    assert not [entry for entry in saved if entry[0] == KEY_INDICATORS]


def test_the_figure_the_mode_offered_is_not_taken_back():
    """One direction only, so the toggle behaves the same after a restart.

    By the time the console is reopened, ``ui.indicators`` says ``raster`` and
    no later run can tell that from a set the reader picked. Taking it back on
    the way out would therefore make the same key do two different things
    depending on whether the console had been closed since.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        app._kit_chosen = False
        app._apply_kit("bench")
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            assert app.query_one("#activity", ActivityMonitor).kit.name == "raster"
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            assert app.display_mode == dos.MODE_BENCH
            assert app.query_one("#activity", ActivityMonitor).kit.name == "raster"

    _run(scenario())


def test_the_live_figures_read_the_tube_they_are_being_shown_on():
    async def scenario():
        app = _console(glow=dos.GLOW_MAX, scanlines=True)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            monitor = app.query_one("#activity", ActivityMonitor)
            monitor.render()
            assert monitor.kit.scanlines is True
            assert monitor.kit.bloom == pytest.approx(dos.GLOW_BLOOM[dos.GLOW_MAX])

            app.run_keyline_action("dos-scanlines")
            await _settle(pilot)
            monitor.render()
            assert monitor.kit.scanlines is False

    _run(scenario())


def test_the_lamps_take_ink_on_an_inverse_title_bar():
    from curie_cli.bench_ui.instruments import PanelLamps

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            lamps = app.query_one("#titlebar-lamps", PanelLamps)
            drawn = lamps.render()
            palette = app.bench_palette
            colours = {str(span.style) for span in drawn.spans}
            assert any(palette["ink"] in colour for colour in colours), colours
            # A signal colour on a lit band is a hole in the band, not a lamp.
            assert not any(palette["success"] in colour for colour in colours)
            assert dos.LAMP_LIT in drawn.plain and dos.LAMP_DARK in drawn.plain

    _run(scenario())


# ── The settings pane ────────────────────────────────────────────────────

def test_the_toggle_is_on_the_settings_pane_where_it_was_asked_to_be():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 44)) as pilot:
            panel = await _panel(app, pilot)
            keys = {s.switch_key for s in panel.query(ToggleSwitch)}
            assert {"dos-mode", "dos-scanlines", "dos-cursor"} <= keys
            assert app.query_one("#phosphor-table") is not None
            assert app.query_one("#phosphor-preview", PhosphorPreview) is not None
            shown = _screen_text(app)
            assert "DISPLAY" in shown and "DOS MODE" in shown, shown

    _run(scenario())


def test_the_preview_shows_the_whole_brightness_scale():
    drawn = dos.preview("amber", 52, chosen=3).plain.split("\n")
    assert len(drawn) == dos.GLOW_MAX - dos.GLOW_MIN + 1
    for label, row in zip(dos.GLOW_LABELS, drawn):
        assert label in row, row
        assert len(row) == 52, f"the preview is ragged: {len(row)}"
    marked = [row for row in drawn if row.startswith("►")]
    assert len(marked) == 1 and dos.GLOW_LABELS[3] in marked[0]


def test_the_preview_follows_the_tube_being_chosen():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 44)) as pilot:
            await _panel(app, pilot)
            preview = app.query_one("#phosphor-preview", PhosphorPreview)
            before = preview.render().spans[1].style
            app._set_optics(phosphor="green")
            await _settle(pilot)
            assert preview.render().spans[1].style != before

    _run(scenario())


def test_the_settings_pane_repaints_itself_when_the_mode_changes():
    """It is the one page the reader is looking at when they change this."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 44)) as pilot:
            await _panel(app, pilot)
            before = _screen_text(app)
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            after = _screen_text(app)
            assert after != before
            # The DOS check box, not the panel's throw switch.
            assert "[X] DOS MODE" in after, after

    _run(scenario())


def test_the_stylesheet_scopes_the_mode_rather_than_replacing_it():
    """One document, both modes — so the two cannot drift apart."""
    assert "Screen.-dos" in BenchConsole.CSS
    assert BenchConsole.CSS.count("Screen.-dos #titlebar {") == 1

    async def scenario():
        app = _console(mode=dos.MODE_BENCH)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            titlebar = app.query_one("#titlebar")
            # Outer, not content: the bench bar is one row of lettering with a
            # heavy rule under it, and the rule is the row the DOS bar gives
            # back — a content-size comparison would see no difference at all.
            bench_height = titlebar.outer_size.height
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            assert app.screen.has_class("-dos")
            assert titlebar.outer_size.height < bench_height, (
                "the DOS title bar is not the one-row inverse band"
            )

    _run(scenario())



# ── What it costs to install ─────────────────────────────────────────────

def test_the_mode_adds_nothing_for_an_installer_to_install():
    """The skin has to ship inside the `ui` extra both installers already fit.

    `curie ui` is reachable because `[all]` carries `curie-agent[ui]` and both
    installers then *verify* Textual actually landed, repairing it in place
    when the tiered resolution fell short of it. A display mode that reached
    for a colour library, a font shaper or an image codec would sit outside
    all of that: it would work on the machine it was written on and be a
    console that will not start on a fresh install until somebody notices the
    extra needs another line.

    So the whole mode is the standard library plus what the console already
    had. This walks the imports rather than trusting the claim, because the
    failure it guards is one nobody sees until an install is already broken.

    Imports inside a ``try`` are exempt: the console already has one of those
    (``pyperclip``, for a clipboard paste it degrades without), and a guarded
    import is by construction not a thing an installer has to provide.
    """
    import ast
    import sys

    from curie_cli.bench_ui import app as app_module

    console = pathlib.Path(app_module.__file__).parent
    root = console.parent.parent

    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    #: Curie's own top-level modules and packages, read off the tree rather
    #: than listed — a list would go stale the first time one was renamed.
    first_party = {
        entry.stem if entry.suffix == ".py" else entry.name
        for entry in root.iterdir()
        if entry.suffix == ".py" or (entry / "__init__.py").exists()
    }
    #: Everything the `ui` extra and the core dependencies supply that this
    #: console is entitled to reach for.
    supplied = {"rich", "textual"}

    def guarded(tree: ast.AST) -> set[ast.AST]:
        """Every import node that sits inside a ``try``."""
        inside: set[ast.AST] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        inside.add(child)
        return inside

    offenders: dict[str, set[str]] = {}
    for source in sorted(console.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        optional = guarded(tree)
        roots: set[str] = set()
        for node in ast.walk(tree):
            if node in optional:
                continue
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        outside = roots - stdlib - first_party - supplied
        if outside:
            offenders[source.name] = outside

    assert not offenders, (
        "the console reaches outside the `ui` extra unguarded, so a fresh "
        f"install would not have it: {offenders}"
    )
