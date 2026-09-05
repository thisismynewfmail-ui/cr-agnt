"""The console must not carry a palette of its own.

Every colour it paints comes from the active skin, so `curie skin <name>`
re-themes the console, the CLI banner and the TUI status bar in one move. A
second palette living in the console would drift from the other two the first
time anyone edited either.
"""

from __future__ import annotations

import pytest

from curie_cli.bench_ui.theme import resolve_palette, terminal_prefers_dark

ROLES = (
    "background", "panel", "foreground", "primary", "secondary", "accent",
    "success", "warning", "error", "border", "dim", "rule", "selection",
    # The band roles: a run of fill with text on it, which is what a
    # highlighted row, a thrown switch and a title bar all are. Every palette
    # defines them because the stylesheet is one document — a rule naming
    # $bench-band has to resolve under this palette as well as under the DOS
    # mode's, which is the one that paints hard inverse video with them.
    "band", "ink", "inkdim",
)


class _Skin:
    """A skin stand-in with exactly the attributes the bridge reads."""

    def __init__(self, name="test", colors=None, light_colors=None, dark_colors=None):
        self.name = name
        self.colors = colors or {}
        self.light_colors = light_colors or {}
        self.dark_colors = dark_colors or {}


def test_every_role_resolves_to_a_colour():
    palette = resolve_palette(_Skin(), dark=False)
    for role in ROLES:
        value = palette[role]
        assert value.startswith("#") and len(value) == 7, f"{role} = {value!r}"


def test_skin_colours_win_over_fallbacks():
    skin = _Skin(colors={"ui_accent": "#123456", "banner_text": "#654321"})
    palette = resolve_palette(skin, dark=False)
    assert palette["primary"] == "#123456"
    assert palette["foreground"] == "#654321"


def _hue(colour: str) -> float:
    """Hue of a hex colour, for assertions that survive contrast correction.

    The palette runs a contrast pass that may move a colour's lightness, so a
    test pinning an exact hex is really testing the correction, not the thing
    it means to. Hue is what identifies the colour as the skin's.
    """
    import colorsys

    from curie_cli.bench_ui.contrast import parse_hex

    return colorsys.rgb_to_hls(*parse_hex(colour))[0]


def test_paired_block_overlays_the_base_rather_than_replacing_it():
    """A partial paired block must still resolve to a complete palette.

    Same contract the TUI uses: ``dark_colors`` is a set of corrections for
    a dark terminal, not a whole second theme, so a skin that tunes two keys
    keeps the other thirty-nine.
    """
    skin = _Skin(
        colors={"ui_accent": "#AA0000", "banner_text": "#EEEEEE"},
        dark_colors={"ui_accent": "#FF8888"},
    )
    dark = resolve_palette(skin, dark=True)
    # The paired value is a pink-red; the base is a pure red. Both share a hue
    # near 0, so compare against what the *base* would have produced instead.
    base_only = resolve_palette(
        _Skin(colors={"ui_accent": "#AA0000", "banner_text": "#EEEEEE"}), dark=True
    )
    assert dark["primary"] != base_only["primary"], "paired override should win"
    assert dark["foreground"] == "#EEEEEE", "unpaired key should fall through"


def test_light_and_dark_resolve_differently_for_a_paired_skin():
    """The two halves must not collapse onto one another."""
    light_skin = _Skin(
        colors={"ui_accent": "#B4541A", "banner_text": "#2B2A27"},
        dark_colors={"ui_accent": "#E07B32", "banner_text": "#EFE7D8"},
    )
    light = resolve_palette(light_skin, dark=False)
    dark = resolve_palette(light_skin, dark=True)

    assert light["primary"] != dark["primary"]
    # Both are the skin's amber, just at different lightness for their ground.
    assert _hue(light["primary"]) == pytest.approx(_hue(dark["primary"]), abs=0.03)
    # And the grounds themselves must differ in polarity.
    from curie_cli.bench_ui.contrast import is_dark

    assert is_dark(dark["background"]) and not is_dark(light["background"])


def test_non_colour_values_are_ignored():
    """A skin key holding a name rather than a hex triplet must not be used.

    Textual CSS needs a value it can parse; a colour *name* that Rich accepts
    would fail there, and the failure would be a stylesheet parse error at
    startup rather than one wrong colour.
    """
    skin = _Skin(colors={"ui_accent": "red", "banner_text": ""})
    palette = resolve_palette(skin, dark=False)
    assert palette["primary"].startswith("#")
    assert palette["foreground"].startswith("#")


def test_missing_skin_still_yields_a_complete_palette():
    palette = resolve_palette(_Skin(colors={}), dark=False)
    assert all(palette[role].startswith("#") for role in ROLES)


def test_css_variables_are_namespaced():
    """``$bench-*`` cannot collide with Textual's own design tokens."""
    variables = resolve_palette(_Skin(), dark=False).as_css_variables()
    assert set(variables) == {f"bench-{role}" for role in ROLES}


@pytest.mark.parametrize("value,expected", [("dark", True), ("light", False)])
def test_explicit_polarity_wins(monkeypatch, value, expected):
    monkeypatch.setenv("CURIE_UI_POLARITY", value)
    assert terminal_prefers_dark() is expected


@pytest.mark.parametrize(
    "colorfgbg,expected",
    [
        ("15;0", True),    # white on black
        ("0;15", False),   # black on white
        ("0;7", False),    # black on light gray
        ("7;0", True),
    ],
)
def test_colorfgbg_is_read_when_polarity_is_unset(monkeypatch, colorfgbg, expected):
    monkeypatch.delenv("CURIE_UI_POLARITY", raising=False)
    monkeypatch.setenv("COLORFGBG", colorfgbg)
    assert terminal_prefers_dark() is expected


def test_light_is_the_fallback(monkeypatch):
    """The palette is authored light; guessing light is the safer error."""
    monkeypatch.delenv("CURIE_UI_POLARITY", raising=False)
    monkeypatch.delenv("COLORFGBG", raising=False)
    assert terminal_prefers_dark() is False


def test_garbage_colorfgbg_does_not_raise(monkeypatch):
    monkeypatch.delenv("CURIE_UI_POLARITY", raising=False)
    monkeypatch.setenv("COLORFGBG", "not;a;number")
    assert terminal_prefers_dark() is False


def test_the_real_default_skin_resolves():
    """The shipped skin must produce a usable palette with no arguments."""
    palette = resolve_palette(dark=False)
    assert all(palette[role].startswith("#") for role in ROLES)
    assert palette.skin_name
