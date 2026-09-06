"""The console's lettering — the glyph alphabet it draws its chrome with.

A terminal program cannot choose a *typeface*: the font is the terminal
emulator's, and nothing an application writes can change it. What it can
choose is the **alphabet** it letters with — which box-drawing weights, which
shading densities, which markers — and on a character grid that is what a
reader sees as the face of the interface. Swap the double-line frames for
single ones and the four shading densities for two, and the same layout reads
as a different machine.

So that is what a face is here: a named set of the glyphs the console builds
its chrome out of. One is built in — :data:`DEFAULT_TYPEFACE`, the CP437
alphabet every part of the console is drawn in — and it is the one F1 puts
back.

This module is **only** the built-in alphabet. A font the reader chooses is a
different mechanism and lives in :mod:`curie_cli.bench_ui.fonts`: an outline
file, rasterised into cells, used for the console's display type. The two meet
at one setting — ``ui.typeface`` is ``default`` for this alphabet and a font
for that one — and at one key, F1, which puts this one back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class Typeface:
    """One lettering set: what the console draws its chrome out of."""

    #: The stored name — lower case, and what goes in ``config.yaml``.
    name: str
    #: How the PANEL pane titles it.
    title: str
    #: One line saying what it is.
    blurb: str
    #: Four shading densities, lightest first, with the blank at the head.
    shades: str
    #: The eighth-blocks, used for partial fills and the column traces.
    columns: str
    #: The 2×2 quadrant blocks, indexed by a bitmask — see
    #: :mod:`curie_cli.bench_ui.indicators`.
    quadrants: str
    #: Light, heavy and double horizontal rules, in that order.
    rules: Tuple[str, str, str]

    def sample(self, width: int = 14) -> str:
        """A strip of the face, for the chooser to show it by."""
        strip = (self.shades + self.columns).replace(" ", "")
        if not strip:
            return " " * max(0, width)
        out = (strip * (width // len(strip) + 1))[:max(0, width)]
        return out


#: The console's own alphabet: IBM code page 437, which is what a text-mode
#: program had to build an interface out of. Four shading densities, the
#: eighth-blocks, the quadrants, and three weights of rule. Every glyph here
#: is one the console is already drawn with — this face is a *description* of
#: what ships, not a new appearance, which is why restoring it is a repaint
#: and never a surprise.
CP437 = Typeface(
    name="default",
    title="CP437",
    blurb=(
        "the console's own alphabet: four shading densities, the "
        "eighth blocks, and three weights of rule"
    ),
    shades=" ░▒▓█",
    columns=" ▁▂▃▄▅▆▇█",
    quadrants=(
        " ▘▝▀▖▌▞▛"
        "▗▚▐▜▄▙▟█"
    ),
    rules=("─", "━", "═"),
)

_FACES: Tuple[Typeface, ...] = (CP437,)

#: The name a console falls back to when nothing has been chosen, or when what
#: has been chosen is not a face this build knows.
DEFAULT_TYPEFACE = CP437.name


def typeface_names() -> List[str]:
    """Every face this build can letter with, in the order PANEL lists them."""
    return [face.name for face in _FACES]


def typeface_catalogue() -> List[Tuple[str, str, str]]:
    """``(name, title, blurb)`` for each face — what a chooser would show."""
    return [(face.name, face.title, face.blurb) for face in _FACES]


def resolve_typeface(name: object) -> Typeface:
    """The named face, or the default for anything this build cannot letter.

    Total, like every other appearance read: a config file mid-edit, a face
    removed since it was chosen, or a value of the wrong shape resolves to
    the default rather than raising. A console that will not start because a
    lettering preference is malformed is worse than one that starts in CP437.
    """
    wanted = str(name or "").strip().lower()
    for face in _FACES:
        if face.name == wanted:
            return face
    return CP437


def is_default_typeface(name: object) -> bool:
    """Whether ``name`` *is* the default face, rather than resolving to it.

    The distinction is the whole point: a name this build cannot letter with
    resolves to the default — that is what :func:`resolve_typeface` is for —
    but it is still the wrong thing to have written down, and something has
    to be able to tell the two apart.
    """
    return str(name or "").strip().lower() == DEFAULT_TYPEFACE


__all__ = [
    "CP437",
    "DEFAULT_TYPEFACE",
    "Typeface",
    "is_default_typeface",
    "resolve_typeface",
    "typeface_catalogue",
    "typeface_names",
]
