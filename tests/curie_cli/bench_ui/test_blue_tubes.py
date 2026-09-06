"""The blue tubes, and the one that breathes.

Blue is the awkward primary. It has the lowest relative luminance of the
three, so a blue stroke on dark glass starts closer to its background than an
amber or a green one does — every one of these tubes leans on the contrast
floor rather than clearing it unaided, and a set of them is exactly the case
where "it looked fine when I picked the hex codes" is not evidence.

The animated one carries a second kind of risk, and it is the expensive kind:
an effect that runs eight times a second forever. Three properties keep it a
feature rather than a bug, and all three are pinned below —

* **no colour moves, at any phase.** The palette reaches the screen by two
  routes — widgets that render their own ``Text`` read it live, the stylesheet
  freezes it until a ``refresh_css`` costing ~170 ms — and the console has
  surfaces drawn both ways sitting against each other. What pulses instead is
  the *drive*, an optic with one consumer and no stylesheet reader, so the two
  routes cannot fall out of step;
* **the picture actually changes.** A character cell has five levels of
  shading; a drive that moves by a number the glyph ramp quantises away is an
  animation in the log and a still picture on the screen. That is what the
  first version of this did, and ``test_the_picture_actually_changes_in_every_configuration``
  is what caught it;
* **the contrast floor applies at every phase**, not merely at rest.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui.app import PHOSPHOR_PULSE_HZ, BenchConsole  # noqa: E402
from curie_cli.bench_ui.contrast import (  # noqa: E402
    AA_LARGE,
    AA_TEXT,
    contrast_ratio,
)
from curie_cli.bench_ui.indicators import get_kit, kit_names  # noqa: E402
from curie_cli.bench_ui.settings import read_settings  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402

#: The four this file is about.
BLUE = ("azure", "ice", "cobalt", "aurora")

#: Enough of a breath to catch a dip. Twenty-four points over a cycle puts a
#: sample every 460 ms of an eleven-second one — finer than any dip the eye
#: would read as a flicker rather than as a fault.
PHASES = tuple(i / 24 for i in range(24))

#: Every colour the palette carries a name for.
ROLES = (
    "background", "panel", "foreground", "primary", "secondary", "accent",
    "success", "warning", "error", "border", "dim", "rule", "selection",
    "band", "ink", "inkdim",
)



def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 5) -> None:
    for _ in range(times):
        await pilot.pause()


def _console(phosphor: str) -> BenchConsole:
    app = BenchConsole(bridge=_StubBridge())
    app._display_mode = dos.MODE_DOS
    app._optics = dos.Optics(mode=dos.MODE_DOS, phosphor=phosphor)
    app.bench_palette = app._resolve_palette()
    return app


# ── The set exists and is reachable ──────────────────────────────────────


def test_there_are_four_blue_tubes_and_they_are_all_registered():
    assert set(BLUE) <= set(dos.PHOSPHOR_NAMES)
    catalogue = {name for name, _title, _blurb in dos.phosphor_catalogue()}
    assert set(BLUE) <= catalogue, "a tube the chooser does not list is not a choice"


@pytest.mark.parametrize("name", BLUE)
def test_each_blue_tube_is_a_complete_tube(name):
    """A missing stop is a palette that resolves to the wrong colour silently."""
    tube = dos.get_phosphor(name)
    assert tube.name == name
    assert tube.title and tube.blurb
    for stop in (tube.glass, *tube.ramp()):
        assert stop.startswith("#") and len(stop) == 7, stop


@pytest.mark.parametrize("name", BLUE)
def test_each_blue_tube_is_actually_blue(name):
    """Blue-dominant at every lit stop, or the name is a lie.

    Checked on the ramp rather than on the resolved palette, because the
    contrast floor is allowed to lift a stop's lightness — what it must not do
    is turn the tube into a different colour, and holding the ramp is how that
    stays true.
    """
    from curie_cli.bench_ui.contrast import parse_hex

    for stop in dos.get_phosphor(name).ramp():
        red, green, blue = parse_hex(stop)
        assert blue > red, f"{name}: {stop} is not blue-dominant over red"
        assert blue >= green, f"{name}: {stop} is not blue-dominant over green"


def test_the_four_are_four_distinct_tones():
    """A set of four that reads as two is three wasted entries on the chooser."""
    grounds = {}
    lights = {}
    for name in BLUE:
        palette = dos.resolve_palette(phosphor=name, glow=dos.DEFAULT_GLOW)
        grounds[name] = palette["background"]
        lights[name] = palette["primary"]
    assert len(set(grounds.values())) == 4, grounds
    assert len(set(lights.values())) == 4, lights


def test_the_default_tube_is_still_amber():
    """A new set must not quietly re-skin every console that has one."""
    assert dos.DEFAULT_PHOSPHOR == "amber"
    assert not dos.get_phosphor(dos.DEFAULT_PHOSPHOR).animated


# ── Readability, which is the whole risk with blue ───────────────────────


@pytest.mark.parametrize("name", BLUE)
@pytest.mark.parametrize("glow", range(dos.GLOW_MIN, dos.GLOW_MAX + 1))
def test_every_blue_tube_is_readable_at_every_brightness_and_phase(name, glow):
    tube = dos.get_phosphor(name)
    for phase in (PHASES if tube.animated else (0.0,)):
        palette = dos.resolve_palette(phosphor=name, glow=glow, phase=phase)
        ground = palette["background"]
        where = f"{name} glow={glow} phase={phase:.3f}"

        assert contrast_ratio(palette["foreground"], ground) >= AA_TEXT, where
        for role in ("primary", "error", "warning", "success", "border", "rule"):
            assert contrast_ratio(palette[role], ground) >= AA_LARGE, f"{where} {role}"
        # Inverse video, held the right way round: the glass is the ink.
        assert contrast_ratio(palette["ink"], palette["band"]) >= AA_TEXT, where
        assert contrast_ratio(palette["inkdim"], palette["band"]) >= AA_LARGE, where


@pytest.mark.parametrize("name", BLUE)
@pytest.mark.parametrize("glow", range(dos.GLOW_MIN, dos.GLOW_MAX + 1))
def test_every_role_resolves_on_every_blue_tube(name, glow):
    palette = dos.resolve_palette(phosphor=name, glow=glow)
    for role in ROLES:
        value = palette[role]
        assert value.startswith("#") and len(value) == 7, f"{name} {role} = {value!r}"


# ── The breath ───────────────────────────────────────────────────────────


def test_exactly_one_tube_breathes():
    """The motion is a choice a reader makes, not a default they are given."""
    animated = [p.name for p in dos.PHOSPHORS if p.animated]
    assert animated == ["aurora"], animated


def test_a_still_tube_ignores_the_phase_entirely():
    """Otherwise every tube pays for the one that moves."""
    for name in dos.PHOSPHOR_NAMES:
        if dos.get_phosphor(name).animated:
            continue
        seen = {
            dos.resolve_palette(phosphor=name, phase=phase)["primary"]
            for phase in PHASES
        }
        assert len(seen) == 1, f"{name} moved without being asked to"


@pytest.mark.parametrize("glow", range(dos.GLOW_MIN + 1, dos.GLOW_MAX + 1))
def test_the_drive_swings_at_every_usable_brightness(glow):
    """``OFF`` is excluded: a tube with no beam current has none to swing."""
    drives = [
        dos.resolve_palette(phosphor="aurora", glow=glow, phase=phase).optics["bloom"]
        for phase in PHASES
    ]
    assert max(drives) - min(drives) > 0.05, f"glow {glow}: {drives}"
    assert all(0.0 <= drive <= 1.0 for drive in drives)


@pytest.mark.parametrize("scanlines", [True, False])
@pytest.mark.parametrize("glow", range(dos.GLOW_MIN, dos.GLOW_MAX + 1))
@pytest.mark.parametrize("kit", sorted(kit_names()))
def test_the_picture_actually_changes_in_every_configuration(kit, glow, scanlines):
    """The test the first attempt at this failed, and the reason it exists.

    A character cell has five levels of shading and eight of column height,
    and the kits step a lit cell up that ramp in whole glyphs. The first
    version of the breath moved the drive by six percent — a real number, a
    real change in the optics, and at most brightnesses not one that crossed a
    step of the ramp. The console reported a breath and drew a still picture.

    So the claim under test is the only one that matters to a reader: across
    one cycle, does the *rendered figure* differ from itself. Every indicator
    set, every brightness, and with the scanline switch both ways.
    """
    figures = set()
    for phase in PHASES:
        drawn = get_kit(kit)
        drawn.optics = dos.resolve_palette(
            phosphor="aurora", glow=glow, phase=phase, scanlines=scanlines
        ).optics
        figures.add(
            tuple(tuple(row) for row in drawn.frame("streaming", 40, 24, 5))
        )
    assert len(figures) > 1, (
        f"{kit} at {dos.GLOW_LABELS[glow]} with scanlines={scanlines} "
        "does not move across a whole breath"
    )


def test_a_still_tube_draws_a_still_figure():
    """The counterpart: six tubes must cost nothing and show nothing."""
    for name in dos.PHOSPHOR_NAMES:
        if dos.get_phosphor(name).animated:
            continue
        figures = set()
        for phase in PHASES:
            drawn = get_kit("raster")
            drawn.optics = dos.resolve_palette(phosphor=name, phase=phase).optics
            figures.add(
                tuple(tuple(row) for row in drawn.frame("streaming", 40, 24, 5))
            )
        assert len(figures) == 1, f"{name} moved without being asked to"


def test_the_animated_tube_keeps_the_raster_its_drift_needs():
    """The one place a tube overrules a stored preference, and why.

    A vertical hold that drifts is a statement about where the dark lines
    are; a tube with no dark lines has nothing to drift, and at three of the
    five brightnesses the drive alone does not cross a step of the glyph
    ramp. Rather than a theme that advertises motion and, with one switch off,
    silently has none, the tube keeps its raster — and only the figures see
    it, so the stored preference comes straight back on the next tube.
    """
    assert dos.get_phosphor("aurora").pulse.needs_raster is True
    assert dos.resolve_palette(phosphor="aurora", scanlines=False).optics["scanlines"]
    # And no other tube takes that liberty.
    for name in dos.PHOSPHOR_NAMES:
        if name == "aurora":
            continue
        painted = dos.resolve_palette(phosphor=name, scanlines=False)
        assert not painted.optics["scanlines"], name


def test_the_raster_creeps_by_exactly_one_line():
    """Two states, because a raster is rows and a row is the smallest step."""
    pulse = dos.get_phosphor("aurora").pulse
    offsets = {pulse.scan_offset_at(phase) for phase in PHASES}
    assert offsets == {0, 1}
    assert all(
        dos.resolve_palette(phosphor=name, phase=0.75).optics["scan_offset"] == 0
        for name in dos.PHOSPHOR_NAMES
        if not dos.get_phosphor(name).animated
    )


def test_no_colour_moves_at_any_point_in_the_breath():
    """The property that makes the animation both safe and affordable.

    The palette's colours reach the screen by two routes: widgets that render
    their own ``Text`` read them live, and the stylesheet, where they are
    variables frozen until a ``refresh_css`` costing ~170 ms. A pulsed colour
    would move on one route and not the other — and the console has surfaces
    drawn both ways sitting against each other, the content frame (a CSS
    border) against the title plate inside it (box-drawing this module
    paints). Two nominally identical rules, one drifting, is a fault.

    So *every* colour is byte-identical at every phase, not merely the ones
    that happen to be in the stylesheet today. That is a property a rule added
    to the CSS next year cannot break.
    """
    for glow in range(dos.GLOW_MIN, dos.GLOW_MAX + 1):
        first = dos.resolve_palette(phosphor="aurora", glow=glow, phase=0.0)
        for phase in PHASES:
            painted = dos.resolve_palette(phosphor="aurora", glow=glow, phase=phase)
            for role in ROLES:
                assert painted[role] == first[role], (
                    f"glow {glow} phase {phase:.3f}: {role} moved, "
                    "which the stylesheet cannot follow"
                )


def test_the_cycle_closes_on_itself():
    """A phase that wraps to a different drive is a jump once per cycle."""
    pulse = dos.get_phosphor("aurora").pulse
    assert pulse.bloom_at(0.22, 0.0) == pytest.approx(pulse.bloom_at(0.22, 1.0))
    assert pulse.scan_offset_at(0.0) == pulse.scan_offset_at(1.0)


def test_the_pulse_never_leaves_the_drive_scale():
    """``bloom`` is a 0…1 fraction the indicator kits multiply by."""
    pulse = dos.get_phosphor("aurora").pulse
    assert pulse is not None
    for base in (0.0, 0.5, 1.0):
        for phase in PHASES:
            assert 0.0 <= pulse.bloom_at(base, phase) <= 1.0


def test_the_phase_is_reported_to_the_widgets_that_draw_on_it():
    animated = dos.resolve_palette(phosphor="aurora", phase=0.3)
    assert animated.optics["phase"] == pytest.approx(0.3)
    still = dos.resolve_palette(phosphor="azure", phase=0.3)
    assert still.optics["phase"] == 0.0, "a still tube reported a phase"


def test_the_phase_is_not_a_stored_preference():
    """It changes eight times a second; ``Optics`` is compared for equality.

    If the phase lived on the stored-preference object, the multi-window sync
    would see the settings change on every frame and re-apply them — which is
    a full re-skin, at 8 Hz.
    """
    assert "phase" not in dos.Optics().as_dict()
    left = dos.optics_of(dos.resolve_palette(phosphor="aurora", phase=0.0))
    right = dos.optics_of(dos.resolve_palette(phosphor="aurora", phase=0.5))
    assert left == right


# ── The console ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", BLUE)
def test_the_console_paints_in_each_blue_tube(name):
    async def scenario():
        app = _console(name)
        async with app.run_test(size=(112, 30)) as pilot:
            await _settle(pilot)
            assert app.bench_palette.dos
            assert dos.optics_of(app.bench_palette).phosphor == name
            # The tube's name reaches the title plate, which is where a reader
            # confirms which one they are looking at.
            painted = "\n".join(
                strip.text for strip in app.screen._compositor.render_strips()
            )
            assert dos.get_phosphor(name).title in painted

    _run(scenario())


def test_the_console_breathes_on_the_animated_tube_only():
    async def scenario():
        app = _console("aurora")
        async with app.run_test(size=(112, 30)) as pilot:
            await _settle(pilot)
            assert app.phosphor_is_animated

            colours = set()
            drives = set()
            for _ in range(24):
                app._pulse_phosphor()
                colours.add(app.bench_palette["primary"])
                drives.add(round(app.bench_palette.optics["bloom"], 6))
                await pilot.pause()
            assert len(drives) > 4, f"the console did not breathe: {drives}"
            assert len(colours) == 1, "a colour moved, and the stylesheet cannot follow"

            app._optics = dos.Optics(mode=dos.MODE_DOS, phosphor="azure")
            app.bench_palette = app._resolve_palette()
            assert not app.phosphor_is_animated
            still = set()
            for _ in range(24):
                app._pulse_phosphor()
                still.add(round(app.bench_palette.optics["bloom"], 6))
                await pilot.pause()
            assert len(still) == 1, f"a still tube moved: {still}"

    _run(scenario())


def test_the_pulse_does_nothing_at_all_outside_dos_mode():
    """The bench palette has no tube, so there is nothing to breathe."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(112, 30)) as pilot:
            await _settle(pilot)
            assert not app.phosphor_is_animated
            before = app.bench_palette
            for _ in range(8):
                app._pulse_phosphor()
            assert app.bench_palette is before

    _run(scenario())


def test_the_pulse_survives_a_console_that_is_coming_apart(monkeypatch):
    """It runs on a timer eight times a second; it cannot be allowed to raise."""

    async def scenario():
        app = _console("aurora")
        async with app.run_test(size=(112, 30)) as pilot:
            await _settle(pilot)

            def explode(*_a, **_k):
                raise RuntimeError("the palette is gone")

            monkeypatch.setattr(app, "_resolve_palette", explode)
            app._pulse_phosphor()  # must not raise
            await _settle(pilot)
            assert app.is_running

    _run(scenario())


def test_the_breath_is_cheap_enough_to_run_forever():
    """0.2 ms a frame is the budget the design was chosen for."""
    import time

    async def scenario():
        app = _console("aurora")
        async with app.run_test(size=(112, 30)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for index in range(80):
                pane.write("reply", f"line {index} of a conversation that wraps")
            await _settle(pilot)

            app._pulse_phosphor()
            start = time.perf_counter()
            for _ in range(50):
                app._pulse_phosphor()
            per_tick = (time.perf_counter() - start) / 50
            # Generous against the ~0.2 ms measured, because CI machines are
            # not this one. What it rules out is a tick that has quietly
            # started re-parsing the stylesheet, which is three orders of
            # magnitude slower.
            assert per_tick < 0.020, f"{per_tick * 1000:.1f} ms/tick"
            assert per_tick * PHOSPHOR_PULSE_HZ < 0.05, "over 5% of a core"

    _run(scenario())


# ── Chosen, saved, and read back ─────────────────────────────────────────


@pytest.mark.parametrize("name", BLUE)
def test_a_blue_tube_chosen_on_the_panel_is_applied_and_saved(name, tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))

    async def scenario():
        from textual.widgets import DataTable

        app = _console("amber")
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)

            table = app.query_one("#phosphor-table", DataTable)
            listed = [
                str(table.get_row_at(index)[0]).replace("▶", "").strip()
                for index in range(table.row_count)
            ]
            assert name in listed, listed
            row = listed.index(name)
            table.move_cursor(row=row)
            table.post_message(
                DataTable.RowSelected(
                    table, row, table.coordinate_to_cell_key((row, 0)).row_key
                )
            )
            await _settle(pilot)

            assert app._optics.phosphor == name
            assert dos.optics_of(app.bench_palette).phosphor == name
            assert read_settings().dos_phosphor == name
            assert app.phosphor_is_animated is dos.get_phosphor(name).animated

    _run(scenario())


@pytest.mark.parametrize("name", BLUE)
def test_a_blue_tube_survives_the_round_trip_through_config(name, tmp_path, monkeypatch):
    """The console has to open the way it was left."""
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    tmp_path.joinpath("config.yaml").write_text(
        f"ui:\n  skin_mode: dos\n  dos:\n    phosphor: {name}\n", encoding="utf-8"
    )
    settings = read_settings()
    assert settings.dos_phosphor == name
    assert settings.optics().phosphor == name
    assert dos.resolve_palette(phosphor=settings.dos_phosphor).skin_name == f"dos-{name}"


def test_a_tube_name_that_means_nothing_still_resolves():
    """These come out of a hand-edited YAML file."""
    for value in ("", None, "blue", "AURORA ", 17, object()):
        assert dos.get_phosphor(value).name in dos.PHOSPHOR_NAMES


# ── The sampler on the settings pane ─────────────────────────────────────


@pytest.mark.parametrize("name", BLUE)
def test_the_sampler_draws_every_brightness_of_a_blue_tube(name):
    drawn = dos.preview(name, 48)
    assert drawn.plain.count("\n") == dos.GLOW_MAX - dos.GLOW_MIN
    for label in dos.GLOW_LABELS:
        assert label in drawn.plain


def test_the_sampler_shows_brightness_and_leaves_the_breath_to_the_figure():
    """The sampler compares the five brightnesses; it is not the animation.

    What a pulse moves is the drive, and the drive is what the state figures
    walk their glyphs up — not what this sampler's colours are made of. The
    figure on the same page shows the breath.
    """
    import inspect

    assert "phase" not in inspect.signature(dos.preview).parameters
    for name in ("azure", "aurora"):
        drawn = dos.preview(name, 48)
        assert drawn.plain.count("\n") == dos.GLOW_MAX - dos.GLOW_MIN
