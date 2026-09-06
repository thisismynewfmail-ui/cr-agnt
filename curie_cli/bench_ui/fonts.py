"""Real font files, lettered into character cells.

What a terminal program can and cannot do with a font, stated plainly,
because the boundary decides everything in this module:

* It **cannot** change the font its body text is drawn in. The glyphs on the
  screen are painted by the terminal emulator out of the font *it* is
  configured with, one glyph per cell, and no escape sequence in any portable
  terminal lets an application ask for another. A chat transcript in Irken is
  not a thing this console can refuse to do well — it is a thing no terminal
  program can do at all.
* It **can** letter with a real font anywhere it draws type as a *picture*
  rather than as text: rasterise the outlines and paint the result into cells
  with the half-block glyphs. Two vertical sub-pixels per cell, one
  horizontal — which is square, because a terminal cell is about twice as
  tall as it is wide.

So that is what this does. A font file named in ``ui.typeface`` is loaded and
used for the console's *display lettering* — the title plate's wordmark, and
the sample on the PANEL pane — and F1 puts the built-in lettering back. The
body of the interface stays in the terminal's own font, and the PANEL pane
says so rather than leaving the reader to wonder why their font only reached
half the screen.

Everything here is total. A font that has been deleted since it was chosen, a
file that is not a font, a Pillow build without FreeType, no Pillow at all —
each resolves to the built-in lettering carrying a sentence saying which,
because a console that will not start over an appearance setting is worse
than one that starts in its own face.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Sequence, Tuple

#: Suffixes FreeType reads. ``.ttc``/``.otc`` are collections — FreeType opens
#: the first face, which is the one a reader naming the file means.
FONT_SUFFIXES: Tuple[str, ...] = (".ttf", ".otf", ".ttc", ".otc", ".pfb")

#: Where a font lives on each platform, most personal first: a font the reader
#: dropped in their own folder should win over one the system shipped under the
#: same name, because they put it there on purpose.
FONT_DIRS: Tuple[str, ...] = (
    "~/.fonts",
    "~/.local/share/fonts",
    "~/Library/Fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "/usr/local/share/fonts",
    "/usr/share/fonts",
    "C:/Windows/Fonts",
)

#: How tall the lettered plate is, in rows, and the range the control moves in.
#: Five is the default because it is two and a half rows of pixels per row of
#: type — enough for a cap height, an x-height and a descender to be told
#: apart — and because it is the most a title plate can take from a chat
#: window without the conversation noticing.
DEFAULT_ROWS = 5
MIN_ROWS = 2
MAX_ROWS = 12

#: Coverage at which a sub-pixel counts as ink, 0-255. Half way: a threshold
#: that leans light loses the thin strokes of a serif face, and one that leans
#: dark fattens a bold one into a blob.
INK = 128

#: Rendered plates, keyed by everything that changes one. Bounded because the
#: console re-letters on every resize and a reader dragging a window edge
#: walks through a hundred widths in a second.
_CACHE: dict = {}
_CACHE_MAX = 64


@dataclass(frozen=True)
class FontFace:
    """A lettering face: the built-in alphabet, or a font file on disk."""

    #: What was asked for, as stored in ``ui.typeface``.
    spec: str
    #: What to call it on screen — the font's own family and style when it
    #: loaded, the built-in face's name when it did not.
    title: str
    #: The file it was loaded from. Empty for the built-in face.
    path: str = ""
    #: Where it was found: ``builtin``, ``file`` (a path the reader gave) or
    #: ``system`` (a family name found in the font folders).
    source: str = "builtin"
    #: Why it could not be used, as a sentence. Empty when it loaded.
    problem: str = ""

    @property
    def custom(self) -> bool:
        """Whether this is a real font file rather than the built-in face."""
        return bool(self.path)

    def label(self) -> str:
        """One line for the panel and the notice line."""
        if not self.custom:
            return self.title
        return f"{self.title}  ({Path(self.path).name})"


def _pillow() -> "tuple[Any, Any, Any] | None":
    """``(Image, ImageDraw, ImageFont)``, or ``None`` when unavailable.

    Imported here rather than at module scope: this module is imported while
    the console's settings are read, which is before the first frame, and
    Pillow is a heavy import to pay for on a console nobody has set a font on.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        return Image, ImageDraw, ImageFont
    except Exception:
        return None


def _font_dirs() -> List[Path]:
    out: List[Path] = []
    for raw in FONT_DIRS:
        try:
            path = Path(raw).expanduser()
        except Exception:
            continue
        if path.is_dir():
            out.append(path)
    return out


def system_fonts(limit: int = 400) -> List[Tuple[str, str]]:
    """``(name, path)`` for every font file in the font folders.

    The *name* is the file's stem, not the family recorded inside the font:
    reading the family out of every file means opening every file, and a
    reader choosing from a list of four hundred fonts is waiting on it. The
    family is read when one is actually chosen, and that is what the panel
    then shows.

    Bounded, sorted, and de-duplicated by name — the first folder that has a
    given name wins, which is why :data:`FONT_DIRS` puts the reader's own
    folders first.
    """
    seen: dict[str, str] = {}
    for directory in _font_dirs():
        try:
            walker = os.walk(directory)
        except Exception:
            continue
        for root, _dirs, files in walker:
            for filename in sorted(files):
                if not filename.lower().endswith(FONT_SUFFIXES):
                    continue
                name = Path(filename).stem
                if name not in seen:
                    seen[name] = str(Path(root) / filename)
                if len(seen) >= limit:
                    return sorted(seen.items())
    return sorted(seen.items())


def _find_in_font_dirs(spec: str) -> str:
    """A font file matching ``spec`` by name, or "" when there is none.

    Matched on the file's stem, case- and space-insensitively, so
    ``Irken Like All Caps``, ``irkenlikeallcaps`` and ``IrkenLikeAllCaps``
    all find the same file — which is what a reader who has seen the name in
    three places written three ways will type.
    """
    wanted = "".join(spec.lower().split()).replace("-", "").replace("_", "")
    if not wanted:
        return ""
    for directory in _font_dirs():
        try:
            walker = os.walk(directory)
        except Exception:
            continue
        for root, _dirs, files in walker:
            for filename in sorted(files):
                if not filename.lower().endswith(FONT_SUFFIXES):
                    continue
                stem = Path(filename).stem.lower()
                flat = "".join(stem.split()).replace("-", "").replace("_", "")
                if flat == wanted:
                    return str(Path(root) / filename)
    return ""


def resolve_face(spec: Any, *, builtin_title: str = "CP437") -> FontFace:
    """Work out which face ``spec`` names, and whether it can be lettered with.

    ``spec`` is whatever is in ``ui.typeface``:

    * ``""``, ``default``, or anything falsy — the built-in alphabet;
    * a path to a font file, with ``~`` and environment variables expanded;
    * a font's name, looked for in the platform's font folders.

    The returned face always letters *something*: one that could not be loaded
    carries the built-in title and a ``problem`` saying why, so every caller
    draws the same thing and exactly one of them — the panel — has to explain
    it.
    """
    text = str(spec or "").strip()
    if not text or text.lower() == "default":
        return FontFace(spec="default", title=builtin_title)

    path = ""
    source = "file"
    looks_like_path = any(mark in text for mark in ("/", "\\", "~")) or text.lower(
    ).endswith(FONT_SUFFIXES)
    if looks_like_path:
        try:
            candidate = Path(os.path.expandvars(text)).expanduser()
        except Exception:
            candidate = None
        if candidate is not None and candidate.is_file():
            path = str(candidate)
        else:
            return FontFace(
                spec=text,
                title=builtin_title,
                problem=f"there is no font file at {text}",
            )
    else:
        path = _find_in_font_dirs(text)
        source = "system"
        if not path:
            return FontFace(
                spec=text,
                title=builtin_title,
                problem=(
                    f"no font called {text!r} in the font folders — give the "
                    "path to the file instead, or drop it in ~/.fonts"
                ),
            )

    parts = _pillow()
    if parts is None:
        return FontFace(
            spec=text,
            title=builtin_title,
            path="",
            source=source,
            problem=(
                "Pillow is not installed, so a font file cannot be rasterised "
                "— it is one of Curie's core dependencies, so `curie update` "
                "should put it back"
            ),
        )
    _Image, _Draw, ImageFont = parts
    try:
        font = ImageFont.truetype(path, 16)
        family, style = font.getname()
    except Exception as exc:  # noqa: BLE001 — every failure is the same answer
        return FontFace(
            spec=text,
            title=builtin_title,
            path="",
            source=source,
            problem=f"{Path(path).name} could not be read as a font ({exc})",
        )
    title = " ".join(part for part in (family, style) if part) or Path(path).stem
    return FontFace(spec=text, title=title, path=path, source=source)


def _fit(ImageFont: Any, path: str, text: str, rows: int, columns: int):
    """The largest size whose render fits ``rows`` of cells and ``columns``.

    Height first, width second. A plate is a fixed number of rows and the
    string in it varies, so sizing on height keeps every wordmark the same
    weight on the page; the width check then shrinks only the ones that would
    run off the end — which is the case a long model name in the DOS plate
    hits and a short title never does.
    """
    target = max(2, rows * 2)
    best = None
    low, high = 4, 400
    while low <= high:
        middle = (low + high) // 2
        try:
            font = ImageFont.truetype(path, middle)
            box = font.getbbox(text)
        except Exception:
            return None
        height = max(1, box[3] - box[1])
        width = max(1, box[2] - box[0])
        if height <= target and width <= columns:
            best = (font, box)
            low = middle + 1
        else:
            high = middle - 1
    return best


def render_lettering(
    face: FontFace,
    text: str,
    *,
    rows: int = DEFAULT_ROWS,
    columns: int = 80,
) -> List[str]:
    """``text`` in ``face``, as rows of half-block glyphs.

    Returns ``[]`` when there is nothing to draw — no custom face, no room, a
    font that will not rasterise — and the caller letters the plate the way it
    always did. An empty list rather than an exception because this runs
    inside a paint.

    Half-blocks rather than quadrants or braille: one sub-pixel across and two
    down makes each sub-pixel square on a grid whose cells are twice as tall
    as they are wide, so a circle comes out round. Quadrants would double the
    horizontal resolution and squash the letters; braille draws type as
    perforations.
    """
    if not face.custom or not text.strip():
        return []
    rows = max(MIN_ROWS, min(MAX_ROWS, int(rows)))
    columns = int(columns)
    if columns < 4:
        return []
    try:
        stamp = os.path.getmtime(face.path)
    except OSError:
        stamp = 0.0
    key = (face.path, stamp, text, rows, columns)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    drawn = _render(face, text, rows, columns)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = drawn
    return drawn


def _render(face: FontFace, text: str, rows: int, columns: int) -> List[str]:
    parts = _pillow()
    if parts is None:
        return []
    Image, ImageDraw, ImageFont = parts
    fitted = _fit(ImageFont, face.path, text, rows, columns)
    if fitted is None:
        return []
    font, box = fitted
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    # An even number of pixel rows, so the last cell is a whole cell rather
    # than a half one that has to be special-cased in the walk below.
    canvas_height = height + (height % 2)
    try:
        image = Image.new("L", (width, canvas_height), 0)
        ImageDraw.Draw(image).text((-box[0], -box[1]), text, font=font, fill=255)
        pixels = image.load()
    except Exception:
        return []

    out: List[str] = []
    for y in range(0, canvas_height - 1, 2):
        line = []
        for x in range(width):
            top = pixels[x, y] >= INK
            bottom = pixels[x, y + 1] >= INK
            line.append(
                "\u2588" if top and bottom
                else "\u2580" if top
                else "\u2584" if bottom
                else " "
            )
        out.append("".join(line).rstrip())
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return out


def render_wordmark(
    face: FontFace,
    titles: Sequence[str],
    *,
    rows: int = DEFAULT_ROWS,
    columns: int = 80,
) -> List[str]:
    """The longest of ``titles`` that letters at the full asked-for height.

    The wordmark **shortens before it shrinks**, and that rule is the whole
    difference between lettering and texture. :func:`render_lettering` fits
    height first and width second, so a string too long for the space comes
    back at whatever size did fit — which for a plate fifty columns wide and a
    twenty-eight character title is two pixels of cap height, a row of grey
    specks that is not a word at all.

    So a render that did not reach the requested height is refused, and the
    next title down is tried. Every wordmark the console draws is therefore
    the same size; only which words are in it changes with the width. That is
    what a title plate does — and it is the same ladder the built-in plate
    already walks, which is why they agree about where the widths are.

    ``[]`` when nothing fits, when the face is the built-in one, or when a
    font will not rasterise: the caller letters the plate the way it always
    did.
    """
    rows = max(MIN_ROWS, min(MAX_ROWS, int(rows)))
    for title in titles:
        drawn = render_lettering(face, title.strip(), rows=rows, columns=columns)
        if len(drawn) >= rows:
            return drawn
    return []


def clamp_rows(value: Any) -> int:
    """A stored plate height, made safe."""
    try:
        rows = int(value)
    except (TypeError, ValueError):
        return DEFAULT_ROWS
    return max(MIN_ROWS, min(MAX_ROWS, rows))


def forget_rendered() -> None:
    """Drop the render cache. For tests, and for a font replaced on disk."""
    _CACHE.clear()


__all__ = [
    "DEFAULT_ROWS",
    "FONT_DIRS",
    "FONT_SUFFIXES",
    "FontFace",
    "MAX_ROWS",
    "MIN_ROWS",
    "clamp_rows",
    "forget_rendered",
    "render_lettering",
    "render_wordmark",
    "resolve_face",
    "system_fonts",
]
