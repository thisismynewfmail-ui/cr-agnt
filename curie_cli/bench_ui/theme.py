"""Skin → Textual theme bridge.

The console must not carry a second palette. Everything it paints comes from
the active skin, so ``curie skin curie-vga`` re-themes this console, the CLI
banner, and the TUI status bar in one move.

Textual wants a small set of semantic roles (primary, panel, foreground, …);
the skin speaks in surface-specific keys (``banner_title``, ``status_bar_bad``,
…). This module maps one onto the other, and picks the light or dark half of
a skin's paired palette to match the terminal it is actually running in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict

from curie_cli.bench_ui.contrast import (
    AA_LARGE,
    AA_TEXT,
    contrast_ratio,
    ensure_contrast,
    is_dark,
    parse_hex,
    to_hex,
)

# Fallbacks are the bench palette's own values, used only when a skin omits a
# key entirely. They are never a *second* theme: a complete skin overrides
# every one of them.
_FALLBACK = {
    "background": "#F4F1EA",
    "panel": "#EDE8DE",
    "foreground": "#2B2A27",
    "primary": "#B4541A",
    "secondary": "#8A6A21",
    "accent": "#8A3B12",
    "success": "#2F6B33",
    "warning": "#B07105",
    "error": "#A32218",
    "border": "#8C7A5B",
    "dim": "#6E675C",
    "rule": "#B39A72",
    "selection": "#E4D9C6",
}


@dataclass(frozen=True)
class BenchPalette:
    """The colours the console paints with, resolved from the active skin."""

    values: Dict[str, str]
    dark: bool
    skin_name: str
    #: Non-colour marks the skin supplies. Kept apart from ``values`` because
    #: everything in there becomes a CSS colour variable.
    glyphs: Dict[str, str] = field(default_factory=dict)
    #: Non-colour *rendering* parameters the display mode supplies — which
    #: mode is in force, and whatever that mode draws differently (the DOS
    #: mode's phosphor, glow and scanlines). Kept out of ``values`` for the
    #: same reason ``glyphs`` is: nothing in here is a CSS colour. Widgets
    #: that draw their own glyphs read it off the app rather than importing
    #: the mode, so a mode can be added without touching an instrument.
    optics: Dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> str:
        return self.values[key]

    @property
    def tool_prefix(self) -> str:
        """The gutter mark drawn beside a tool call, from the active skin."""
        return self.glyphs.get("tool_prefix") or "\u2502"

    @property
    def mode(self) -> str:
        """Which display mode painted this palette. ``bench`` by default."""
        return str(self.optics.get("mode") or "bench")

    @property
    def dos(self) -> bool:
        """Whether the DOS phosphor mode is the one in force."""
        return self.mode == "dos"

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def as_css_variables(self) -> Dict[str, str]:
        """Render as Textual CSS variables (``$bench-primary`` etc.).

        Prefixed so they cannot collide with Textual's own design tokens,
        which the stylesheet also uses for widget chrome.
        """
        return {f"bench-{k}": v for k, v in self.values.items()}


def terminal_prefers_dark() -> bool:
    """Best guess at the terminal's background polarity.

    There is no portable query for this, so use the signals that exist, in
    order of how much they actually mean:

    1. ``CURIE_UI_POLARITY`` — the user telling us outright.
    2. ``COLORFGBG`` — set by xterm/rxvt/Konsole as ``fg;bg`` ANSI indices;
       a background index of 7 or 15 is a light terminal.
    3. Otherwise assume light, because that is what this palette is authored
       for and a light-authored palette on a dark terminal is the more
       recoverable mistake (the paired dark block exists precisely for it).
    """
    explicit = os.environ.get("CURIE_UI_POLARITY", "").strip().lower()
    if explicit in {"dark", "light"}:
        return explicit == "dark"

    fgbg = os.environ.get("COLORFGBG", "")
    if ";" in fgbg:
        bg = fgbg.rsplit(";", 1)[-1].strip()
        if bg.isdigit():
            return int(bg) not in {7, 15}

    return False


def _pick_palette(skin: Any, dark: bool) -> Dict[str, str]:
    """Resolve a skin's colour block for the given polarity.

    A skin authored for one polarity may ship a hand-tuned block for the
    other (``light_colors`` / ``dark_colors``). Those are merged *over* the
    base ``colors`` rather than replacing it, so a partial paired block still
    resolves to a complete palette — the same contract the TUI uses.
    """
    base = dict(getattr(skin, "colors", {}) or {})
    paired = getattr(skin, "dark_colors" if dark else "light_colors", {}) or {}
    base.update(paired)
    return base


# Minimum contrast each role must clear against the ground it is painted on.
# Body text gets WCAG AA for normal text; everything else is either large text
# or a non-text UI component, both of which AA sets at 3:1.
_TARGETS = {
    "foreground": AA_TEXT,
    "primary": AA_LARGE,
    "secondary": AA_LARGE,
    "accent": AA_LARGE,
    "success": AA_LARGE,
    "warning": AA_LARGE,
    "error": AA_LARGE,
    "dim": AA_LARGE,
    "border": AA_LARGE,
    "rule": AA_LARGE,
}

# Roles the console paints on the panel fill rather than on the window ground.
_ON_PANEL = frozenset({"accent", "dim", "secondary"})


def _neutral_ground(dark: bool) -> str:
    """A last-resort ground when a skin's own is unusable."""
    return "#14120F" if dark else "#F4F1EA"


def _derive_grounds(
    colours: Dict[str, str], foreground: str, dark: bool
) -> tuple[str, str]:
    """Pick a window background and panel fill that the foreground can sit on.

    A skin's ``background`` cannot be taken at face value. Skins are authored
    for the CLI, where the window ground belongs to the user's terminal, and
    unset keys inherit from the default skin — so a dark-authored skin like
    ``mono`` arrives carrying the bench's *light* background alongside its own
    *light* foreground, and every word would land at 1.05:1.

    So the foreground decides. Whichever ground candidate actually contrasts
    with it wins; if none does, a neutral of the correct polarity is used.
    """
    fg_is_light = not is_dark(foreground)
    # A usable ground is one the body text can be read on.
    def usable(candidate: str | None) -> bool:
        if not isinstance(candidate, str) or not candidate.startswith("#"):
            return False
        try:
            return (
                contrast_ratio(foreground, candidate) >= AA_TEXT
                and is_dark(candidate) == fg_is_light
            )
        except ValueError:
            return False

    background = next(
        (c for c in (colours.get("background"), _neutral_ground(fg_is_light)) if usable(c)),
        _neutral_ground(fg_is_light),
    )

    # The panel is a fill that sits *next to* the background, so it should
    # differ from it visibly while still carrying text. A skin's status-bar
    # fill is the natural candidate; when it is the wrong polarity (again,
    # inheritance), nudge the background instead of adopting a fill that
    # swallows the text.
    panel_candidates = (
        colours.get("status_bar_bg"),
        colours.get("completion_menu_bg"),
    )
    panel = next((c for c in panel_candidates if usable(c)), "")
    if not panel:
        panel = _shift_fill(background, fg_is_light)
    return background, panel


def _shift_fill(background: str, toward_dark: bool) -> str:
    """A panel fill one step off the background, in the same polarity.

    Used when a skin has no usable fill of its own. Keeping it close to the
    background means the panel reads as a raised area rather than as a
    second, competing surface.
    """
    try:
        r, g, b = parse_hex(background)
    except ValueError:
        return background
    delta = -0.06 if toward_dark else 0.06
    return to_hex(tuple(max(0.0, min(1.0, channel + delta)) for channel in (r, g, b)))


def resolve_palette(skin: Any = None, dark: bool | None = None) -> BenchPalette:
    """Map the active skin onto the console's semantic roles.

    Every role is contrast-checked against the ground it will be painted on
    and corrected if it falls short. A well-authored skin passes through
    untouched; a skin whose colours do not combine is made readable rather
    than rendered as-is, because "the theme is unreadable" is not a state the
    user can debug from the outside.
    """
    if skin is None:
        try:
            from curie_cli.skin_engine import get_active_skin

            skin = get_active_skin()
        except Exception:
            skin = None
    if dark is None:
        dark = terminal_prefers_dark()

    colours = _pick_palette(skin, dark) if skin is not None else {}

    def pick(*keys: str, role: str) -> str:
        for key in keys:
            value = colours.get(key)
            if isinstance(value, str) and value.startswith("#"):
                return value
        return _FALLBACK[role]

    foreground = pick("banner_text", "prompt", role="foreground")
    background, panel = _derive_grounds(colours, foreground, bool(dark))

    values = {
        "background": background,
        "panel": panel,
        "foreground": foreground,
        # Signal colours.
        "primary": pick("ui_accent", "banner_accent", role="primary"),
        "secondary": pick("ui_label", "session_label", role="secondary"),
        "accent": pick("banner_title", "status_bar_strong", role="accent"),
        "success": pick("ui_ok", "status_bar_good", role="success"),
        "warning": pick("ui_warn", "status_bar_warn", role="warning"),
        "error": pick("ui_error", "status_bar_critical", role="error"),
        # Chrome.
        "border": pick("banner_border", "session_border", role="border"),
        "dim": pick("banner_dim", "status_bar_dim", role="dim"),
        "rule": pick("input_rule", "banner_border", role="rule"),
        "selection": pick("selection_bg", "completion_menu_current_bg", role="selection"),
    }

    # The correction pass. Each role is measured against its own ground —
    # panel-painted roles against the panel, the rest against the window.
    for role, target in _TARGETS.items():
        ground = panel if role in _ON_PANEL else background
        values[role] = ensure_contrast(values[role], ground, target)

    # The selection fill is a background, not a foreground: it has to differ
    # from the window ground enough to be visible, while still carrying the
    # body text.
    if contrast_ratio(values["selection"], background) < 1.15:
        values["selection"] = _shift_fill(background, not is_dark(background))
    if contrast_ratio(values["foreground"], values["selection"]) < AA_LARGE:
        values["selection"] = _shift_fill(background, not is_dark(background))

    values.update(_band_roles(values))

    return BenchPalette(
        values=values,
        dark=bool(dark),
        skin_name=str(getattr(skin, "name", "") or "default"),
        glyphs={
            "tool_prefix": str(getattr(skin, "tool_prefix", "") or "") or "\u2502",
        },
    )


#: The roles a *band* is painted with — a run of fill with text on top of it,
#: which is what a highlighted row, a selected switch and a title bar all are.
#: Every palette defines them, whichever mode built it, because the stylesheet
#: is one document: a rule that names ``$bench-band`` has to resolve under the
#: bench palette too, even though only the DOS mode paints hard inverse video
#: with it.
BAND_ROLES = ("band", "ink", "inkdim")


def _band_roles(values: Dict[str, str]) -> Dict[str, str]:
    """Derive the band fill and the two weights of ink that go on it.

    Under the bench palette a band is the ordinary selection highlight, so the
    ink on it is simply the body text. The DOS mode overrides all three with
    true inverse video — a bright phosphor fill and the dark glass as ink.
    """
    band = values.get("selection") or values["panel"]
    ink = ensure_contrast(values["foreground"], band, AA_TEXT)
    return {
        "band": band,
        "ink": ink,
        "inkdim": ensure_contrast(values["dim"], band, AA_LARGE),
    }


__all__ = [
    "BAND_ROLES",
    "BenchPalette",
    "resolve_palette",
    "terminal_prefers_dark",
]
