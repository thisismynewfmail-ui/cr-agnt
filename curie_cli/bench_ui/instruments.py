"""The panel's live instruments.

Four widgets, each one a thing that exists on a real instrument panel and
each one showing a number the console actually has:

``PanelLamps``    indicator lamps — session state, at a glance.
``StripChart``    a multi-channel chart recorder tracing output over time.
``DialGauge``     a needle gauge with a swept scale.
``TapeMeter``     a shaded bar, the CGA density ramp used as a fill level.

They are deliberately plain: each takes values pushed in from outside and
draws them. Nothing here talks to the agent, reads config, or invents data —
an instrument that shows a made-up number is worse than no instrument.

The animated *state* figures live next door, in
:mod:`curie_cli.bench_ui.indicators`, because those come in selectable sets
and these do not: a gauge is a gauge in every skin.

The one thing a display mode changes here is the *ground*. The DOS mode paints
its title bar as inverse video — a lit band with the glass as ink — and a lamp
drawn in signal green on that band is invisible. So the lamps ask the palette
which ground they are on and pick their ink accordingly. Nothing else does:
a shaded bar and a needle over a scale are the same instrument on either
display, drawn with glyphs that were CP437 to begin with.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Iterable, Sequence

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

# The CGA/EGA shading glyphs, in density order. Every DOS program that wanted
# a gradient had exactly these four to work with.
SHADE_RAMP = " ░▒▓█"

# Eighth-block glyphs, for a bar that can land between two columns.
EIGHTHS = " ▏▎▍▌▋▊▉█"

# Vertical eighths, for the strip chart's trace.
COLUMNS = " ▁▂▃▄▅▆▇█"


class PanelLamps(Widget):
    """A row of indicator lamps with stencilled labels.

    Each lamp is a state, not a number: lit, dark, blinking, or pulsing.
    Blinking is reserved for something that needs an answer — an approval
    waiting, a turn that has stalled — because a blinking lamp is the one
    thing on a panel the eye cannot ignore.

    A *pulse* is the opposite: something happened and is already over, and
    the lamp says so by fading out over a couple of seconds rather than by
    demanding attention. It is what a write to the record gets — a session
    named, a memory flushed — because those are worth seeing and never worth
    interrupting for.
    """

    DEFAULT_CSS = """
    PanelLamps {
        height: 1;
        content-align: left middle;
    }
    """

    lamps: reactive[tuple] = reactive(tuple, layout=True)
    blink_phase: reactive[bool] = reactive(False)

    #: How long a pulse takes to fade, in seconds. Long enough to be caught
    #: out of the corner of an eye, short enough that two writes in quick
    #: succession read as two events rather than as one long glow.
    PULSE_SECONDS = 2.0

    def __init__(self, lamps: Iterable[tuple[str, str]] = (), **kwargs) -> None:
        super().__init__(**kwargs)
        self.lamps = tuple(lamps)
        #: label -> (started_at, seconds, state to return to when it is over)
        self._pulses: dict[str, tuple[float, float, str]] = {}

    def on_mount(self) -> None:
        # 4 Hz: still reads as blinking on the half-second (every other
        # frame), and gives a two-second pulse eight steps to fade over
        # instead of four, which is the difference between a fade and a
        # stutter.
        self.set_interval(0.25, self._tick)

    def _tick(self) -> None:
        now = time.monotonic()
        self._phase_counter = getattr(self, "_phase_counter", 0) + 1
        if self._phase_counter % 2 == 0:
            self.blink_phase = not self.blink_phase
        if not self._pulses:
            return
        expired = [
            label
            for label, (started, seconds, _base) in self._pulses.items()
            if now - started >= seconds
        ]
        for label in expired:
            _started, _seconds, base = self._pulses.pop(label)
            self.set_lamp(label, base)
        self.refresh()

    def flash(self, label: str, seconds: float | None = None) -> None:
        """Pulse one lamp, then return it to the state it was in.

        Restoring the previous state matters: the lamp a write pulses is
        often one that was already lit for another reason, and leaving it
        dark afterwards would report a session as closed because it saved.
        """
        base = next(
            (state for name, state in self.lamps if name == label and state != "pulse"),
            "off",
        )
        held = self._pulses.get(label)
        if held is not None:
            base = held[2]
        self._pulses[label] = (
            time.monotonic(),
            float(seconds or self.PULSE_SECONDS),
            base,
        )
        self.set_lamp(label, "pulse")

    def set_lamp(self, label: str, state: str) -> None:
        """Set one lamp to ``on``, ``off``, ``warn``, ``blink`` or ``pulse``.

        Use :meth:`flash` rather than setting ``pulse`` by hand — a pulse
        needs a start time and a state to fall back to, and this setter has
        neither.
        """
        updated = []
        found = False
        for name, current in self.lamps:
            if name == label:
                updated.append((name, state))
                found = True
            else:
                updated.append((name, current))
        if not found:
            updated.append((label, state))
        self.lamps = tuple(updated)

    def render(self) -> Text:
        # On an inverse-video title bar the whole vocabulary changes: a lit
        # lamp is a solid block of the glass colour, an unlit one is a shade
        # of it, and a signal colour would be a hole in the band rather than
        # a lamp. Resolved once per frame, because it is a property of the
        # display and not of any one lamp.
        band = _on_band(self)
        lit_style = "$bench-ink" if band else "$bench-success"
        dark_style = "$bench-inkdim" if band else "$bench-dim"
        text_style = "$bench-ink" if band else "$bench-foreground"
        lit_glyph, dark_glyph = ("█", "░") if band else ("▉", "▁")

        out = Text(no_wrap=True, overflow="ellipsis")
        for index, (label, state) in enumerate(self.lamps):
            if index:
                out.append("  ")
            if state == "on":
                glyph, style = lit_glyph, lit_style
            elif state == "warn":
                glyph, style = lit_glyph, "$bench-ink" if band else "$bench-warning"
            elif state == "blink":
                lit = self.blink_phase
                glyph = lit_glyph if lit else dark_glyph
                if band:
                    style = "$bench-ink" if lit else "$bench-inkdim"
                else:
                    style = "$bench-error" if lit else "$bench-dim"
            elif state == "pulse":
                glyph, style = self._pulse_glyph(label, band)
            else:
                glyph, style = dark_glyph, dark_style
            out.append(glyph, style=self._resolve(style))
            out.append(" ")
            out.append(label, style=self._resolve(text_style))
        return out

    def _pulse_glyph(self, label: str, band: bool = False) -> tuple[str, str]:
        """A fading lamp, drawn down the CGA density ramp."""
        held = self._pulses.get(label)
        if held is None:
            return ("░", "$bench-inkdim") if band else ("▁", "$bench-dim")
        started, seconds, _base = held
        remaining = 1.0 - min(1.0, (time.monotonic() - started) / max(0.01, seconds))
        glyph = SHADE_RAMP[max(1, min(4, int(round(remaining * 4))))]
        return glyph, "$bench-ink" if band else "$bench-success"

    def _resolve(self, token: str) -> str:
        """Turn a ``$bench-*`` token into a concrete colour.

        Textual resolves CSS variables in stylesheets, not inside ``Text``
        styles built at render time, so widgets that colour their own glyphs
        look the palette up directly.
        """
        return _palette_colour(self, token)


class StripChart(Widget):
    """A chart recorder: output over time, newest on the right, by channel.

    The trace is drawn with vertical eighth-blocks, which gives eight
    sub-rows of resolution per terminal row — enough that a single-row chart
    still shows a shape rather than a binary on/off.

    Every sample carries the *channel* that produced it, and the channel
    decides the colour and the glyph the column is drawn with. That is the
    difference between a recorder and a rate counter: a single undifferenced
    trace answers "is anything happening", which the reader can already see,
    while a channelled one answers the question they actually have — whether
    the last twenty seconds went into reasoning, into tools, or into the
    answer. The three are the same number of characters a second and mean
    entirely different things.
    """

    #: channel -> (palette role, full-deflection glyph). The glyphs differ so
    #: the chart is still readable on a monochrome terminal, where the roles
    #: all resolve to the same ink.
    CHANNELS: dict[str, tuple[str, str]] = {
        "text": ("$bench-primary", "█"),
        "think": ("$bench-accent", "▓"),
        "tool": ("$bench-secondary", "▒"),
    }

    #: The channel a sample lands in when the caller does not say.
    DEFAULT_CHANNEL = "text"

    # A fixed height, not ``1fr``. The chart was the only flexible child of
    # the instrument stack, so it absorbed every spare row — in a 40-row
    # window it came out two dozen rows tall, and a chart recorder driven to
    # full deflection at that height is a solid black column down the side of
    # the console rather than a trace anyone can read. Seven rows is enough
    # for the eighth-block resolution to show a shape.
    DEFAULT_CSS = """
    StripChart {
        height: 7;
        min-height: 3;
    }
    """

    #: Headroom above the highest sample seen, so the trace has somewhere to
    #: go. Without it the first sample sets the scale it is then measured
    #: against and every reading pegs at full deflection.
    HEADROOM = 1.25

    label: reactive[str] = reactive("")
    peak: reactive[float] = reactive(1.0)

    def __init__(self, label: str = "", capacity: int = 512, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self._samples: Deque[tuple[float, str]] = deque(maxlen=capacity)
        self._sawtooth = False

    def push(self, value: float, channel: str = DEFAULT_CHANNEL) -> None:
        """Record one sample on one channel.

        The scale follows the highest value seen on any channel, so the three
        traces stay comparable — a reasoning burst and an answer burst of the
        same size have to draw the same height or the chart is lying.
        """
        value = max(0.0, float(value))
        if channel not in self.CHANNELS:
            channel = self.DEFAULT_CHANNEL
        self._samples.append((value, channel))
        if value * self.HEADROOM > self.peak:
            self.peak = value * self.HEADROOM
        self.refresh()

    def clear(self) -> None:
        self._samples.clear()
        self.peak = 1.0
        self.refresh()

    def channels_seen(self) -> tuple[str, ...]:
        """Which channels the trace on screen actually contains."""
        return tuple(
            name for name in self.CHANNELS
            if any(channel == name for _value, channel in self._samples)
        )

    def toggle_calibration(self) -> bool:
        """Switch the trace to a calibration sawtooth, and back.

        A real chart recorder has a test button that drives the pen through
        its full travel, so you can tell a flat line from a dead pen. This is
        that button.
        """
        self._sawtooth = not self._sawtooth
        self.refresh()
        return self._sawtooth

    def render(self) -> Text:
        width = max(1, self.size.width)
        height = max(1, self.size.height)
        if self._sawtooth:
            series = [
                ((i % 20) / 19.0 * self.peak, self.DEFAULT_CHANNEL)
                for i in range(width)
            ]
        else:
            series = list(self._samples)[-width:]
            series = [(0.0, self.DEFAULT_CHANNEL)] * (width - len(series)) + series

        scale = self.peak or 1.0
        # Resolved once per frame rather than once per cell: a 34-column
        # chart at seven rows is 238 cells, and looking a colour up for each
        # of them is the whole cost of the instrument.
        styles = {name: self._trace_style(name) for name in self.CHANNELS}
        rows: list[Text] = []
        for row in range(height):
            # Row 0 is the top of the chart.
            row_top = (height - row) / height
            row_bottom = (height - row - 1) / height
            line = Text(no_wrap=True)
            for value, channel in series:
                frac = min(1.0, value / scale)
                if frac <= row_bottom:
                    line.append(" ")
                    continue
                style = styles.get(channel, styles[self.DEFAULT_CHANNEL])
                if frac >= row_top:
                    line.append(self._full_glyph(channel), style=style)
                else:
                    within = (frac - row_bottom) / (row_top - row_bottom)
                    idx = max(1, min(len(COLUMNS) - 1, int(round(within * 8))))
                    line.append(COLUMNS[idx], style=style)
            rows.append(line)

        out = Text()
        for index, line in enumerate(rows):
            if index:
                out.append("\n")
            out.append_text(line)
        return out

    def _full_glyph(self, channel: str) -> str:
        """The glyph a column at full deflection is drawn with.

        Per channel, so the three traces are still told apart on a terminal
        that renders every palette role as the same ink.
        """
        if self._sawtooth:
            return "█"
        return self.CHANNELS.get(channel, self.CHANNELS[self.DEFAULT_CHANNEL])[1]

    def _trace_style(self, channel: str = DEFAULT_CHANNEL) -> str:
        if self._sawtooth:
            return _palette_colour(self, "$bench-warning")
        token = self.CHANNELS.get(channel, self.CHANNELS[self.DEFAULT_CHANNEL])[0]
        return _palette_colour(self, token)


class ChartLegend(Widget):
    """The strip chart's three channels, as swatches.

    Colour and glyph only. The words that were beside them ("answer",
    "think", "tool") named the three things the chart above is already
    drawing in those exact marks, so they were a caption for a picture the
    reader was looking at — and they cost a third of the row's width in a
    column that is 26 columns wide at its narrowest.
    """

    DEFAULT_CSS = """
    ChartLegend {
        height: 1;
    }
    """

    #: The channels shown, in the order the chart draws them.
    CHANNELS: tuple[str, ...] = ("text", "think", "tool")

    #: Cells of swatch per channel. Wide enough that the three read as
    #: distinct blocks rather than as three characters of noise.
    SWATCH = 3

    def render(self) -> Text:
        out = Text(no_wrap=True, overflow="ellipsis")
        for index, channel in enumerate(self.CHANNELS):
            if index:
                out.append("  ")
            role, glyph = StripChart.CHANNELS[channel]
            out.append(glyph * self.SWATCH, style=_palette_colour(self, role))
        return out


class DialGauge(Widget):
    """A needle gauge with a swept scale and a numeric readout.

    Drawn as an arc of scale ticks with the needle as a filled tick, so it
    stays readable at any width — a gauge that needs 40 columns to be legible
    is not a gauge, it is a decoration.
    """

    DEFAULT_CSS = """
    DialGauge {
        height: 3;
    }
    """

    value: reactive[float] = reactive(0.0)
    ceiling: reactive[float] = reactive(100.0)
    label: reactive[str] = reactive("")
    unit: reactive[str] = reactive("")

    def __init__(
        self,
        label: str = "",
        ceiling: float = 100.0,
        unit: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.ceiling = ceiling or 100.0
        self.unit = unit

    def set_reading(self, value: float, ceiling: float | None = None) -> None:
        if ceiling:
            self.ceiling = float(ceiling)
        self.value = max(0.0, float(value))

    def _fraction(self) -> float:
        return min(1.0, self.value / (self.ceiling or 1.0))

    def _band_style(self) -> str:
        frac = self._fraction()
        if frac >= 0.90:
            token = "$bench-error"
        elif frac >= 0.75:
            token = "$bench-warning"
        else:
            token = "$bench-success"
        return _palette_colour(self, token)

    def render(self) -> Text:
        width = max(8, self.size.width)
        dim = _palette_colour(self, "$bench-dim")
        fg = _palette_colour(self, "$bench-foreground")
        band = self._band_style()

        scale_width = width
        needle_at = int(round(self._fraction() * (scale_width - 1)))

        scale = Text(no_wrap=True)
        for i in range(scale_width):
            if i == needle_at:
                scale.append("▲", style=band)
            elif i % 5 == 0:
                scale.append("┴", style=dim)
            else:
                scale.append("─", style=dim)

        readout = f"{self.value:,.0f}"
        if self.unit:
            readout += f" {self.unit}"
        pct = f"{self._fraction() * 100:.0f}%"

        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(self.label, style=fg)
        pad = width - len(self.label) - len(pct)
        head.append(" " * max(1, pad))
        head.append(pct, style=band)

        foot = Text(no_wrap=True, overflow="ellipsis")
        foot.append("0", style=dim)
        mid = readout.center(max(1, width - 2 - len(f"{self.ceiling:,.0f}")))
        foot.append(mid, style=band)
        foot.append(f"{self.ceiling:,.0f}", style=dim)

        out = Text()
        out.append_text(head)
        out.append("\n")
        out.append_text(scale)
        out.append("\n")
        out.append_text(foot)
        return out


class TapeMeter(Widget):
    """A shaded fill bar, drawn with the CGA density ramp.

    Distinct from :class:`DialGauge` on purpose: a gauge answers "how close
    to the limit", a tape answers "how much of the run is done".
    """

    DEFAULT_CSS = """
    TapeMeter {
        height: 1;
    }
    """

    fraction: reactive[float] = reactive(0.0)
    label: reactive[str] = reactive("")

    def __init__(self, label: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label

    def set_fraction(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, float(fraction)))

    def render(self) -> Text:
        width = max(4, self.size.width)
        prefix = f"{self.label} " if self.label else ""
        bar_width = max(1, width - len(prefix))

        exact = self.fraction * bar_width
        whole = int(exact)
        remainder = exact - whole

        primary = _palette_colour(self, "$bench-primary")
        dim = _palette_colour(self, "$bench-dim")
        fg = _palette_colour(self, "$bench-foreground")

        out = Text(no_wrap=True, overflow="ellipsis")
        if prefix:
            out.append(prefix, style=fg)
        out.append("█" * whole, style=primary)
        if whole < bar_width:
            partial = EIGHTHS[int(round(remainder * 8))]
            out.append(partial, style=primary)
            out.append("░" * (bar_width - whole - 1), style=dim)
        return out


def _on_band(widget: Widget) -> bool:
    """Whether this widget is painted on an inverse-video band.

    Asked of the palette rather than of a display mode by name, so a mode that
    does not paint bands — or a palette from before they existed — answers no
    and the instrument draws exactly what it always drew.
    """
    try:
        optics = widget.app.bench_palette.optics
    except Exception:
        return False
    return bool(isinstance(optics, dict) and optics.get("mode") == "dos")


def _palette_colour(widget: Widget, token: str) -> str:
    """Resolve a ``$bench-*`` CSS variable to a concrete colour.

    Textual expands CSS variables when it parses a stylesheet, but a widget
    that builds its own ``Text`` at render time is past that point — the
    variable has to be looked up. The app owns the resolved palette; falling
    back to the raw token would paint literal text, so an unknown token
    resolves to the foreground instead.
    """
    name = token.lstrip("$")
    if name.startswith("bench-"):
        name = name[len("bench-"):]
    palette = getattr(widget.app, "bench_palette", None)
    if palette is not None:
        value = palette.get(name, "")
        if value:
            return value
    return "default"


__all__ = [
    "COLUMNS",
    "ChartLegend",
    "DialGauge",
    "EIGHTHS",
    "PanelLamps",
    "SHADE_RAMP",
    "StripChart",
    "TapeMeter",
]
