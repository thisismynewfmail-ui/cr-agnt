"""Contrast arithmetic for the console's palette.

The console cannot trust a skin to hand it a readable combination. Skins are
authored for the CLI, where most colours are painted on whatever background
the user's terminal happens to have, and where unset keys inherit from the
default skin — so a dark-authored skin like ``mono`` inherits the bench's
light ``background`` while keeping its own light foreground, and every word
lands invisibly at 1.05:1.

So the bridge measures, and fixes what it finds. Given a foreground and the
ground it will actually be painted on, :func:`ensure_contrast` walks the
foreground's lightness away from the ground until it clears a target ratio,
holding hue and saturation so the skin still looks like itself.

Ratios are WCAG 2.x relative luminance, which is what "4.5:1" and "3:1" mean
everywhere else.
"""

from __future__ import annotations

import colorsys

__all__ = [
    "AA_LARGE",
    "AA_TEXT",
    "contrast_ratio",
    "ensure_contrast",
    "is_dark",
    "parse_hex",
    "relative_luminance",
    "to_hex",
]

#: WCAG AA for normal-size body text.
AA_TEXT = 4.5
#: WCAG AA for large text and for non-text UI components (borders, rules).
AA_LARGE = 3.0


def parse_hex(value: str) -> tuple[float, float, float]:
    """``"#RRGGBB"`` → three floats in 0..1. Accepts a leading ``#`` or not."""
    text = value.strip().lstrip("#")
    if len(text) == 3:  # #abc shorthand
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb: tuple[float, float, float]) -> str:
    """Three floats in 0..1 → ``"#RRGGBB"``."""
    return "#" + "".join(
        f"{max(0, min(255, round(channel * 255))):02X}" for channel in rgb
    )


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of a hex colour."""
    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    r, g, b = (linear(c) for c in parse_hex(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 … 21.0)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def is_dark(colour: str) -> bool:
    """Whether a colour reads as dark — i.e. white text beats black on it."""
    return contrast_ratio(colour, "#FFFFFF") > contrast_ratio(colour, "#000000")


def ensure_contrast(foreground: str, background: str, target: float) -> str:
    """Return ``foreground``, darkened or lightened until it clears ``target``.

    Hue and saturation are held; only lightness moves, and only in the one
    direction that increases contrast — away from the background. A colour
    that already clears the target is returned untouched, so a well-authored
    skin passes through unchanged and only the broken combinations are
    corrected.

    When even pure black or pure white cannot reach the target (a mid-grey
    background leaves at most ~10.4:1 in either direction, so any target above
    that is unreachable), the best available extreme is returned rather than
    an exception: a slightly-too-low ratio is still legible, and refusing to
    render is not an improvement.
    """
    try:
        if contrast_ratio(foreground, background) >= target:
            return foreground
        r, g, b = parse_hex(foreground)
    except ValueError:
        return foreground

    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    # Move away from the background: toward white on a dark ground, toward
    # black on a light one.
    towards_white = is_dark(background)

    best, best_ratio = foreground, contrast_ratio(foreground, background)
    # 100 steps over the lightness range is finer than the eye resolves and
    # costs nothing at palette-resolution time (once per skin change).
    for step in range(1, 101):
        fraction = step / 100.0
        moved = (
            lightness + (1.0 - lightness) * fraction
            if towards_white
            else lightness * (1.0 - fraction)
        )
        candidate = to_hex(colorsys.hls_to_rgb(hue, moved, saturation))
        ratio = contrast_ratio(candidate, background)
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
        if ratio >= target:
            return candidate
    return best
