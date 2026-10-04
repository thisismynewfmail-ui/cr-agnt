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

So that is what this does. A font named in ``ui.typeface`` is loaded and used
for the console's *display lettering* — the title plate's wordmark, and the
sample on the PANEL pane — and F1 puts the built-in lettering back. The body
of the interface stays in the terminal's own font, and the PANEL pane says so
rather than leaving the reader to wonder why their font only reached half the
screen.

**Where a font can come from.** Fonts arrive the way they are downloaded, so
every one of these letters:

* a path to a font file — ``.ttf``, ``.otf``, the ``.ttc``/``.otc``
  collections, ``.woff``, or a Type 1 ``.pfb``/``.pfa``;
* a path to the ``.zip`` it was downloaded in, or to the folder it was
  unpacked into — the regular face inside is used before a bold or an italic,
  and the ``__MACOSX`` shadow files a Mac's archiver adds are skipped;
* a name — the font's own family or full name, the one a font manager shows
  ("Irken"), or its file name ("Irken-Like-AllCaps") — looked for in the
  console's own font folder (``<CURIE_HOME>/fonts``, where a font or its zip
  can simply be dropped), then the reader's font folders, then the system's.
  On Windows that includes the per-user folder a right-click *Install* puts a
  font in, and under WSL the Windows side's folders as well; on Linux, every
  folder fontconfig knows about.

**What a font file can be like.** Free display fonts are often old, and old
fonts are often odd. One with no Unicode map at all — encoded for the Windows
symbol page, or for the classic Mac only — is mapped here, because FreeType
will not map text onto it by itself and every letter would come out as the
font's empty box. A character the face does not have — no lowercase, no dash,
no space — is swapped for one it has (the other case, a plain hyphen) or left
out, rather than drawn as that box either.

**How it is drawn.** Rasterised in monochrome, with the hinting the font
carries: at the dozen pixels of cap height a plate has, an anti-aliased
outline cut at a threshold loses its thin strokes, and a hinted one keeps
them. A wordmark *shortens before it shrinks* — the longest title that fits
at the asked-for height wins — and shrinks, a row at a time, only when even
the shortest title will not fit; it never shrinks below legibility. A font
therefore always letters a plate it can, instead of the plate quietly going
back to the built-in title.

Everything here is total. A font that has been deleted since it was chosen, a
file that is not a font, a Pillow build without FreeType, no Pillow at all —
each resolves to the built-in lettering carrying a sentence saying which,
because a console that will not start over an appearance setting is worse
than one that starts in its own face.
"""

from __future__ import annotations

import bisect
import functools
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

#: Suffixes FreeType reads. ``.ttc``/``.otc`` are collections — FreeType opens
#: the first face, which is the one a reader naming the file means. ``.woff``
#: is the web's wrapper around the same tables; Type 1 comes as ``.pfb`` or
#: ``.pfa``.
FONT_SUFFIXES: Tuple[str, ...] = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".pfb", ".pfa")

#: What a font is downloaded in. A path to one letters with the best face
#: inside it, and one dropped in the console's own font folder is listed.
ARCHIVE_SUFFIXES: Tuple[str, ...] = (".zip",)

#: Where a font lives, most personal first: a font the reader dropped in their
#: own folder should win over one the system shipped under the same name,
#: because they put it there on purpose. The full list is worked out per
#: platform by :func:`font_folders` — this is the summary of it.
FONT_DIRS: Tuple[str, ...] = (
    "<CURIE_HOME>/fonts",
    "~/.fonts",
    "~/.local/share/fonts",
    "~/Library/Fonts",
    "%LOCALAPPDATA%/Microsoft/Windows/Fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "/usr/local/share/fonts",
    "/usr/share/fonts",
    "C:/Windows/Fonts",
)

#: The three kinds of folder, in the order they are searched.
TIER_CONSOLE = 0
TIER_PERSONAL = 1
TIER_SYSTEM = 2

#: How tall the lettered plate is, in rows, and the range the control moves in.
#: Five is the default because it is two and a half rows of pixels per row of
#: type — enough for a cap height, an x-height and a descender to be told
#: apart — and because it is the most a title plate can take from a chat
#: window without the conversation noticing.
DEFAULT_ROWS = 5
MIN_ROWS = 2
MAX_ROWS = 12

#: The shortest a wordmark is drawn when it has to shrink to fit: six pixels
#: of type, about the least in which letters are still letters. A plate asked
#: to be shorter than this is drawn as short as it was asked.
LEGIBLE_ROWS = 3

#: Coverage at which a sub-pixel counts as ink, 0-255, for the anti-aliased
#: fallback. Half way: a threshold that leans light loses the thin strokes of
#: a serif face, and one that leans dark fattens a bold one into a blob.
INK = 128

#: Rendered plates, keyed by everything that changes one. Bounded because the
#: console re-letters on every resize and a reader dragging a window edge
#: walks through a hundred widths in a second.
_CACHE: dict = {}
_CACHE_MAX = 256

#: The most font files one index holds. A machine with more than this has
#: installed everything twice; the reader's own folders are read first, so it
#: is the system's tail that is dropped.
_MAX_FONTS = 6000

#: Pixel sizes a face drawn only as bitmaps is commonly cut at. Such a face
#: loads at its own sizes and no others, so these are what is tried when the
#: outline sizes are refused.
_STRIKE_SIZES = (8, 9, 10, 11, 12, 13, 14, 15, 18, 20, 22, 24, 28, 32, 48, 64)

#: The most fonts the PANEL list names. Each is opened for the name inside
#: it, so this is what the first look at the list costs.
_MAX_LISTED = 2000

#: The largest member a font archive may unpack: a font is a few megabytes at
#: most, and anything bigger in a zip that claims to be fonts is not one.
_MAX_MEMBER = 64 * 1024 * 1024

#: Words in a file name that mark a face as one of a family's variations, and
#: words that mark the one to use when nothing says otherwise.
_STYLED_WORDS = (
    "bold", "italic", "oblique", "light", "thin", "black", "heavy", "semi",
    "demi", "medium", "condensed", "narrow", "extra", "ultra", "outline",
    "shadow", "inline", "slant",
)
_REGULAR_WORDS = ("regular", "book", "normal", "roman", "plain", "standard")
_KIND_ORDER = {".ttf": 0, ".otf": 0, ".ttc": 1, ".otc": 1, ".woff": 2, ".pfb": 3, ".pfa": 3}

#: A character a face does not have, and what to set instead, in order of
#: preference. A dash with no dash in the face becomes a hyphen; with no
#: hyphen either it is left out, and the space either side closes up.
_STAND_INS: Dict[str, Tuple[str, ...]] = {
    "\u2014": ("\u2013", "-"),
    "\u2013": ("-",),
    "\u2012": ("-",),
    "\u2015": ("\u2014", "-"),
    "\u2212": ("-",),
    "\u2010": ("-",),
    "\u2011": ("-",),
    "\u2018": ("'",),
    "\u2019": ("'",),
    "\u201c": ('"',),
    "\u201d": ('"',),
    "\u2026": ("...",),
    "\u00b7": (".",),
    "\u2022": (".", "*"),
    "\u00a0": (" ",),
}

_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/]?(.*)$")


@dataclass(frozen=True)
class FontFace:
    """A lettering face: the built-in alphabet, or a font file on disk."""

    #: What was asked for, as stored in ``ui.typeface``.
    spec: str
    #: What to call it on screen — the font's own family and style when it
    #: loaded, the built-in face's name when it did not.
    title: str
    #: The file it was loaded from. Empty for the built-in face. For a font
    #: that came in an archive, the unpacked copy.
    path: str = ""
    #: Where it was found: ``builtin``, ``file`` (a path the reader gave) or
    #: ``system`` (a name found in the font folders).
    source: str = "builtin"
    #: Why it could not be used, as a sentence. Empty when it loaded.
    problem: str = ""
    #: The ``.zip`` the font came out of, when it came out of one.
    archive: str = ""

    @property
    def custom(self) -> bool:
        """Whether this is a real font file rather than the built-in face."""
        return bool(self.path)

    def label(self) -> str:
        """One line for the panel and the notice line."""
        if not self.custom:
            return self.title
        if self.archive:
            return f"{self.title}  ({Path(self.path).name}, from {Path(self.archive).name})"
        return f"{self.title}  ({Path(self.path).name})"


@dataclass(frozen=True)
class FontNames:
    """What a font file calls itself, out of its ``name`` table."""

    family: str = ""
    style: str = ""
    full: str = ""
    postscript: str = ""
    #: The "typographic" family and style, where a font family has more
    #: weights than the four the old family/style pair can name.
    preferred_family: str = ""
    preferred_style: str = ""

    @property
    def display(self) -> str:
        """What a font manager would call this face."""
        if self.full:
            return self.full
        family = self.preferred_family or self.family
        style = self.preferred_style or self.style
        if family and style and style.lower() not in _REGULAR_WORDS:
            return f"{family} {style}"
        return family

    @property
    def regular(self) -> bool:
        """Whether this is the plain face of its family."""
        style = (self.preferred_style or self.style).strip().lower()
        return not style or style in _REGULAR_WORDS

    def exact(self) -> List[str]:
        """Names that pick out exactly this face rather than its family."""
        out = [self.full, self.postscript]
        for family, style in (
            (self.family, self.style),
            (self.preferred_family, self.preferred_style),
        ):
            if family and style:
                out.append(f"{family} {style}")
        return [name for name in out if name]

    def families(self) -> List[str]:
        return [name for name in (self.family, self.preferred_family) if name]


@dataclass(frozen=True)
class Wordmark:
    """A plate's lettering, and the facts about how it was arrived at."""

    lines: Tuple[str, ...] = ()
    #: The title that was lettered.
    title: str = ""
    #: The height it was lettered at, and the height that was asked for.
    rows: int = 0
    asked: int = 0
    #: Why nothing was lettered: ``narrow`` (no title fits even shrunk),
    #: ``glyphs`` (the face has none of the title's letters), or ``builtin``.
    reason: str = ""

    @property
    def shrunk(self) -> bool:
        """Lettered, but shorter than asked because the width would not take it."""
        return bool(self.lines) and self.rows < self.asked


@dataclass(frozen=True)
class _Charmap:
    """Which characters a face draws, and how text has to be put to it.

    ``encoding`` is the FreeType charmap to select — empty for Unicode,
    ``symb`` for a Windows symbol font, ``armn`` for a Mac Roman one — and
    ``shift`` is added to each character before the lookup, for symbol fonts
    that keep their glyphs at U+F0xx. ``starts``/``ends`` are the covered
    ranges, *as the text is written*; ``known`` is False when the font's map
    could not be read, in which case nothing is swapped and FreeType decides.
    """

    encoding: str = ""
    shift: int = 0
    starts: Tuple[int, ...] = ()
    ends: Tuple[int, ...] = ()
    known: bool = False

    def covers(self, char: str) -> bool:
        if not self.known:
            return True
        code = ord(char)
        index = bisect.bisect_right(self.starts, code) - 1
        return index >= 0 and code <= self.ends[index]


@dataclass(frozen=True)
class _Found:
    """One font in the index: the file to load, how personal its folder is,
    and the archive it was unpacked from, if any."""

    path: str
    tier: int
    archive: str = ""


# ── Pillow ───────────────────────────────────────────────────────────────


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


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


# ── Where fonts live ─────────────────────────────────────────────────────


def curie_font_dir() -> Optional[Path]:
    """The console's own font folder, ``<CURIE_HOME>/fonts``.

    Profile-aware, like every other piece of state, and the one folder on
    every platform that needs no administrator and no font installer: a font
    file — or the zip it came in — dropped here is lettered with.
    """
    try:
        from curie_constants import get_curie_home

        return Path(get_curie_home()) / "fonts"
    except Exception:
        return None


def display_font_dir() -> str:
    """:func:`curie_font_dir` as the reader should see it written."""
    try:
        from curie_constants import display_curie_home

        return f"{display_curie_home()}/fonts"
    except Exception:
        return "~/.curie/fonts"


def ensure_font_dir() -> Optional[Path]:
    """Make the console's font folder exist, so there is somewhere to drop one."""
    folder = curie_font_dir()
    if folder is None:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return folder


@functools.lru_cache(maxsize=1)
def _in_wsl() -> bool:
    """Whether this is Linux running under Windows (WSL)."""
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    for probe in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            if "microsoft" in Path(probe).read_text(encoding="utf-8", errors="ignore").lower():
                return True
        except OSError:
            continue
    return False


def _wsl_windows_roots() -> List[Path]:
    """The Windows drives WSL has mounted that hold a Windows install."""
    if not _in_wsl():
        return []
    try:
        entries = sorted(Path("/mnt").iterdir())
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if len(entry.name) == 1
        and entry.name.isalpha()
        and _is_dir(entry / "Windows" / "Fonts")
    ]


def wsl_path(text: str, mount: str = "/mnt") -> Optional[Path]:
    """A Windows path (``C:\\Users\\me\\x.ttf``) as WSL sees it, or ``None``.

    So a path copied out of Windows Explorer works in a console running under
    WSL: the drive becomes ``/mnt/c`` and the separators turn round.
    """
    match = _WINDOWS_PATH.match(text.strip())
    if not match:
        return None
    drive, rest = match.groups()
    parts = [part for part in re.split(r"[\\/]+", rest) if part]
    return Path(mount, drive.lower(), *parts)


def _folders_for(
    *,
    platform: str,
    environ: Mapping[str, str],
    home: Path,
    curie_dir: Optional[Path] = None,
    windows_roots: Sequence[Path] = (),
) -> List[Tuple[Path, int]]:
    """Every folder a font is looked for in, most personal first, with its tier.

    A pure function of the platform it is handed, so the list each platform
    gets can be checked on any host. ``windows_roots`` are the Windows drives
    as WSL mounts them (``/mnt/c``), and empty everywhere else.
    """
    out: List[Tuple[Path, int]] = []
    windows = platform.startswith("win")

    if curie_dir is not None:
        out.append((Path(curie_dir), TIER_CONSOLE))

    out.append((home / ".fonts", TIER_PERSONAL))
    data_home = (environ.get("XDG_DATA_HOME") or "").strip()
    if data_home and not windows:
        out.append((Path(data_home) / "fonts", TIER_PERSONAL))
    out.append((home / ".local" / "share" / "fonts", TIER_PERSONAL))
    if platform == "darwin":
        out.append((home / "Library" / "Fonts", TIER_PERSONAL))
    if windows:
        # Where Windows 10 and 11 put a font installed with a right-click
        # "Install" — for this account only, and without asking for an
        # administrator. The font readme every download site ships says to
        # do exactly that, so this is where most fonts on Windows are.
        local = (environ.get("LOCALAPPDATA") or "").strip()
        base = Path(local) if local else home / "AppData" / "Local"
        out.append((base / "Microsoft" / "Windows" / "Fonts", TIER_PERSONAL))
    for root in windows_roots:
        try:
            profiles = sorted((root / "Users").iterdir())
        except OSError:
            profiles = []
        for profile in profiles:
            if profile.name.lower() in ("public", "default", "default user", "all users"):
                continue
            out.append(
                (profile / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts", TIER_PERSONAL)
            )

    if platform == "darwin":
        for folder in ("/Library/Fonts", "/System/Library/Fonts", "/Network/Library/Fonts"):
            out.append((Path(folder), TIER_SYSTEM))
    if windows:
        windir = (environ.get("WINDIR") or environ.get("SystemRoot") or "").strip()
        out.append((Path(windir or "C:/Windows") / "Fonts", TIER_SYSTEM))
    else:
        for folder in (environ.get("XDG_DATA_DIRS") or "").split(":"):
            if folder.strip():
                out.append((Path(folder.strip()) / "fonts", TIER_SYSTEM))
        out.append((Path("/usr/local/share/fonts"), TIER_SYSTEM))
        out.append((Path("/usr/share/fonts"), TIER_SYSTEM))
    for root in windows_roots:
        out.append((root / "Windows" / "Fonts", TIER_SYSTEM))

    seen: set = set()
    unique: List[Tuple[Path, int]] = []
    for folder, tier in out:
        key = os.path.normcase(os.path.normpath(str(folder)))
        if key in seen:
            continue
        seen.add(key)
        unique.append((folder, tier))
    return unique


def font_folders() -> List[Tuple[Path, int]]:
    """The font folders on *this* machine that exist, most personal first."""
    try:
        home = Path.home()
    except Exception:
        home = Path(os.path.expanduser("~"))
    folders = _folders_for(
        platform=sys.platform,
        environ=os.environ,
        home=home,
        curie_dir=curie_font_dir(),
        windows_roots=_wsl_windows_roots(),
    )
    return [(folder, tier) for folder, tier in folders if _is_dir(folder)]


def parse_fontconfig_listing(text: str, home: str = "") -> List[Tuple[str, int]]:
    """``fc-list --format '%{file}\\n'`` output as ``(path, tier)`` pairs."""
    out: List[Tuple[str, int]] = []
    for line in text.splitlines():
        path = line.strip()
        if not path or not path.lower().endswith(FONT_SUFFIXES):
            continue
        tier = TIER_PERSONAL if home and path.startswith(home.rstrip("/") + "/") else TIER_SYSTEM
        out.append((path, tier))
    return sorted(out, key=lambda pair: (pair[1], pair[0]))


def _fontconfig_files() -> List[Tuple[str, int]]:
    """Every font file fontconfig knows of, where it is there to ask.

    On Linux fontconfig is what decides which fonts exist — distributions,
    Nix and Flatpak all add folders through its configuration rather than
    through any path this module could guess — so its answer is folded in
    after the folders are walked. Anywhere it is missing, or slow to answer,
    the folders are the whole answer.
    """
    if sys.platform.startswith("win"):
        return []
    program = shutil.which("fc-list")
    if not program:
        return []
    try:
        listing = subprocess.run(
            [program, "--format", "%{file}\n"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        ).stdout
    except Exception:
        return []
    try:
        home = str(Path.home())
    except Exception:
        home = ""
    return [
        (path, tier)
        for path, tier in parse_fontconfig_listing(listing or "", home)
        if os.path.isfile(path)
    ]


def _walk(folder: Path, max_depth: Optional[int] = None) -> Iterator[Path]:
    """Every file under ``folder``, in a stable order, following links once.

    ``max_depth`` stops the descent that many folders down — for a path the
    reader typed, which could as easily be their home folder as a download.
    """
    visited: set = set()
    base = len(Path(folder).parts)
    try:
        walker = os.walk(folder, followlinks=True)
    except Exception:
        return
    for root, dirs, files in walker:
        real = os.path.realpath(root)
        if real in visited:
            dirs[:] = []
            continue
        visited.add(real)
        if max_depth is not None and len(Path(root).parts) - base >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for filename in sorted(files):
            if filename.startswith("._"):
                # A Mac's resource-fork shadow of the real file beside it:
                # named like a font, and not one.
                continue
            yield Path(root) / filename


_INDEX: Optional[Tuple[_Found, ...]] = None
_INDEX_BUILT = 0.0
_LISTING: Optional[List[Tuple[str, str]]] = None
_LOOKUPS: Dict[str, Tuple[str, str]] = {}


def _index(*, fresh: bool = False) -> Tuple[_Found, ...]:
    """Every font this machine has, most personal folder first. Cached.

    Fonts in an archive count only in the console's own folder: that is the
    folder a reader drops a download into, and a zip anywhere else is just a
    zip. Their faces are unpacked once into the cache and indexed from there.
    """
    global _INDEX, _INDEX_BUILT, _LISTING
    if _INDEX is not None and not fresh:
        return _INDEX
    found: List[_Found] = []
    seen: set = set()

    def add(path: Path, tier: int, archive: str = "") -> None:
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            return
        seen.add(key)
        found.append(_Found(str(path), tier, archive))

    for folder, tier in font_folders():
        for path in _walk(folder):
            if len(found) >= _MAX_FONTS:
                break
            lower = path.name.lower()
            if lower.endswith(FONT_SUFFIXES):
                add(path, tier)
            elif tier == TIER_CONSOLE and lower.endswith(ARCHIVE_SUFFIXES):
                faces, _problem = unpack_archive(path, every=True)
                for face in faces:
                    add(face, tier, archive=str(path))
    for path, tier in _fontconfig_files():
        if len(found) >= _MAX_FONTS:
            break
        add(Path(path), tier)

    _INDEX = tuple(found)
    _INDEX_BUILT = time.monotonic()
    _LISTING = None
    return _INDEX


def system_fonts(limit: int = 2000) -> List[Tuple[str, str]]:
    """``(name, where)`` for every font on the machine, for the PANEL list.

    The *name* is the font's own — the full name out of the file, which is
    what a font manager shows and what a reader will type — falling back to
    the file name for a font that does not say. *Where* is the file, or the
    zip it came in. One row per name, the most personal folder's copy first,
    sorted with the console's folder and the reader's own ahead of the
    system's, because those are the fonts somebody chose.
    """
    global _LISTING
    if _LISTING is None:
        rows: List[Tuple[int, str, str, str]] = []
        seen: set = set()
        for entry in _index():
            if len(rows) >= _MAX_LISTED:
                # The index is in folder order, most personal first, so it is
                # the system's long tail that is left off the list — and even
                # that is still found by name.
                break
            name = read_font_names(entry.path).display or Path(entry.path).stem
            key = _flat(name)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append((entry.tier, name.lower(), name, entry.archive or entry.path))
        rows.sort(key=lambda row: (row[0], row[1]))
        _LISTING = [(name, where) for _tier, _key, name, where in rows]
    return list(_LISTING[: max(0, int(limit))])


# ── Archives and folders ─────────────────────────────────────────────────


def _face_rank(name: str) -> tuple:
    """Order the faces of a family: the plain one first, then the rest."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, suffix = os.path.splitext(base.lower())
    styled = sum(1 for word in _STYLED_WORDS if word in stem)
    regular = 0 if any(word in stem for word in _REGULAR_WORDS) else 1
    return (styled, regular, _KIND_ORDER.get(suffix, 9), len(stem), base.lower())


def _font_cache_dir() -> Optional[Path]:
    try:
        from curie_constants import get_curie_home

        return Path(get_curie_home()) / "cache" / "fonts"
    except Exception:
        return None


def _safe_name(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(" .")
    return cleaned or "font.ttf"


def unpack_archive(archive: Path, *, every: bool = False) -> Tuple[List[Path], str]:
    """The font faces in a zip, unpacked into the cache. ``(paths, problem)``.

    The best face first — the regular weight, then the rest — and only that
    one unless ``every`` is set. Unpacked once per archive version: the cache
    folder is named after the archive's path, size and modification time, so
    a zip replaced on disk is unpacked again and an unchanged one never is.
    Only a member's file name is used, never its folder, so nothing in an
    archive can write outside the cache.
    """
    archive = Path(archive)
    cache = _font_cache_dir()
    if cache is None:
        return [], "there is nowhere to unpack the archive into"
    try:
        stat = archive.stat()
        stamp = f"{os.path.realpath(archive)}|{stat.st_size}|{stat.st_mtime_ns}"
        folder = cache / hashlib.sha1(stamp.encode("utf-8", "replace")).hexdigest()[:16]
        with zipfile.ZipFile(archive) as bundle:
            members = []
            for info in bundle.infolist():
                name = info.filename.replace("\\", "/")
                base = name.rsplit("/", 1)[-1]
                if info.is_dir() or not base:
                    continue
                if name.startswith("__MACOSX/") or "/__MACOSX/" in name or base.startswith("._"):
                    continue
                if not base.lower().endswith(FONT_SUFFIXES):
                    continue
                if info.file_size <= 0 or info.file_size > _MAX_MEMBER:
                    continue
                members.append(info)
            if not members:
                return [], f"there is no font file inside {archive.name}"
            members.sort(key=lambda info: _face_rank(info.filename))
            if not every:
                members = members[:1]
            out: List[Path] = []
            for info in members:
                tag = hashlib.sha1(info.filename.encode("utf-8", "replace")).hexdigest()[:8]
                target = folder / tag / _safe_name(info.filename)
                if not (_is_file(target) and target.stat().st_size == info.file_size):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    partial = target.with_name(f"{target.name}.{os.getpid()}.part")
                    written = 0
                    with bundle.open(info) as source, open(partial, "wb") as sink:
                        while True:
                            chunk = source.read(1 << 16)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > _MAX_MEMBER:
                                raise ValueError(f"{info.filename} is larger than a font can be")
                            sink.write(chunk)
                    os.replace(partial, target)
                out.append(target)
            return out, ""
    except zipfile.BadZipFile:
        return [], f"{archive.name} is not a zip archive that can be read"
    except Exception as exc:  # noqa: BLE001 — every failure is the same answer
        return [], f"{archive.name} could not be unpacked ({exc})"


def _best_in_folder(folder: Path, depth: int = 3) -> Optional[Path]:
    """The face to use out of a folder of fonts — a download, unpacked.

    A few folders deep and a few thousand files at most: a download unpacks
    into a folder or two, and a path that turns out to be somebody's home
    folder must not be walked to the bottom before the console can open.
    """
    candidates: List[Path] = []
    for seen, path in enumerate(_walk(folder, max_depth=depth)):
        if seen >= 5000 or len(candidates) >= 500:
            break
        if "__MACOSX" in path.parts:
            continue
        if path.name.lower().endswith(FONT_SUFFIXES):
            candidates.append(path)
    if not candidates:
        return None
    return min(candidates, key=lambda path: _face_rank(path.name))


# ── What a font calls itself ─────────────────────────────────────────────


def _sfnt_tables(path: str, wanted: Sequence[bytes]) -> Dict[bytes, bytes]:
    """The raw bytes of the ``wanted`` tables of a TrueType/OpenType file.

    Reads only the header, the table directory and the tables asked for —
    never the whole file, which for a CJK face is twenty megabytes. Handles
    the first face of a collection and WOFF's compressed tables. ``{}`` for
    anything else, including a file too short to be what it says it is.
    """
    out: Dict[bytes, bytes] = {}
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
            if len(header) < 12:
                return out
            tag = header[:4]
            if tag == b"wOFF":
                handle.seek(0)
                woff = handle.read(44)
                count = struct.unpack(">H", woff[12:14])[0]
                directory = handle.read(20 * count)
                for index in range(count):
                    name, offset, packed, size, _sum = struct.unpack_from(
                        ">4sIIII", directory, 20 * index
                    )
                    if name not in wanted or size > _MAX_MEMBER:
                        continue
                    handle.seek(offset)
                    data = handle.read(packed)
                    out[name] = zlib.decompress(data) if packed < size else data
                return out
            if tag == b"ttcf":
                handle.seek(12)
                first = struct.unpack(">I", handle.read(4))[0]
                handle.seek(first)
                header = handle.read(12)
                tag = header[:4]
            if tag not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
                return out
            count = struct.unpack(">H", header[4:6])[0]
            directory = handle.read(16 * count)
            for index in range(count):
                name, _sum, offset, length = struct.unpack_from(">4sIII", directory, 16 * index)
                if name not in wanted or length > _MAX_MEMBER:
                    continue
                handle.seek(offset)
                out[name] = handle.read(length)
    except (OSError, struct.error, zlib.error):
        return {}
    return out


def _parse_names(data: bytes) -> FontNames:
    """The family, style, full and PostScript names out of a ``name`` table."""
    best: Dict[int, Tuple[int, str]] = {}
    try:
        _format, count, strings = struct.unpack_from(">HHH", data, 0)
        for index in range(count):
            platform, encoding, language, name_id, length, offset = struct.unpack_from(
                ">6H", data, 6 + 12 * index
            )
            if name_id not in (1, 2, 4, 6, 16, 17):
                continue
            raw = data[strings + offset: strings + offset + length]
            if platform in (0, 3):
                text = raw.decode("utf-16-be", "replace")
                rank = 0 if platform == 3 and language == 0x409 else 1 if platform == 3 else 2
            elif platform == 1 and encoding == 0:
                text = raw.decode("mac_roman", "replace")
                rank = 3 if language == 0 else 4
            else:
                continue
            text = text.replace("\x00", "").strip()
            if text and (name_id not in best or rank < best[name_id][0]):
                best[name_id] = (rank, text)
    except struct.error:
        pass

    def get(name_id: int) -> str:
        return best.get(name_id, (0, ""))[1]

    return FontNames(
        family=get(1),
        style=get(2),
        full=get(4),
        postscript=get(6),
        preferred_family=get(16),
        preferred_style=get(17),
    )


def _type1_names(path: str) -> FontNames:
    """Names out of a Type 1 font's clear-text header."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(65536).decode("latin-1", "ignore")
    except OSError:
        return FontNames()

    def grab(key: str) -> str:
        match = re.search(r"/" + key + r"\s*\(((?:[^()\\]|\\.)*)\)", head)
        return match.group(1).strip() if match else ""

    match = re.search(r"/FontName\s*/(\S+)", head)
    return FontNames(
        family=grab("FamilyName"),
        style=grab("Weight"),
        full=grab("FullName"),
        postscript=match.group(1) if match else "",
    )


_NAMES: Dict[str, Tuple[float, FontNames]] = {}


def read_font_names(path: "str | Path") -> FontNames:
    """What the font at ``path`` calls itself. Empty names where it cannot say."""
    key = str(path)
    stamp = _mtime(key)
    cached = _NAMES.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    if key.lower().endswith((".pfb", ".pfa")):
        names = _type1_names(key)
    else:
        names = _parse_names(_sfnt_tables(key, (b"name",)).get(b"name", b""))
    _NAMES[key] = (stamp, names)
    return names


# ── Which characters a font draws ────────────────────────────────────────


def _runs(codes) -> List[Tuple[int, int]]:
    """Sorted code points as ``(start, end)`` runs."""
    out: List[Tuple[int, int]] = []
    for code in sorted(set(codes)):
        if out and code == out[-1][1] + 1:
            out[-1] = (out[-1][0], code)
        else:
            out.append((code, code))
    return out


def _merge(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _cmap_ranges(data: bytes, offset: int) -> Optional[List[Tuple[int, int]]]:
    """The code points one ``cmap`` subtable gives a real glyph, as runs.

    Formats 0, 4, 6, 10, 12 and 13 — every format that maps characters to
    glyphs. ``None`` for a format that does not, or a table that is cut short.
    """
    try:
        kind = struct.unpack_from(">H", data, offset)[0]
        if kind == 0:
            glyphs = data[offset + 6: offset + 262]
            return _runs(code for code, glyph in enumerate(glyphs) if glyph)
        if kind == 4:
            doubled = struct.unpack_from(">H", data, offset + 6)[0]
            count = doubled // 2
            ends = struct.unpack_from(f">{count}H", data, offset + 14)
            starts = struct.unpack_from(f">{count}H", data, offset + 16 + doubled)
            deltas = struct.unpack_from(f">{count}h", data, offset + 16 + 2 * doubled)
            base = offset + 16 + 3 * doubled
            offsets = struct.unpack_from(f">{count}H", data, base)
            ranges: List[Tuple[int, int]] = []
            for index in range(count):
                start, end = starts[index], ends[index]
                if start > end or start == 0xFFFF:
                    continue
                if offsets[index] == 0:
                    # glyph = code + delta: zero for at most one code.
                    zero = (-deltas[index]) & 0xFFFF
                    if start <= zero <= end:
                        if start < zero:
                            ranges.append((start, zero - 1))
                        if zero < end:
                            ranges.append((zero + 1, end))
                    else:
                        ranges.append((start, end))
                    continue
                codes = []
                for code in range(start, end + 1):
                    address = base + 2 * index + offsets[index] + 2 * (code - start)
                    if address + 2 > len(data):
                        break
                    glyph = struct.unpack_from(">H", data, address)[0]
                    if glyph and (glyph + deltas[index]) & 0xFFFF:
                        codes.append(code)
                ranges.extend(_runs(codes))
            return _merge(ranges)
        if kind == 6:
            first, count = struct.unpack_from(">HH", data, offset + 6)
            glyphs = struct.unpack_from(f">{count}H", data, offset + 10)
            return _runs(first + index for index, glyph in enumerate(glyphs) if glyph)
        if kind == 10:
            first, count = struct.unpack_from(">II", data, offset + 12)
            count = min(count, 0x110000)
            glyphs = struct.unpack_from(f">{count}H", data, offset + 20)
            return _runs(first + index for index, glyph in enumerate(glyphs) if glyph)
        if kind in (12, 13):
            groups = min(struct.unpack_from(">I", data, offset + 12)[0], 200000)
            ranges = []
            for index in range(groups):
                start, end, glyph = struct.unpack_from(">III", data, offset + 16 + 12 * index)
                if kind == 12 and glyph == 0:
                    start += 1  # only the first code of the group lands on .notdef
                elif kind == 13 and glyph == 0:
                    continue
                if start <= end:
                    ranges.append((start, min(end, 0x10FFFF)))
            return _merge(ranges)
    except struct.error:
        return None
    return None


#: Unicode subtables in the order FreeType itself prefers them: the full
#: repertoire first, the Basic Multilingual Plane after.
_UNICODE_MAPS = ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0))

_CHARMAPS: Dict[str, Tuple[float, _Charmap]] = {}


def _charmap(path: str) -> _Charmap:
    """How text has to be put to the font at ``path``. Cached per file version."""
    stamp = _mtime(path)
    cached = _CHARMAPS.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    found = _read_charmap(path)
    _CHARMAPS[path] = (stamp, found)
    return found


def _read_charmap(path: str) -> _Charmap:
    data = _sfnt_tables(path, (b"cmap",)).get(b"cmap")
    if not data:
        return _Charmap()
    try:
        count = struct.unpack_from(">H", data, 2)[0]
        subtables: Dict[Tuple[int, int], int] = {}
        for index in range(count):
            platform, encoding, offset = struct.unpack_from(">HHI", data, 4 + 8 * index)
            subtables.setdefault((platform, encoding), offset)
    except struct.error:
        return _Charmap()

    def made(encoding: str, shift: int, ranges: List[Tuple[int, int]]) -> _Charmap:
        return _Charmap(
            encoding=encoding,
            shift=shift,
            starts=tuple(start for start, _end in ranges),
            ends=tuple(end for _start, end in ranges),
            known=True,
        )

    for key in _UNICODE_MAPS:
        if key in subtables:
            ranges = _cmap_ranges(data, subtables[key])
            if ranges:
                return made("", 0, ranges)
    if (3, 0) in subtables:
        # The Windows symbol page: glyphs keyed by byte, almost always at
        # U+F020-U+F0FF. Text is shifted onto them before the lookup.
        ranges = _cmap_ranges(data, subtables[(3, 0)])
        if ranges:
            high = [
                (max(start, 0xF000) - 0xF000, min(end, 0xF0FF) - 0xF000)
                for start, end in ranges
                if end >= 0xF000 and start <= 0xF0FF
            ]
            if high:
                return made("symb", 0xF000, _merge(high))
            return made("symb", 0, ranges)
    if (1, 0) in subtables:
        # A classic Mac font: glyphs keyed by Mac Roman byte. Covered as the
        # characters those bytes are, so text is checked as it is written.
        ranges = _cmap_ranges(data, subtables[(1, 0)])
        if ranges:
            chars = []
            for start, end in ranges:
                for byte in range(start, min(end, 255) + 1):
                    chars.append(ord(bytes([byte]).decode("mac_roman")))
            if chars:
                return made("armn", 0, _runs(chars))
    return _Charmap()


def fit_text_to_face(text: str, charmap: _Charmap) -> str:
    """``text`` with every character the face lacks swapped or left out.

    A face with one case gets the other; a dash it lacks becomes a hyphen; a
    character with no stand-in it has is dropped, and the space either side
    of it closes up. Spaces are kept whether or not the face has one — a face
    without a space glyph is spaced by the layout instead.
    """
    if not charmap.known:
        return text
    out: List[str] = []
    for char in text:
        if char == " ":
            out.append(char)
            continue
        for option in (char, char.upper(), char.lower(), *_STAND_INS.get(char, ())):
            if option and all(char_ == " " or charmap.covers(char_) for char_ in option):
                out.append(option)
                break
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _encode(text: str, charmap: _Charmap) -> str:
    """``text`` as the character codes the face's own map is keyed by."""
    if charmap.encoding == "armn":
        return "".join(chr(byte) for byte in text.encode("mac_roman", "ignore"))
    if charmap.shift:
        return "".join(
            chr(charmap.shift + ord(char)) if ord(char) < 0x100 else char for char in text
        )
    return text


# ── Which face a setting names ───────────────────────────────────────────


def _flat(text: str) -> str:
    """A name with case, spaces, hyphens and underscores taken out of it."""
    return re.sub(r"[\s_\-]+", "", str(text or "")).lower()


def _clean_spec(spec: Any) -> str:
    """A setting as typed, made into something to look up.

    Quotes are taken off (a path copied out of Windows Explorer comes wrapped
    in them, and one dragged into a terminal often does), and a ``file://``
    address becomes the path it names.
    """
    text = str(spec or "").strip()
    for _ in range(3):
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
            text = text[1:-1].strip()
    if text.lower().startswith("file:"):
        parsed = urllib.parse.urlparse(text)
        path = urllib.parse.unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            path = f"//{parsed.netloc}{path}"
        if re.match(r"^/[A-Za-z]:[\\/]", path):
            path = path[1:]
        text = path or text
    return text


def _looks_like_path(text: str) -> bool:
    return (
        any(mark in text for mark in ("/", "\\", "~"))
        or text.lower().endswith(FONT_SUFFIXES + ARCHIVE_SUFFIXES)
        or bool(_WINDOWS_PATH.match(text))
    )


def _as_path(text: str) -> Path:
    expanded = os.path.expandvars(text)
    try:
        path = Path(expanded).expanduser()
    except Exception:
        path = Path(expanded)
    if not path.exists() and _in_wsl():
        translated = wsl_path(expanded)
        if translated is not None and translated.exists():
            return translated
    return path


def _lookup(spec: str) -> Tuple[str, str]:
    """``(font path, archive)`` for a font *name*, or ``("", "")``.

    Tried against, in order of the folders and in this order for each font:
    the file's name, then the names inside it. A name that picks out exactly
    one face — a full name, a PostScript name, a family and style — wins on
    the first font that has it. A bare family name ("DejaVu Sans") settles on
    the family's plain face rather than whichever weight comes first.
    """
    wanted = _flat(spec)
    suffixed = spec.lower().endswith(FONT_SUFFIXES + ARCHIVE_SUFFIXES)
    stem = _flat(os.path.splitext(spec)[0]) if suffixed else wanted
    if not wanted:
        return "", ""
    cached = _LOOKUPS.get(wanted)
    if cached is not None and os.path.isfile(cached[0]):
        return cached
    for fresh in (False, True):
        if fresh and time.monotonic() - _INDEX_BUILT < 2.0:
            break
        entries = _index(fresh=fresh)
        family_match: Optional[Tuple[int, _Found]] = None
        for entry in entries:
            file_stem = _flat(Path(entry.path).stem)
            # A face out of a zip answers to the zip's name as well: the
            # download is what the reader has seen it called.
            archive_stem = _flat(Path(entry.archive).stem) if entry.archive else ""
            if file_stem in (wanted, stem) or (archive_stem and archive_stem in (wanted, stem)):
                result = (entry.path, entry.archive)
                _LOOKUPS[wanted] = result
                return result
            names = read_font_names(entry.path)
            if any(_flat(name) == wanted for name in names.exact()):
                result = (entry.path, entry.archive)
                _LOOKUPS[wanted] = result
                return result
            if any(_flat(name) == wanted for name in names.families()):
                rank = 0 if names.regular else 1
                if family_match is None or rank < family_match[0]:
                    family_match = (rank, entry)
        if family_match is not None:
            entry = family_match[1]
            result = (entry.path, entry.archive)
            _LOOKUPS[wanted] = result
            return result
    return "", ""


def resolve_face(spec: Any, *, builtin_title: str = "CP437") -> FontFace:
    """Work out which face ``spec`` names, and whether it can be lettered with.

    ``spec`` is whatever is in ``ui.typeface``:

    * ``""``, ``default``, or anything falsy — the built-in alphabet;
    * a path to a font file, to the ``.zip`` one was downloaded in, or to the
      folder it was unpacked into — with ``~``, environment variables,
      surrounding quotes and ``file://`` all understood, and a Windows path
      translated under WSL;
    * a font's name — its own family or full name, or its file name — looked
      for in the font folders.

    The returned face always letters *something*: one that could not be loaded
    carries the built-in title and a ``problem`` saying why, so every caller
    draws the same thing and exactly one of them — the panel — has to explain
    it.
    """
    raw = str(spec or "").strip()
    text = _clean_spec(spec)
    if not text or text.lower() == "default":
        return FontFace(spec="default", title=builtin_title)

    path = ""
    archive = ""
    source = "file"
    problem = ""
    found_on_disk = False
    if _looks_like_path(text):
        candidate = _as_path(text)
        if _is_dir(candidate):
            found_on_disk = True
            best = _best_in_folder(candidate)
            if best is None:
                problem = f"there is no font file in {text}"
            else:
                path = str(best)
        elif _is_file(candidate):
            found_on_disk = True
            if candidate.suffix.lower() in ARCHIVE_SUFFIXES:
                faces, problem = unpack_archive(candidate)
                if faces:
                    path, archive = str(faces[0]), str(candidate)
            else:
                path = str(candidate)
        elif any(mark in text for mark in ("/", "\\", "~")) or _WINDOWS_PATH.match(text):
            return FontFace(
                spec=raw,
                title=builtin_title,
                problem=f"there is no font file at {text}",
            )
    if problem:
        return FontFace(spec=raw, title=builtin_title, source=source, problem=problem)
    if not found_on_disk:
        # A name — or a bare file name that is not in the working folder,
        # which is a name with a suffix on it.
        path, archive = _lookup(text)
        source = "system"
        if not path:
            return FontFace(
                spec=raw,
                title=builtin_title,
                source=source,
                problem=(
                    f"no font called {text!r} in the font folders — give the "
                    "path to the file (or to the .zip it came in), or drop it "
                    f"in {display_font_dir()}"
                ),
            )

    parts = _pillow()
    if parts is None:
        return FontFace(
            spec=raw,
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
    family = style = None
    failure: Optional[Exception] = None
    # Sixteen first; then the sizes a face made only of bitmaps is usually cut
    # at, because such a face loads at its own sizes and refuses every other.
    for size in (16, *_STRIKE_SIZES):
        try:
            family, style = ImageFont.truetype(path, size).getname()
            failure = None
            break
        except Exception as exc:  # noqa: BLE001 — every failure is the same answer
            failure = failure or exc
            if "pixel size" not in str(exc).lower():
                break
    if failure is not None:
        return FontFace(
            spec=raw,
            title=builtin_title,
            path="",
            source=source,
            problem=f"{Path(path).name} could not be read as a font ({failure})",
        )
    names = read_font_names(path)
    title = (
        " ".join(part for part in (family, style) if part)
        or names.display
        or Path(path).stem
    )
    return FontFace(spec=raw, title=title, path=path, source=source, archive=archive)


# ── Lettering ────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=48)
def _load(path: str, stamp: float, size: int, encoding: str):
    """A FreeType face at ``size`` pixels. Raises when it will not load.

    ``stamp`` is the file's modification time, so a font replaced on disk is
    a different key rather than a stale face.
    """
    parts = _pillow()
    if parts is None:
        raise RuntimeError("Pillow is not installed")
    ImageFont = parts[2]
    # The basic layout, always, rather than a shaping engine where one is
    # installed. It sets each glyph on a whole pixel with the hinted advance,
    # which is what keeps one-bit type crisp and its spacing even — a shaper
    # places glyphs at fractions of a pixel, and the hinting that made each
    # stem a clean pixel wide is shifted off it. It looks every character up
    # in the charmap selected here, which is what a symbol or Mac face needs.
    # And it is the same on every machine: whether Pillow found its shaping
    # library is an accident of the install, and the plate should not be.
    # The titles it letters are Latin, so there is nothing to shape.
    kwargs: Dict[str, Any] = {"layout_engine": ImageFont.Layout.BASIC}
    if encoding:
        kwargs["encoding"] = encoding
    return ImageFont.truetype(path, size, **kwargs)


def _layout(font: Any, text: str, charmap: _Charmap, mode: str):
    """``(width, top, bottom, runs)`` for ``text``, or ``None`` with no ink.

    ``runs`` are ``(x, string)`` pairs drawn at ``y = -top``. Normally one
    run; for a face with no space glyph, one per word, set apart by a third
    of an em, because a space it cannot draw would come out as its empty box.
    """
    words = [text] if charmap.covers(" ") else [word for word in text.split(" ") if word]
    size = getattr(font, "size", 10) or 10
    gap = max(1, round(size * 0.33)) if len(words) > 1 else 0
    runs: List[Tuple[int, str]] = []
    cursor = 0
    top: Optional[int] = None
    bottom: Optional[int] = None
    for word in words:
        encoded = _encode(word, charmap)
        try:
            x0, y0, x1, y1 = font.getbbox(encoded, mode=mode)
        except Exception:
            return None
        if x1 <= x0 or y1 <= y0:
            continue
        runs.append((cursor - x0, encoded))
        cursor += (x1 - x0) + gap
        top = y0 if top is None else min(top, y0)
        bottom = y1 if bottom is None else max(bottom, y1)
    if not runs or top is None or bottom is None:
        return None
    return cursor - gap, top, bottom, runs


def _draw(font: Any, layout, mode: str):
    """The laid-out string on a canvas of its layout box, or ``None``."""
    parts = _pillow()
    if parts is None:
        return None
    Image, ImageDraw, _ImageFont = parts
    width, top, bottom, runs = layout
    try:
        image = Image.new("L", (max(1, width), max(1, bottom - top)), 0)
        draw = ImageDraw.Draw(image)
        if mode == "1":
            draw.fontmode = "1"
        for x, encoded in runs:
            draw.text((x, -top), encoded, font=font, fill=255)
    except Exception:
        return None
    return image


def _ink(image) -> Optional[Tuple[int, int, int, int]]:
    """The box of the pixels that count as ink, or ``None`` for a blank."""
    try:
        return image.point(lambda value: 255 if value >= INK else 0).getbbox()
    except Exception:
        return None


def _measured(path: str, stamp: float, text: str, size: float, charmap: _Charmap, mode: str):
    """``(image, ink)`` for ``text`` drawn at ``size``, or ``None`` with no ink."""
    try:
        font = _load(path, stamp, size, charmap.encoding)
    except Exception:
        return None
    layout = _layout(font, text, charmap, mode)
    if layout is None:
        return None
    image = _draw(font, layout, mode)
    ink = _ink(image) if image is not None else None
    if ink is None:
        return None
    return image, ink


def _tallest(path: str, stamp: float, text: str, rows: int, charmap: _Charmap, mode: str):
    """``(size, image, ink)`` at the largest size whose ink fits ``rows``.

    Height only — the width is the caller's question, because the answer to
    this one does not change as a window is dragged wider and narrower, and
    so is worked out once per title and height rather than once per width.

    Measured on the *ink*, not on FreeType's boxes. A glyph's box is its
    outline's control box rounded outwards, which over-states the ink by a
    pixel or two at the top and bottom — and at ten pixels of plate that is a
    whole row of the five asked for, left empty. So the boxes find the
    neighbourhood quickly, and the drawn ink settles it, down to a quarter of
    a pixel of font size where the installed Pillow can take one.
    """
    target = max(2, rows * 2)

    # The neighbourhood: the largest size whose boxes fit the height. The ink
    # is inside the boxes, so it fits there too.
    low, high, start = 4, 400, 4
    loaded = False
    while low <= high:
        middle = (low + high) // 2
        try:
            font = _load(path, stamp, middle, charmap.encoding)
            loaded = True
            layout = _layout(font, text, charmap, mode)
        except Exception:
            layout = None
        if layout is not None and layout[2] - layout[1] <= target:
            start = middle
            low = middle + 1
        else:
            high = middle - 1
    if not loaded:
        # Not one size in the search would load: a face drawn only as
        # bitmaps, which exists at its own few sizes. Take the largest of
        # those whose ink fits.
        best = None
        for size in range(4, 161):
            measured = _measured(path, stamp, text, size, charmap, mode)
            if measured is not None:
                _x0, y0, _x1, y1 = measured[1]
                if y1 - y0 <= target:
                    best = (size, measured[0], measured[1])
        return best

    # Settled on the ink: walk up from there while the ink still fits.
    best = None
    size: float = start
    while size <= min(400, start + 12):
        measured = _measured(path, stamp, text, size, charmap, mode)
        if measured is not None:
            _x0, y0, _x1, y1 = measured[1]
            if y1 - y0 > target:
                break
            best = (size, measured[0], measured[1])
        size += 1
    if best is None:
        return None
    for fraction in (0.75, 0.5, 0.25):
        measured = _measured(path, stamp, text, best[0] + fraction, charmap, mode)
        if measured is not None:
            _x0, y0, _x1, y1 = measured[1]
            if y1 - y0 <= target:
                best = (best[0] + fraction, measured[0], measured[1])
                break
    return best


def _narrowest(path: str, stamp: float, text: str, rows: int, columns: int, below: float, charmap: _Charmap, mode: str):
    """``(image, ink)`` at the largest size under ``below`` that fits both ways."""
    target = max(2, rows * 2)
    found = None
    low, high = 4, int(below) - (1 if float(below).is_integer() else 0)
    while low <= high:
        middle = (low + high) // 2
        measured = _measured(path, stamp, text, middle, charmap, mode)
        fits = False
        if measured is not None:
            _x0, y0, x1, y1 = measured[1]
            fits = y1 - y0 <= target and x1 <= columns
        if fits:
            found = measured
            low = middle + 1
        else:
            high = middle - 1
    return found


#: The full-height measurement of a title, keyed by the face file, the text
#: and the height: ``(mode, size, image, ink, lines)``. Width-independent, so
#: a window being dragged re-uses it at every width instead of re-measuring.
_TALLEST: dict = {}


def _full_height(face: FontFace, text: str, rows: int):
    """``(mode, size, image, ink, lines)`` for ``text`` at full height, or ``None``.

    Monochrome first: FreeType's own one-bit rendering, hinted, which is what
    keeps a thin stroke a stroke at a dozen pixels — anti-aliased and cut at a
    threshold, the same stroke comes out half-covered on two pixels and drops
    out of both. Anti-aliased only when that drew nothing at all: a face whose
    glyphs are bitmaps or colour, which the one-bit path does not draw.
    """
    stamp = _mtime(face.path)
    key = (face.path, stamp, text, rows)
    if key in _TALLEST:
        return _TALLEST[key]
    found = None
    if _pillow() is not None:
        charmap = _charmap(face.path)
        for mode in ("1", ""):
            tallest = _tallest(face.path, stamp, text, rows, charmap, mode)
            if tallest is None:
                continue
            size, image, ink = tallest
            lines = _fold(image, ink)
            if lines:
                found = (mode, size, image, ink, lines)
                break
    if len(_TALLEST) >= _CACHE_MAX:
        _TALLEST.clear()
    _TALLEST[key] = found
    return found


def _fold(image, ink) -> List[str]:
    """A drawn string, cropped to its ink rows, as rows of half-blocks.

    Cropped top and bottom to the ink so the type starts on the first pixel
    row of the first cell — a pixel of air above it would push the bottom of
    the letters into a row the plate did not ask for. Not cropped on the
    left: the rows are slices of one image and keep its origin, which is what
    lets every caller centre the block rather than each row.
    """
    _x0, y0, x1, y1 = ink
    try:
        cropped = image.crop((0, y0, x1, y1))
        width, height = cropped.size
        pixels = cropped.load()
    except Exception:
        return []

    def lit(x: int, y: int) -> bool:
        return y < height and pixels[x, y] >= INK

    out: List[str] = []
    for y in range(0, height, 2):
        line = []
        for x in range(width):
            upper = lit(x, y)
            lower = lit(x, y + 1)
            line.append(
                "\u2588" if upper and lower
                else "\u2580" if upper
                else "\u2584" if lower
                else " "
            )
        out.append("".join(line).rstrip())
    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return out


def _render(face: FontFace, text: str, rows: int, columns: int) -> Tuple[List[str], bool]:
    """``(lines, narrowed)`` — ``text`` lettered in ``face`` at the best fit.

    Height first, width second. A plate is a fixed number of rows and the
    string in it varies, so sizing on height keeps every wordmark the same
    weight on the page; the width check then shrinks only the ones that would
    run off the end. ``narrowed`` says the width did shrink it — which is what
    :func:`plan_wordmark` refuses, so that it can try a shorter title instead.
    """
    if _pillow() is None:
        return [], False
    fitted_text = fit_text_to_face(text, _charmap(face.path))
    if not fitted_text.strip():
        return [], False
    full = _full_height(face, fitted_text, rows)
    if full is None:
        return [], False
    mode, size, _image, ink, lines = full
    if ink[2] <= columns:
        return list(lines), False
    narrowed = _narrowest(
        face.path, _mtime(face.path), fitted_text, rows, columns, size, _charmap(face.path), mode
    )
    if narrowed is None:
        return [], True
    return _fold(*narrowed), True


def _lettering(face: FontFace, text: str, rows: int, columns: int) -> Tuple[List[str], bool]:
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
    return list(_lettering(face, text, rows, columns)[0])


def plan_wordmark(
    face: FontFace,
    titles: Sequence[str],
    *,
    rows: int = DEFAULT_ROWS,
    columns: int = 80,
) -> Wordmark:
    """The wordmark for a plate ``columns`` wide, and how it was arrived at.

    The wordmark **shortens before it shrinks**, and that rule is the whole
    difference between lettering and texture. Fitted to width, a long title
    in a narrow plate comes back at whatever size fitted — two pixels of cap
    height, a row of grey specks that is not a word at all. So a title the
    width would shrink is refused and the next one down is tried, and every
    wordmark the console draws is the same size; only which words are in it
    changes with the width. That is the same ladder the built-in plate walks.

    And when even the shortest title will not fit at the asked-for height, it
    **shrinks a row at a time** rather than giving up — down to
    :data:`LEGIBLE_ROWS`, below which letters stop being letters. A plate made
    taller than the window can take, or a face too wide for a narrow one, is
    drawn at the tallest height that fits instead of silently going back to
    the built-in title, which is what a font that "does nothing" looks like.
    """
    asked = max(MIN_ROWS, min(MAX_ROWS, int(rows)))
    if not face.custom:
        return Wordmark(asked=asked, reason="builtin")
    columns = int(columns)
    wanted = [title.strip() for title in titles if title and title.strip()]
    charmap = _charmap(face.path)
    if not any(fit_text_to_face(title, charmap).strip() for title in wanted):
        return Wordmark(asked=asked, reason="glyphs")
    if columns >= 4 and _pillow() is not None:
        floor = min(asked, LEGIBLE_ROWS)
        adapted = [(title, fit_text_to_face(title, charmap)) for title in wanted]
        adapted = [(title, text) for title, text in adapted if text.strip()]
        # Columns per pixel of height, per title, from the first measurement
        # of it: a title already far too wide for this plate one row up is
        # not measured again a row down only to be refused again. Generous,
        # because hinting makes small type relatively wider, never narrower.
        aspect: Dict[str, float] = {}
        for height in range(asked, floor - 1, -1):
            for title, text in adapted:
                known = aspect.get(text)
                if known is not None and known * 2 * height > columns * 1.15:
                    continue
                # The full-height lettering only: a title the width would
                # shrink is the one this ladder exists to refuse, so there is
                # no narrowed render to make — which is also what keeps a
                # window being dragged from re-rasterising at every width.
                full = _full_height(face, text, height)
                if full is None:
                    continue
                _mode, _size, _image, ink, lines = full
                aspect.setdefault(text, ink[2] / max(1, ink[3] - ink[1]))
                if ink[2] <= columns:
                    return Wordmark(tuple(lines), title, height, asked)
    return Wordmark(asked=asked, reason="narrow")


def render_wordmark(
    face: FontFace,
    titles: Sequence[str],
    *,
    rows: int = DEFAULT_ROWS,
    columns: int = 80,
) -> List[str]:
    """The lines of :func:`plan_wordmark` — ``[]`` when nothing can be lettered.

    ``[]`` when nothing fits even shrunk, when the face is the built-in one,
    or when a font will not rasterise: the caller letters the plate the way it
    always did.
    """
    return list(plan_wordmark(face, titles, rows=rows, columns=columns).lines)


def clamp_rows(value: Any) -> int:
    """A stored plate height, made safe."""
    try:
        rows = int(value)
    except (TypeError, ValueError):
        return DEFAULT_ROWS
    return max(MIN_ROWS, min(MAX_ROWS, rows))


def forget_rendered() -> None:
    """Drop every cache: renders, loaded faces, names, and the font index.

    For tests, for RESCAN, and for a font replaced or installed on disk while
    the console is open.
    """
    global _INDEX, _INDEX_BUILT, _LISTING
    _CACHE.clear()
    _TALLEST.clear()
    _NAMES.clear()
    _CHARMAPS.clear()
    _LOOKUPS.clear()
    _INDEX = None
    _INDEX_BUILT = 0.0
    _LISTING = None
    clear = getattr(_load, "cache_clear", None)
    if clear is not None:
        clear()


__all__ = [
    "ARCHIVE_SUFFIXES",
    "DEFAULT_ROWS",
    "FONT_DIRS",
    "FONT_SUFFIXES",
    "FontFace",
    "FontNames",
    "LEGIBLE_ROWS",
    "MAX_ROWS",
    "MIN_ROWS",
    "TIER_CONSOLE",
    "TIER_PERSONAL",
    "TIER_SYSTEM",
    "Wordmark",
    "clamp_rows",
    "curie_font_dir",
    "display_font_dir",
    "ensure_font_dir",
    "fit_text_to_face",
    "font_folders",
    "forget_rendered",
    "parse_fontconfig_listing",
    "plan_wordmark",
    "read_font_names",
    "render_lettering",
    "render_wordmark",
    "resolve_face",
    "system_fonts",
    "unpack_archive",
    "wsl_path",
]
