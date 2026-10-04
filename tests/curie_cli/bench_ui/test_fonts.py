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

A real font file is needed for most of it, and there are two kinds here. One
is whatever the platform's own font folders have, which is what most readers
will pick. The other is made by :mod:`tests.curie_cli.bench_ui.fontmaker` —
small, complete TrueType files built from a pixel alphabet — because the
fonts that break things are not the ones a test machine has: a face whose
family name is not its file name, one with no lowercase, no dash or no space,
one encoded only for the Windows symbol page or the classic Mac, one too wide
for its plate, and the zip they arrive in. Shipping a third-party face in a
test directory would be a licensing decision; these belong to nobody.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

import os  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui import fonts as fonts_module  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.fonts import (  # noqa: E402
    DEFAULT_ROWS,
    LEGIBLE_ROWS,
    MAX_ROWS,
    MIN_ROWS,
    TIER_CONSOLE,
    TIER_PERSONAL,
    TIER_SYSTEM,
    FontFace,
    clamp_rows,
    curie_font_dir,
    display_font_dir,
    forget_rendered,
    parse_fontconfig_listing,
    plan_wordmark,
    read_font_names,
    render_lettering,
    render_wordmark,
    resolve_face,
    system_fonts,
    wsl_path,
)
from curie_cli.bench_ui.panes import FontPreview  # noqa: E402
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_TYPEFACE,
    KEY_TYPEFACE_ROWS,
    read_settings,
    write_setting,
)
from curie_cli.bench_ui.typeface import CP437, DEFAULT_TYPEFACE  # noqa: E402

from tests.curie_cli.bench_ui.fontmaker import build_font, build_woff  # noqa: E402
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


@pytest.fixture
def a_font() -> tuple:
    """``(name, path)`` of some font on this machine, or skip.

    Skipped rather than faked: the thing under test is FreeType reading a real
    outline, and a stub that returns rectangles would pass every assertion
    here while telling us nothing about whether a font loads. Per test rather
    than per session, because the list includes the console's own font
    folder, which is under each test's own ``CURIE_HOME``.
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
    # who has downloaded a font and not installed it will hit: where to drop
    # it, and that the zip it came in will do.
    assert display_font_dir() in face.problem
    assert ".zip" in face.problem


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


# ── Fonts the way they arrive ────────────────────────────────────────────
#
# Everything below runs on fonts made for it (see fontmaker), so it holds on a
# machine with no fonts at all — which is exactly the machine on which the
# section above is skipped.

#: Every inked half-block, counted in pixels: a full block is two.
def _ink(lines) -> int:
    return sum(2 if glyph == "█" else 1 for line in lines for glyph in line if glyph in "▀▄█")


@pytest.fixture
def made(tmp_path):
    """Build a font into this test's own folder: ``made("Name.ttf", **kw)``."""
    pytest.importorskip("PIL", reason="Pillow is what rasterises a font")

    def build(name: str = "Odd-File-Name.ttf", folder: "Path | None" = None, **kwargs) -> Path:
        return build_font((folder or tmp_path / "made") / name, **kwargs)

    return build


@pytest.fixture
def console_fonts() -> Path:
    """The console's own font folder, under this test's ``CURIE_HOME``."""
    folder = curie_font_dir()
    assert folder is not None
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _zip(path: Path, members: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path


def test_a_font_answers_to_the_name_inside_it(made, console_fonts):
    """The bug a font like Irken hits: the file is called one thing and the
    font another. Font managers, word processors and the font's own readme
    all say "Irken"; the file says "Irken-Like-AllCaps". Both have to work."""
    path = made("Odd-File-Name.ttf", folder=console_fonts, family="Glyphic Test")
    for spec in ("Glyphic Test", "glyphic test", "GLYPHIC-TEST", "glyphic_test", "Odd-File-Name"):
        face = resolve_face(spec)
        assert face.custom and face.path == str(path), (spec, face.problem)
        assert face.source == "system"


def test_a_family_name_settles_on_the_plain_face(made, console_fonts):
    """A family's bold sorts before its regular; the family name still means
    the regular one, and the exact name still means the bold. The files are
    named for neither, so only the names inside them can find them."""
    bold = made("a1.ttf", folder=console_fonts, family="Fam", style="Bold")
    regular = made("a2.ttf", folder=console_fonts, family="Fam", style="Regular")
    assert resolve_face("Fam").path == str(regular)
    assert resolve_face("Fam Bold").path == str(bold)


def test_a_bare_file_name_is_looked_for_in_the_font_folders(made, console_fonts):
    path = made("Odd-File-Name.ttf", folder=console_fonts)
    face = resolve_face("Odd-File-Name.ttf")
    assert face.custom and face.path == str(path)


def test_the_names_a_font_carries_are_read_out_of_it(made):
    names = read_font_names(made(family="Glyphic Test", style="Bold"))
    assert names.family == "Glyphic Test"
    assert names.style == "Bold"
    assert names.display == "Glyphic Test Bold"
    assert names.regular is False


def test_a_font_in_the_zip_it_was_downloaded_in_letters(made, tmp_path):
    """The attachment shape: a folder in a zip, a readme beside the font, and
    the ``__MACOSX`` shadow a Mac's archiver adds — named like the font and
    not one."""
    regular = made("Glyphic-Regular.ttf", family="Glyphic")
    bold = made("Glyphic-Bold.ttf", family="Glyphic", style="Bold")
    archive = _zip(
        tmp_path / "downloads" / "glyphic.zip",
        {
            "Glyphic/Glyphic-Bold.ttf": bold.read_bytes(),
            "Glyphic/Glyphic-Regular.ttf": regular.read_bytes(),
            "Glyphic/readme.txt": "Your font download is ready.",
            "__MACOSX/Glyphic/._Glyphic-Regular.ttf": b"\x00\x05\x16\x07 not a font",
        },
    )
    face = resolve_face(archive)
    assert face.custom, face.problem
    assert face.archive == str(archive)
    assert Path(face.path).name == "Glyphic-Regular.ttf", "the regular face is the one meant"
    assert "Glyphic" in face.title and "glyphic.zip" in face.label()
    # Unpacked into this console's cache, never next to the download.
    assert Path(face.path).is_relative_to(Path(os.environ["CURIE_HOME"]))
    assert render_lettering(face, "CURIE", rows=5, columns=200)


def test_a_zip_with_no_font_in_it_says_so(tmp_path):
    archive = _zip(tmp_path / "empty.zip", {"readme.txt": "no font here"})
    face = resolve_face(archive)
    assert not face.custom and "no font file inside" in face.problem


def test_a_file_named_zip_that_is_not_one_says_so(tmp_path):
    fake = tmp_path / "fake.zip"
    fake.write_text("not a zip", encoding="utf-8")
    face = resolve_face(fake)
    assert not face.custom and "not a zip archive" in face.problem


def test_a_zip_dropped_in_the_console_folder_is_listed_by_the_font_s_name(made, console_fonts):
    font = made("Glyphic-Regular.ttf", family="Glyphic Drop")
    archive = _zip(console_fonts / "glyphic-drop.zip", {"Pack/Glyphic-Regular.ttf": font.read_bytes()})
    assert ("Glyphic Drop", str(archive)) in system_fonts()
    face = resolve_face("Glyphic Drop")
    assert face.custom and face.archive == str(archive)
    # And by what the download was called.
    assert resolve_face("glyphic-drop").archive == str(archive)


def test_the_folder_a_download_was_unpacked_into_letters_its_regular_face(made, tmp_path):
    folder = tmp_path / "Unpacked"
    made("Glyphic-Bold.ttf", folder=folder / "fonts", family="Glyphic", style="Bold")
    regular = made("Glyphic-Regular.ttf", folder=folder / "fonts", family="Glyphic")
    (folder / "readme.txt").write_text("hello", encoding="utf-8")
    assert resolve_face(folder).path == str(regular)


def test_a_path_in_quotes_or_as_a_file_address_is_the_same_path(made, tmp_path):
    """What a path looks like after "Copy as path" on Windows, or after a
    file is dragged into a terminal: quoted, or a ``file://`` address with
    its spaces escaped."""
    path = made("Odd File.ttf", folder=tmp_path / "My Fonts")
    for spec in (f'"{path}"', f"'{path}'", path.as_uri(), f"  {path}  "):
        face = resolve_face(spec)
        assert face.custom and face.path == str(path), (spec, face.problem)


def test_a_woff_font_letters(made, tmp_path):
    face = resolve_face(build_woff(made(), tmp_path / "web" / "Glyphic.woff"))
    assert face.custom, face.problem
    assert render_lettering(face, "CURIE", rows=5, columns=200)


# ── Where the folders are, per platform ──────────────────────────────────
#
# A pure function of the platform it is handed, so every platform's list is
# checked here without pretending to be that platform.


def test_windows_looks_where_a_right_click_install_puts_a_font(tmp_path):
    local = tmp_path / "Local"
    folders = fonts_module._folders_for(
        platform="win32",
        environ={"LOCALAPPDATA": str(local), "WINDIR": str(tmp_path / "Windows")},
        home=tmp_path / "me",
        curie_dir=tmp_path / "curie" / "fonts",
    )
    where = dict(folders)
    personal = local / "Microsoft" / "Windows" / "Fonts"
    assert where.get(personal) == TIER_PERSONAL
    assert where.get(tmp_path / "Windows" / "Fonts") == TIER_SYSTEM
    order = [path for path, _tier in folders]
    assert order.index(personal) < order.index(tmp_path / "Windows" / "Fonts")


def test_wsl_looks_on_the_windows_side_as_well(tmp_path):
    drive = tmp_path / "mnt" / "c"
    for profile in ("alice", "Public"):
        (drive / "Users" / profile / "AppData/Local/Microsoft/Windows/Fonts").mkdir(parents=True)
    (drive / "Windows" / "Fonts").mkdir(parents=True)
    folders = dict(
        fonts_module._folders_for(
            platform="linux", environ={}, home=tmp_path / "home", windows_roots=[drive]
        )
    )
    assert folders.get(drive / "Users/alice/AppData/Local/Microsoft/Windows/Fonts") == TIER_PERSONAL
    assert drive / "Users/Public/AppData/Local/Microsoft/Windows/Fonts" not in folders
    assert folders.get(drive / "Windows" / "Fonts") == TIER_SYSTEM


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_the_console_folder_comes_first_and_the_system_s_last(tmp_path, platform):
    folders = fonts_module._folders_for(
        platform=platform,
        environ={"XDG_DATA_DIRS": "/opt/share", "LOCALAPPDATA": str(tmp_path / "L")},
        home=tmp_path / "home",
        curie_dir=tmp_path / "curie" / "fonts",
    )
    tiers = [tier for _path, tier in folders]
    assert tiers[0] == TIER_CONSOLE
    assert tiers == sorted(tiers), "a more personal folder came after a less personal one"
    assert len({str(path) for path, _tier in folders}) == len(folders)


def test_a_windows_path_reads_as_wsl_sees_it():
    assert wsl_path(r"C:\Users\me\Downloads\Irken\Irken.ttf") == Path(
        "/mnt/c/Users/me/Downloads/Irken/Irken.ttf"
    )
    assert wsl_path("D:/fonts/x.otf") == Path("/mnt/d/fonts/x.otf")
    assert wsl_path("/home/me/x.ttf") is None


def test_fontconfig_s_list_is_read_into_tiers():
    listing = "/home/me/.local/share/fonts/A.ttf\n/usr/share/fonts/B.otf\n/usr/share/fonts/notes.txt\n\n"
    found = parse_fontconfig_listing(listing, home="/home/me")
    assert found == [
        ("/home/me/.local/share/fonts/A.ttf", TIER_PERSONAL),
        ("/usr/share/fonts/B.otf", TIER_SYSTEM),
    ]


# ── Fonts that are odd inside ────────────────────────────────────────────


@pytest.mark.parametrize("encoding", ["symbol", "mac"])
def test_a_font_with_no_unicode_map_letters_its_glyphs_not_empty_boxes(made, encoding):
    """Old free fonts are often encoded only for the Windows symbol page or
    the classic Mac. FreeType will not map text onto either by itself, so
    every letter comes out as the font's empty box — a row of identical
    rectangles, which is what "the font does not work" looks like."""
    face = resolve_face(made(f"{encoding}.ttf", encoding=encoding))
    assert face.custom, face.problem

    def letter(text):
        return render_lettering(face, text, rows=6, columns=400)

    # Through the empty box every letter is the same rectangle, so two
    # different letters would come out the same in either order, and a
    # slender letter would carry as much ink as a heavy one.
    assert letter("CU") and letter("CU") != letter("UC")
    assert 0 < _ink(letter("III")) < _ink(letter("MMM"))


def test_a_dash_the_face_lacks_becomes_a_hyphen(made):
    face = resolve_face(made(chars="ABCDEFGHIJKLMNOPQRSTUVWXYZ-"))
    assert render_lettering(face, "AB \u2014 CD", rows=5, columns=300) == render_lettering(
        face, "AB - CD", rows=5, columns=300
    )


def test_a_character_with_no_stand_in_is_left_out_not_drawn_as_a_box(made):
    face = resolve_face(made(chars="ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    assert render_lettering(face, "AB \u2014 CD", rows=5, columns=300) == render_lettering(
        face, "AB CD", rows=5, columns=300
    )


def test_a_face_with_one_case_letters_the_other(made):
    face = resolve_face(made(lowercase=False))
    assert render_lettering(face, "curie", rows=5, columns=300) == render_lettering(
        face, "CURIE", rows=5, columns=300
    )


def test_a_face_with_no_space_still_spaces_its_words(made):
    """A space the face cannot draw would be drawn as its empty box. The
    words are set apart instead — so the gap adds width and not one pixel."""
    face = resolve_face(made(space=False))
    spaced = render_lettering(face, "AB CD", rows=5, columns=300)
    solid = render_lettering(face, "ABCD", rows=5, columns=300)
    assert spaced and solid
    assert _ink(spaced) == _ink(solid)
    assert max(map(len, spaced)) > max(map(len, solid))


def test_a_face_with_none_of_the_title_s_letters_says_so(made):
    face = resolve_face(made(chars="0123"))
    plan = plan_wordmark(face, ("CURIE",), rows=5, columns=200)
    assert not plan.lines and plan.reason == "glyphs"


# ── How a wordmark fits its plate ────────────────────────────────────────


@pytest.mark.parametrize("rows", [2, 3, 5, 8, 12])
def test_a_plate_fills_the_rows_it_asked_for(made, rows):
    """Measured on the ink, not on FreeType's boxes — which count a curve's
    control points and so stand a pixel or two taller than the letters, and
    at ten pixels of plate that is a row of the five asked for left empty.
    The face is domed for exactly that reason: its boxes over-state its ink
    the way a real font's round letters do."""
    face = resolve_face(made(dome=True))
    assert len(render_lettering(face, "CURIE", rows=rows, columns=400)) == rows


def test_the_ladder_shortens_before_it_shrinks(made):
    face = resolve_face(made())
    titles = ("CURIE AGENT", "CURIE")
    long_width = max(map(len, render_lettering(face, "CURIE AGENT", rows=5, columns=1000)))
    short_width = max(map(len, render_lettering(face, "CURIE", rows=5, columns=1000)))
    between = (long_width + short_width) // 2
    plan = plan_wordmark(face, titles, rows=5, columns=between)
    assert plan.title == "CURIE" and plan.rows == 5 and not plan.shrunk
    assert plan_wordmark(face, titles, rows=5, columns=long_width).title == "CURIE AGENT"


def test_a_plate_too_narrow_for_its_height_letters_shorter_rather_than_not_at_all(made):
    """The failure that looked like a font doing nothing: a plate made taller
    than the window could take, or a face too wide for a narrow window, and
    the plate quietly went back to the built-in title."""
    face = resolve_face(made(width_scale=3))
    full_width = max(map(len, render_lettering(face, "CURIE", rows=8, columns=2000)))
    plan = plan_wordmark(face, ("CURIE AGENT", "CURIE"), rows=8, columns=full_width - 10)
    assert plan.lines, plan.reason
    assert plan.shrunk and LEGIBLE_ROWS <= plan.rows < 8
    assert plan.title == "CURIE"
    assert max(map(len, plan.lines)) <= full_width - 10


def test_shrinking_stops_where_letters_stop_being_letters(made):
    face = resolve_face(made(width_scale=3))
    plan = plan_wordmark(face, ("CURIE",), rows=8, columns=12)
    assert not plan.lines and plan.reason == "narrow"


# ── The console, with fonts as they arrive ───────────────────────────────


def test_a_font_from_a_zip_letters_the_plate_and_f1_puts_the_built_in_back(made, tmp_path):
    archive = _zip(tmp_path / "glyphic.zip", {"Glyphic/Glyphic.ttf": made().read_bytes()})
    write_setting(KEY_TYPEFACE, str(archive))

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 40)) as pilot:
            await _settle(pilot, 8)
            assert app._face.custom and app._face.archive == str(archive)
            assert _lettered(_plate_rows(app))

            await pilot.press("f1")
            await _settle(pilot, 8)
            assert app._face.custom is False
            assert read_settings().typeface == DEFAULT_TYPEFACE
            rows = _plate_rows(app)
            assert not _lettered(rows) and "CURIE" in "".join(rows)

    _run(scenario())


def test_a_plate_made_too_tall_for_the_window_still_letters(made):
    """TALLER past what the window can take used to make the font vanish."""
    write_setting(KEY_TYPEFACE, str(made()))
    write_setting(KEY_TYPEFACE_ROWS, MAX_ROWS)

    async def scenario():
        app = _console()
        async with app.run_test(size=(100, 40)) as pilot:
            await _settle(pilot, 8)
            rows = _plate_rows(app)
            assert _lettered(rows), rows
            width = app.query_one("#masthead").region.width
            assert all(len(row) == width for row in rows)

            app.show_pane("panel")
            await _settle(pilot, 4)
            readout = str(app.query_one("#font-readout").render())
            assert f"PLATE {MAX_ROWS} rows" in readout and "fit" in readout, readout
            # The short form beside the control, and the sentence under it.
            note = str(app.query_one("#font-note").render())
            assert f"{MAX_ROWS} do not fit this window's width" in note, note

    _run(scenario())


def test_the_font_list_is_read_when_the_panel_is_first_shown(made, console_fonts):
    """Reading it opens every font on the machine for its name, which is not
    a cost to pay at start-up for a table nobody has looked at."""
    made("Glyphic-Regular.ttf", folder=console_fonts, family="Glyphic Panel")

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 44)) as pilot:
            await _settle(pilot)
            from textual.widgets import DataTable

            pane = app.query_one("#pane-panel")
            table = app.query_one("#font-table", DataTable)
            assert pane.fonts_stale and table.row_count == 0

            app.show_pane("panel")
            await _settle(pilot)
            assert not pane.fonts_stale
            names = [str(table.get_row_at(index)[0]).strip() for index in range(table.row_count)]
            # The console's own folder is listed straight after the built-in.
            assert names[0].endswith("default")
            assert names[1] == "Glyphic Panel", names[:4]

    _run(scenario())


def test_selecting_a_dropped_in_font_letters_the_plate(made, console_fonts):
    made("Glyphic-Regular.ttf", folder=console_fonts, family="Glyphic Pick")

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 44)) as pilot:
            await _settle(pilot)
            from textual.widgets import DataTable

            app.show_pane("panel")
            await _settle(pilot)
            table = app.query_one("#font-table", DataTable)
            index = next(
                row
                for row in range(table.row_count)
                if str(table.get_row_at(row)[0]).strip() == "Glyphic Pick"
            )
            table.focus()
            table.move_cursor(row=index)
            await _settle(pilot)
            await pilot.press("enter")
            await _settle(pilot, 8)

            assert app._face.custom and app._face.title.startswith("Glyphic Pick")
            # Written down by the name inside the font, which is what another
            # machine with the same font in another folder still resolves.
            assert read_settings().typeface == "Glyphic Pick"
            marked = [
                str(table.get_row_at(row)[0])
                for row in range(table.row_count)
                if "▶" in str(table.get_row_at(row)[0])
            ]
            assert marked == ["▶ Glyphic Pick"]

            app.show_pane("bench")
            await _settle(pilot, 8)
            assert _lettered(_plate_rows(app))

    _run(scenario())


def test_a_folder_path_is_searched_a_few_levels_down_not_to_the_bottom(made, tmp_path):
    """A path to a folder is a download, unpacked — a folder or two deep. It
    could as easily be a home folder, which must not be walked to the bottom
    before the console can open."""
    shallow = tmp_path / "shallow"
    font = made("Glyphic-Regular.ttf", folder=shallow / "Glyphic" / "ttf")
    assert resolve_face(shallow).path == str(font)

    deep = tmp_path / "deep"
    made("Glyphic-Regular.ttf", folder=deep / "a" / "b" / "c" / "d" / "e")
    face = resolve_face(deep)
    assert not face.custom and "no font file in" in face.problem


def test_the_console_font_folder_exists_once_the_panel_is_shown():
    """The panel says to drop a font in it, so it has to be there to find."""
    folder = curie_font_dir()
    assert folder is not None and not folder.exists()

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 44)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            assert folder.is_dir()

    _run(scenario())


def test_a_font_chosen_with_the_plate_hidden_says_where_the_plate_went(made):
    """F10 puts the chrome away, the plate with it — and every notice, so the
    one place left to say why a chosen font changed nothing is the panel."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(150, 44)) as pilot:
            await _settle(pilot)
            await pilot.press("f10")
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            app._set_typeface(str(made()))
            await _settle(pilot, 6)
            note = str(app.query_one("#font-note").render())
            assert "F10 brings it back" in note, note

    _run(scenario())


def test_a_face_drawn_only_as_bitmaps_letters_at_its_own_sizes(made, monkeypatch):
    """A face made of bitmaps loads at the sizes it was cut at and refuses
    every other, so a search over sizes finds nothing to load at all. Its
    own sizes are tried instead, and the largest whose ink fits is used."""
    face = resolve_face(made())
    real = fonts_module._load
    strikes = {12, 24}

    def strike_only(path, stamp, size, encoding):
        if size not in strikes:
            raise OSError("invalid pixel size")
        return real(path, stamp, size, encoding)

    monkeypatch.setattr(fonts_module, "_load", strike_only)
    forget_rendered()
    short = render_lettering(face, "CURIE", rows=5, columns=400)
    tall = render_lettering(face, "CURIE", rows=10, columns=400)
    assert short and len(short) <= 5
    assert tall and len(short) < len(tall) <= 10
