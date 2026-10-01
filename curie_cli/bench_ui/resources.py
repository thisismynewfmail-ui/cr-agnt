"""The resource monitor: CPU, memory, and every GPU drawn as a field of neurons.

Sits under the elapsed tape in the instrument stack (F8), below a rule, and
reads the machine rather than the turn — which is the other half of "what is
the console doing": a turn that has gone quiet may be waiting on a provider,
or may be waiting on a local model that has the GPU pinned at a hundred
percent.

**The GPUs are drawn as neurons.** Each GPU gets a small spiking network:
layers of neurons wired through crossbar buses, in the box-drawing set the
rest of the console is drawn in. The GPU's readings drive it —

* **utilisation** is the input layer's firing rate. An idle GPU throws the
  odd spark; a busy one sends a steady stream of spikes down the wires, and
  at full load the whole field is alight;
* **memory in use** is how much of the network is recruited. Neurons beyond
  it are dormant (``·``) and pass nothing on, so a GPU with its memory full
  is a field with every neuron in play;
* **temperature** is the colour the spikes burn: the accent while the card
  is cool, the warning colour past 70 °C, the error colour past 85 °C.

The numbers are printed beside each field as well, because a picture is how
a reading is *noticed* and a number is how it is *read*. Nothing is invented:
a GPU that reports no utilisation is drawn dormant and says so, and a machine
with no GPU any tool here can see says that instead of drawing an idle one.

**Sampling is on demand.** A background thread takes the readings — a GPU
query is a subprocess, which has no business on the UI thread — and it only
takes them while the monitor is actually on screen: hidden by F8, collapsed,
or squeezed out by a narrow window, it costs nothing.
"""

from __future__ import annotations

import glob
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from rich.text import Text
from textual import events
from textual.widget import Widget

from curie_cli.bench_ui.indicators import (
    Cell,
    Frame,
    IndicatorKit,
    palette_colour,
    widget_optics,
)

# ── Readings ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GpuReading:
    """One GPU, as its driver reports it. ``None`` is "not reported"."""

    index: int
    name: str
    vendor: str
    utilization: Optional[float] = None  # 0…1
    memory_used: Optional[float] = None  # bytes
    memory_total: Optional[float] = None  # bytes
    temperature: Optional[float] = None  # °C

    @property
    def memory_fraction(self) -> Optional[float]:
        if not self.memory_total or self.memory_used is None:
            return None
        return max(0.0, min(1.0, self.memory_used / self.memory_total))


@dataclass(frozen=True)
class Reading:
    """The machine, at one moment."""

    cpu: Optional[float] = None  # 0…1
    cpu_count: int = 0
    memory_used: Optional[float] = None  # bytes
    memory_total: Optional[float] = None  # bytes
    gpus: Tuple[GpuReading, ...] = ()
    #: Why there are no GPUs, when there are none.
    gpu_note: str = ""
    at: float = field(default_factory=time.monotonic)


def _number(text: str) -> Optional[float]:
    """A number out of a driver's text, or None for N/A and its relatives."""
    text = str(text or "").strip().strip("[]")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# ── GPU probes ───────────────────────────────────────────────────────────
#
# Each is total — any failure is "no GPUs from this source" — and each is
# tried on its own, so a machine with an NVIDIA card and an AMD iGPU shows
# both. None of them needs root.

_MIB = 1024 * 1024

#: The fields asked of nvidia-smi, in the order its CSV answers in.
NVIDIA_SMI_QUERY = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu"


def parse_nvidia_smi(output: str) -> List[GpuReading]:
    """``nvidia-smi --query-gpu=… --format=csv,noheader,nounits`` → readings."""
    gpus: List[GpuReading] = []
    for line in (output or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        index = _number(parts[0])
        util, used, total, temp = (_number(p) for p in parts[2:6])
        gpus.append(
            GpuReading(
                index=int(index) if index is not None else len(gpus),
                name=parts[1] or "NVIDIA GPU",
                vendor="nvidia",
                utilization=None if util is None else max(0.0, min(1.0, util / 100.0)),
                memory_used=None if used is None else used * _MIB,
                memory_total=None if total is None else total * _MIB,
                temperature=temp,
            )
        )
    return gpus


#: NVML's state in this process: ``None`` untried, then whether it came up.
#: ``nvmlInit`` is reference-counted and loads the driver library, so it is
#: called once rather than on every sample — and a machine where it failed
#: (``pynvml`` installed, no NVIDIA driver) is not asked again every two
#: seconds; ``nvidia-smi`` is the fallback there.
_nvml_ready: Optional[bool] = None


def _nvml_gpus() -> Optional[List[GpuReading]]:
    """NVIDIA through NVML, when ``pynvml`` is installed. None if it is not."""
    global _nvml_ready
    if _nvml_ready is False:
        return None
    try:
        import pynvml  # type: ignore[import-not-found]
    except Exception:
        _nvml_ready = False
        return None
    if _nvml_ready is None:
        try:
            pynvml.nvmlInit()
        except Exception:
            _nvml_ready = False
            return None
        _nvml_ready = True
    try:
        gpus = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            reading = {"utilization": None, "memory_used": None,
                       "memory_total": None, "temperature": None}
            try:
                reading["utilization"] = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu / 100.0
            except Exception:
                pass
            try:
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                reading["memory_used"], reading["memory_total"] = float(memory.used), float(memory.total)
            except Exception:
                pass
            try:
                reading["temperature"] = float(
                    pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                )
            except Exception:
                pass
            gpus.append(GpuReading(index=index, name=str(name), vendor="nvidia", **reading))
        return gpus
    except Exception:
        return None


def _nvidia_smi_gpus(timeout: float = 3.0) -> List[GpuReading]:
    binary = shutil.which("nvidia-smi")
    if binary is None:
        return []
    try:
        result = subprocess.run(
            [binary, f"--query-gpu={NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, **_quiet_spawn(),
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return parse_nvidia_smi(result.stdout)


def read_amdgpu_sysfs(root: str = "/sys/class/drm") -> List[GpuReading]:
    """AMD GPUs through the amdgpu driver's own files — no tool, no root."""
    gpus: List[GpuReading] = []
    for busy in sorted(glob.glob(os.path.join(root, "card[0-9]*", "device", "gpu_busy_percent"))):
        device = os.path.dirname(busy)
        card = os.path.basename(os.path.dirname(device))
        if not re.fullmatch(r"card\d+", card):
            continue
        util = _number(_read(busy))
        used = _number(_read(os.path.join(device, "mem_info_vram_used")))
        total = _number(_read(os.path.join(device, "mem_info_vram_total")))
        temp = None
        for sensor in sorted(glob.glob(os.path.join(device, "hwmon", "hwmon*", "temp1_input"))):
            milli = _number(_read(sensor))
            if milli is not None:
                temp = milli / 1000.0
                break
        name = _read(os.path.join(device, "product_name")).strip() or "AMD Radeon"
        gpus.append(
            GpuReading(
                index=int(card[4:]),
                name=name,
                vendor="amd",
                utilization=None if util is None else max(0.0, min(1.0, util / 100.0)),
                memory_used=used,
                memory_total=total,
                temperature=temp,
            )
        )
    return gpus


def parse_ioreg_accelerator(output: str, memory_total: Optional[float] = None) -> List[GpuReading]:
    """Apple silicon's GPU, out of ``ioreg -r -d 1 -c IOAccelerator``.

    The GPU shares the machine's memory, so its total is the machine's.
    """
    gpus: List[GpuReading] = []
    blocks = re.split(r"\n(?=\+-o )", output or "")
    for block in blocks:
        util = re.search(r'"Device Utilization %"\s*=\s*(\d+)', block)
        if util is None:
            continue
        used = re.search(r'"In use system memory"\s*=\s*(\d+)', block)
        model = re.search(r'"model"\s*=\s*"([^"]+)"', block)
        gpus.append(
            GpuReading(
                index=len(gpus),
                name=model.group(1) if model else "Apple GPU",
                vendor="apple",
                utilization=max(0.0, min(1.0, int(util.group(1)) / 100.0)),
                memory_used=float(used.group(1)) if used else None,
                memory_total=memory_total if used else None,
            )
        )
    return gpus


def _apple_gpus(memory_total: Optional[float], timeout: float = 3.0) -> List[GpuReading]:
    if sys.platform != "darwin" or shutil.which("ioreg") is None:
        return []
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except Exception:
        return []
    return parse_ioreg_accelerator(result.stdout, memory_total)


def probe_gpus(memory_total: Optional[float] = None) -> Tuple[List[GpuReading], str]:
    """Every GPU any source here can see, and why there are none if none."""
    nvidia = _nvml_gpus()
    if nvidia is None:
        nvidia = _nvidia_smi_gpus()
    gpus = list(nvidia) + read_amdgpu_sysfs() + _apple_gpus(memory_total)
    if gpus:
        return gpus, ""
    return [], "no GPU found"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except Exception:
        return ""


def _quiet_spawn() -> dict:
    """No console window flashing up on Windows for each GPU query."""
    try:
        from curie_cli._subprocess_compat import windows_hide_flags

        flags = windows_hide_flags()
    except Exception:
        flags = 0
    return {"creationflags": flags} if flags else {}


def sample_system() -> Reading:
    """CPU and memory, through psutil. GPUs are added by the sampler."""
    try:
        import psutil
    except Exception:
        return Reading()
    try:
        cpu = psutil.cpu_percent(interval=None) / 100.0
    except Exception:
        cpu = None
    try:
        memory = psutil.virtual_memory()
        # Used as "not available": the figure that means "how close to
        # running out", which psutil's own ``used`` is not on every platform.
        total = float(memory.total)
        used = total - float(memory.available)
    except Exception:
        total = used = None
    return Reading(
        cpu=cpu,
        cpu_count=int(psutil.cpu_count() or 0),
        memory_used=used,
        memory_total=total,
    )


class ResourceSampler:
    """Takes readings on its own thread, only while someone is looking.

    :meth:`want` is called by the monitor on every frame it draws; the thread
    samples while the last call was recent and idles otherwise. A monitor
    that is hidden draws no frames, so it costs no samples.
    """

    #: How long a ``want`` keeps the sampler busy.
    WANT_SECONDS = 3.0

    def __init__(
        self,
        interval: float = 1.0,
        gpu_interval: float = 2.0,
        system: Optional[Callable[[], Reading]] = None,
        gpus: Optional[Callable[[Optional[float]], Tuple[List[GpuReading], str]]] = None,
    ) -> None:
        self.interval = interval
        self.gpu_interval = gpu_interval
        # Looked up when the sampler is made rather than when this module
        # was, so a stand-in installed on the module reaches every monitor.
        self._system = system if system is not None else sample_system
        self._gpus = gpus if gpus is not None else probe_gpus
        self._lock = threading.Lock()
        self._latest: Optional[Reading] = None
        self._wanted_until = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def want(self) -> None:
        self._wanted_until = time.monotonic() + self.WANT_SECONDS
        if self._thread is None or not self._thread.is_alive():
            if self._stop.is_set():
                return
            self._thread = threading.Thread(
                target=self._run, name="bench-ui-resources", daemon=True
            )
            self._thread.start()

    def latest(self) -> Optional[Reading]:
        with self._lock:
            return self._latest

    def stop(self) -> None:
        self._stop.set()

    def sample_once(self, with_gpus: bool = True, gpus=None, note: str = "") -> Reading:
        """One reading, taken now. What the thread runs; callable directly."""
        system = self._system()
        if with_gpus:
            found, note = self._gpus(system.memory_total)
            gpus = tuple(found)
        reading = Reading(
            cpu=system.cpu,
            cpu_count=system.cpu_count,
            memory_used=system.memory_used,
            memory_total=system.memory_total,
            gpus=tuple(gpus or ()),
            gpu_note=note,
        )
        with self._lock:
            self._latest = reading
        return reading

    def _run(self) -> None:
        next_gpus = 0.0
        gpus: tuple = ()
        note = ""
        while not self._stop.is_set():
            now = time.monotonic()
            if now <= self._wanted_until:
                try:
                    due = now >= next_gpus
                    reading = self.sample_once(with_gpus=due, gpus=gpus, note=note)
                    if due:
                        gpus, note = reading.gpus, reading.gpu_note
                        next_gpus = now + self.gpu_interval
                except Exception:
                    pass
            self._stop.wait(self.interval)


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


def heat_role(temperature: Optional[float]) -> str:
    """The palette role a GPU's spikes burn in, from its temperature."""
    if temperature is None or temperature < 70:
        return "accent"
    if temperature < 85:
        return "warning"
    return "error"


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

    Integrate-and-fire, kept as simple as reads well at eight frames a
    second: input arrives at the first layer at a rate set by the GPU's
    utilisation, potential leaks away between arrivals, a neuron over
    threshold fires and rests, and each firing sends a pulse along the wire
    to one or two neurons of the next layer. Seeded, so a test can hold it to
    a figure.
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

    def step(self, width: int, height: int, activity: float, recruited: float = 1.0) -> None:
        """Advance one tick at ``activity`` (0…1) with ``recruited`` alive."""
        layers, per_layer, offset = self._ensure(width, height)
        activity = max(0.0, min(1.0, activity))
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

        # The input. An idle GPU still throws the odd spark — a field that
        # is perfectly still reads as a dead instrument, not an idle one.
        rate = 0.02 + 0.6 * activity
        for row in range(per_layer):
            if self.alive(0, row, recruited) and rng.random() < rate:
                self._potential[0][row] += 0.7 + 0.5 * rng.random()

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
                fan = 1 + (rng.random() < 0.35 + 0.5 * activity)
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
    ) -> Frame:
        """The field as rows of ``(glyph, role)`` cells, exactly the box."""
        layers, per_layer, offset = self._ensure(width, height)
        rows: Frame = [[(" ", "dim")] * width for _ in range(height)]
        last_row = 2 * (per_layer - 1)

        def put(x: int, y: int, cell: Cell) -> None:
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


# ── The instrument ───────────────────────────────────────────────────────

#: Frames a second the neuron fields are drawn at.
FIELD_HZ = 8.0

#: How fast a field's drive follows its GPU's reading, per frame. Readings
#: arrive every two seconds; a field that jumped to each would lurch.
DRIVE_FOLLOW = 0.15

#: How many GPUs get a field of their own; the rest are a line each.
FIELDED_GPUS = 4

#: The glass the optics are applied through — a kit is what knows how.
_GLASS = IndicatorKit()


def field_rows(gpu_count: int) -> int:
    """How tall each GPU's field is: roomier for one, tighter for several."""
    return 5 if gpu_count <= 1 else 3


def gigabytes(value: Optional[float]) -> str:
    if value is None:
        return "?"
    figure = value / (1024 ** 3)
    # Tenths only where they are worth a column: 6.1 of 8 says something
    # that 6 of 8 does not, 20.4 of 24 says nothing that 20 of 24 does not.
    return f"{figure:.0f}" if figure >= 10 else f"{figure:.1f}"


class ResourceMonitor(Widget):
    """The instrument: a title, CPU and memory bars, and a field per GPU.

    The title is also its switch: click it to fold the monitor to that one
    line (and stop it sampling), click again to open it. The same switch is
    on the PANEL pane, under METERS, and on Shift+F8.
    """

    DEFAULT_CSS = """
    ResourceMonitor {
        height: auto;
    }
    """

    def __init__(self, sampler: Optional[ResourceSampler] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sampler = sampler if sampler is not None else ResourceSampler()
        self._reading: Optional[Reading] = None
        self._nets: Dict[Tuple[str, int], NeuronNet] = {}
        self._drive: Dict[Tuple[str, int], float] = {}
        self._expanded = True
        self._tick = 0

    # ── State ────────────────────────────────────────────────────────────

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def sampler(self) -> ResourceSampler:
        return self._sampler

    @property
    def reading(self) -> Optional[Reading]:
        return self._reading

    def set_expanded(self, expanded: bool) -> None:
        if bool(expanded) == self._expanded:
            return
        self._expanded = bool(expanded)
        self.refresh(layout=True)

    def set_reading(self, reading: Optional[Reading]) -> None:
        """Adopt a reading — the sampler's, or a test's."""
        layout = self._row_count(self._reading) != self._row_count(reading)
        self._reading = reading
        self.refresh(layout=layout)

    # ── Clock ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.set_interval(1.0 / FIELD_HZ, self._advance)

    def on_unmount(self) -> None:
        self._sampler.stop()

    def _showing(self) -> bool:
        """Drawn in the last screen update — not hidden by F8 or a narrow window."""
        size = self.size
        return size.width > 0 and size.height > 0 and self.is_on_screen

    def _advance(self) -> None:
        if not self._expanded or not self._showing():
            return
        self._sampler.want()
        latest = self._sampler.latest()
        if latest is not None and latest is not self._reading:
            self.set_reading(latest)
        self._tick += 1
        self.step_fields()
        self.refresh()

    def step_fields(self) -> None:
        """One frame of every GPU's network, driven by its last reading."""
        reading = self._reading
        if reading is None:
            return
        width = max(8, (self.size.width or 30))
        rows = field_rows(len(reading.gpus))
        for gpu in reading.gpus[:FIELDED_GPUS]:
            key = (gpu.vendor, gpu.index)
            net = self._net(key)
            target = gpu.utilization or 0.0
            drive = self._drive.get(key, target)
            drive += (target - drive) * DRIVE_FOLLOW
            self._drive[key] = drive
            net.step(width, rows, drive, self._recruited(gpu))

    def _net(self, key: Tuple[str, int]) -> NeuronNet:
        """The GPU's network, made on first sight. Seeded from the GPU, not
        from ``hash``, so a card draws the same field from one run to the
        next rather than whatever the interpreter's hash seed made of it."""
        net = self._nets.get(key)
        if net is None:
            vendor, index = key
            seed = sum(ord(ch) for ch in vendor) * 31 + int(index)
            net = self._nets[key] = NeuronNet(seed=seed)
        return net

    @staticmethod
    def _recruited(gpu: GpuReading) -> float:
        """The share of the field in play: a quarter, plus memory in use."""
        fraction = gpu.memory_fraction
        return 1.0 if fraction is None else 0.25 + 0.75 * fraction

    # ── Size ─────────────────────────────────────────────────────────────

    def _row_count(self, reading: Optional[Reading]) -> int:
        if not self._expanded:
            return 1
        rows = 3  # title, CPU, memory
        if reading is None:
            return rows + 1
        if not reading.gpus:
            return rows + 1
        each = field_rows(len(reading.gpus))
        for index, _gpu in enumerate(reading.gpus):
            rows += 1 + (each if index < FIELDED_GPUS else 0)
        return rows

    def get_content_height(self, container, viewport, width: int) -> int:
        return self._row_count(self._reading)

    # ── Drawing ──────────────────────────────────────────────────────────

    def render(self) -> Text:
        width = max(8, self.size.width or 30)
        out = Text(no_wrap=True, overflow="crop")
        out.append_text(self._title(width))
        if not self._expanded:
            return out
        reading = self._reading
        dim = palette_colour(self, "dim")
        out.append("\n")
        if reading is None:
            out.append_text(self._line("CPU", "…", width))
            out.append("\n")
            out.append_text(self._line("MEM", "…", width))
            out.append("\n")
            out.append(" reading the machine …", style=dim)
            return out
        cpu = _percent(reading.cpu)
        memory = f"{gigabytes(reading.memory_used)}/{gigabytes(reading.memory_total)}G"
        # Both figures right-aligned in one column, so the two bars start and
        # end together and read as a pair rather than as two ragged rows.
        figures = max(len(cpu), len(memory))
        out.append_text(self._bar("CPU", reading.cpu, cpu.rjust(figures), width))
        out.append("\n")
        out.append_text(
            self._bar(
                "MEM",
                None if not reading.memory_total else (reading.memory_used or 0) / reading.memory_total,
                memory.rjust(figures),
                width,
            )
        )
        if not reading.gpus:
            out.append("\n")
            out.append("GPU ", style=f"bold {palette_colour(self, 'foreground')}")
            out.append(reading.gpu_note or "none found", style=dim)
            return out
        each = field_rows(len(reading.gpus))
        _GLASS.optics = widget_optics(self)
        for index, gpu in enumerate(reading.gpus):
            out.append("\n")
            out.append_text(self._gpu_caption(gpu, width))
            if index >= FIELDED_GPUS:
                continue
            net = self._net((gpu.vendor, gpu.index))
            frame = net.frame(width, each, self._recruited(gpu), heat_role(gpu.temperature))
            frame = _GLASS.apply_optics(frame, self._tick)
            for row in frame:
                out.append("\n")
                out.append_text(self._paint_row(row))
        return out

    def _title(self, width: int) -> Text:
        marker = "▾" if self._expanded else "▸"
        label = f"RESOURCES {marker}"
        optics = widget_optics(self)
        title = Text(no_wrap=True, overflow="crop")
        if optics.get("mode") == "dos":
            ink, band = palette_colour(self, "ink"), palette_colour(self, "band")
            title.append(label.ljust(width), style=f"bold {ink} on {band}")
        else:
            title.append(label, style=f"bold {palette_colour(self, 'secondary')}")
        return title

    def _line(self, label: str, value: str, width: int) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        line.append(f"{label} ", style=f"bold {palette_colour(self, 'foreground')}")
        line.append(value, style=palette_colour(self, "dim"))
        return line

    def _bar(self, label: str, fraction: Optional[float], value: str, width: int) -> Text:
        """``CPU ▕████░░░░▏ 37%`` — the tape's own ramp, in a row."""
        line = Text(no_wrap=True, overflow="crop")
        line.append(f"{label} ", style=f"bold {palette_colour(self, 'foreground')}")
        room = max(1, width - len(label) - 1 - len(value) - 2)
        if fraction is None:
            line.append("░" * room, style=palette_colour(self, "dim"))
        else:
            fraction = max(0.0, min(1.0, fraction))
            role = "error" if fraction >= 0.9 else "warning" if fraction >= 0.75 else "primary"
            filled = int(round(fraction * room))
            line.append("█" * filled, style=palette_colour(self, role))
            line.append("░" * (room - filled), style=palette_colour(self, "dim"))
        line.append(f"  {value}", style=palette_colour(self, "foreground"))
        return line

    def _gpu_caption(self, gpu: GpuReading, width: int) -> Text:
        """``GPU0 RTX 4090     37% 61° 6.1/24G`` — the numbers beside the field."""
        figures = [
            _percent(gpu.utilization) if gpu.utilization is not None else "—%",
        ]
        if gpu.temperature is not None:
            figures.append(f"{gpu.temperature:.0f}°")
        if gpu.memory_total:
            figures.append(f"{gigabytes(gpu.memory_used)}/{gigabytes(gpu.memory_total)}G")
        readout = " ".join(figures)
        label = f"GPU{gpu.index} "
        name = _short_name(gpu.name)
        room = max(0, width - len(label) - len(readout) - 1)
        if len(name) > room:
            name = name[: max(0, room - 1)].rstrip() + ("…" if room > 1 else "")
        caption = Text(no_wrap=True, overflow="crop")
        caption.append(label, style=f"bold {palette_colour(self, 'foreground')}")
        caption.append(name.ljust(room), style=palette_colour(self, "dim"))
        caption.append(" ")
        caption.append(readout, style=palette_colour(self, heat_role(gpu.temperature)))
        return caption

    def _paint_row(self, row: Sequence[Cell]) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        colours: Dict[str, str] = {}
        run: List[str] = []
        run_role = ""
        for glyph, role in row:
            if role != run_role and run:
                line.append("".join(run), style=colours.setdefault(run_role, palette_colour(self, run_role)))
                run = []
            run_role = role
            run.append(glyph)
        if run:
            line.append("".join(run), style=colours.setdefault(run_role, palette_colour(self, run_role)))
        return line

    # ── Mouse ────────────────────────────────────────────────────────────

    def on_click(self, event: events.Click) -> None:
        """The title row is the monitor's own switch."""
        if int(event.y) != 0:
            return
        event.stop()
        try:
            self.app.run_keyline_action("resource-monitor")
        except Exception:
            self.set_expanded(not self._expanded)


def _percent(value: Optional[float]) -> str:
    return "—%" if value is None else f"{value * 100:.0f}%"


def _short_name(name: str) -> str:
    """A GPU's name without the vendor's own words for itself."""
    text = " ".join(str(name or "").split())
    for noise in ("NVIDIA ", "GeForce ", "AMD ", "Radeon(TM) ", "(TM)", "Corporation "):
        text = text.replace(noise, "")
    return text.strip() or "GPU"


__all__ = [
    "FIELDED_GPUS",
    "GpuReading",
    "NVIDIA_SMI_QUERY",
    "NeuronNet",
    "Reading",
    "ResourceMonitor",
    "ResourceSampler",
    "field_rows",
    "heat_role",
    "parse_ioreg_accelerator",
    "parse_nvidia_smi",
    "probe_gpus",
    "read_amdgpu_sysfs",
    "sample_system",
]
