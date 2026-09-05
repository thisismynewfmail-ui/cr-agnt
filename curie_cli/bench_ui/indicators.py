"""Animated state indicators, and the selectable sets they come in.

The console has to answer one question continuously and without words: *what
is it doing right now?* A spinner cannot answer it — every wait looks the
same. So the panel draws the answer as a moving figure whose shape, not just
its motion, is different for each state: waiting for the first token looks
nothing like reasoning, which looks nothing like a tool running, which looks
nothing like an answer streaming out.

Three ideas hold this together.

**States, not messages.** :data:`STATES` is the whole vocabulary. Every
indicator in the console renders the same eight states, so a reader learns the
figures once.

**Kits.** An :class:`IndicatorKit` is one complete set of figures — a way of
drawing all eight states in a single visual language. Six ship here, each
built on different mathematics: wave interference, parametric curves,
cellular automata, phyllotaxis, a decaying radar sweep, and a spectrum bank.
The set is chosen on the PANEL pane, beside the skin, because it is the same
kind of choice.

**Colour comes last.** A kit emits ``(glyph, role)`` cells, never a colour.
The widget resolves each role against the live skin at paint time, so a kit
re-themes with everything else and a new skin needs no work here. The same
separation is what lets one kit drive a one-row strip beside a fold and a
six-row panel in the instrument stack: the figure is a function of the box it
is given, not of a fixed drawing.

**Optics are the display's, not the figure's.** The DOS mode is a phosphor
tube, and a tube does two things to whatever is drawn on it: lit cells bloom
into their neighbours, and the raster leaves every other line dark. Neither is
something a kit should have to know about, so both are applied to the finished
frame in :meth:`IndicatorKit.frame` from parameters the *palette* carries.
Every set therefore glows and scans under the DOS mode without a line of
per-kit work, and none of them changes by so much as a cell under the bench
one — where the parameters are simply absent.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Mapping, Sequence, Tuple

from rich.text import Text
from textual.widget import Widget

TAU = math.pi * 2.0

#: The golden angle, in radians. Phyllotaxis — the arrangement a sunflower
#: uses — is what you get from rotating by it once per seed.
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


# ── The vocabulary ───────────────────────────────────────────────────────
# Eight states, and every indicator in the console renders all of them. They
# are deliberately about what the *console* is doing, not about what the model
# said: an indicator that needs the reader to interpret prose has failed.

IDLE = "idle"
"""Nothing is running, and nothing is expected. A parked, flat trace."""

READY = "ready"
"""The console is up and waiting for a request. A slow, low idle motion."""

WAITING = "waiting"
"""The request is out; no token has come back yet."""

THINKING = "thinking"
"""Reasoning tokens are arriving."""

TOOL = "tool"
"""A tool call is running."""

STREAMING = "streaming"
"""Answer text is arriving."""

SAVING = "saving"
"""Auxiliary write — a session title, a memory flush, a compression."""

ERROR = "error"
"""The turn failed, or was cut short."""

STATES: Tuple[str, ...] = (
    IDLE, READY, WAITING, THINKING, TOOL, STREAMING, SAVING, ERROR,
)

#: The stencilled caption beside the figure. Short enough for a 26-column
#: instrument stack, and a noun phrase rather than a sentence.
STATE_LABELS: Dict[str, str] = {
    IDLE: "IDLE",
    READY: "READY",
    WAITING: "WAITING",
    THINKING: "THINKING",
    TOOL: "TOOL",
    STREAMING: "ANSWERING",
    SAVING: "RECORDING",
    ERROR: "FAULT",
}

#: Which palette role each state paints in. Roles, not colours — the skin
#: decides what they are, and a skin change re-themes every indicator.
STATE_ROLES: Dict[str, str] = {
    IDLE: "dim",
    READY: "dim",
    WAITING: "secondary",
    THINKING: "accent",
    TOOL: "secondary",
    STREAMING: "primary",
    SAVING: "success",
    ERROR: "error",
}

#: How often each state earns a repaint, in frames per second. A state that
#: is not moving does not get a frame clock at all: ``idle`` is static by
#: contract, so a parked indicator costs nothing and reads as finished.
STATE_RATE: Dict[str, float] = {
    IDLE: 0.0,
    READY: 2.0,
    WAITING: 5.0,
    THINKING: 8.0,
    TOOL: 6.0,
    STREAMING: 12.0,
    SAVING: 8.0,
    ERROR: 6.0,
}

#: The frame clock every kit-driven widget runs on. States sample it down.
TICK_HZ = 12.0

# ── Glyph ramps ──────────────────────────────────────────────────────────
# The same alphabet the rest of the console draws with: CP437's four shading
# densities, the eighth-blocks, and the quadrants. Nothing here needs a font
# the panel does not already rely on.

SHADE = " ░▒▓█"
COLUMNS = " ▁▂▃▄▅▆▇█"
#: Quadrant blocks indexed by a bitmask: 1 top-left, 2 top-right,
#: 4 bottom-left, 8 bottom-right. A cell therefore carries 2×2 sub-pixels,
#: which is what makes a curve out of a character grid.
QUADRANTS = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"

#: One drawn cell: the glyph, and the palette role it is painted in.
Cell = Tuple[str, str]
#: A frame: rows of cells, top row first.
Frame = List[List[Cell]]

BLANK: Cell = (" ", "dim")


def shade(value: float) -> str:
    """Quantise 0…1 onto the four CGA densities."""
    return SHADE[max(0, min(4, int(round(value * 4))))]


def column(value: float) -> str:
    """Quantise 0…1 onto the eighth-blocks, never below the baseline.

    Index 0 is a space, which would leave gaps in a single-row trace and make
    a live indicator look broken. A trace at rest is a floor, not a hole.
    """
    return COLUMNS[max(1, min(8, int(round(value * 8))))]


#: Every density ramp a kit draws with, so the optics pass can walk a glyph
#: up or down one. Quadrants are handled separately — they are a sub-pixel
#: mask, not a density, so a bloom widens the stroke rather than darkening it.
#: Later ramps win the two glyphs that appear in both. ``" "`` resolves the
#: same either way; ``"█"`` does not — a full cell dimmed down the *column*
#: ramp becomes ``▇``, a bottom-aligned bar, which is right inside a trace and
#: plainly wrong inside a field of ``░▒▓``. Shading is the field, so shading
#: wins, and a dimmed column trace loses its top eighth as a shade instead.
_RAMPS: Tuple[str, ...] = (COLUMNS, SHADE)

#: glyph → (ramp, index). Built once.
_RAMP_POSITION: Dict[str, Tuple[str, int]] = {
    glyph: (ramp, index)
    for ramp in _RAMPS
    for index, glyph in enumerate(ramp)
}

#: Quadrant glyph → its 2×2 bitmask, and back. A bloom sets the bits beside
#: the ones already lit, which is a stroke widening — what phosphor bloom
#: physically is — rather than a stroke brightening, which a mask cannot do.
_QUADRANT_MASK: Dict[str, int] = {
    glyph: mask for mask, glyph in enumerate(QUADRANTS)
}

#: How much bloom a stroke needs before the mask widens. Below it the effect
#: would be a curve that thickens at random as the figure moves.
_QUADRANT_BLOOM_AT = 0.30


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


# ── The kit contract ─────────────────────────────────────────────────────


class IndicatorKit:
    """One complete set of state figures, in a single visual language.

    Subclasses implement :meth:`figure` for the seven live states. ``idle``
    is handled here for every kit on purpose: a parked indicator has to mean
    the same thing whichever set is chosen, so it is always the same flat,
    static trace.
    """

    name = ""
    title = ""
    blurb = ""

    #: The display's rendering parameters, pushed in by the widget before it
    #: asks for a frame — never read from here by a subclass. Replaced
    #: wholesale on every paint, never mutated, so the empty class-level
    #: default is safe to share: under the bench mode it stays empty and the
    #: optics pass below is the identity.
    optics: Mapping[str, Any] = {}

    # ── Public ───────────────────────────────────────────────────────────

    def frame(self, state: str, tick: int, width: int, height: int) -> Frame:
        """Draw ``state`` at ``tick`` into a ``width`` × ``height`` box.

        The figure is the subclass's; the *optics* are the display's, and are
        applied here so every set gets them and no set has to implement them.
        """
        width = max(1, int(width))
        height = max(1, int(height))
        if state == IDLE or state not in STATES:
            drawn = self.parked(width, height)
        else:
            drawn = self.figure(state, int(tick), width, height)
        return self.apply_optics(drawn, int(tick))

    # ── The display's own effects ────────────────────────────────────────

    @property
    def bloom(self) -> float:
        """How hard the tube is being driven, 0…1. Zero under the bench mode."""
        try:
            return max(0.0, min(1.0, float(self.optics.get("bloom", 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    @property
    def scanlines(self) -> bool:
        """Whether alternate raster lines are drawn dark.

        One flag, asked of the display. A mode with no raster reports false
        here whatever a reader has stored against a mode that has one — which
        is why this can be a plain read and why a set never has to know which
        modes exist.
        """
        return bool(self.optics.get("scanlines"))

    def apply_optics(self, rows: Frame, tick: int) -> Frame:
        """Put a finished figure behind the glass of whatever is showing it.

        Two effects, in the order the tube applies them.

        **Bloom.** A cell driven harder spills light into the cells around it,
        so a lit glyph walks up its density ramp and a sub-pixel stroke
        widens. A blank cell stays blank: bloom is light spreading *from*
        something, and a tube with nothing on it is dark however far the
        brightness is wound up.

        **Scanlines.** The beam draws one line and skips the next, so every
        other row is the unlit gap between two of them: the same picture, with
        less light on it. Both halves of that are done — the row's role drops
        to ``dim`` and its glyphs come down one step of their ramp — because
        either alone reads as a colour change rather than as a raster. Only
        where there are alternate rows to drop: a one-row strip beside a fold
        has no raster to speak of.
        """
        bloom = self.bloom
        if bloom > 0.0:
            rows = [[self._bloom_cell(cell, bloom) for cell in row] for row in rows]
        if self.scanlines and len(rows) > 1:
            rows = [
                row if index % 2 == 0 else [self._gap_cell(cell) for cell in row]
                for index, row in enumerate(rows)
            ]
        return rows

    @staticmethod
    def _gap_cell(cell: Cell) -> Cell:
        """One cell on the dark line between two scanned ones."""
        glyph, _role = cell
        position = _RAMP_POSITION.get(glyph)
        if position is not None:
            ramp, index = position
            glyph = ramp[max(0, index - 1)]
        return (glyph, "dim")

    @staticmethod
    def _bloom_cell(cell: Cell, bloom: float) -> Cell:
        glyph, role = cell
        position = _RAMP_POSITION.get(glyph)
        if position is not None:
            ramp, index = position
            if index == 0:
                return cell
            # Proportional to the ramp, not a fixed number of steps: a four-
            # step shading ramp and an eight-step column ramp cover the same
            # range of brightness, so a fixed lift saturates one while barely
            # moving the other.
            lift = int(round(bloom * (len(ramp) - 1) * 0.4))
            return (ramp[min(len(ramp) - 1, index + lift)], role)
        if bloom >= _QUADRANT_BLOOM_AT:
            mask = _QUADRANT_MASK.get(glyph)
            if mask:
                # Widen sideways: a lit sub-pixel lights the one beside it.
                # 1 top-left, 2 top-right, 4 bottom-left, 8 bottom-right.
                widened = mask
                if mask & 1:
                    widened |= 2
                if mask & 2:
                    widened |= 1
                if mask & 4:
                    widened |= 8
                if mask & 8:
                    widened |= 4
                return (QUADRANTS[widened], role)
        return cell

    def parked(self, width: int, height: int) -> Frame:
        """A finished trace: flat, static, and identical in every kit."""
        rows: Frame = [[BLANK] * width for _ in range(height - 1)]
        rows.append([("▁", "dim")] * width)
        return rows

    def sample(self, width: int = 18, state: str = THINKING, tick: int = 9) -> str:
        """A still of one state as plain text, for a table cell."""
        rows = self.frame(state, tick, width, 1)
        return "".join(glyph for glyph, _role in rows[0])

    # ── For subclasses ───────────────────────────────────────────────────

    def figure(self, state: str, tick: int, width: int, height: int) -> Frame:
        raise NotImplementedError

    def role(self, state: str) -> str:
        return STATE_ROLES.get(state, "primary")

    @staticmethod
    def seconds(tick: int) -> float:
        """The frame clock in seconds, so kit speeds read as real rates."""
        return tick / TICK_HZ


# ── Kit 1: interference ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _Wave:
    kx: float       # cycles across the width
    ky: float       # cycles down the height
    speed: float    # travel, in cycles per second
    drift: float    # how fast the second grating slides against the first
    mix: bool       # multiply the two gratings (moiré) or show one alone
    radial: bool = False


class InterferenceKit(IndicatorKit):
    """Two sine gratings, multiplied. The console's native look.

    A moiré pattern is the honest picture of a wait: nothing is being
    measured, but two things are running against each other and the beat
    between them is visible. Each state gets a different pair of spatial
    frequencies, so the *texture* changes, not merely the speed — coarse
    horizontal banding while waiting, a fine cross-hatch while reasoning, a
    hard travelling front while an answer streams.
    """

    name = "bench"
    title = "INTERFERENCE"
    blurb = "Moiré beats between two gratings — the console's native ramp."

    _WAVES: Dict[str, _Wave] = {
        READY:     _Wave(0.75, 0.35, 0.09, 0.20, False),
        WAITING:   _Wave(1.00, 0.50, 0.30, 0.25, False),
        THINKING:  _Wave(2.25, 1.30, 0.75, 0.55, True),
        TOOL:      _Wave(3.50, 0.60, 0.45, -0.40, True),
        STREAMING: _Wave(1.50, 0.90, 1.50, 0.80, False),
        SAVING:    _Wave(0.00, 0.00, 0.90, 0.00, False, radial=True),
        ERROR:     _Wave(6.00, 3.10, 2.10, 1.60, True),
    }

    def figure(self, state, tick, width, height):
        wave = self._WAVES.get(state, self._WAVES[READY])
        role = self.role(state)
        t = self.seconds(tick) * wave.speed
        flat = height == 1
        rows: Frame = []
        for y in range(height):
            v = (y + 0.5) / height
            row: List[Cell] = []
            for x in range(width):
                u = (x + 0.5) / width
                if wave.radial:
                    # A ring leaving the centre: the shape of something being
                    # written out and finished with.
                    dx, dy = (u - 0.5) * 2.0, (v - 0.5) * 2.0
                    r = math.hypot(dx, dy * 0.5)
                    value = 0.5 + 0.5 * math.sin(TAU * (2.4 * r - t * 1.7))
                    value *= max(0.0, 1.0 - r * 0.85)
                else:
                    # The single-grating states still take a vertical term,
                    # as a phase shift rather than a second wave: without it
                    # every row of a multi-row panel is identical and the
                    # figure reads as a flat barcode instead of as travel.
                    a = math.sin(TAU * (wave.kx * u + wave.ky * v * 0.5 - t))
                    b = math.cos(TAU * (wave.ky * v + t * wave.drift))
                    value = 0.5 + 0.5 * (a * b if wave.mix else a)
                value = _clamp01(value)
                row.append((column(value) if flat else shade(value), role))
            rows.append(row)
        return rows


# ── Kit 2: parametric curves ─────────────────────────────────────────────


@dataclass(frozen=True)
class _Figure:
    a: int          # horizontal frequency
    b: int          # vertical frequency
    phase: float    # standing offset between the two axes
    speed: float    # how fast the phase precesses
    jitter: float = 0.0


class ScopeKit(IndicatorKit):
    """Lissajous figures on a quadrant canvas.

    Two oscillators, one per axis. The ratio between their frequencies is the
    whole picture: 1:1 is a line, 3:2 is a knot, 7:5 is a thicket. That makes
    a frequency ratio a genuinely good way to name a state — the figures are
    not stylistic variants of one shape, they are different shapes, and an
    oscilloscope operator could read them from across the room.

    Quadrant blocks give each cell 2×2 sub-pixels, which is what turns a
    character grid into a curve.
    """

    name = "scope"
    title = "LISSAJOUS"
    blurb = "Two-axis oscillator figures; the ratio names the state."

    _FIGURES: Dict[str, _Figure] = {
        READY:     _Figure(1, 1, 0.00, 0.06),
        WAITING:   _Figure(1, 2, 0.00, 0.20),
        THINKING:  _Figure(3, 2, 0.00, 0.45),
        TOOL:      _Figure(5, 4, 0.50, 0.30),
        STREAMING: _Figure(1, 3, 0.00, 0.85),
        SAVING:    _Figure(1, 1, 0.25, 0.60),
        ERROR:     _Figure(7, 5, 0.00, 1.10, jitter=0.09),
    }

    def figure(self, state, tick, width, height):
        spec = self._FIGURES.get(state, self._FIGURES[READY])
        role = self.role(state)
        t = self.seconds(tick)
        if height == 1:
            return [self._spot(spec, t, width, role)]

        sub_w, sub_h = width * 2, height * 2
        lit = bytearray(sub_w * sub_h)
        cx, cy = (sub_w - 1) / 2.0, (sub_h - 1) / 2.0
        rx, ry = cx * 0.94, cy * 0.94
        delta = spec.phase * TAU + t * spec.speed * TAU
        # Enough samples that the densest ratio still draws as a line rather
        # than as a row of dots, and it scales with the box it is given.
        samples = max(96, sub_w * 6)
        grow = 1.0
        if state == SAVING:
            # The recording figure breathes: a circle opening out and closing,
            # which is a different motion from every other state's precession.
            grow = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * TAU * 0.5))
        for i in range(samples):
            s = (i / samples) * TAU
            x = math.sin(spec.a * s + delta)
            y = math.sin(spec.b * s)
            if spec.jitter:
                x += spec.jitter * math.sin(s * 11.0 + t * 9.0)
                y += spec.jitter * math.cos(s * 13.0 + t * 7.0)
            col = int(round(cx + x * rx * grow))
            row = int(round(cy + y * ry * grow))
            if 0 <= col < sub_w and 0 <= row < sub_h:
                lit[row * sub_w + col] = 1
        return self._quadrants(lit, width, height, role)

    @staticmethod
    def _quadrants(lit: bytearray, width: int, height: int, role: str) -> Frame:
        sub_w = width * 2
        rows: Frame = []
        for y in range(height):
            row: List[Cell] = []
            top, bottom = (y * 2) * sub_w, (y * 2 + 1) * sub_w
            for x in range(width):
                left, right = x * 2, x * 2 + 1
                mask = (
                    (1 if lit[top + left] else 0)
                    | (2 if lit[top + right] else 0)
                    | (4 if lit[bottom + left] else 0)
                    | (8 if lit[bottom + right] else 0)
                )
                row.append((QUADRANTS[mask], role) if mask else BLANK)
            rows.append(row)
        return rows

    @staticmethod
    def _spot(spec: _Figure, t: float, width: int, role: str) -> List[Cell]:
        """One row: the spot and its phosphor tail, the way a scope decays.

        A Lissajous figure needs two dimensions. Given one row, the honest
        reduction is the other thing a scope shows — a single beam sweeping,
        with the trail behind it fading at the tube's own rate.
        """
        span = max(1, width - 1)
        position = (0.5 + 0.5 * math.sin(t * spec.speed * TAU * 2.0)) * span
        row: List[Cell] = []
        for x in range(width):
            distance = abs(x - position)
            glow = math.exp(-distance * distance / max(1.0, span * 0.30))
            row.append((shade(glow), role) if glow > 0.12 else ("▁", "dim"))
        return row


# ── Kit 3: cellular automata ─────────────────────────────────────────────


class AutomatonKit(IndicatorKit):
    """Elementary cellular automata, one generation per frame, scrolling up.

    Wolfram's numbering gives 256 one-dimensional rules from the same
    machinery, and they fall into genuinely different classes of behaviour:
    rule 4 dies out, rule 90 draws a Sierpiński triangle, rule 110 grows
    structure and gliders, rule 30 is chaotic. Assigning a class to a state
    is the point — a settled wait and a chaotic one look different because
    they *are* different, not because the palette changed.

    Deterministic: generation *n* is a pure function of the rule and the seed,
    so the same tick always draws the same picture.
    """

    name = "cells"
    title = "AUTOMATA"
    blurb = "Wolfram rules — each state a different class of behaviour."

    _RULES: Dict[str, int] = {
        READY: 4,        # every live cell dies alone: a still surface
        WAITING: 2,      # one cell drifting left, and nothing else
        THINKING: 30,    # chaotic
        TOOL: 110,       # structured, with gliders
        STREAMING: 90,   # Sierpiński: regular, fast, obviously ordered
        SAVING: 150,     # dense XOR fabric — a page being written
        ERROR: 105,      # noisy inverse of 150
    }

    #: Generations per second, per state. Kept below the frame clock so the
    #: pattern reads as scrolling rather than as flicker.
    _GENS_PER_SECOND = 6.0

    #: How far the automaton will be advanced in one draw before it is
    #: cheaper to start over. A console that has been idle for an hour must
    #: not spend a second catching up an automaton nobody watched.
    _MAX_CATCH_UP = 64

    def __init__(self) -> None:
        self._key: Tuple[int, int] | None = None
        self._generation = -1
        self._rows: Deque[List[int]] = deque(maxlen=48)

    def figure(self, state, tick, width, height):
        rule = self._RULES.get(state, 4)
        role = self.role(state)
        target = int(self.seconds(tick) * self._GENS_PER_SECOND)
        self._advance(rule, width, target)

        rows: Frame = []
        held = list(self._rows)
        for y in range(height):
            # The newest generation is the bottom row, so the pattern grows
            # upward out of the present.
            age = height - 1 - y
            index = len(held) - 1 - age
            cells = held[index] if 0 <= index < len(held) else [0] * width
            # Older generations fade: the top of the panel is the past.
            weight = 1.0 if height == 1 else 1.0 - 0.55 * (age / max(1, height - 1))
            glyph = shade(weight) if height > 1 else "█"
            # On a single row the dead cells are the track the live ones run
            # along, so they are drawn as a floor rather than as blanks: a
            # sparse generation on an empty line reads as a dead indicator.
            gap = BLANK if height > 1 else ("▁", "dim")
            rows.append([(glyph, role) if cell else gap for cell in cells])
        return rows

    def _advance(self, rule: int, width: int, target: int) -> None:
        key = (rule, width)
        if key != self._key or target < self._generation:
            self._key = key
            self._generation = 0
            self._rows.clear()
            self._rows.append(self._seed(width))
        steps = target - self._generation
        if steps > self._MAX_CATCH_UP:
            # Skipping straight to the target keeps the figure alive without
            # paying for the frames nobody saw.
            self._generation = target - self._MAX_CATCH_UP
            steps = self._MAX_CATCH_UP
        for _ in range(max(0, steps)):
            self._rows.append(self._step(rule, self._rows[-1]))
            self._generation += 1

    @staticmethod
    def _seed(width: int) -> List[int]:
        row = [0] * width
        row[width // 2] = 1
        return row

    @staticmethod
    def _step(rule: int, row: Sequence[int]) -> List[int]:
        width = len(row)
        out = [0] * width
        for i in range(width):
            # Wrapped edges, so the pattern stays on the panel instead of
            # walking off it and leaving the indicator blank.
            left = row[(i - 1) % width]
            centre = row[i]
            right = row[(i + 1) % width]
            out[i] = (rule >> ((left << 2) | (centre << 1) | right)) & 1
        return out


# ── Kit 4: phyllotaxis ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _Orbit:
    points: int
    spin: float      # revolutions per second
    divergence: float  # multiples of the golden angle between successive seeds
    breathe: float = 0.0


class OrbitKit(IndicatorKit):
    """Seed spirals, drawn as a density field.

    Place *n* points at successive multiples of an angle, at radii that grow
    as √i, and the arrangement depends entirely on that angle: the golden
    angle packs them evenly, a rational multiple of it collapses them into
    spokes. So the divergence angle is the state, and the number of visible
    arms is the reading. Density shading rather than an outline, because what
    is being shown is a distribution and not a curve.
    """

    name = "orbit"
    title = "PHYLLOTAXIS"
    blurb = "Seed spirals; the divergence angle sets the arm count."

    _ORBITS: Dict[str, _Orbit] = {
        READY:     _Orbit(70, 0.03, 1.000),
        WAITING:   _Orbit(90, 0.09, 0.500),   # two arms: a slow alternation
        THINKING:  _Orbit(190, 0.22, 1.000),  # even packing, no spokes
        TOOL:      _Orbit(120, 0.16, 0.250),  # four hard arms
        STREAMING: _Orbit(230, 0.45, 1.000),
        SAVING:    _Orbit(150, 0.12, 1.000, breathe=1.0),
        ERROR:     _Orbit(210, 0.70, 0.125),  # eight arms, spinning hard
    }

    def figure(self, state, tick, width, height):
        orbit = self._ORBITS.get(state, self._ORBITS[READY])
        role = self.role(state)
        t = self.seconds(tick)
        counts = [0.0] * (width * height)
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        rx, ry = max(0.5, cx * 0.96), max(0.5, cy * 0.96)
        spin = t * orbit.spin * TAU
        scale = 1.0
        if orbit.breathe:
            scale = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * TAU * 0.45))
        for i in range(orbit.points):
            angle = i * GOLDEN_ANGLE * orbit.divergence + spin
            radius = math.sqrt((i + 0.5) / orbit.points) * scale
            x = int(round(cx + math.cos(angle) * radius * rx))
            y = int(round(cy + math.sin(angle) * radius * ry))
            if 0 <= x < width and 0 <= y < height:
                counts[y * width + x] += 1.0

        peak = max(counts) or 1.0
        rows: Frame = []
        for y in range(height):
            row: List[Cell] = []
            for x in range(width):
                density = counts[y * width + x] / peak
                if height == 1:
                    row.append((column(density), role))
                elif density <= 0.0:
                    row.append(BLANK)
                else:
                    # A single seed still has to be visible, so the ramp
                    # starts at its second density rather than at a space.
                    row.append((shade(0.25 + 0.75 * density), role))
            rows.append(row)
        return rows


# ── Kit 5: radar sweep ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _Sweep:
    rate: float      # revolutions per second
    beams: int
    persistence: float  # how much of a revolution the phosphor holds
    returns: int


class SweepKit(IndicatorKit):
    """A rotating beam with a decaying phosphor trail, and its returns.

    The one instrument whose whole purpose is "something is being looked for
    and has not been found yet", which is exactly what a wait is. The beam
    rate says how hard the console is working; the persistence says how long
    the trail holds; the returns are fixed marks that light as the beam
    crosses them, so there is something to watch even at a slow sweep.
    """

    name = "sweep"
    title = "SWEEP"
    blurb = "A rotating beam, its phosphor decay, and fixed returns."

    _SWEEPS: Dict[str, _Sweep] = {
        READY:     _Sweep(0.10, 1, 0.55, 3),
        WAITING:   _Sweep(0.28, 1, 0.70, 5),
        THINKING:  _Sweep(0.55, 2, 0.85, 8),
        TOOL:      _Sweep(0.40, 3, 0.60, 4),
        STREAMING: _Sweep(1.10, 1, 0.95, 11),
        SAVING:    _Sweep(0.45, 4, 0.50, 6),
        ERROR:     _Sweep(1.60, 2, 0.35, 13),
    }

    def figure(self, state, tick, width, height):
        sweep = self._SWEEPS.get(state, self._SWEEPS[READY])
        role = self.role(state)
        t = self.seconds(tick)
        if height == 1:
            return [self._linear(sweep, t, width, role)]

        head = (t * sweep.rate * TAU) % TAU
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        rx, ry = max(0.5, cx), max(0.5, cy)
        arc = TAU / max(1, sweep.beams)
        tail = max(0.05, sweep.persistence) * arc
        returns = self._returns(sweep, width, height)
        rows: Frame = []
        for y in range(height):
            row: List[Cell] = []
            dy = (y - cy) / ry
            for x in range(width):
                dx = (x - cx) / rx
                radius = math.hypot(dx, dy)
                if radius > 1.02:
                    row.append(BLANK)
                    continue
                angle = math.atan2(dy, dx) % TAU
                # Distance *behind* the nearest beam, so the trail is drawn
                # where the beam has been and not where it is going.
                behind = (head - angle) % arc
                glow = math.exp(-behind / tail) if tail else 0.0
                # The rim carries more of the trace than the hub, the way a
                # real tube does — the beam spends longer out there.
                glow *= 0.35 + 0.65 * radius
                if (x, y) in returns:
                    glow = max(glow, 0.55 + 0.45 * glow)
                row.append((shade(glow), role) if glow > 0.12 else BLANK)
            rows.append(row)
        return rows

    @staticmethod
    def _returns(sweep: _Sweep, width: int, height: int) -> frozenset:
        """Fixed marks, spread by a low-discrepancy sequence.

        Deterministic on purpose: a return that moves between frames is
        noise, and noise is the one thing a radar display must not add. The
        additive recurrence with an irrational step spreads points far more
        evenly over a small grid than a random generator does — which matters
        at 34 columns, where a random draw clumps visibly.
        """
        if not sweep.returns:
            return frozenset()
        return frozenset(
            (
                min(width - 1, int(((n * 0.7548776662) % 1.0) * width)),
                min(height - 1, int(((n * 0.5698402910) % 1.0) * height)),
            )
            for n in range(1, sweep.returns + 1)
        )

    @staticmethod
    def _linear(sweep: _Sweep, t: float, width: int, role: str) -> List[Cell]:
        """One row: the beam as a position, with the same decay behind it."""
        span = max(1, width)
        head = (t * sweep.rate * 2.0 * span) % span
        tail = max(1.0, span * sweep.persistence * 0.5)
        row: List[Cell] = []
        for x in range(width):
            behind = (head - x) % span
            glow = math.exp(-behind / tail)
            row.append((column(glow), role) if glow > 0.10 else ("▁", "dim"))
        return row


# ── Kit 6: spectrum ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Spectrum:
    partials: Tuple[Tuple[float, float, float], ...]  # (frequency, rate, amplitude)
    window: str
    floor: float = 0.06


class SpectrumKit(IndicatorKit):
    """A bank of bars driven by a sum of partials — an analyser, not a bar.

    The oldest readable indicator there is: parallel channels, each with its
    own level. What makes it say something here is that the *number and
    spacing* of the partials is the state — one slow partial while waiting,
    three beating against each other while reasoning, a hard travelling
    envelope while an answer streams. Nothing about it is a measurement of
    audio; it is a measurement of which state is in force.
    """

    name = "bars"
    title = "SPECTRUM"
    blurb = "A partial bank; the beat pattern names the state."

    _SPECTRA: Dict[str, _Spectrum] = {
        READY:     _Spectrum(((1.5, 0.07, 1.0),), "flat", floor=0.03),
        WAITING:   _Spectrum(((2.0, 0.20, 1.0), (3.0, -0.11, 0.35)), "flat"),
        THINKING:  _Spectrum(
            ((3.0, 0.40, 1.0), (5.0, -0.27, 0.6), (8.0, 0.63, 0.35)), "bell",
        ),
        TOOL:      _Spectrum(((6.0, 0.18, 1.0), (12.0, 0.36, 0.4)), "comb"),
        STREAMING: _Spectrum(((2.0, 0.95, 1.0), (4.0, 0.47, 0.5)), "travel"),
        SAVING:    _Spectrum(((1.5, 0.30, 1.0), (3.0, 0.60, 0.5)), "pulse"),
        ERROR:     _Spectrum(
            ((9.0, 1.30, 1.0), (13.0, -0.90, 0.8), (17.0, 1.70, 0.6)), "flat",
        ),
    }

    def figure(self, state, tick, width, height):
        spec = self._SPECTRA.get(state, self._SPECTRA[READY])
        role = self.role(state)
        t = self.seconds(tick)
        total = sum(amp for _f, _r, amp in spec.partials) or 1.0

        levels: List[float] = []
        for x in range(width):
            u = x / max(1, width - 1)
            value = 0.0
            for frequency, rate, amplitude in spec.partials:
                value += amplitude * math.sin(TAU * (frequency * u + rate * t))
            level = 0.5 + 0.5 * (value / total)
            # The window shapes the bank without flattening it: a bar outside
            # the envelope still moves, it just does not tower. Multiplying
            # outright left whole states pinned to the bottom row.
            shaped = level * (0.35 + 0.65 * self._window(spec.window, u, t))
            levels.append(_clamp01(max(spec.floor, 0.04 + 0.92 * shaped)))

        if height == 1:
            return [[(column(level), role) for level in levels]]

        rows: Frame = []
        for y in range(height):
            top = (height - y) / height
            bottom = (height - y - 1) / height
            row: List[Cell] = []
            for level in levels:
                if level >= top:
                    row.append(("█", role))
                elif level <= bottom:
                    row.append(BLANK)
                else:
                    within = (level - bottom) / (top - bottom)
                    row.append((column(within), role))
            rows.append(row)
        return rows

    @staticmethod
    def _window(kind: str, u: float, t: float) -> float:
        """The envelope over the bank — what shapes the bars as a whole."""
        if kind == "bell":
            return 0.35 + 0.65 * math.exp(-((u - 0.5) ** 2) / 0.06)
        if kind == "comb":
            return 0.30 + 0.70 * (1.0 if int(u * 12.0) % 2 == 0 else 0.25)
        if kind == "travel":
            centre = (t * 0.55) % 1.0
            distance = min(abs(u - centre), 1.0 - abs(u - centre))
            return 0.25 + 0.75 * math.exp(-(distance ** 2) / 0.02)
        if kind == "pulse":
            return 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * TAU * 0.55))
        return 1.0


# ── The registry ─────────────────────────────────────────────────────────

# ── Kit 7: the raster ────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Raster:
    sweeps: float        # complete fields drawn per second
    beams: int           # how many spots are on the field at once
    persistence: float   # trail length, as a fraction of one field
    roll: float = 0.0    # fields per second of vertical drift (lost hold)
    tear: float = 0.0    # horizontal displacement per row, 0..1 of the width
    interlace: bool = False
    flood: bool = False  # a whole-field flash instead of a moving spot


class RasterKit(IndicatorKit):
    """A scanning spot with phosphor persistence — how the picture is made.

    Every other set here draws a *pattern* on the display. This one draws the
    display: a single spot crossing the field line by line, and behind it the
    phosphor decaying at the rate the tube's coating decays. That makes it the
    DOS mode's native figure, and it makes the states genuinely different
    pictures rather than different speeds — persistence is the variable, and
    persistence is what a CRT has instead of a frame rate.

    Waiting is one slow spot with a long tail, which is a tube with nothing to
    put on it. Reasoning is two spots redrawing the field faster than it
    decays, which is what an over-driven display looks like. A tool call is
    interlaced — every other line, marching — because a tool is a thing
    happening in steps. An answer is one fast spot with a bright head, which
    is text being written. A write to the record floods the field and lets it
    settle. A fault is the vertical hold gone: the picture rolls and tears.
    """

    name = "raster"
    title = "CRT RASTER"
    blurb = "A scanning spot with phosphor decay — the DOS mode's own figure."

    _RASTERS: Dict[str, _Raster] = {
        READY:     _Raster(0.14, 1, 0.60),
        WAITING:   _Raster(0.45, 1, 0.34),
        THINKING:  _Raster(1.60, 2, 0.13),
        TOOL:      _Raster(0.85, 1, 0.22, interlace=True),
        STREAMING: _Raster(2.40, 1, 0.30),
        SAVING:    _Raster(0.90, 1, 0.40, flood=True),
        ERROR:     _Raster(1.10, 1, 0.24, roll=0.85, tear=0.18),
    }

    def figure(self, state, tick, width, height):
        spec = self._RASTERS.get(state, self._RASTERS[READY])
        role = self.role(state)
        t = self.seconds(tick)
        flat = height == 1
        cells = max(1, width * height)
        # A lost vertical hold shifts the whole picture down the glass; the
        # picture is unchanged, which is exactly why it is so recognisable.
        roll = int(t * spec.roll * height) if spec.roll else 0

        rows: Frame = []
        for y in range(height):
            source_y = (y + roll) % height
            # A torn line is displaced sideways by an amount that varies down
            # the field, so consecutive lines break in different places.
            shift = (
                int(math.sin(TAU * (t * 1.7 + source_y * 0.37)) * spec.tear * width)
                if spec.tear
                else 0
            )
            row: List[Cell] = []
            for x in range(width):
                source_x = (x + shift) % width
                if spec.flood:
                    # One flash per field, decaying from the top of the glass
                    # down, with the ripple of a supply settling behind it —
                    # a write reaching the record and being done with.
                    phase = (t * spec.sweeps) % 1.0
                    depth = (source_y + 0.5) / height
                    across = (source_x + 0.5) / width
                    settle = 0.72 + 0.28 * math.sin(
                        TAU * (across * 1.5 - t * 2.2 + depth * 0.5)
                    )
                    value = max(0.0, 1.0 - phase * 0.92) * settle * (
                        1.0 - depth * 0.35
                    )
                else:
                    here = (source_y * width + source_x) / cells
                    value = 0.0
                    for beam in range(max(1, spec.beams)):
                        head = (t * spec.sweeps + beam / max(1, spec.beams)) % 1.0
                        # How long ago the spot passed this cell, in fields.
                        age = (head - here) % 1.0
                        value = max(
                            value, math.exp(-age / max(0.01, spec.persistence))
                        )
                    if spec.interlace and (source_y + int(t * 6.0)) % 2:
                        # The field the beam is not writing this pass.
                        value *= 0.22
                value = _clamp01(value)
                row.append((column(value) if flat else shade(value), role))
            rows.append(row)
        return rows


_KIT_TYPES: Tuple[type, ...] = (
    InterferenceKit,
    ScopeKit,
    AutomatonKit,
    OrbitKit,
    SweepKit,
    SpectrumKit,
    RasterKit,
)

#: The name a console falls back to when nothing has been chosen. It is the
#: one built from the same shading ramp as the rest of the panel.
DEFAULT_KIT = InterferenceKit.name


def kit_names() -> List[str]:
    """Every selectable set, in the order the PANEL pane lists them."""
    return [kit.name for kit in _KIT_TYPES]


def kit_catalogue() -> List[Tuple[str, str, str]]:
    """``(name, title, blurb)`` for each set — what the chooser shows."""
    return [(kit.name, kit.title, kit.blurb) for kit in _KIT_TYPES]


def get_kit(name: str | None) -> IndicatorKit:
    """A fresh instance of the named set, or the default when unknown.

    Fresh rather than shared: two indicators on screen at once must not share
    an automaton's generation counter, or one would drag the other's pattern
    sideways every time it drew.
    """
    wanted = (name or "").strip().lower()
    for kit in _KIT_TYPES:
        if kit.name == wanted:
            return kit()
    return InterferenceKit()


# ── Widgets ──────────────────────────────────────────────────────────────


def widget_optics(widget: Widget) -> Mapping[str, Any]:
    """The display's rendering parameters, read off the app that owns ``widget``.

    Deliberately a dict read rather than an import of the display mode: an
    indicator must not know which modes exist, only that the one in force may
    have something to say about how a lit cell looks. Total — a widget with no
    app, or a palette from before optics existed, gets an empty mapping and
    therefore the plain figure.
    """
    try:
        optics = widget.app.bench_palette.optics
    except Exception:
        return {}
    return optics if isinstance(optics, Mapping) else {}


def palette_colour(widget: Widget, role: str) -> str:
    """Resolve a palette role against the skin in force right now.

    Roles are late-bound on purpose: a kit that baked in a colour would keep
    it after a skin change, which is exactly the bug the transcript had.
    """
    palette = getattr(widget.app, "bench_palette", None)
    if palette is not None:
        try:
            value = palette.get(role, "")
        except Exception:
            value = ""
        if value:
            return value
    return "default"


class KitWidget(Widget):
    """Shared frame clock and painter for every kit-driven indicator.

    One clock at :data:`TICK_HZ`, sampled down per state, so a console that
    is doing nothing repaints nothing and a console mid-answer repaints at
    twelve frames a second.
    """

    def __init__(self, kit: IndicatorKit | None = None, state: str = IDLE, **kwargs):
        super().__init__(**kwargs)
        self._kit = kit if kit is not None else get_kit(None)
        self._state = state if state in STATES else IDLE
        self._tick = 0
        self._timer = None

    # ── State ────────────────────────────────────────────────────────────

    @property
    def kit(self) -> IndicatorKit:
        return self._kit

    @property
    def state(self) -> str:
        return self._state

    def set_kit(self, kit: IndicatorKit | str | None) -> None:
        self._kit = kit if isinstance(kit, IndicatorKit) else get_kit(kit)
        self.refresh()

    def set_state(self, state: str) -> None:
        if state not in STATES or state == self._state:
            return
        self._state = state
        self.refresh()

    # ── Clock ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0 / TICK_HZ, self._advance)

    def _advance(self) -> None:
        self._tick += 1
        if not self.on_screen:
            return
        rate = STATE_RATE.get(self._state, 6.0)
        if rate <= 0.0:
            return
        divisor = max(1, int(round(TICK_HZ / rate)))
        if self._tick % divisor == 0:
            self.refresh()

    @property
    def on_screen(self) -> bool:
        """Whether this indicator currently occupies any of the screen.

        Every pane is mounted at start-up whether or not its switch is
        thrown, and the instrument stack collapses on a narrow window — so
        without this the console would run a frame clock for five indicators
        nobody is looking at. A widget under a hidden ancestor is never
        arranged, so its size is the honest test and costs nothing to ask.
        """
        size = self.size
        return size.width > 0 and size.height > 0

    # ── Painting ─────────────────────────────────────────────────────────

    def _paint(self, width: int, height: int, state: str | None = None) -> Text:
        """One frame as Rich text, with each role resolved to a colour.

        Cells are grouped into runs of the same role before being appended:
        a thirty-column figure is two or three styled spans rather than
        thirty, which is the difference between a frame clock that keeps up
        and one that does not.
        """
        # Handed over on every paint rather than at ``set_kit`` time: the
        # brightness control is on a settings pane the reader can be moving
        # while this is drawing, and a figure that keeps last minute's optics
        # would make the control look broken.
        self._kit.optics = widget_optics(self)
        frame = self._kit.frame(state or self._state, self._tick, width, height)
        colours: Dict[str, str] = {}
        out = Text(no_wrap=True, overflow="crop")
        for index, row in enumerate(frame):
            if index:
                out.append("\n")
            run: List[str] = []
            run_role = ""
            for glyph, role in row:
                if role != run_role and run:
                    out.append("".join(run), style=colours.setdefault(
                        run_role, palette_colour(self, run_role)))
                    run = []
                run_role = role
                run.append(glyph)
            if run:
                out.append("".join(run), style=colours.setdefault(
                    run_role, palette_colour(self, run_role)))
        return out


class StateStrip(KitWidget):
    """One row of the active kit. The indicator that fits beside anything."""

    DEFAULT_CSS = """
    StateStrip {
        height: 1;
    }
    """

    def render(self) -> Text:
        return self._paint(max(1, self.size.width or 12), 1)


class Pen(StateStrip):
    """The thinking indicator beside a turn's workings.

    Reasoning is the one thing the console cannot show honestly. The token
    text is the model's scratch work, not an answer, and putting it on screen
    invites the reader to read something that was never addressed to them —
    while a running character count says only that a number is going up. So
    the fold reports the one fact that matters, that work is happening, and
    reports it the way the panel reports everything else: as an instrument.

    Parked means finished, in every kit: a flat, static trace. Running means
    the active set's figure for whatever the turn is doing right now —
    reasoning, or a tool.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(state=IDLE, **kwargs)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, state: str = THINKING) -> None:
        """Begin indicating. Idempotent, and re-aimable at another state."""
        self._running = True
        self.set_state(state if state in STATES and state != IDLE else THINKING)

    def stop(self) -> None:
        """Park the pen: a flat, finished trace."""
        self._running = False
        self.set_state(IDLE)


class ActivityMonitor(KitWidget):
    """The instrument stack's state panel: the figure, named, with a reading.

    The figure fills every row but the last; the last carries the state's
    name and whatever number the console actually has for it (elapsed
    seconds, characters, the tool that is running). A figure nobody can name
    is decoration, and a name with no figure is a log line — the panel needs
    both on the same instrument.
    """

    DEFAULT_CSS = """
    ActivityMonitor {
        height: 6;
        min-height: 2;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(state=READY, **kwargs)
        self._detail = ""

    def set_detail(self, detail: str) -> None:
        if detail != self._detail:
            self._detail = detail
            self.refresh()

    def render(self) -> Text:
        width = max(1, self.size.width or 24)
        height = max(2, self.size.height or 4)
        out = self._paint(width, height - 1)
        out.append("\n")
        out.append_text(self._caption(width))
        return out

    def _caption(self, width: int) -> Text:
        """The state's name, and the reading, on one row.

        The name is right-aligned against the reading so the two read as a
        label and a value rather than as a sentence. Where they do not both
        fit, the *reading* is the half that gives way — a truncated number is
        still a number, and a truncated state name is a different word.
        """
        label = STATE_LABELS.get(self._state, self._state.upper())
        caption = Text(no_wrap=True, overflow="ellipsis")
        caption.append(
            label, style=f"bold {palette_colour(self, self._kit.role(self._state))}"
        )
        room = width - len(label) - 1
        if self._detail and room >= 3:
            detail = self._detail
            if len(detail) > room:
                detail = detail[: room - 1].rstrip() + "…"
            caption.append(" " * max(1, width - len(label) - len(detail)))
            caption.append(detail, style=palette_colour(self, "dim"))
        return caption


class KitPreview(KitWidget):
    """A live sampler: every state of one set, side by side, on one row each.

    Choosing a set from a table of names alone is choosing blind — the sets
    differ in motion, which no still can show. This runs all of them at once
    so the choice is made from the thing being chosen.
    """

    DEFAULT_CSS = """
    KitPreview {
        height: auto;
        min-height: 4;
    }
    """

    #: The states worth showing in a preview: the four a reader will actually
    #: spend time watching, in the order a turn passes through them.
    PREVIEW_STATES = (WAITING, THINKING, TOOL, STREAMING, SAVING)

    #: Columns reserved for the state's name, so the strips line up.
    LABEL_WIDTH = 11

    def render(self) -> Text:
        width = max(8, self.size.width or 30)
        figure_width = max(4, width - self.LABEL_WIDTH)
        dim = palette_colour(self, "dim")
        out = Text(no_wrap=True, overflow="crop")
        for index, state in enumerate(self.PREVIEW_STATES):
            if index:
                out.append("\n")
            label = STATE_LABELS.get(state, state.upper())
            out.append(f"{label:<{self.LABEL_WIDTH - 1}} ", style=dim)
            out.append_text(self._paint(figure_width, 1, state=state))
        return out

    def _advance(self) -> None:
        # A preview shows several states at once, so it cannot sample its
        # clock down to any one of them: it runs at the rate of the fastest
        # thing on it — and only while the settings pane it lives on is the
        # one being looked at.
        self._tick += 1
        if self.on_screen:
            self.refresh()


__all__ = [
    "ActivityMonitor",
    "AutomatonKit",
    "Cell",
    "DEFAULT_KIT",
    "ERROR",
    "Frame",
    "IDLE",
    "IndicatorKit",
    "InterferenceKit",
    "KitPreview",
    "KitWidget",
    "OrbitKit",
    "Pen",
    "READY",
    "RasterKit",
    "SAVING",
    "STATES",
    "STATE_LABELS",
    "STATE_RATE",
    "STATE_ROLES",
    "STREAMING",
    "ScopeKit",
    "SpectrumKit",
    "StateStrip",
    "SweepKit",
    "THINKING",
    "TICK_HZ",
    "TOOL",
    "WAITING",
    "get_kit",
    "kit_catalogue",
    "kit_names",
    "palette_colour",
    "widget_optics",
]
