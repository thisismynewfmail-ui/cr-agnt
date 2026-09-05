"""Every skin must be readable in the console, including ones we didn't write.

Skins are authored for the CLI, where the window ground belongs to the user's
terminal and unset keys inherit from the default skin. The console owns its
ground, so those two facts combine badly: a dark-authored skin like ``mono``
arrives carrying the bench's *light* inherited background alongside its own
*light* foreground, and every word lands at 1.05:1.

Hand-patching each shipped skin would not fix a user's own. So the bridge
measures and corrects, and these tests hold it to that.
"""

from __future__ import annotations

import pytest

from curie_cli.bench_ui.contrast import (
    AA_LARGE,
    AA_TEXT,
    contrast_ratio,
    ensure_contrast,
    is_dark,
    parse_hex,
    relative_luminance,
    to_hex,
)
from curie_cli.bench_ui.theme import resolve_palette
from curie_cli.skin_engine import _BUILTIN_SKINS, load_skin

ON_BACKGROUND = (
    "foreground", "primary", "secondary", "accent",
    "success", "warning", "error", "dim", "border", "rule",
)
ON_PANEL = ("foreground", "accent", "dim", "secondary")

# Floating-point slack: ensure_contrast stops at the first step that clears
# the target, so results land just above it.
EPSILON = 0.05


# ── The arithmetic ───────────────────────────────────────────────────────

def test_known_contrast_ratios():
    """Anchors against the WCAG definition, not against our own output."""
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)
    assert relative_luminance("#000000") == pytest.approx(0.0, abs=1e-9)
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0, abs=1e-9)


def test_hex_round_trip():
    for value in ("#000000", "#FFFFFF", "#B4541A", "#2B2A27"):
        assert to_hex(parse_hex(value)) == value.upper()


def test_shorthand_hex_is_accepted():
    assert to_hex(parse_hex("#abc")) == "#AABBCC"


def test_is_dark_matches_which_text_colour_wins():
    assert is_dark("#000000") and is_dark("#1F1F1F")
    assert not is_dark("#FFFFFF") and not is_dark("#F4F1EA")


def test_a_passing_colour_is_returned_untouched():
    """A well-authored skin must not be repainted."""
    assert ensure_contrast("#2B2A27", "#F4F1EA", AA_TEXT) == "#2B2A27"


def test_a_failing_colour_is_corrected_to_target():
    fixed = ensure_contrast("#c9d1d9", "#F4F1EA", AA_TEXT)
    assert contrast_ratio(fixed, "#F4F1EA") >= AA_TEXT - EPSILON
    assert fixed != "#c9d1d9"


def test_correction_moves_away_from_the_ground():
    """On a light ground the fix darkens; on a dark ground it lightens.

    Each case uses a colour that genuinely fails on that ground — a mid-grey
    already clears 4.5:1 against black, so it would be returned untouched and
    prove nothing.
    """
    on_light = ensure_contrast("#888888", "#FFFFFF", AA_TEXT)
    assert relative_luminance(on_light) < relative_luminance("#888888")

    on_dark = ensure_contrast("#333333", "#000000", AA_TEXT)
    assert relative_luminance(on_dark) > relative_luminance("#333333")


def test_correction_preserves_hue():
    """The skin should still look like itself after correction."""
    import colorsys

    original = "#B4541A"
    fixed = ensure_contrast(original, "#B4541A", AA_TEXT)
    hue_before = colorsys.rgb_to_hls(*parse_hex(original))[0]
    hue_after = colorsys.rgb_to_hls(*parse_hex(fixed))[0]
    assert hue_after == pytest.approx(hue_before, abs=0.02)


def test_an_unreachable_target_returns_the_best_available():
    """A mid-grey ground caps at ~10.4:1; asking for 21 must not raise."""
    result = ensure_contrast("#808080", "#808080", 21.0)
    assert result.startswith("#")
    assert contrast_ratio(result, "#808080") > 1.0


def test_garbage_input_is_returned_unchanged():
    assert ensure_contrast("not-a-colour", "#FFFFFF", AA_TEXT) == "not-a-colour"


# ── The palettes ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("skin_name", sorted(_BUILTIN_SKINS))
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_every_shipped_skin_is_readable(skin_name, dark):
    palette = resolve_palette(load_skin(skin_name), dark=dark)
    background, panel = palette["background"], palette["panel"]

    body = contrast_ratio(palette["foreground"], background)
    assert body >= AA_TEXT - EPSILON, (
        f"{skin_name} ({'dark' if dark else 'light'}): body text at {body:.2f}:1"
    )

    for role in ON_BACKGROUND:
        ratio = contrast_ratio(palette[role], background)
        assert ratio >= AA_LARGE - EPSILON, (
            f"{skin_name}: {role} at {ratio:.2f}:1 on the window ground"
        )

    for role in ON_PANEL:
        ratio = contrast_ratio(palette[role], panel)
        assert ratio >= AA_LARGE - EPSILON, (
            f"{skin_name}: {role} at {ratio:.2f}:1 on the panel fill"
        )


@pytest.mark.parametrize("skin_name", sorted(_BUILTIN_SKINS))
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_ground_and_panel_are_the_same_polarity(skin_name, dark):
    """A panel of the opposite polarity reads as a hole punched in the window."""
    palette = resolve_palette(load_skin(skin_name), dark=dark)
    assert is_dark(palette["background"]) == is_dark(palette["panel"]), (
        f"{skin_name}: background {palette['background']} and panel "
        f"{palette['panel']} disagree on polarity"
    )


@pytest.mark.parametrize("skin_name", sorted(_BUILTIN_SKINS))
def test_selection_is_visible_but_still_carries_text(skin_name):
    palette = resolve_palette(load_skin(skin_name), dark=False)
    assert contrast_ratio(palette["selection"], palette["background"]) >= 1.1, (
        f"{skin_name}: selection fill is invisible against the ground"
    )
    assert contrast_ratio(palette["foreground"], palette["selection"]) >= AA_LARGE, (
        f"{skin_name}: text on a selected row is unreadable"
    )


def test_a_dark_authored_skin_gets_a_dark_ground():
    """The exact bug: mono's light text inherited the bench's light ground."""
    palette = resolve_palette(load_skin("mono"), dark=False)
    assert is_dark(palette["background"]), (
        "a skin whose foreground is light must be given a dark ground"
    )


def test_the_bench_palette_survives_correction_intact():
    """The shipped default is authored to pass; it must not be repainted."""
    palette = resolve_palette(load_skin("default"), dark=False)
    assert palette["background"] == "#F4F1EA"
    assert palette["foreground"] == "#2B2A27"
    assert palette["primary"] == "#B4541A"
    assert palette["accent"] == "#8A3B12"


def test_a_hostile_skin_is_still_readable():
    """The case that matters most: a skin we have never seen."""
    class _Hostile:
        name = "hostile"
        colors = dict.fromkeys(
            ("banner_text", "ui_accent", "ui_label", "banner_title", "ui_ok",
             "ui_warn", "ui_error", "banner_border", "banner_dim",
             "input_rule", "background", "status_bar_bg"),
            "#808080",
        )
        light_colors: dict = {}
        dark_colors: dict = {}

    palette = resolve_palette(_Hostile(), dark=False)
    background = palette["background"]
    assert contrast_ratio(palette["foreground"], background) >= AA_TEXT - EPSILON
    for role in ON_BACKGROUND:
        assert contrast_ratio(palette[role], background) >= AA_LARGE - EPSILON
