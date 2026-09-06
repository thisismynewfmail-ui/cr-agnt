"""A font file, chosen by the reader, lettering the console.

What this can and cannot be is the first thing to say, because it decides
what every test below is testing. A program running inside a terminal cannot
change the font its body text is drawn in: the glyphs are painted by the
terminal emulator out of the font *it* is configured with, one per cell, and
no portable escape sequence lets an application ask for another. What it can
do is letter with a real font wherever it draws type as a *picture* —
rasterise the outlines and paint the result into cells with the half-block
glyphs — and that is the console's display type: the title plate above the
conversation, and the sample on the PANEL pane.

So these hold three things:

* the resolution — ``default``, a path, a name in the font folders, and every
  way each of those can fail, all of which have to end at the built-in
  alphabet carrying a sentence rather than at a traceback;
* the rendering — that what comes out is half-blocks, fits the box it was
  given, and shortens the wordmark rather than shrinking the type into
  texture;
* the console — that a chosen font reaches the plate in both display modes,
  that F1 takes it away whatever state it was in, and that two windows agree.

A real font file is needed for most of it. One is found in the platform's own
font folders rather than committed here: shipping a third-party face in a test
directory is a licensing decision, and a font nobody chose is not a better
test than the one the machine already has.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.fonts import (  # noqa: E402
    DEFAULT_ROWS,
    MAX_ROWS,
    MIN_ROWS,
    FontFace,
    clamp_rows,
    forget_rendered,
    render_lettering,
    render_wordmark,
    resolve_face,
    system_fonts,
)
from curie_cli.bench_ui.panes import FontPreview  # noqa: E402
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_TYPEFACE,
    KEY_TYPEFACE_ROWS,
    read_settings,
    write_setting,
)
from curie_cli.bench_ui.typeface import CP437, DEFAULT_TYPEFACE  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402

#: Only these three glyphs and a space. A renderer that reached for anything
#: else would be drawing at a resolution the grid does not have.
HALF_BLOCKS = set(" ▀▄█")


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 6) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    forget_rendered()
    yield tmp_path
    forget_rendered()


@pytest.fixture(scope="session")
def a_font() -> tuple:
    """``(name, path)`` of some font on this machine, or skip.

    Skipped rather than faked: the thing under test is FreeType reading a real
    outline, and a stub that returns rectangles would pass every assertion
    here while telling us nothing about whether a font loads.
    """
    pytest.importorskip("PIL", reason="Pillow is what rasterises a font")
    found = system_fonts(limit=40)
    if not found:
        pytest.skip("no font files in this machine's font folders")
    return found[0]


def _console(mode: str = dos.MODE_BENCH) -> BenchConsole:
    app = BenchConsole(bridge=_StubBridge())
    app._display_mode = mode
    app._optics = dos.Optics(mode=mode)
    app.bench_palette = app._resolve_palette()
    return app


# ── Which face a setting names ───────────────────────────────────────────


@pytest.mark.parametrize("spec", ["default", "DEFAULT", " default ", "", None, 0])
def test_the_built_in_alphabet_is_what_nothing_resolves_to(spec):
    face = resolve_face(spec)
    assert face.custom is False
    assert face.title == CP437.title
    assert face.problem == ""


def test_a_font_file_is_loaded_and_names_itself(a_font):
    _name, path = a_font
    face = resolve_face(path)
    assert face.custom is True
    assert face.path == path
    assert face.source == "file"
    assert face.problem == ""
    # The family out of the font, not the file name off the disk: those two
    # disagree often enough that showing the wrong one is a real confusion.
    assert face.title and face.title.lower() != "default"


def test_a_font_is_found_by_name_in_the_font_folders(a_font):
    name, path = a_font
    face = resolve_face(name)
    assert face.custom is True
    assert face.source == "system"
    assert face.path == path


def test_a_name_is_matched_however_the_reader_writes_it(a_font):
    """Case, spaces, hyphens and underscores all fall out of the comparison.

    A reader who has seen a font's name in three places has seen it written
    three ways, and being told "no font called that" by a console that is
    looking straight at it is the kind of correct that is useless.
    """
    name, path = a_font
    variants = {
        name.lower(),
        name.upper(),
        name.replace("-", " "),
        name.replace("-", "_"),
        f"  {name}  ",
    }
    for variant in variants:
        assert resolve_face(variant).path == path, variant


def test_a_path_that_is_not_there_says_so_and_letters_anyway(tmp_path):
    face = resolve_face(tmp_path / "Nothing.ttf")
    assert face.custom is False, "a face that cannot letter must not claim to"
    assert face.title == CP437.title
    assert "no font file at" in face.problem
    assert "Nothing.ttf" in face.problem


def test_a_file_that_is_not_a_font_says_so(tmp_path):
    pytest.importorskip("PIL")
    impostor = tmp_path / "NotAFont.ttf"
    impostor.write_text("this is not a font", encoding="utf-8")
    face = resolve_face(impostor)
    assert face.custom is False
    assert "could not be read as a font" in face.problem


def test_a_name_nothing_has_says_where_to_put_one():
    face = resolve_face("a font nobody has installed")
    assert face.custom is False
    assert "no font called" in face.problem
    # The sentence has to carry the fix, because this is the failure a reader
    # who has downloaded a font and not installed it will hit.
    assert "~/.fonts" in face.problem


def test_the_label_names_the_face_and_the_file_it_came_from(a_font):
    _name, path = a_font
    label = resolve_face(path).label()
    assert label.endswith(")") and path.rsplit("/", 1)[-1] in label
    assert resolve_face("default").label() == CP437.title


# ── What comes out of the renderer ───────────────────────────────────────


def test_the_built_in_face_letters_nothing_of_its_own():
    """It is the alphabet the console already draws with, not a picture."""
    assert render_lettering(resolve_face("default"), "CURIE", rows=5) == []


def test_a_rendered_wordmark_is_half_blocks_and_nothing_else(a_font):
    _name, path = a_font
    drawn = render_lettering(resolve_face(path), "CURIE", rows=5, columns=90)
    assert drawn, "nothing was drawn"
    assert set("".join(drawn)) <= HALF_BLOCKS, set("".join(drawn)) - HALF_BLOCKS


def test_a_render_fits_the_box_it_was_given(a_font):
    """Both ways. A row wider than the plate pushes the frame off the window."""
    _name, path = a_font
    face = resolve_face(path)
    for rows, columns in ((2, 20), (5, 60), (8, 120)):
        drawn = render_lettering(face, "CURIE AGENT", rows=rows, columns=columns)
        assert len(drawn) <= rows, (rows, columns, len(drawn))
        for line in drawn:
            assert len(line) <= columns, (rows, columns, len(line))


def test_nothing_is_drawn_where_there_is_no_room(a_font):
    _name, path = a_font
    assert render_lettering(resolve_face(path), "CURIE", rows=5, columns=3) == []
    assert render_lettering(resolve_face(path), "   ", rows=5, columns=80) == []


def test_the_rows_share_one_origin(a_font):
    """They are slices of one image, so the left edge cannot wander.

    This is what makes centring a block-level decision, and the reason the
    consumers centre the *block*: centre each row on its own trimmed length
    instead and the wordmark shears into a diagonal — a bug that renders
    perfectly well, and so one only a test catches.

    Stated as the shift a leading space produces. Every row has to move by the
    same amount, because there is only one image and the space is in front of
    all of it; a renderer that trimmed each row separately would move the rows
    whose first glyph is inset by less, or not at all.
    """
    _name, path = a_font
    face = resolve_face(path)
    plain = render_lettering(face, "CURIE", rows=6, columns=120)
    shifted = render_lettering(face, " CURIE", rows=6, columns=120)
    assert plain and len(plain) == len(shifted)
    moves = {
        len(after) - len(before)
        for before, after in zip(plain, shifted)
        if before.strip()
    }
    assert len(moves) == 1, f"the rows moved by different amounts: {moves}"
    assert moves.pop() > 0, "a leading space moved nothing"


def test_the_wordmark_shortens_before_it_shrinks(a_font):
    """The rule that separates lettering from texture.

    Fitted to width, a long title in a narrow plate comes back at whatever
    size fitted — two pixels of cap height, a row of grey specks that is not a
    word. So a render that did not reach the asked-for height is refused and
    the next title down is tried, and every wordmark the console draws is the
    same size however narrow the window is.
    """
    _name, path = a_font
    face = resolve_face(path)
    titles = ("CURIE AGENT — BENCH TERMINAL", "CURIE AGENT", "CURIE")
    narrow = render_wordmark(face, titles, rows=5, columns=46)
    wide = render_wordmark(face, titles, rows=5, columns=200)
    assert len(narrow) >= 5 and len(wide) >= 5, (len(narrow), len(wide))
    assert max(len(line) for line in narrow) <= 46
    assert max(len(line) for line in wide) > max(len(line) for line in narrow), (
        "the wide plate drew no more of the wordmark than the narrow one"
    )


def test_a_wordmark_with_no_title_that_fits_draws_nothing(a_font):
    _name, path = a_font
    assert render_wordmark(resolve_face(path), ("CURIE",), rows=12, columns=8) == []


def test_the_same_plate_is_rendered_once(a_font, monkeypatch):
    """It re-letters on every resize; a reader dragging an edge walks a
    hundred widths a second, and each one is a rasterise."""
    _name, path = a_font
    face = resolve_face(path)
    first = render_lettering(face, "CURIE", rows=5, columns=80)
    import curie_cli.bench_ui.fonts as module

    def _boom(*_a, **_k):
        raise AssertionError("the same plate was rasterised twice")

    monkeypatch.setattr(module, "_render", _boom)
    assert render_lettering(face, "CURIE", rows=5, columns=80) == first


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, DEFAULT_ROWS),
        ("", DEFAULT_ROWS),
        ("nonsense", DEFAULT_ROWS),
        (0, MIN_ROWS),
        (-4, MIN_ROWS),
        (999, MAX_ROWS),
        (6, 6),
        ("7", 7),
    ],
)
def test_a_stored_plate_height_is_made_safe(value, expected):
    assert clamp_rows(value) == expected


def test_the_font_list_is_pairs_that_exist():
    import os

    found = system_fonts(limit=25)
    assert len(found) <= 25
    for name, path in found:
        assert name and not name.endswith(".ttf")
        assert os.path.isfile(path), path


# ── Without Pillow ───────────────────────────────────────────────────────
#
# Pillow is a core dependency of Curie and the thing that rasterises a font,
# but "core" is a statement about the install, not about the machine the
# console is running on: an install repaired by hand, a partial wheel, a
# distribution that split the package. The console has to open in all of them.


@pytest.fixture
def no_pillow(monkeypatch):
    import curie_cli.bench_ui.fonts as module

    monkeypatch.setattr(module, "_pillow", lambda: None)
    forget_rendered()
    return module


def test_without_pillow_a_font_says_so_rather_than_failing(no_pillow, tmp_path):
    stand_in = tmp_path / "Anything.ttf"
    stand_in.write_bytes(b"\x00\x01\x00\x00")
    face = resolve_face(stand_in)
    assert face.custom is False
    assert "Pillow is not installed" in face.problem
    # And the sentence carries the fix, because it is one command.
    assert "curie update" in face.problem


def test_without_pillow_nothing_is_lettered(no_pillow, tmp_path):
    face = FontFace(spec="x", title="X", path=str(tmp_path / "x.ttf"), source="file")
    assert render_lettering(face, "CURIE", rows=5, columns=80) == []
    assert render_wordmark(face, ("CURIE",), rows=5, columns=80) == []


def test_without_pillow_the_console_still_opens(no_pillow, tmp_path):
    stand_in = tmp_path / "Anything.ttf"
    stand_in.write_bytes(b"\x00\x01\x00\x00")
    write_setting(KEY_TYPEFACE, str(stand_in))

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            assert app.is_running
            rows = _plate_rows(app)
            assert not _lettered(rows)
            assert "CURIE" in "".join(rows)

    _run(scenario())


# ── The console ──────────────────────────────────────────────────────────


def _plate_rows(app) -> list[str]:
    strips = app.screen._compositor.render_strips()
    region = app.query_one("#masthead").region
    return [
        "".join(segment.text for segment in strips[y])[
            region.x : region.x + region.width
        ]
        for y in range(region.y, min(region.y + region.height, len(strips)))
    ]


def _lettered(rows: list[str]) -> bool:
    return any(any(glyph in row for glyph in "▀▄█") for row in rows)


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_a_chosen_font_letters_the_title_plate(a_font, mode):
    _name, path = a_font

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            before = _plate_rows(app)
            assert not _lettered(before), "the built-in plate is set in text"
            assert "CURIE" in "".join(before)

            app._set_typeface(path)
            await _settle(pilot, 10)
            assert app._face.custom and not app._face.problem
            after = _plate_rows(app)
            assert _lettered(after), after
            assert len(after) > len(before), "the plate did not make room"

    _run(scenario())


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_lettered_plate_still_ends_where_the_pane_does(a_font, mode):
    """The invariant the plate has always had, now with a picture in it.

    A row wider than the box does not wrap — it puts a corner of the frame
    past the edge of the window, which reads as a rendering fault rather than
    as a narrow window.
    """
    _name, path = a_font

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app._set_typeface(path)
            await _settle(pilot, 10)
            plate = app.query_one("#masthead")
            width = plate.region.width
            for row in _plate_rows(app):
                assert len(row) == width, (len(row), width, repr(row))

    _run(scenario())


def test_f1_takes_a_loaded_font_away(a_font):
    _name, path = a_font

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            app._set_typeface(path)
            await _settle(pilot, 10)
            assert _lettered(_plate_rows(app))

            await pilot.press("f1")
            await _settle(pilot, 10)
            assert app._typeface == DEFAULT_TYPEFACE
            assert app._face.custom is False
            assert read_settings().typeface == DEFAULT_TYPEFACE
            rows = _plate_rows(app)
            assert not _lettered(rows)
            assert "CURIE" in "".join(rows)

    _run(scenario())


def test_f1_takes_away_a_font_that_never_loaded():
    """The state a reader is most likely to want undone, and the one where
    the console has the least to go on: nothing is lettered, so nothing on
    screen shows the setting is even set."""

    async def scenario():
        write_setting(KEY_TYPEFACE, "/nowhere/Ghost.ttf")
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            assert app._face.problem
            await pilot.press("f1")
            await _settle(pilot, 8)
            assert app._typeface == DEFAULT_TYPEFACE
            assert read_settings().typeface == DEFAULT_TYPEFACE
            assert app._face.problem == ""

    _run(scenario())


def test_a_font_that_will_not_load_leaves_the_console_lettering(tmp_path):
    """It says why, on the notice line, and draws the plate it always drew."""

    async def scenario():
        impostor = tmp_path / "Broken.ttf"
        impostor.write_text("not a font", encoding="utf-8")
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            app._set_typeface(str(impostor))
            await _settle(pilot, 8)
            assert app._face.problem
            rows = _plate_rows(app)
            assert not _lettered(rows)
            assert "CURIE" in "".join(rows), "the plate lost its title as well"

    _run(scenario())


def test_the_plate_height_control_moves_and_stops(a_font):
    _name, path = a_font

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 44)) as pilot:
            await _settle(pilot)
            app._set_typeface(path)
            await _settle(pilot, 8)
            start = len(_plate_rows(app))

            app.run_keyline_action("typeface-taller")
            app.run_keyline_action("typeface-taller")
            await _settle(pilot, 10)
            assert app._typeface_rows == DEFAULT_ROWS + 2
            assert len(_plate_rows(app)) > start
            assert read_settings().typeface_rows == DEFAULT_ROWS + 2

            for _ in range(MAX_ROWS + 4):
                app.run_keyline_action("typeface-taller")
            await _settle(pilot)
            assert app._typeface_rows == MAX_ROWS
            for _ in range(MAX_ROWS + 4):
                app.run_keyline_action("typeface-shorter")
            await _settle(pilot)
            assert app._typeface_rows == MIN_ROWS

    _run(scenario())


def test_the_panel_marks_the_font_in_force_and_previews_it(a_font):
    name, path = a_font

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 44)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            app._set_typeface(path)
            await _settle(pilot, 10)

            from textual.widgets import DataTable

            table = app.query_one("#font-table", DataTable)
            marked = [
                str(table.get_row_at(index)[0])
                for index in range(table.row_count)
                if "▶" in str(table.get_row_at(index)[0])
            ]
            assert len(marked) == 1, marked
            assert name in marked[0] or path.rsplit("/", 1)[-1] in marked[0]

            preview = app.query_one("#font-preview", FontPreview)
            assert _lettered(preview.render().plain.split("\n"))

            await pilot.press("f1")
            await _settle(pilot, 10)
            table = app.query_one("#font-table", DataTable)
            marked = [
                str(table.get_row_at(index)[0])
                for index in range(table.row_count)
                if "▶" in str(table.get_row_at(index)[0])
            ]
            assert marked == ["▶ default"], marked

    _run(scenario())


def test_a_second_window_catches_up_with_the_font(a_font):
    _name, path = a_font

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            assert not app._face.custom

            write_setting(KEY_TYPEFACE, path)
            write_setting(KEY_TYPEFACE_ROWS, 7)
            app._adopt_external_settings()
            await _settle(pilot, 10)

            assert app._face.custom and app._face.path == path
            assert app._typeface_rows == 7
            assert _lettered(_plate_rows(app))

    _run(scenario())


def test_the_face_survives_a_restart(a_font):
    _name, path = a_font

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot)
            app._set_typeface(path)
            await _settle(pilot, 8)

        second = _console()
        async with second.run_test(size=(150, 40)) as pilot:
            await _settle(pilot, 8)
            assert second._face.custom and second._face.path == path
            assert _lettered(_plate_rows(second))

    _run(scenario())


def test_the_settings_object_resolves_its_own_face(a_font):
    _name, path = a_font
    write_setting(KEY_TYPEFACE, path)
    settings = read_settings()
    assert settings.typeface == path
    face = settings.face()
    assert isinstance(face, FontFace) and face.path == path
