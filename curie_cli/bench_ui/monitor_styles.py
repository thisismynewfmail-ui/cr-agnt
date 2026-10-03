"""The resource monitor's styles: six ways of drawing one machine.

Every style is an animation, and every one of them is *driven* by the
readings rather than decorated with them — the motion is the measurement:

* **NEURAL** — spiking networks. Each CPU core is an input neuron firing at
  its own load; each GPU is a network firing at its utilisation, recruited
  as far as its memory is used, burning the colour of its temperature.
* **ORRERY** — a solar system in braille. The sun swells with total CPU
  load; every core is a planet whose angular speed *is* its load, so an idle
  core stands still; the asteroid belt is lit as far round as memory is
  used, swap an inner arc; network traffic arrives and leaves as comets;
  disk I/O sparks on the belt; each GPU is a giant whose moons orbit at its
  utilisation inside a ring as full as its memory.
* **WATERFALL** — a spectrogram of the last few seconds. A column for every
  core, and for memory, swap, network down and up, disk reads and writes
  and each GPU's use and memory; newest at the top, two samples to a row,
  coloured cool to hot.
* **SCOPE** — a phosphor oscilloscope in roll mode. CPU, memory, GPU,
  network and disk traced against a graticule, scrolling smoothly with
  time, the beam leaving a fading afterglow where a trace has jumped.
* **RAIN** — digital rain. Each core rains in its own columns, as often and
  as fast as it is loaded, the falling heads printing the core's load as a
  digit; the rain falls into a pool as wide as memory is used; a burst of
  network traffic is lightning; disk activity is dust lifting off the pool.
* **TIDE** — tanks filled to the exact level of CPU, memory, swap, GPU and
  VRAM, whose surfaces swell with how fast the level is moving, with
  bubbles that rise as fast as the work behind them, under a pipe that
  carries the network's traffic in and out.

The network and disk are drawn on a logarithmic scale, 1 KiB/s to 1 GiB/s,
because their range is six orders of magnitude and a linear one would show
nothing but the largest burst.

A style is a pure function of what it is given: the newest reading, the
history behind it, the time step and its own animation state. It does not
read a clock, a file or a widget — so a test can hold any of them to a
figure, and the widget that shows them (:class:`ResourceMonitor` in
:mod:`curie_cli.bench_ui.resources`) owns everything that touches the world.

Frames are rows of ``(glyph, role, background role)`` cells, roles being the
console's palette names; a ``None`` background is the widget's own.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Cell = Tuple[str, str, Optional[str]]
Frame = List[List[Cell]]
#: A two-element cell, the shape the neuron field and the optics speak.
Cell2 = Tuple[str, str]

BLANK: Cell = (" ", "dim", None)

#: Eighth blocks: a level inside one cell, bottom up.
EIGHTHS = " ▁▂▃▄▅▆▇█"
#: Cool to hot, for anything coloured by how loaded it is.
HEAT = ("rule", "secondary", "warning", "primary", "error")

#: How often the sampler takes a reading, seconds — the history's spacing.
SAMPLE_SECONDS = 0.5


# ── Small helpers ────────────────────────────────────────────────────────


def clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number):
        return 0.0
    return max(0.0, min(1.0, number))


def fraction(used: Optional[float], total: Optional[float]) -> Optional[float]:
    if not total or used is None:
        return None
    return clamp01(used / total)


def log_rate(rate: Optional[float]) -> float:
    """A byte rate on the monitor's log scale: 0 at 1 KiB/s, 1 at 1 GiB/s."""
    if not rate or rate <= 1024.0:
        return 0.0
    return clamp01(math.log(rate / 1024.0, 1024.0 ** 2))


def level_role(value: float) -> str:
    """The colour a load is drawn in: calm, then warning, then error."""
    if value >= 0.9:
        return "error"
    if value >= 0.75:
        return "warning"
    return "primary"


def heat_index(value: float) -> int:
    """0 for nothing, else 1…5 up :data:`HEAT`."""
    value = clamp01(value)
    if value < 0.03:
        return 0
    return 1 + min(4, int(value * 5.0))


def heat_role(temperature: Optional[float]) -> str:
    """The palette role a part burns in, from its temperature."""
    if temperature is None or temperature < 70:
        return "accent"
    if temperature < 85:
        return "warning"
    return "error"


def group(values: Sequence[float], slots: int) -> List[float]:
    """``values`` folded into at most ``slots`` means, in order."""
    values = [clamp01(v) for v in values]
    if not values or slots <= 0:
        return []
    if len(values) <= slots:
        return values
    out = []
    for slot in range(slots):
        start = slot * len(values) // slots
        end = max(start + 1, (slot + 1) * len(values) // slots)
        chunk = values[start:end]
        out.append(sum(chunk) / len(chunk))
    return out


def blank(width: int, height: int) -> Frame:
    return [[BLANK] * width for _ in range(height)]


def put_text(frame: Frame, x: int, y: int, text: str, role: str = "dim") -> int:
    """Write ``text`` at (x, y), clipped to the frame. Returns the next x."""
    if not 0 <= y < len(frame):
        return x
    row = frame[y]
    for char in text:
        if 0 <= x < len(row):
            row[x] = (char, role, None)
        x += 1
    return x


def follow(current: float, target: float, dt: float, speed: float = 4.0) -> float:
    """Ease ``current`` towards ``target`` — exponentially, in real time."""
    return current + (target - current) * (1.0 - math.exp(-max(0.0, dt) * speed))


def cores_of(reading: Any) -> List[float]:
    """Per-core loads, or the total standing in for a machine that gave none."""
    cores = [clamp01(v) for v in (getattr(reading, "cores", ()) or ())]
    if cores:
        return cores
    total = getattr(reading, "cpu", None)
    return [clamp01(total)] if total is not None else []


def gpus_of(reading: Any) -> list:
    return list(getattr(reading, "gpus", ()) or ())


def memory_of(reading: Any) -> Optional[float]:
    return fraction(getattr(reading, "memory_used", None), getattr(reading, "memory_total", None))


def swap_of(reading: Any) -> Optional[float]:
    return fraction(getattr(reading, "swap_used", None), getattr(reading, "swap_total", None))


def disk_rate(reading: Any) -> Optional[float]:
    read, write = getattr(reading, "disk_read", None), getattr(reading, "disk_write", None)
    if read is None and write is None:
        return None
    return (read or 0.0) + (write or 0.0)


def net_rate(reading: Any) -> Optional[float]:
    rx, tx = getattr(reading, "net_rx", None), getattr(reading, "net_tx", None)
    if rx is None and tx is None:
        return None
    return (rx or 0.0) + (tx or 0.0)


# ── Sub-cell plotting ────────────────────────────────────────────────────


class BrailleCanvas:
    """A field of dots, two across and four down to every cell.

    A terminal cell is about twice as tall as it is wide, so its braille dots
    come out square: a circle drawn here is a circle on screen. Every dot has
    a brightness that :meth:`fade` lowers each frame — the afterglow that
    makes a moving thing leave a trail and a still one stay sharp — and the
    role of whatever last lit it brightest.
    """

    #: The braille bit for the dot in each (row, column) of a cell.
    _BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

    def __init__(self, cells_wide: int, cells_high: int) -> None:
        self.cells_wide = max(1, cells_wide)
        self.cells_high = max(1, cells_high)
        self.width = self.cells_wide * 2
        self.height = self.cells_high * 4
        self._level = [0.0] * (self.width * self.height)
        self._role: List[str] = ["dim"] * (self.width * self.height)

    def fits(self, cells_wide: int, cells_high: int) -> bool:
        return cells_wide == self.cells_wide and cells_high == self.cells_high

    def fade(self, keep: float) -> None:
        level = self._level
        for index in range(len(level)):
            if level[index]:
                value = level[index] * keep
                level[index] = value if value > 0.02 else 0.0

    @staticmethod
    def _dot(value: float) -> int:
        """The dot a coordinate falls in — halves up, always.

        Not :func:`round`, which takes halves to the even neighbour: a centre
        on a half (the middle of an even width is one) then rounds 30.5 down
        and 31.5 up, so a filled disc comes out striped with empty columns.
        """
        return int(math.floor(value + 0.5))

    def level_at(self, x: float, y: float) -> float:
        ix, iy = self._dot(x), self._dot(y)
        if 0 <= ix < self.width and 0 <= iy < self.height:
            return self._level[iy * self.width + ix]
        return 0.0

    def plot(self, x: float, y: float, level: float = 1.0, role: str = "primary") -> None:
        ix, iy = self._dot(x), self._dot(y)
        if 0 <= ix < self.width and 0 <= iy < self.height:
            index = iy * self.width + ix
            if level >= self._level[index]:
                self._level[index] = level
                self._role[index] = role

    def line(self, x0: float, y0: float, x1: float, y1: float,
             level: float = 1.0, role: str = "primary") -> None:
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for step in range(steps + 1):
            t = step / steps
            self.plot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, level, role)

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, level: float,
                role: str, start: float = 0.0, sweep: float = 2 * math.pi,
                spacing: float = 1.0) -> None:
        """An ellipse, or the arc of one from ``start`` round ``sweep``."""
        perimeter = 2 * math.pi * math.sqrt((rx * rx + ry * ry) / 2.0)
        count = max(8, int(perimeter * (sweep / (2 * math.pi)) / max(0.5, spacing)))
        for step in range(count + 1):
            angle = start + sweep * step / count
            self.plot(cx + rx * math.cos(angle), cy + ry * math.sin(angle), level, role)

    def disc(self, cx: float, cy: float, radius: float, level: float = 1.0,
             role: str = "primary") -> None:
        reach = int(math.ceil(radius))
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                if dx * dx + dy * dy <= radius * radius + 0.25:
                    self.plot(cx + dx, cy + dy, level, role)

    def render(self, floor: float = 0.1) -> Frame:
        """The dots, as cells: a braille glyph each, in its brightest dot's role.

        A cell lit only by afterglow is drawn ``dim`` whatever lit it — the
        glow is the trail, not the thing.
        """
        frame: Frame = []
        for cy in range(self.cells_high):
            row: List[Cell] = []
            for cx in range(self.cells_wide):
                bits = 0
                best = 0.0
                role = "dim"
                for dy in range(4):
                    base = (cy * 4 + dy) * self.width + cx * 2
                    for dx in range(2):
                        level = self._level[base + dx]
                        if level > floor:
                            bits |= self._BITS[dy][dx]
                            if level > best:
                                best = level
                                role = self._role[base + dx]
                if not bits:
                    row.append(BLANK)
                else:
                    row.append((chr(0x2800 + bits), role if best >= 0.45 else "dim", None))
            frame.append(row)
        return frame


# ── The neuron field ─────────────────────────────────────────────────────

#: Columns per layer: the neuron, two cells of wire, the bus, two more.
SPACING = 6

#: Neuron glyphs, all from code page 437 so the field draws on the phosphor
#: as it does on the panel.
DORMANT = "·"
RESTING = "○"
CHARGED = "◙"
FIRING = "☼"
RECOVERING = "•"

#: The wiring, and the same wiring carrying a spike: single lines at rest,
#: double where a pulse is passing.
WIRE = {"h": "─", "v": "│", "x": "┼", "t": "┬", "b": "┴"}
LIVE = {"h": "═", "v": "║", "x": "╬", "t": "╦", "b": "╩"}

#: Which piece of wiring a glyph is, lit or not — so a second pulse over a
#: cell the first has already doubled keeps the cell's shape.
_WIRE_KIND = {glyph: kind for table in (WIRE, LIVE) for kind, glyph in table.items()}

#: Firing threshold, leak per tick, and how long a neuron rests after firing.
THRESHOLD = 1.0
LEAK = 0.82
REFRACTORY = 2

#: Cells a spike advances per tick.
SPIKE_SPEED = 2


@dataclass
class _Spike:
    path: List[Tuple[int, int]]
    target: Tuple[int, int]
    weight: float
    position: int = 0


class NeuronNet:
    """A small spiking network, laid out to whatever box it is given.

    Layers are columns, :data:`SPACING` cells apart; neurons sit on every
    other row, with the crossbar's vertical runs between them. Every neuron
    is wired to every neuron of the next layer through the bus column between
    the two — a fully connected layer, drawn the way a schematic draws one.

    Integrate-and-fire, kept as simple as reads well at a handful of frames a
    second: input arrives at the first layer at a rate set by the activity it
    is driven with — one figure for the whole layer, or one per input neuron
    — potential leaks away between arrivals, a neuron over threshold fires
    and rests, and each firing sends a pulse along the wire to one or two
    neurons of the next layer. Seeded, so a test can hold it to a figure.
    """

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._seed = seed
        self._shape: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self._potential: List[List[float]] = []
        self._since: List[List[int]] = []
        self._rank: List[List[float]] = []
        self._spikes: List[_Spike] = []
        self.fired_last = 0

    # ── Geometry ─────────────────────────────────────────────────────────

    @staticmethod
    def shape_for(width: int, height: int) -> Tuple[int, int, int]:
        """(layers, neurons per layer, left offset) for a box."""
        layers = max(1, (max(1, width) - 1) // SPACING + 1)
        per_layer = max(1, (max(1, height) + 1) // 2)
        used = (layers - 1) * SPACING + 1
        return layers, per_layer, max(0, (width - used) // 2)

    def _ensure(self, width: int, height: int) -> Tuple[int, int, int]:
        layers, per_layer, offset = self.shape_for(width, height)
        if self._shape != (layers, per_layer, offset, height):
            self._shape = (layers, per_layer, offset, height)
            self._potential = [[0.0] * per_layer for _ in range(layers)]
            self._since = [[99] * per_layer for _ in range(layers)]
            ranks = random.Random(self._seed + 7)
            self._rank = [[ranks.random() for _ in range(per_layer)] for _ in range(layers)]
            self._spikes = []
        return layers, per_layer, offset

    def _x(self, layer: int, offset: int) -> int:
        return offset + layer * SPACING

    def _path(self, src: Tuple[int, int], dst: Tuple[int, int], offset: int) -> List[Tuple[int, int]]:
        """The cells a pulse crosses: out along its row, along the bus, in."""
        (layer, row), (_next, target) = src, dst
        x0 = self._x(layer, offset)
        bus = x0 + SPACING // 2
        x1 = self._x(layer + 1, offset)
        y0, y1 = 2 * row, 2 * target
        cells = [(x, y0) for x in range(x0 + 1, bus)]
        step = 1 if y1 >= y0 else -1
        cells += [(bus, y) for y in range(y0, y1 + step, step)]
        cells += [(x, y1) for x in range(bus + 1, x1)]
        return cells

    # ── Dynamics ─────────────────────────────────────────────────────────

    def alive(self, layer: int, row: int, recruited: float) -> bool:
        """Whether a neuron is in play, given the share of the net recruited.

        Fixed per neuron (a rank drawn once), so the dormant ones stay
        dormant while memory holds steady rather than flickering at random.
        The first layer always has one neuron alive, so input has somewhere
        to land.
        """
        if layer == 0 and row == 0:
            return True
        return self._rank[layer][row] < recruited

    def step(self, width: int, height: int, activity, recruited: float = 1.0) -> None:
        """Advance one tick with ``recruited`` of the net alive.

        ``activity`` is 0…1 for the whole input layer, or a sequence of them,
        one per input neuron (folded or repeated to fit the layer).
        """
        layers, per_layer, offset = self._ensure(width, height)
        if isinstance(activity, (int, float)):
            drives = [clamp01(activity)] * per_layer
        else:
            values = [clamp01(v) for v in activity] or [0.0]
            drives = group(values, per_layer) if len(values) >= per_layer else [
                values[row * len(values) // per_layer] for row in range(per_layer)
            ]
        rng = self._rng
        for layer in range(layers):
            for row in range(per_layer):
                self._potential[layer][row] *= LEAK
                self._since[layer][row] = min(99, self._since[layer][row] + 1)

        landed: List[_Spike] = []
        moving: List[_Spike] = []
        for spike in self._spikes:
            spike.position += SPIKE_SPEED
            (landed if spike.position >= len(spike.path) else moving).append(spike)
        self._spikes = moving
        for spike in landed:
            layer, row = spike.target
            if self.alive(layer, row, recruited):
                self._potential[layer][row] += spike.weight

        # The input. An idle unit still throws the odd spark — a field that
        # is perfectly still reads as a dead instrument, not an idle one.
        for row in range(per_layer):
            rate = 0.02 + 0.6 * drives[row]
            if self.alive(0, row, recruited) and rng.random() < rate:
                self._potential[0][row] += 0.7 + 0.5 * rng.random()

        mean_drive = sum(drives) / len(drives)
        fired = 0
        for layer in range(layers):
            for row in range(per_layer):
                if not self.alive(layer, row, recruited):
                    self._potential[layer][row] = 0.0
                    continue
                if self._potential[layer][row] < THRESHOLD or self._since[layer][row] <= REFRACTORY:
                    continue
                self._potential[layer][row] = 0.0
                self._since[layer][row] = 0
                fired += 1
                if layer + 1 >= layers:
                    continue
                fan = 1 + (rng.random() < 0.35 + 0.5 * mean_drive)
                for target in rng.sample(range(per_layer), min(fan, per_layer)):
                    self._spikes.append(
                        _Spike(
                            path=self._path((layer, row), (layer + 1, target), offset),
                            target=(layer + 1, target),
                            weight=0.55 + 0.5 * rng.random(),
                        )
                    )
        self.fired_last = fired

    # ── Drawing ──────────────────────────────────────────────────────────

    def frame(
        self, width: int, height: int, recruited: float = 1.0, heat: str = "accent"
    ) -> List[List[Cell2]]:
        """The field as rows of ``(glyph, role)`` cells, exactly the box."""
        layers, per_layer, offset = self._ensure(width, height)
        rows: List[List[Cell2]] = [[(" ", "dim")] * width for _ in range(height)]
        last_row = 2 * (per_layer - 1)

        def put(x: int, y: int, cell: Cell2) -> None:
            if 0 <= y < height and 0 <= x < width:
                rows[y][x] = cell

        # Wiring.
        for layer in range(layers - 1):
            x0 = self._x(layer, offset)
            bus = x0 + SPACING // 2
            x1 = self._x(layer + 1, offset)
            for row in range(per_layer):
                y = 2 * row
                for x in range(x0 + 1, x1):
                    put(x, y, (WIRE["h"], "dim"))
                if per_layer == 1:
                    kind = "h"
                elif row == 0:
                    kind = "t"
                elif row == per_layer - 1:
                    kind = "b"
                else:
                    kind = "x"
                put(bus, y, (WIRE[kind], "dim"))
            for y in range(1, last_row, 2):
                put(bus, y, (WIRE["v"], "dim"))

        # Pulses in flight: their cell and the one behind it, doubled.
        for spike in self._spikes:
            for back in (0, 1):
                index = spike.position - back
                if 0 <= index < len(spike.path):
                    x, y = spike.path[index]
                    if 0 <= y < height and 0 <= x < width:
                        kind = _WIRE_KIND.get(rows[y][x][0], "h")
                        put(x, y, (LIVE[kind], heat))

        # Neurons, last, over everything.
        for layer in range(layers):
            for row in range(per_layer):
                x, y = self._x(layer, offset), 2 * row
                if not self.alive(layer, row, recruited):
                    put(x, y, (DORMANT, "dim"))
                    continue
                since = self._since[layer][row]
                if since <= 1:
                    put(x, y, (FIRING, heat))
                elif since <= REFRACTORY + 1:
                    put(x, y, (RECOVERING, "dim"))
                elif self._potential[layer][row] >= 0.45:
                    put(x, y, (CHARGED, "primary"))
                else:
                    put(x, y, (RESTING, "secondary"))
        return rows


#: How many GPUs get a field of their own; the rest are a line each.
FIELDED_GPUS = 4


def field_rows(gpu_count: int) -> int:
    """How tall each GPU's field is: roomier for one, tighter for several."""
    return 5 if gpu_count <= 1 else 3


def recruited(memory_fraction: Optional[float]) -> float:
    """The share of a field in play: a quarter, plus memory in use."""
    return 1.0 if memory_fraction is None else 0.25 + 0.75 * memory_fraction


# ── Styles ───────────────────────────────────────────────────────────────


class Style:
    """One way of drawing the machine. Subclasses draw; this keeps the time."""

    name = ""
    title = ""
    blurb = ""
    #: Rows the animation takes, under the shared readouts.
    canvas_rows = 12

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)
        self.clock = 0.0

    def rows(self, reading: Any) -> int:
        return self.canvas_rows

    def step(self, dt: float, reading: Any, history: Sequence[Any]) -> None:
        """Advance the animation by ``dt`` seconds of real time."""
        self.clock += max(0.0, dt)

    def frame(self, width: int, height: int, reading: Any,
              history: Sequence[Any]) -> Frame:  # pragma: no cover - interface
        raise NotImplementedError


# ── NEURAL ───────────────────────────────────────────────────────────────


class NeuralStyle(Style):
    name = "neural"
    title = "Neural"
    blurb = "spiking networks — every core and GPU fires at its load"

    #: The CPU's field: four neurons a layer.
    CPU_ROWS = 7
    #: Ticks a second the networks advance at, whatever the frame rate.
    TICK_HZ = 8.0

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.cpu_net = NeuronNet(seed=seed + 101)
        self.gpu_nets: Dict[Tuple[str, int], NeuronNet] = {}
        self.cpu_drive: List[float] = []
        self.gpu_drive: Dict[Tuple[str, int], float] = {}
        self._width = 30
        #: Network ticks taken so far — counted against the clock rather
        #: than accumulated, so ten steps of a tenth of a second and a
        #: hundred of a hundredth come to the same ticks.
        self.ticked = 0

    def rows(self, reading: Any) -> int:
        gpus = gpus_of(reading)
        each = field_rows(len(gpus))
        return 1 + self.CPU_ROWS + sum(1 + each for _ in gpus[:FIELDED_GPUS])

    def gpu_net(self, key: Tuple[str, int]) -> NeuronNet:
        """The GPU's network, made on first sight. Seeded from the GPU, not
        from ``hash``, so a card draws the same field from one run to the
        next rather than whatever the interpreter's hash seed made of it."""
        net = self.gpu_nets.get(key)
        if net is None:
            vendor, index = key
            seed = sum(ord(ch) for ch in vendor) * 31 + int(index)
            net = self.gpu_nets[key] = NeuronNet(seed=seed)
        return net

    def step(self, dt: float, reading: Any, history: Sequence[Any]) -> None:
        super().step(dt, reading, history)
        cores = cores_of(reading)
        if len(self.cpu_drive) != len(cores):
            self.cpu_drive = list(cores)
        self.cpu_drive = [follow(d, c, dt, 2.0) for d, c in zip(self.cpu_drive, cores)]
        memory = memory_of(reading)
        gpus = gpus_of(reading)
        due = int(self.clock * self.TICK_HZ + 1e-6)
        # A long gap (a monitor back on screen) is not caught up on in a
        # burst: four ticks at most, and the rest let go.
        ticks = min(4, due - self.ticked)
        self.ticked = due
        for _ in range(ticks):
            self.cpu_net.step(self._width, self.CPU_ROWS, self.cpu_drive or [0.0], recruited(memory))
            for gpu in gpus[:FIELDED_GPUS]:
                key = (gpu.vendor, gpu.index)
                target = clamp01(gpu.utilization or 0.0)
                drive = follow(self.gpu_drive.get(key, target), target, 1.0 / self.TICK_HZ, 1.2)
                self.gpu_drive[key] = drive
                self.gpu_net(key).step(
                    self._width, field_rows(len(gpus)), drive, recruited(gpu.memory_fraction)
                )

    def frame(self, width: int, height: int, reading: Any, history: Sequence[Any]) -> Frame:
        self._width = width
        frame: Frame = []
        cores = cores_of(reading)
        caption = blank(width, 1)
        put_text(caption, 0, 0, f"▸ cpu · {len(cores)} core{'s' if len(cores) != 1 else ''} fire at load", "dim")
        frame.extend(caption)
        heat = heat_role(getattr(reading, "cpu_temp", None))
        for row in self.cpu_net.frame(width, self.CPU_ROWS, recruited(memory_of(reading)), heat):
            frame.append([(glyph, role, None) for glyph, role in row])
        gpus = gpus_of(reading)
        each = field_rows(len(gpus))
        for gpu in gpus[:FIELDED_GPUS]:
            caption = blank(width, 1)
            put_text(caption, 0, 0, f"▸ gpu{gpu.index} · fires at use", "dim")
            frame.extend(caption)
            net = self.gpu_net((gpu.vendor, gpu.index))
            for row in net.frame(width, each, recruited(gpu.memory_fraction), heat_role(gpu.temperature)):
                frame.append([(glyph, role, None) for glyph, role in row])
        return fit(frame, width, height)


# ── ORRERY ───────────────────────────────────────────────────────────────


@dataclass
class _Comet:
    radius: float  # in belt radii: 1 is the belt, 0 the sun
    angle: float
    speed: float  # belt radii a second, signed: inbound is negative
    role: str


class OrreryStyle(Style):
    name = "orrery"
    title = "Orrery"
    blurb = "a solar system — cores orbit at their load, memory is the belt"
    canvas_rows = 13

    #: Radians a second a planet covers at full load on the innermost orbit.
    SPEED = 2.6
    #: Afterglow kept per frame: a short tail behind each planet, enough to
    #: show which way and how fast it is going and no more.
    GLOW = 0.58
    #: The most planets drawn; more cores than this share them.
    PLANETS = 15
    #: The most orbits they ride.
    ORBITS = 5
    #: Comets a second at the top of the network's log scale.
    COMET_RATE = 3.5

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.canvas: Optional[BrailleCanvas] = None
        #: Per planet: its load (eased), and where it is round its orbit.
        self.loads: List[float] = []
        self.angles: List[float] = []
        self.giants: Dict[int, float] = {}
        self.moons: Dict[int, float] = {}
        self.comets: List[_Comet] = []
        self.sun = 0.0

    @classmethod
    def orbit_of(cls, planet: int, planets: int) -> int:
        """Which orbit a planet rides: dealt round them, innermost first."""
        return planet % max(1, min(cls.ORBITS, planets))

    def step(self, dt: float, reading: Any, history: Sequence[Any]) -> None:
        super().step(dt, reading, history)
        dt = max(0.0, dt)
        loads = group(cores_of(reading), self.PLANETS)
        if len(loads) != len(self.loads):
            self.loads = list(loads)
            self.angles = [
                2 * math.pi * planet / max(1, len(loads)) + 0.7 * self.orbit_of(planet, len(loads))
                for planet in range(len(loads))
            ]
        self.loads = [follow(a, b, dt, 3.0) for a, b in zip(self.loads, loads)]
        for planet, load in enumerate(self.loads):
            # Inner orbits are quicker, as they are in a real system; on each
            # one the speed is that core's load and nothing else, so an idle
            # core stands still.
            omega = self.SPEED * load / math.sqrt(1.0 + self.orbit_of(planet, len(self.loads)))
            self.angles[planet] = (self.angles[planet] + omega * dt) % (2 * math.pi)
        self.sun = follow(self.sun, clamp01(getattr(reading, "cpu", 0.0) or 0.0), dt, 3.0)
        for gpu in gpus_of(reading)[:2]:
            util = clamp01(gpu.utilization or 0.0)
            start = self.giants.get(gpu.index, math.pi * (0.25 + gpu.index))
            self.giants[gpu.index] = (start + 0.9 * util * dt) % (2 * math.pi)
            self.moons[gpu.index] = (self.moons.get(gpu.index, 0.0) + 5.0 * util * dt) % (2 * math.pi)
        # Comets: arrivals for traffic down, departures for traffic up, as
        # often as the log-scaled rate says — none at all without traffic.
        for rate, inbound in (
            (getattr(reading, "net_rx", None), True),
            (getattr(reading, "net_tx", None), False),
        ):
            expected = self.COMET_RATE * log_rate(rate) * dt
            while expected > 0:
                if self.rng.random() < min(1.0, expected):
                    self.launch(inbound)
                expected -= 1.0
        for comet in self.comets:
            comet.radius += comet.speed * dt
        self.comets = [c for c in self.comets if 0.18 < c.radius < 1.12]

    def launch(self, inbound: bool) -> None:
        speed = 0.9 + 0.5 * self.rng.random()
        self.comets.append(
            _Comet(
                radius=1.08 if inbound else 0.2,
                angle=self.rng.random() * 2 * math.pi,
                speed=-speed if inbound else speed,
                role="accent" if inbound else "success",
            )
        )

    def frame(self, width: int, height: int, reading: Any, history: Sequence[Any]) -> Frame:
        rows = max(1, height - 1)
        if self.canvas is None or not self.canvas.fits(width, rows):
            self.canvas = BrailleCanvas(width, rows)
        canvas = self.canvas
        canvas.fade(self.GLOW)
        cx, cy = (canvas.width - 1) / 2.0, (canvas.height - 1) / 2.0
        rx, ry = max(1.0, cx - 1.0), max(1.0, cy - 0.5)

        # The belt: lit as far round as memory is used, the rest a ghost of
        # the capacity left. Swap is an inner arc, in warning.
        canvas.ellipse(cx, cy, rx, ry, 0.16, "dim", spacing=3.0)
        memory = memory_of(reading)
        if memory:
            canvas.ellipse(cx, cy, rx, ry, 0.62, "secondary", -math.pi / 2, 2 * math.pi * memory, 1.5)
        swap = swap_of(reading)
        if swap:
            canvas.ellipse(cx, cy, rx * 0.94, ry * 0.9, 0.55, "warning", -math.pi / 2, 2 * math.pi * swap, 2.0)
        # Disk I/O: impacts on the belt, as many as the log rate says.
        disk = log_rate(disk_rate(reading))
        for _ in range(int(disk * 6 + (self.rng.random() if disk else 0.0))):
            angle = self.rng.random() * 2 * math.pi
            canvas.plot(cx + rx * math.cos(angle), cy + ry * math.sin(angle), 1.0, "accent")

        # The orbits, faint, and the planets on them.
        planets = len(self.loads)
        orbits = max(1, min(self.ORBITS, planets))
        for orbit in range(orbits if planets else 0):
            scale = 0.3 + 0.52 * (orbit + 1) / orbits
            canvas.ellipse(cx, cy, rx * scale, ry * scale, 0.2, "dim", spacing=4.0)
        for planet, load in enumerate(self.loads):
            orbit = self.orbit_of(planet, planets)
            scale = 0.3 + 0.52 * (orbit + 1) / orbits
            angle = self.angles[planet]
            px, py = cx + rx * scale * math.cos(angle), cy + ry * scale * math.sin(angle)
            canvas.disc(px, py, 1.0, 1.0, level_role(load))

        # GPU giants beyond the planets: moons at their use, a ring as full
        # as their memory, burning their temperature.
        for slot, gpu in enumerate(gpus_of(reading)[:2]):
            angle = self.giants.get(gpu.index, 0.0) + math.pi * slot
            gx, gy = cx + rx * 0.86 * math.cos(angle), cy + ry * 0.86 * math.sin(angle)
            role = heat_role(gpu.temperature)
            canvas.disc(gx, gy, 1.6, 1.0, role)
            vram = gpu.memory_fraction
            if vram:
                canvas.ellipse(gx, gy, 3.4, 2.6, 0.7, role, -math.pi / 2, 2 * math.pi * vram, 1.0)
            moon = self.moons.get(gpu.index, 0.0)
            for offset in (0.0, math.pi):
                canvas.plot(gx + 4.4 * math.cos(moon + offset), gy + 3.4 * math.sin(moon + offset), 1.0, "foreground")

        # Comets, at their place on the way in or out; the glow is the tail.
        for comet in self.comets:
            canvas.plot(cx + rx * comet.radius * math.cos(comet.angle),
                        cy + ry * comet.radius * math.sin(comet.angle), 1.0, comet.role)

        # The sun, last and brightest: as big and as stormy as the CPU.
        radius = 1.4 + 3.0 * self.sun
        canvas.disc(cx, cy, radius, 1.0, "error" if self.sun >= 0.9 else "warning")
        for _ in range(int(8 * self.sun)):
            angle = self.rng.random() * 2 * math.pi
            reach = radius + 1.0 + 2.0 * self.rng.random() * self.sun
            canvas.plot(cx + reach * math.cos(angle), cy + reach * math.sin(angle), 0.8, "warning")

        legend = blank(width, 1)
        x = 0
        for glyph, role, word in (
            ("☼", "warning", "cpu "), ("•", "primary", "core "), ("∙", "secondary", "mem "),
            ("☄", "accent", "net "), ("✦", "accent", "disk"),
        ):
            x = put_text(legend, x, 0, glyph, role)
            x = put_text(legend, x, 0, word, "dim")
        return fit(canvas.render() + legend, width, height)


# ── WATERFALL ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Channel:
    label: str
    width: int
    #: A reading to 0…1; None marks a separator column.
    pick: Optional[Callable[[Any], float]]


def _gpu_value(reading: Any, index: int, what: str) -> float:
    for gpu in gpus_of(reading):
        if gpu.index == index:
            if what == "utilization":
                return clamp01(gpu.utilization or 0.0)
            return gpu.memory_fraction or 0.0
    return 0.0


class _CoreGroups:
    """Each reading's cores folded into ``slots``, worked out once a frame.

    A column per core slot asks every sample in the history for its slot:
    folding a 128-core reading afresh for each of them was most of a frame.
    """

    def __init__(self, slots: int) -> None:
        self.slots = slots
        self._seen: Dict[int, List[float]] = {}

    def value(self, reading: Any, slot: int) -> float:
        loads = self._seen.get(id(reading))
        if loads is None:
            loads = self._seen[id(reading)] = group(cores_of(reading), self.slots)
        return loads[slot] if slot < len(loads) else 0.0


class WaterfallStyle(Style):
    name = "waterfall"
    title = "Waterfall"
    blurb = "a heat-map of every core, memory, network, disk and GPU"
    canvas_rows = 14

    @staticmethod
    def fixed_channels(reading: Any) -> List[Channel]:
        """The columns beside the cores, in the order they are drawn."""
        channels = [Channel("M", 1, lambda r: memory_of(r) or 0.0)]
        if getattr(reading, "swap_total", None):
            channels.append(Channel("S", 1, lambda r: swap_of(r) or 0.0))
        channels += [
            Channel("↓", 1, lambda r: log_rate(getattr(r, "net_rx", None))),
            Channel("↑", 1, lambda r: log_rate(getattr(r, "net_tx", None))),
            Channel("r", 1, lambda r: log_rate(getattr(r, "disk_read", None))),
            Channel("w", 1, lambda r: log_rate(getattr(r, "disk_write", None))),
        ]
        for gpu in gpus_of(reading)[:2]:
            channels.append(Channel("G", 1, lambda r, i=gpu.index: _gpu_value(r, i, "utilization")))
            channels.append(Channel("V", 1, lambda r, i=gpu.index: _gpu_value(r, i, "memory")))
        return channels

    def channels(self, width: int, reading: Any) -> List[Channel]:
        """Every column, fitted to ``width``: the cores, a rule, the rest.

        The cores take what the fixed channels leave, one column each while
        they fit and grouped when they do not; a channel that does not fit at
        all is left off the end rather than squeezed into nothing.
        """
        fixed = self.fixed_channels(reading)
        while fixed and width - len(fixed) - 1 < 1:
            fixed.pop()
        room = max(1, width - len(fixed) - (1 if fixed else 0))
        cores = cores_of(reading)
        slots = max(1, min(len(cores) or 1, room))
        base, spare = divmod(room, slots)
        grouped = len(cores) > slots
        folded = _CoreGroups(slots)
        out: List[Channel] = []
        for slot in range(slots):
            label = "c" if grouped else format(slot % 16, "x")
            out.append(Channel(label, base + (1 if slot < spare else 0),
                               lambda r, s=slot: folded.value(r, s)))
        if fixed:
            out.append(Channel("│", 1, None))
            out.extend(fixed)
        return out

    def frame(self, width: int, height: int, reading: Any, history: Sequence[Any]) -> Frame:
        frame = blank(width, height)
        rows = max(0, height - 2)  # a label row above, a legend below
        samples = list(history)[-(rows * 2):] if history else [reading]
        samples = samples[::-1]  # newest first
        x = 0
        for channel in self.channels(width, reading):
            if channel.pick is None:
                for y in range(height - 1):
                    if x < width:
                        frame[y][x] = ("│", "dim", None)
                x += 1
                continue
            put_text(frame, x, 0, channel.label.center(channel.width)[: channel.width], "dim")
            for row in range(rows):
                upper = samples[2 * row] if 2 * row < len(samples) else None
                lower = samples[2 * row + 1] if 2 * row + 1 < len(samples) else None
                cell = half_cell(
                    heat_index(channel.pick(upper)) if upper is not None else 0,
                    heat_index(channel.pick(lower)) if lower is not None else 0,
                )
                for dx in range(channel.width):
                    if x + dx < width:
                        frame[row + 1][x + dx] = cell
            x += channel.width
        legend = height - 1
        x = put_text(frame, 0, legend, "now↓ ", "dim")
        for role in HEAT:
            x = put_text(frame, x, legend, "█", role)
        put_text(frame, x, legend, f" hot · {rows * SAMPLE_SECONDS * 2:g}s", "dim")
        return frame


def half_cell(top: int, bottom: int) -> Cell:
    """Two samples in one cell: the upper half and the lower, each coloured."""
    if not top and not bottom:
        return BLANK
    if top and not bottom:
        return ("▀", HEAT[top - 1], None)
    if bottom and not top:
        return ("▄", HEAT[bottom - 1], None)
    if top == bottom:
        return ("█", HEAT[top - 1], None)
    return ("▀", HEAT[top - 1], HEAT[bottom - 1])


# ── SCOPE ────────────────────────────────────────────────────────────────


class ScopeStyle(Style):
    name = "scope"
    title = "Scope"
    blurb = "a phosphor oscilloscope — five traces rolling with time"

    #: Afterglow kept per frame.
    GLOW = 0.55
    #: Dots between two samples across the screen.
    PITCH = 2.0

    TRACES = (
        ("c", "cpu", "accent"),
        ("m", "mem", "primary"),
        ("g", "gpu", "warning"),
        ("n", "net", "secondary"),
        ("d", "dsk", "success"),
    )

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.canvas: Optional[BrailleCanvas] = None
        self.since_sample = 0.0
        self._newest_at: Any = None

    def step(self, dt: float, reading: Any, history: Sequence[Any]) -> None:
        super().step(dt, reading, history)
        at = getattr(reading, "at", None)
        if at != self._newest_at:
            self._newest_at = at
            self.since_sample = 0.0
        else:
            self.since_sample = min(SAMPLE_SECONDS, self.since_sample + max(0.0, dt))

    @staticmethod
    def value(reading: Any, key: str) -> Optional[float]:
        """A trace's value in one reading, 0…1, or None where it has none."""
        if reading is None:
            return None
        if key == "c":
            cpu = getattr(reading, "cpu", None)
            return None if cpu is None else clamp01(cpu)
        if key == "m":
            return memory_of(reading)
        if key == "g":
            gpus = gpus_of(reading)
            return clamp01(gpus[0].utilization) if gpus and gpus[0].utilization is not None else None
        if key == "n":
            rate = net_rate(reading)
            return None if rate is None else log_rate(rate)
        if key == "d":
            rate = disk_rate(reading)
            return None if rate is None else log_rate(rate)
        return None

    @staticmethod
    def y_for(value: float, height: int) -> float:
        """Where a value sits, in dots: the bottom line for 0, the top for 1."""
        return (height - 1) - clamp01(value) * (height - 1)

    def frame(self, width: int, height: int, reading: Any, history: Sequence[Any]) -> Frame:
        rows = max(1, height - 1)
        plot_cells = max(2, width - 1)  # the last column carries the labels
        if self.canvas is None or not self.canvas.fits(plot_cells, rows):
            self.canvas = BrailleCanvas(plot_cells, rows)
        canvas = self.canvas
        canvas.fade(self.GLOW)
        w, h = canvas.width, canvas.height
        # The graticule: a dotted line every quarter, a division every ten
        # samples — five seconds — rolling with the traces.
        shift = self.since_sample / SAMPLE_SECONDS * self.PITCH
        for quarter in (0.25, 0.5, 0.75):
            y = self.y_for(quarter, h)
            for x in range(0, w, 4):
                canvas.plot(x, y, 0.16, "dim")
        division = int(10 * self.PITCH)
        for x in range(int(w - 1 - shift), -1, -division):
            for y in range(0, h, 3):
                canvas.plot(x, y, 0.16, "dim")
        samples = list(history)[-int(w / self.PITCH + 2):] if history else [reading]
        labels: Dict[int, Cell] = {}
        for key, _name, role in self.TRACES:
            points: List[Optional[Tuple[float, float]]] = []
            for age, sample in enumerate(reversed(samples)):
                value = self.value(sample, key)
                x = (w - 1) - age * self.PITCH - shift
                points.append(None if value is None else (x, self.y_for(value, h)))
            previous = None
            for point in points:
                if point is not None and previous is not None:
                    canvas.line(previous[0], previous[1], point[0], point[1], 1.0, role)
                previous = point
            head = points[0] if points else None
            if head is not None:
                canvas.disc(head[0], head[1], 1.0, 1.0, role)
                labels.setdefault(int(head[1] // 4), (key, role, None))
        frame = canvas.render()
        for row_index, row in enumerate(frame):
            row.append(labels.get(row_index, BLANK))
        # The key: each trace's letter, as it is printed at the trace's head
        # down the right-hand edge, in the trace's colour.
        legend = blank(width, 1)
        x = 0
        for key, name, role in self.TRACES:
            x = put_text(legend, x, 0, key, role)
            x = put_text(legend, x, 0, f"·{name} ", "dim")
        return fit(frame + legend, width, height)


# ── RAIN ─────────────────────────────────────────────────────────────────


@dataclass
class _Drop:
    y: float
    speed: float
    length: int
    seed: int


class RainStyle(Style):
    name = "rain"
    title = "Rain"
    blurb = "digital rain — each core rains as hard as it is loaded"

    TRAIL = "0123456789ABCDEF∙•◘○◙♦"

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.columns: Dict[int, List[_Drop]] = {}
        self.splash: Dict[int, float] = {}
        self.dust: List[List[float]] = []
        self.bolt: Optional[Tuple[int, float, int]] = None
        self.net_mean = 0.0
        self.width = 30
        self.height = self.canvas_rows

    @staticmethod
    def core_for(column: int, width: int, cores: int) -> int:
        """Which core a column rains for: the width dealt out in order."""
        return min(cores - 1, column * cores // max(1, width)) if cores else 0

    def step(self, dt: float, reading: Any, history: Sequence[Any]) -> None:
        super().step(dt, reading, history)
        dt = max(0.0, dt)
        cores = cores_of(reading)
        rain_rows = max(1, self.height - 1)
        for column in range(self.width):
            load = cores[self.core_for(column, self.width, len(cores))] if cores else 0.0
            drops = self.columns.setdefault(column, [])
            # As often and as fast as the core is loaded: an idle core is dry.
            if load > 0.0 and self.rng.random() < 3.2 * (load ** 1.3) * dt:
                drops.append(_Drop(-1.0, 4.0 + 16.0 * load, 2 + int(5 * load),
                                   self.rng.randrange(1 << 16)))
            for drop in drops:
                previous = drop.y
                drop.y += drop.speed * dt
                if previous < rain_rows <= drop.y:
                    self.splash[column] = 0.25
            self.columns[column] = [d for d in drops if d.y - d.length < rain_rows]
        for column in list(self.splash):
            self.splash[column] -= dt
            if self.splash[column] <= 0:
                del self.splash[column]
        # Lightning: traffic well above its own recent mean.
        net = net_rate(reading) or 0.0
        if self.bolt is not None:
            x, left, length = self.bolt
            self.bolt = (x, left - dt, length) if left - dt > 0 else None
        elif net > 256 * 1024 and net > 3.0 * max(self.net_mean, 1.0) and self.rng.random() < 0.6:
            self.bolt = (self.rng.randrange(max(1, self.width)), 0.18, 3 + int(6 * log_rate(net)))
        self.net_mean = follow(self.net_mean, net, dt, 0.4)
        # Dust off the pool, as thick as the disk is busy.
        disk = log_rate(disk_rate(reading))
        if disk and self.rng.random() < 6.0 * disk * dt:
            self.dust.append([float(self.rng.randrange(max(1, self.width))), float(rain_rows - 1), 0.0])
        for mote in self.dust:
            mote[1] -= 2.5 * dt
            mote[2] += dt
        self.dust = [m for m in self.dust if m[1] > 0 and m[2] < 2.0]

    def frame(self, width: int, height: int, reading: Any, history: Sequence[Any]) -> Frame:
        self.width, self.height = width, height
        frame = blank(width, height)
        cores = cores_of(reading)
        rain_rows = max(1, height - 1)
        for mote in self.dust:
            x, y = int(mote[0]), int(mote[1])
            if 0 <= x < width and 0 <= y < rain_rows:
                frame[y][x] = ("·", "dim", None)
        for column, drops in self.columns.items():
            if column >= width:
                continue
            load = cores[self.core_for(column, width, len(cores))] if cores else 0.0
            digit = str(min(9, int(load * 10)))
            for drop in drops:
                head = int(drop.y)
                for back in range(drop.length + 1):
                    y = head - back
                    if not 0 <= y < rain_rows:
                        continue
                    if back == 0:
                        frame[y][column] = (digit, "accent", None)
                        continue
                    glyph = self.TRAIL[(drop.seed + y * 7 + column) % len(self.TRAIL)]
                    role = "primary" if back <= drop.length // 3 else (
                        "secondary" if back <= 2 * drop.length // 3 else "dim")
                    frame[y][column] = (glyph, role, None)
        if self.bolt is not None:
            x, _left, length = self.bolt
            for y in range(min(length, rain_rows)):
                frame[y][max(0, min(width - 1, x))] = ("╱" if y % 2 else "╲", "warning", None)
                x += 1 if y % 2 else -1
        # The pool: as far across as memory is used.
        memory = memory_of(reading) or 0.0
        filled = int(round(memory * width))
        pool = height - 1
        for x in range(width):
            cell: Cell = ("▄", level_role(memory), None) if x < filled else ("_", "dim", None)
            if x in self.splash:
                cell = ("∴" if x < filled else "·", "accent", None)
            frame[pool][x] = cell
        return frame


# ── TIDE ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tank:
    label: str
    level: float
    churn: float  # 0…1: how fast the bubbles rise
    role: str


class TideStyle(Style):
    name = "tide"
    title = "Tide"
    blurb = "tanks filled to the level — waves show change, bubbles work"
    canvas_rows = 11

    def __init__(self, seed: int = 0) -> None:
        super().__init__(seed)
        self.levels: Dict[str, float] = {}
        self.swell: Dict[str, float] = {}
        self.bubbles: Dict[str, List[List[float]]] = {}
        self.flow_rx = 0.0
        self.flow_tx = 0.0

    @staticmethod
    def tanks(reading: Any) -> List[Tank]:
        cpu = clamp01(getattr(reading, "cpu", 0.0) or 0.0)
        memory = memory_of(reading) or 0.0
        disk = log_rate(disk_rate(reading))
        out = [
            Tank("CPU", cpu, cpu, level_role(cpu)),
            Tank("MEM", memory, disk, level_role(memory)),
        ]
        if getattr(reading, "swap_total", None):
            swap = swap_of(reading) or 0.0
            out.append(Tank("SWP", swap, swap * 0.5, level_role(swap)))
        for gpu in gpus_of(reading)[:2]:
            util = clamp01(gpu.utilization or 0.0)
            vram = gpu.memory_fraction or 0.0
            out.append(Tank(f"G{gpu.index}", util, util, heat_role(gpu.temperature)))
            out.append(Tank(f"V{gpu.index}", vram, util * 0.6, level_role(vram)))
        return out

    def step(self, dt: float, reading: Any, history: Sequence[Any]) -> None:
        super().step(dt, reading, history)
        dt = max(0.0, dt)
        for tank in self.tanks(reading):
            previous = self.levels.get(tank.label, tank.level)
            level = follow(previous, tank.level, dt, 3.0)
            self.levels[tank.label] = level
            # The swell is how fast the level is moving, settling as it stops.
            speed = abs(level - previous) / max(dt, 1e-3)
            self.swell[tank.label] = follow(self.swell.get(tank.label, 0.0), min(1.0, speed * 1.5), dt, 1.5)
            bubbles = self.bubbles.setdefault(tank.label, [])
            if tank.churn and self.rng.random() < 5.0 * tank.churn * dt:
                bubbles.append([self.rng.random(), 0.0, 1.0 + 3.0 * tank.churn])
            for bubble in bubbles:
                bubble[1] += bubble[2] * dt / 4.0
            self.bubbles[tank.label] = [b for b in bubbles if b[1] < level]
        self.flow_rx = (self.flow_rx + (2.0 + 14.0 * log_rate(getattr(reading, "net_rx", None))) * dt) % 1000
        self.flow_tx = (self.flow_tx + (2.0 + 14.0 * log_rate(getattr(reading, "net_tx", None))) * dt) % 1000

    def frame(self, width: int, height: int, reading: Any, history: Sequence[Any]) -> Frame:
        frame = blank(width, height)
        tanks = self.tanks(reading)
        # The pipe: traffic in flows left, traffic out flows right, as dense
        # as its rate.
        rx, tx = log_rate(getattr(reading, "net_rx", None)), log_rate(getattr(reading, "net_tx", None))
        for x in range(width):
            frame[0][x] = ("═", "dim", None)
            if rx and (x + int(self.flow_rx)) % max(2, int(8 - 6 * rx)) == 0:
                frame[0][x] = ("‹", "accent", None)
            if tx and (x - int(self.flow_tx)) % max(2, int(8 - 6 * tx)) == 0:
                frame[0][x] = ("›", "success", None)
        inner = max(1, height - 3)  # the pipe above; the level and the name below
        slot = max(4, width // max(1, len(tanks)))
        shown = tanks[: max(1, width // slot)]
        for index, tank in enumerate(shown):
            wall_l = index * slot
            wall_r = wall_l + slot - 2
            level = self.levels.get(tank.label, tank.level)
            swell = self.swell.get(tank.label, 0.0)
            for row in range(inner):
                put_text(frame, wall_l, 1 + row, "▕", "dim")
                put_text(frame, wall_r, 1 + row, "▏", "dim")
            for x in range(wall_l + 1, wall_r):
                phase = self.clock * 3.0 + (x - wall_l) * 1.1
                wave = (0.12 + 0.9 * swell) * math.sin(phase) if level > 0 else 0.0
                surface = max(0.0, min(float(inner), level * inner + wave))
                full = int(surface)
                part = surface - full
                for row in range(inner):
                    y = inner - row  # the tank's bottom row is y = inner
                    if row < full:
                        frame[y][x] = ("█", tank.role, None)
                    elif row == full and part > 0.06:
                        frame[y][x] = (EIGHTHS[max(1, int(part * 8))], tank.role, None)
            for bubble in self.bubbles.get(tank.label, []):
                bx = wall_l + 1 + int(bubble[0] * max(1, wall_r - wall_l - 1))
                by = inner - int(bubble[1] * inner)
                if wall_l < bx < wall_r and 1 <= by <= inner:
                    frame[by][bx] = ("°" if bubble[2] < 2.5 else "o", "foreground", None)
            # The level as a figure, and the tank's name, each on its own row:
            # run together in a narrow tank, "G0" at 80% cropped to "G08".
            room = max(1, slot - 1)
            figure = str(int(round(tank.level * 100)))
            put_text(frame, wall_l, height - 2, figure.center(room)[:room],
                     tank.role if tank.level >= 0.75 else "foreground")
            put_text(frame, wall_l, height - 1, tank.label.center(room)[:room], "dim")
        return frame


# ── The catalogue ────────────────────────────────────────────────────────


def fit(frame: Frame, width: int, height: int) -> Frame:
    """Exactly ``width`` × ``height``: padded with blanks, or cropped."""
    out: Frame = []
    for row in frame[:height]:
        row = list(row[:width])
        if len(row) < width:
            row.extend([BLANK] * (width - len(row)))
        out.append(row)
    while len(out) < height:
        out.append([BLANK] * width)
    return out


STYLES: Dict[str, type] = {
    style.name: style
    for style in (NeuralStyle, OrreryStyle, WaterfallStyle, ScopeStyle, RainStyle, TideStyle)
}
STYLE_NAMES = tuple(STYLES)
DEFAULT_STYLE = "neural"


def normalise_style(name: Any) -> str:
    """A known style's name, or the default for anything else."""
    text = str(name or "").strip().lower()
    return text if text in STYLES else DEFAULT_STYLE


def make_style(name: Optional[str], seed: int = 0) -> Style:
    """A fresh instance of the style ``name`` (the default if unknown)."""
    return STYLES[normalise_style(name)](seed)


__all__ = [
    "BLANK",
    "BrailleCanvas",
    "DEFAULT_STYLE",
    "EIGHTHS",
    "FIELDED_GPUS",
    "HEAT",
    "NeuronNet",
    "SAMPLE_SECONDS",
    "STYLES",
    "STYLE_NAMES",
    "Style",
    "clamp01",
    "field_rows",
    "fit",
    "fraction",
    "group",
    "heat_role",
    "log_rate",
    "make_style",
    "normalise_style",
    "recruited",
]
