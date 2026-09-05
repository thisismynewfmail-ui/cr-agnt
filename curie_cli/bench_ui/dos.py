"""The DOS display mode — the console as a phosphor terminal.

A second *skin* for the bench console, not a second palette: the mid-century
instrument panel and a 1988 text-mode program are two different interfaces
that happen to show the same state, so this mode changes the chrome as well
as the colours. A one-row inverse title bar instead of a two-row plate with a
rule under it. A numbered menu box instead of a switch rail. Norton's function
key bar — the digit in plain ink, the word on a lit band — instead of stencil
caps. Double-line CP437 frames around everything, because that is what a
program had to draw a window with.

It exists because the console's own visual language is a *panel*, and a panel
is not the only honest way to show a terminal that is thinking. This one has
one screen's worth of glass and sixteen colours, of which a monochrome
monitor showed you exactly one.

**Everything is one hue.** That is the constraint the look is built on, not a
limitation of it. An amber monitor could not paint an error red, so a text-mode
program said "this is worse than that" with *intensity* and with inverse video
— which is why those two are the only emphases in here. Three phosphors ship:
P3 amber (the orange in every photograph of a 1980s office), P1 green, and P4
white. Each is one hue at five brightnesses, plus the glass it sits on.

**Glow is a real effect, not a filter.** A CRT's lit pixels bloom into the
dark around them and the glass never returns to black, so the setting does two
measurable things: it mixes the phosphor into the ground (the haze) and mixes
the foreground toward its own peak (the bloom). Turn it up and the picture
gets brighter *and* lower in contrast, exactly as it does on a monitor with
the brightness wound past where the picture is sharp — which is why the
contrast floor at the bottom of :func:`resolve_palette` is not optional.

Nothing here talks to the agent, and nothing here decides *what* is drawn: the
state figures, the meters and the transcript are the same widgets reading the
same events in both modes. This module supplies the colours they paint in and
the marks they paint with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from rich.text import Text

from curie_cli.bench_ui.contrast import (
    AA_LARGE,
    AA_TEXT,
    contrast_ratio,
    ensure_contrast,
    parse_hex,
    to_hex,
)
from curie_cli.bench_ui.theme import BenchPalette

# ── The modes ────────────────────────────────────────────────────────────

#: The console's own instrument-panel look. The default, and what every
#: existing preference means when it says nothing about a mode.
MODE_BENCH = "bench"

#: The phosphor terminal in this module.
MODE_DOS = "dos"

MODES: Tuple[str, ...] = (MODE_BENCH, MODE_DOS)

#: What the PANEL pane calls each mode, and what it says about it.
MODE_CATALOGUE: Tuple[Tuple[str, str, str], ...] = (
    (
        MODE_BENCH,
        "BENCH PANEL",
        "Enamel ground, stencilled labels, signal-coloured lamps.",
    ),
    (
        MODE_DOS,
        "DOS PHOSPHOR",
        "One hue on dark glass, CP437 frames, inverse-video bands.",
    ),
)


def normalise_mode(value: Any) -> str:
    """Whatever was stored, as a mode this module recognises."""
    wanted = str(value or "").strip().lower()
    return wanted if wanted in MODES else MODE_BENCH


# ── Phosphors ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Phosphor:
    """One monitor's worth of colour: the glass, and the light on it.

    Five stops of a single hue. ``glass`` is the tube switched off — never
    pure black, because glass is not; ``peak`` is the colour a stroke goes at
    full drive, which on every phosphor is a wash toward white rather than a
    more saturated version of itself. That is why an over-driven amber
    monitor looks cream and not orange, and it is what makes the glow setting
    read as *light* rather than as a colour change.
    """

    name: str
    title: str
    blurb: str
    glass: str
    low: str
    mid: str
    high: str
    peak: str

    def ramp(self) -> Tuple[str, str, str, str]:
        """The four lit stops, dimmest first."""
        return (self.low, self.mid, self.high, self.peak)


#: The three tubes. Amber is first because it is the one in the photograph:
#: P3 phosphor, the "paper white" of the late eighties office, chosen over
#: green for long sessions because the decay is slower and the flicker is
#: therefore less visible.
PHOSPHORS: Tuple[Phosphor, ...] = (
    Phosphor(
        name="amber",
        title="P3 AMBER",
        blurb="The 1980s office. Slow decay, warm, easy for hours.",
        glass="#140B02",
        low="#8A4A00",
        mid="#D2820A",
        high="#FFB000",
        peak="#FFE0A3",
    ),
    Phosphor(
        name="green",
        title="P1 GREEN",
        blurb="The older tube. More contrast, harsher after an hour.",
        glass="#02120A",
        low="#106B33",
        mid="#25B056",
        high="#3BFF6E",
        peak="#C8FFD6",
    ),
    Phosphor(
        name="white",
        title="P4 WHITE",
        blurb="Paper-white. Neutral, and the best at showing dither.",
        glass="#080A0E",
        low="#5E6A78",
        mid="#9AA8B8",
        high="#DCE6F2",
        peak="#FFFFFF",
    ),
)

PHOSPHOR_NAMES: Tuple[str, ...] = tuple(p.name for p in PHOSPHORS)

#: The tube a console falls back to. Amber, because it is the mode's subject.
DEFAULT_PHOSPHOR = PHOSPHORS[0].name


def get_phosphor(name: Any) -> Phosphor:
    """The named tube, or amber when the name means nothing here."""
    wanted = str(name or "").strip().lower()
    for phosphor in PHOSPHORS:
        if phosphor.name == wanted:
            return phosphor
    return PHOSPHORS[0]


def phosphor_catalogue() -> Tuple[Tuple[str, str, str], ...]:
    """``(name, title, blurb)`` per tube — what the chooser lists."""
    return tuple((p.name, p.title, p.blurb) for p in PHOSPHORS)


# ── Glow ─────────────────────────────────────────────────────────────────

#: The lowest and highest the brightness control goes.
GLOW_MIN = 0
GLOW_MAX = 4
#: Where it sits when nobody has touched it: enough halo to read as a tube,
#: not so much that the picture goes soft.
DEFAULT_GLOW = 2

#: What each step of the control actually does, as fractions.
#:
#: ``haze`` is the phosphor mixed into the glass — the light the tube throws
#: back at itself, which is why a CRT in a dark room is never black. ``bloom``
#: is the foreground mixed toward the phosphor's peak — the stroke widening
#: and washing out as it is driven harder. They move together because on a
#: real monitor they are one knob.
GLOW_HAZE: Tuple[float, ...] = (0.00, 0.045, 0.09, 0.15, 0.22)
GLOW_BLOOM: Tuple[float, ...] = (0.00, 0.10, 0.22, 0.36, 0.50)

#: What the panel calls each step.
GLOW_LABELS: Tuple[str, ...] = ("OFF", "LOW", "NORMAL", "HIGH", "BURN")


def clamp_glow(value: Any) -> int:
    """Whatever was stored, as a step on the brightness control."""
    try:
        step = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_GLOW
    return max(GLOW_MIN, min(GLOW_MAX, step))


def glow_label(level: int) -> str:
    return GLOW_LABELS[clamp_glow(level)]


def glow_bar(level: int, width: int = 5) -> str:
    """The control drawn as a lit segment bar, for the readout beside it."""
    level = clamp_glow(level)
    width = max(1, int(width))
    lit = round((level / GLOW_MAX) * width)
    return "█" * lit + "░" * (width - lit)


def mix(a: str, b: str, amount: float) -> str:
    """``amount`` of ``b`` blended into ``a``, both as ``#RRGGBB``."""
    amount = max(0.0, min(1.0, float(amount)))
    try:
        first, second = parse_hex(a), parse_hex(b)
    except ValueError:
        return a
    return to_hex(
        tuple(x + (y - x) * amount for x, y in zip(first, second))  # type: ignore[arg-type]
    )


# ── Optics ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Optics:
    """The mode's non-colour rendering parameters, in one object.

    Carried on the palette (``BenchPalette.optics``) rather than passed
    around, so a widget that draws its own glyphs — a lamp, a state figure, a
    meter — can ask what mode it is in without importing this module or being
    told by the app. That is what keeps the instruments mode-agnostic: they
    read a dict, and a mode that does not set a key gets the bench default.
    """

    mode: str = MODE_BENCH
    phosphor: str = DEFAULT_PHOSPHOR
    glow: int = DEFAULT_GLOW
    scanlines: bool = True
    block_cursor: bool = True

    @property
    def dos(self) -> bool:
        return self.mode == MODE_DOS

    @property
    def bloom(self) -> float:
        """How far a lit cell is driven up its glyph ramp, 0…1."""
        return GLOW_BLOOM[clamp_glow(self.glow)] if self.dos else 0.0

    def as_dict(self) -> Dict[str, Any]:
        """What the display is doing, for the widgets that draw on it.

        Not a dump of the stored preferences: ``scanlines`` and ``bloom`` are
        reported as *false* and *zero* under a mode that has neither, so a
        widget can act on them without knowing which modes exist. The stored
        preferences stay on the object, which is what the settings pane reads.
        """
        return {
            "mode": self.mode,
            "phosphor": self.phosphor,
            "glow": self.glow,
            "scanlines": self.scanlines and self.dos,
            "block_cursor": self.block_cursor,
            # Pre-computed so a kit does not have to know the curve: it is a
            # display property of the tube, not of the figure being drawn.
            "bloom": self.bloom,
        }


def optics_of(palette: Any) -> Optics:
    """The optics carried by a palette, whatever built it."""
    raw = getattr(palette, "optics", None)
    if not isinstance(raw, Mapping):
        return Optics(mode=MODE_BENCH)
    return Optics(
        mode=normalise_mode(raw.get("mode")),
        phosphor=str(raw.get("phosphor") or DEFAULT_PHOSPHOR),
        glow=clamp_glow(raw.get("glow", DEFAULT_GLOW)),
        scanlines=bool(raw.get("scanlines", True)),
        block_cursor=bool(raw.get("block_cursor", True)),
    )


def is_dos(widget: Any) -> bool:
    """Whether the app owning ``widget`` is painting in DOS mode.

    Total: a widget with no app, an app with no palette, and a palette from
    before this mode existed all answer "no" rather than raising. Widgets ask
    this while rendering, and a render that raises takes the console down.
    """
    try:
        palette = widget.app.bench_palette
    except Exception:
        return False
    return bool(getattr(palette, "dos", False))


# ── The palette ──────────────────────────────────────────────────────────


def resolve_palette(
    phosphor: str = DEFAULT_PHOSPHOR,
    glow: int = DEFAULT_GLOW,
    scanlines: bool = True,
    block_cursor: bool = True,
) -> BenchPalette:
    """Build the console's palette from a tube and a brightness setting.

    Unlike the skin bridge next door, this does not read anything: a monitor
    is a monitor, and the point of the mode is that it looks the same
    wherever it is opened. What it *shares* with the bridge is the contrast
    floor — glow mixes the phosphor into the ground and the foreground toward
    white at the same time, so at the top of the control the two ends are
    measurably closer together, and the floor is what stops "brighter" from
    turning into "unreadable".
    """
    tube = get_phosphor(phosphor)
    level = clamp_glow(glow)
    haze, bloom = GLOW_HAZE[level], GLOW_BLOOM[level]

    # The glass, with the light the tube throws back at itself mixed in.
    background = mix(tube.glass, tube.mid, haze)
    # A panel is a lit region of the same glass — the frame fill behind a
    # menu box — so it takes a second helping of the same haze rather than a
    # colour of its own. On a monochrome tube there is no second colour.
    panel = mix(background, tube.mid, 0.10 + haze * 0.5)

    def lit(stop: str, amount: float = 1.0) -> str:
        """One of the tube's stops, driven ``amount`` of the way to peak."""
        return mix(stop, tube.peak, bloom * amount)

    values: Dict[str, str] = {
        "background": background,
        "panel": panel,
        # Body text is the standard-intensity stop. Everything above it is
        # "bright" in the CGA sense — the same hue with the intensity bit on.
        "foreground": lit(tube.high, 0.7),
        "primary": lit(tube.high),
        "accent": lit(tube.peak, 0.4),
        "secondary": lit(tube.mid, 0.9),
        "dim": lit(tube.low, 0.5),
        "border": lit(tube.mid, 0.5),
        "rule": lit(tube.low, 0.7),
        # The three signals. A monochrome tube cannot change hue, so they are
        # separated the way a text-mode program separated them: by intensity,
        # with the worst of them at full drive. `error` gets the one liberty
        # this module takes — a nudge toward red, which is what an over-driven
        # phosphor actually does at the edge of its range.
        "success": lit(tube.mid),
        "warning": lit(tube.high, 0.85),
        "error": mix(lit(tube.peak, 0.6), "#FF6A3D", 0.42),
        # A selected row is a lit band, and on this tube that is inverse
        # video: the fill is the phosphor and the text is the glass.
        "selection": lit(tube.mid, 0.35),
    }

    # The floor. Applied against the ground each role is painted on, exactly
    # as the skin bridge does it — the two modes must not disagree about what
    # "readable" means.
    for role, target in (
        ("foreground", AA_TEXT),
        ("primary", AA_LARGE),
        ("secondary", AA_LARGE),
        ("accent", AA_LARGE),
        ("success", AA_LARGE),
        ("warning", AA_LARGE),
        ("error", AA_LARGE),
        ("dim", AA_LARGE),
        ("border", AA_LARGE),
        ("rule", AA_LARGE),
    ):
        ground = panel if role in ("accent", "dim", "secondary") else background
        values[role] = ensure_contrast(values[role], ground, target)

    # Inverse video, which is the mode's only other emphasis and therefore
    # has to be right: a bright fill, and the glass itself as the ink on it.
    band = lit(tube.high, 0.55)
    values["band"] = band
    values["ink"] = ensure_contrast(tube.glass, band, AA_TEXT)
    values["inkdim"] = ensure_contrast(
        mix(tube.glass, band, 0.42), band, AA_LARGE
    )

    # A selection fill has to be visible against the ground it sits in, and
    # the haze lifts the ground as the glow goes up — so at BURN the two can
    # meet. Nudge the fill, never the ground: the ground is the tube.
    if contrast_ratio(values["selection"], background) < 1.25:
        values["selection"] = mix(values["selection"], tube.high, 0.35)

    return BenchPalette(
        values=values,
        dark=True,
        skin_name=f"dos-{tube.name}",
        glyphs={"tool_prefix": TOOL_PREFIX},
        optics=Optics(
            mode=MODE_DOS,
            phosphor=tube.name,
            glow=level,
            scanlines=bool(scanlines),
            block_cursor=bool(block_cursor),
        ).as_dict(),
    )


# ── Marks ────────────────────────────────────────────────────────────────
#
# Every glyph below is in IBM code page 437, which is the whole point: these
# are the marks a program of the period had, not a modern approximation of
# them. The bench mode's own set uses ✗ (U+2717), which no DOS machine could
# draw; the fault mark here is CP437 19, the double exclamation.

#: The gutter mark drawn beside a tool call inside a fold.
TOOL_PREFIX = "·"

#: Speaker marks: kind → (full name, narrow name, palette role).
ENTRY_MARKS: Dict[str, Tuple[str, str, str]] = {
    "user": ("► YOU", "►", "primary"),
    "reply": ("■ CURIE", "■", "accent"),
    "error": ("‼ FAULT", "‼", "error"),
}

#: The fold's open/shut markers. Turbo Vision's own, and still the clearest
#: pair of arrows CP437 has.
FOLD_COLLAPSED = "►"
FOLD_EXPANDED = "▼"

#: The composer's prompt mark. A DOS program put the cursor after a prompt,
#: and the prompt was a chevron.
COMPOSER_CARET = "›"

#: The lamp glyphs: lit, and dark. On a band the dark one has to be a shade
#: rather than a rule — an unlit lamp is still a lamp, and ▁ on inverse video
#: reads as a gap in the bar.
LAMP_LIT = "█"
LAMP_DARK = "░"

#: Box drawing, doubled. The frames in the photograph are all of these.
BOX_H = "═"
BOX_V = "║"
BOX_TL = "╔"
BOX_TR = "╗"
BOX_BL = "╚"
BOX_BR = "╝"

#: The single-line rule used *inside* a double frame, which is how a text-mode
#: program separated a heading from what it headed.
RULE_H = "─"


# ── Chrome ───────────────────────────────────────────────────────────────
#
# Pure functions returning Rich text, so every one of them can be read back
# in a test without an app, a screen or a paint.


def program_version() -> str:
    """The installed version, for the title bar. Empty when it cannot be read.

    A DOS title bar named the program and its version — that is the whole
    genre, and it is the first thing in the photograph this mode is drawn
    from. It has to be the *real* one: a hard-coded "v1.0" beside a program
    that is not at 1.0 is the one detail here that would be a lie rather than
    a period reference, and the bar is better with nothing than with that.
    """
    try:
        from curie_cli import __version__

        version = str(__version__ or "").strip()
    except Exception:
        return ""
    return f"v{version}" if version and version[0].isdigit() else version


def wordmark(palette: BenchPalette) -> Text:
    """The title bar's left end. Ink on the band, because the bar is the band."""
    out = Text(no_wrap=True)
    out.append("CURIE AGENT", style=f"bold {palette['ink']}")
    version = program_version()
    if version:
        out.append(f"  {version}", style=palette["inkdim"])
    return out


def keycap(cap: str, label: str, palette: BenchPalette) -> Text:
    """One entry on the function-key bar, drawn the way Norton drew it.

    The digit in plain ink and the word on a lit band — which is not a
    stylistic choice but the only way a text-mode program could make a row of
    ten labels scannable with one colour and no bold.

    The ``F`` is dropped from a function key because the bar is the thing that
    tells you they are function keys, and eight columns of ``F`` across the
    row is eight columns not spent on the words. Control keys keep their
    caret: ``^G`` is not a key you find by counting along the row.
    """
    digit = cap[1:] if len(cap) > 1 and cap[0] in "Ff" and cap[1:].isdigit() else cap
    out = Text(no_wrap=True)
    out.append(digit, style=palette["dim"])
    out.append(label, style=f"bold {palette['ink']} on {palette['band']}")
    return out


def rail_switch(
    index: int, label: str, glyph: str, palette: BenchPalette, active: bool, narrow: bool
) -> Text:
    """One line of the menu box: a number, a bracket, and the pane's name.

    Numbered, because that is what a DOS menu was — the number *was* the
    shortcut — and because it lines the pane names up with the digits on the
    key bar below, which are the same keys.
    """
    style = f"bold {palette['ink']} on {palette['band']}" if active else palette["foreground"]
    out = Text(no_wrap=True, overflow="ellipsis")
    if narrow:
        out.append(f" {index} ", style=style)
        return out
    out.append(f" {index}) ", style=palette["dim"] if not active else style)
    out.append(f"{label:<11}", style=style)
    return out


#: The plate's title, longest first. A window narrow enough that the full one
#: would overrun its own frame gets the next one down rather than a rule with
#: letters spilling past the corner — which is what a fixed title does, and
#: what a self-sizing panel cannot do because it grows the box instead.
MASTHEAD_TITLES: Tuple[str, ...] = (
    " CURIE AGENT — BENCH TERMINAL ",
    " CURIE AGENT ",
    " CURIE ",
)

#: Columns of rule to the left of the title. Fixed, so the title starts at the
#: same column at every width and does not slide about during a resize.
MASTHEAD_INSET = 4


def masthead(palette: BenchPalette, width: int, subject: str = "") -> Text:
    """The title plate: a double frame with the name set into its top rule.

    Set *into* the rule rather than above or below it, because that is where a
    text-mode window put its title and because it costs no extra row. The
    second row carries the model on the left and the tube on the right, which
    is the one place the console says out loud what it is being drawn with.

    Every line comes out exactly ``width`` columns. That is the whole
    difficulty: a rule is built from a count, and a count that is computed
    from a title that did not fit produces a box with one corner past the edge
    of the window — which does not look like a narrow window, it looks like a
    rendering fault.
    """
    width = max(8, int(width))
    optics = optics_of(palette)
    border = palette["border"]
    accent = palette["accent"]
    dim = palette["dim"]
    inner = width - 2

    top = Text(no_wrap=True)
    title = next(
        (name for name in MASTHEAD_TITLES if len(name) + MASTHEAD_INSET + 1 <= inner),
        "",
    )
    left = MASTHEAD_INSET if title else 0
    top.append(BOX_TL + BOX_H * left, style=border)
    if title:
        top.append(title, style=f"bold {accent}")
    top.append(BOX_H * (inner - left - len(title)) + BOX_TR, style=border)

    settings = "  ".join(
        (
            get_phosphor(optics.phosphor).title,
            f"GLOW {glow_label(optics.glow)}",
            "SCANLINES" if optics.scanlines else "PROGRESSIVE",
        )
    )
    # The model gives way before the tube does: the tube is the one thing on
    # this row the reader cannot read off any other part of the console.
    left_text = f" {subject}" if subject else " "
    if len(left_text) + len(settings) + 2 > inner:
        left_text = " "
    if len(settings) + 2 > inner:
        settings = ""
    gap = max(1, inner - len(left_text) - len(settings) - 1)
    body = Text(no_wrap=True)
    body.append(BOX_V, style=border)
    body.append(left_text[:inner], style=dim)
    body.append(" " * gap)
    body.append(settings, style=dim)
    body.append(" " * max(0, inner - len(left_text) - gap - len(settings)))
    body.append(BOX_V, style=border)

    bottom = Text(no_wrap=True)
    bottom.append(BOX_BL + BOX_H * inner + BOX_BR, style=border)

    out = Text()
    out.append_text(top)
    out.append("\n")
    out.append_text(body)
    out.append("\n")
    out.append_text(bottom)
    return out


def clock(palette: BenchPalette, now) -> Text:
    """The title bar's right end: the long date, then the time.

    A DOS program with a title bar put the date in it — the machine had no
    other place to show one and no other program running to ask. It is drawn
    in the band's dim ink so the time, which is the part that moves, is the
    part that reads.
    """
    out = Text(no_wrap=True)
    out.append(now.strftime("%a, %b %d %Y"), style=palette["inkdim"])
    out.append("  ")
    out.append(now.strftime("%H:%M:%S"), style=f"bold {palette['ink']}")
    return out


#: The sample a preview row draws, so the five rows differ only by the thing
#: being compared. Density ramp first (that is where bloom shows), then
#: ordinary body text (that is where haze shows).
PREVIEW_SAMPLE = "CURIE ─ READY"


def preview(
    phosphor: str,
    width: int,
    chosen: int = DEFAULT_GLOW,
    scanlines: bool = True,
) -> Text:
    """The tube at all five brightnesses at once, one step per row.

    A brightness control on a monitor is judged by the trade it makes, not by
    its number: winding it up lifts the picture *and* lifts the black the
    picture sits on, so past a point everything is brighter and nothing is
    clearer. A control that only said ``GLOW 3`` would be asking the reader to
    find that out by trying all five and remembering. Five rows, each one its
    own resolved palette, shows it in one look — including the row that is
    already in force, which is marked rather than merely implied.
    """
    width = max(20, int(width))
    out = Text(no_wrap=True, overflow="crop")
    for level in range(GLOW_MIN, GLOW_MAX + 1):
        if level:
            out.append("\n")
        painted = resolve_palette(
            phosphor=phosphor, glow=level, scanlines=scanlines
        )
        ground = painted["background"]
        marker = "\u25ba" if level == clamp_glow(chosen) else " "
        out.append(f"{marker} ", style=f"{painted['accent']} on {ground}")
        out.append(f"{GLOW_LABELS[level]:<7}", style=f"bold {painted['dim']} on {ground}")
        out.append("░▒▓█ ", style=f"{painted['primary']} on {ground}")
        out.append(PREVIEW_SAMPLE, style=f"{painted['foreground']} on {ground}")
        # The rest of the row is the glass itself, which is the half of the
        # trade the swatch above cannot show.
        drawn = 2 + 7 + 5 + len(PREVIEW_SAMPLE)
        out.append(" " * max(0, width - drawn), style=f"on {ground}")
    return out


def fold_title(tools: int) -> str:
    """The shut fold's heading, in the mode's own lettering.

    Deliberately free of square brackets: this reaches a Textual ``Static``
    through ``Collapsible.title``, and content markup would eat them.
    """
    if not tools:
        return "WORKINGS"
    noun = "TOOL CALL" if tools == 1 else "TOOL CALLS"
    return f"WORKINGS ─ {tools:,} {noun}"


# ── The stylesheet ───────────────────────────────────────────────────────
#
# Scoped under ``Screen.-dos`` rather than kept as a second stylesheet,
# because Textual parses ``App.CSS`` once at start-up: a mode that swapped
# documents would have to rebuild the app to change. A class on the screen
# costs one selector and toggles in a frame — and the two documents cannot
# drift, because there is only one.
#
# Every rule below is either a colour the mode paints differently or a piece
# of chrome it draws differently. Nothing here changes a layout the bench mode
# relies on, so F7, F8 and F10 keep working unchanged.

DOS_CSS = """
/* ── The glass ─────────────────────────────────────────────────────── */

Screen.-dos {
    background: $bench-background;
    color: $bench-foreground;
}

/* ── Title bar: one row, inverse video, edge to edge ───────────────── */

Screen.-dos #titlebar {
    height: 1;
    background: $bench-band;
    color: $bench-ink;
    border-bottom: none;
    padding: 0 1;
}

Screen.-dos #titlebar-mark {
    color: $bench-ink;
    min-width: 14;
}

Screen.-dos #titlebar-subject {
    color: $bench-ink;
    padding: 0 2;
}

Screen.-dos #titlebar-clock {
    color: $bench-ink;
    min-width: 10;
}

/* ── The key bar ───────────────────────────────────────────────────── */

Screen.-dos #keyline {
    background: $bench-background;
    color: $bench-foreground;
}

Screen.-dos .keycap {
    padding: 0 1 0 0;
}

Screen.-dos .keycap:hover {
    background: $bench-background;
    color: $bench-accent;
}

Screen.-dos #keyline-note {
    color: $bench-dim;
    padding: 0 1;
}

/* ── The menu box ──────────────────────────────────────────────────── */

Screen.-dos #rail {
    border: double $bench-border;
    border-right: double $bench-border;
    background: $bench-background;
    padding: 0;
    width: 20;
}

Screen.-dos #rail.narrow {
    width: 7;
}

Screen.-dos .switch {
    height: 1;
    padding: 0;
    border-left: none;
    background: $bench-background;
}

Screen.-dos .switch:hover {
    background: $bench-selection;
    border-left: none;
}

Screen.-dos .switch.-active {
    background: $bench-background;
    border-left: none;
}

/* ── Content, framed ───────────────────────────────────────────────── */

Screen.-dos #content {
    border: double $bench-border;
}

/* F10 asks for rows. The frame is chrome like everything else it takes. */
Screen.-dos #content.-tight {
    border: none;
}

Screen.-dos .pane {
    padding: 0 1;
}

Screen.-dos .pane.-tight {
    padding: 0 1;
}

Screen.-dos .section-head {
    color: $bench-ink;
    background: $bench-band;
    text-style: bold;
}

Screen.-dos #transcript {
    background: $bench-background;
    scrollbar-color: $bench-border;
    scrollbar-background: $bench-background;
}

/* ── The instrument box ────────────────────────────────────────────── */

Screen.-dos #instruments {
    border: double $bench-border;
    border-left: double $bench-border;
    background: $bench-background;
    padding: 0 1;
    scrollbar-background: $bench-background;
}

Screen.-dos .instrument-title {
    color: $bench-ink;
    background: $bench-band;
    text-style: bold;
}

/* ── Folds ─────────────────────────────────────────────────────────── */

Screen.-dos Collapsible,
Screen.-dos Collapsible > CollapsibleTitle {
    background: $bench-background;
}

Screen.-dos Collapsible > CollapsibleTitle:hover,
Screen.-dos Collapsible > CollapsibleTitle:focus {
    background: $bench-band;
    color: $bench-ink;
}

/* ── The composer ──────────────────────────────────────────────────── */

/* The one row under the caret. Under the bench palette that row is the
   pane's own bottom padding; this mode spends its vertical padding on the
   double frame instead, so without this the input sits hard against the
   bottom rule and the two modes disagree about how much room a composer
   has. Owned by the frame rather than the pane, so it is one row here and
   one row there rather than one row plus whatever the pane happens to add. */
Screen.-dos #composer-frame {
    border-top: double $bench-border;
    background: $bench-background;
    padding-bottom: 1;
}

Screen.-dos #composer,
Screen.-dos #composer:focus {
    background: $bench-background;
    color: $bench-foreground;
}

Screen.-dos #composer-caret {
    color: $bench-primary;
}

/* The composer's own highlights. Textual paints the cursor's line with its
   design tokens rather than with ours, which on the enamel ground is a grey
   the eye slides past and on dark glass is a slab of the wrong colour lying
   across the input. Scoped to this mode: the bench composer is left exactly
   as it was. */

Screen.-dos #composer > .text-area--cursor-line {
    background: $bench-background;
}

Screen.-dos #composer > .text-area--cursor {
    background: $bench-primary;
    color: $bench-background;
}

Screen.-dos #composer > .text-area--selection {
    background: $bench-band;
    color: $bench-ink;
}

/* ── Tables ────────────────────────────────────────────────────────── */

Screen.-dos DataTable {
    background: $bench-background;
    scrollbar-background: $bench-background;
}

Screen.-dos DataTable > .datatable--header {
    background: $bench-band;
    color: $bench-ink;
}

Screen.-dos DataTable > .datatable--cursor {
    background: $bench-band;
    color: $bench-ink;
    text-style: bold;
}

Screen.-dos DataTable > .datatable--hover {
    background: $bench-selection;
    color: $bench-foreground;
}

Screen.-dos RichLog {
    background: $bench-background;
    border: double $bench-border;
}

/* ── Switches, buttons, notices ────────────────────────────────────── */

Screen.-dos .toggle {
    border-left: none;
}

Screen.-dos .toggle:hover {
    background: $bench-selection;
    border-left: none;
}

Screen.-dos .toggle.-on {
    border-left: none;
}

/* A run of inverse video is what a text-mode button was, so two of them side
   by side need a column of glass between them or they read as one wide key. */
Screen.-dos .panel-button {
    background: $bench-band;
    color: $bench-ink;
    margin-right: 1;
}

Screen.-dos .panel-button:hover {
    background: $bench-primary;
    color: $bench-ink;
}

Screen.-dos Button {
    background: $bench-band;
    color: $bench-ink;
}

Screen.-dos #notice {
    background: $bench-band;
    color: $bench-ink;
    border-bottom: none;
}

Screen.-dos Tooltip {
    background: $bench-panel;
    color: $bench-foreground;
    border: double $bench-border;
}
"""


__all__ = [
    "BOX_BL",
    "BOX_BR",
    "BOX_H",
    "BOX_TL",
    "BOX_TR",
    "BOX_V",
    "COMPOSER_CARET",
    "DEFAULT_GLOW",
    "DEFAULT_PHOSPHOR",
    "DOS_CSS",
    "ENTRY_MARKS",
    "FOLD_COLLAPSED",
    "FOLD_EXPANDED",
    "GLOW_MAX",
    "GLOW_MIN",
    "LAMP_DARK",
    "LAMP_LIT",
    "MODES",
    "MODE_BENCH",
    "MODE_CATALOGUE",
    "MASTHEAD_TITLES",
    "MODE_DOS",
    "PHOSPHORS",
    "PHOSPHOR_NAMES",
    "Optics",
    "Phosphor",
    "RULE_H",
    "TOOL_PREFIX",
    "clamp_glow",
    "clock",
    "fold_title",
    "get_phosphor",
    "glow_bar",
    "glow_label",
    "is_dos",
    "keycap",
    "masthead",
    "mix",
    "normalise_mode",
    "optics_of",
    "phosphor_catalogue",
    "program_version",
    "preview",
    "rail_switch",
    "resolve_palette",
    "wordmark",
]
