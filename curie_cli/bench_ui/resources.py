"""The resource monitor: the whole machine, read out and animated.

Sits under the elapsed tape in the instrument stack (F8), below a rule, and
reads the machine rather than the turn — which is the other half of "what is
the console doing": a turn that has gone quiet may be waiting on a provider,
or may be waiting on a local model that has the GPU pinned at a hundred
percent.

**The readouts** come first, as numbers, because a picture is how a reading
is *noticed* and a number is how it is *read*: total CPU with its
temperature, a strip with a column for every core and the clock speed,
memory and swap, the network's traffic down and up and the disk's reads and
writes as rates, the load average and the number of processes, how long the
machine has been up and its battery, and for every GPU its use, temperature,
memory, power draw against its limit, clock and fan.

**The animation** under them is one of six styles, chosen on the PANEL pane
(``ui.resource_style``) — spiking neural networks, a solar system, a
spectrogram, an oscilloscope, digital rain or tanks of liquid. Every one is
driven by the readings and nothing else; see
:mod:`curie_cli.bench_ui.monitor_styles` for what moves with what.

Nothing is invented: a reading a machine does not give is left out or shown
as a dash, a GPU that reports no utilisation is drawn idle and says so, and
a machine with no GPU any tool here can see says that instead of drawing one.

**Sampling is on demand.** A background thread takes the readings — a GPU
query is a subprocess, which has no business on the UI thread — and it only
takes them while a monitor is actually on screen: hidden by F8, collapsed,
or squeezed out by a narrow window, it costs nothing. One sampler serves
every monitor the console draws, so the preview on the PANEL pane and the
monitor in the stack read the same machine once, not twice.
"""

from __future__ import annotations

import glob
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.widget import Widget

from curie_cli.bench_ui.indicators import IndicatorKit, palette_colour, widget_optics
from curie_cli.bench_ui.monitor_styles import (  # noqa: F401 - re-exported
    CHARGED,
    DEFAULT_STYLE,
    DORMANT,
    EIGHTHS,
    FIELDED_GPUS,
    FIRING,
    LIVE,
    RECOVERING,
    RESTING,
    SAMPLE_SECONDS,
    SPACING,
    STYLE_NAMES,
    STYLES,
    WIRE,
    NeuronNet,
    Style,
    clamp01,
    field_rows,
    group,
    heat_role,
    level_role,
    make_style,
    normalise_style,
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
    power: Optional[float] = None  # watts drawn
    power_limit: Optional[float] = None  # watts allowed
    clock: Optional[float] = None  # graphics clock, MHz
    fan: Optional[float] = None  # 0…1

    @property
    def memory_fraction(self) -> Optional[float]:
        if not self.memory_total or self.memory_used is None:
            return None
        return max(0.0, min(1.0, self.memory_used / self.memory_total))

    @property
    def has_detail(self) -> bool:
        """Whether there is anything for the second line: power, clock, fan."""
        return any(v is not None for v in (self.power, self.clock, self.fan))


@dataclass(frozen=True)
class Reading:
    """The machine, at one moment. ``None`` throughout is "not reported"."""

    cpu: Optional[float] = None  # 0…1
    cpu_count: int = 0
    memory_used: Optional[float] = None  # bytes
    memory_total: Optional[float] = None  # bytes
    gpus: Tuple[GpuReading, ...] = ()
    #: Why there are no GPUs, when there are none.
    gpu_note: str = ""
    at: float = field(default_factory=time.monotonic)
    #: Every core's load, 0…1, in the order the system numbers them.
    cores: Tuple[float, ...] = ()
    cpu_freq: Optional[float] = None  # MHz
    cpu_temp: Optional[float] = None  # °C
    load: Optional[Tuple[float, float, float]] = None  # 1, 5, 15 minutes
    swap_used: Optional[float] = None  # bytes
    swap_total: Optional[float] = None  # bytes
    #: Rates, bytes a second, worked out by the sampler between two readings.
    net_rx: Optional[float] = None
    net_tx: Optional[float] = None
    disk_read: Optional[float] = None
    disk_write: Optional[float] = None
    processes: Optional[int] = None
    uptime: Optional[float] = None  # seconds
    battery: Optional[float] = None  # 0…1
    charging: Optional[bool] = None
    #: The raw counters the rates are worked out from: (received, sent) and
    #: (read, written), bytes since boot.
    net_bytes: Optional[Tuple[float, float]] = None
    disk_bytes: Optional[Tuple[float, float]] = None


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
NVIDIA_SMI_QUERY = (
    "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
    "power.draw,power.limit,clocks.gr,fan.speed"
)


def parse_nvidia_smi(output: str) -> List[GpuReading]:
    """``nvidia-smi --query-gpu=… --format=csv,noheader,nounits`` → readings.

    The first six fields are the ones every driver answers; power, clock and
    fan follow when it has them, and an older output without them still
    parses.
    """
    gpus: List[GpuReading] = []
    for line in (output or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        index = _number(parts[0])
        util, used, total, temp = (_number(p) for p in parts[2:6])
        power, limit, clock, fan = (_number(p) for p in (parts[6:10] + [""] * 4)[:4])
        gpus.append(
            GpuReading(
                index=int(index) if index is not None else len(gpus),
                name=parts[1] or "NVIDIA GPU",
                vendor="nvidia",
                utilization=None if util is None else clamp01(util / 100.0),
                memory_used=None if used is None else used * _MIB,
                memory_total=None if total is None else total * _MIB,
                temperature=temp,
                power=power,
                power_limit=limit,
                clock=clock,
                fan=None if fan is None else clamp01(fan / 100.0),
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

    def ask(call: Callable[[], Any], scale: float = 1.0) -> Optional[float]:
        try:
            return float(call()) * scale
        except Exception:
            return None

    try:
        gpus = []
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            used = total = None
            try:
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                used, total = float(memory.used), float(memory.total)
            except Exception:
                pass
            fan = ask(lambda: pynvml.nvmlDeviceGetFanSpeed(handle), 0.01)
            gpus.append(
                GpuReading(
                    index=index,
                    name=str(name),
                    vendor="nvidia",
                    utilization=ask(lambda: pynvml.nvmlDeviceGetUtilizationRates(handle).gpu, 0.01),
                    memory_used=used,
                    memory_total=total,
                    temperature=ask(lambda: pynvml.nvmlDeviceGetTemperature(
                        handle, pynvml.NVML_TEMPERATURE_GPU)),
                    power=ask(lambda: pynvml.nvmlDeviceGetPowerUsage(handle), 0.001),
                    power_limit=ask(lambda: pynvml.nvmlDeviceGetEnforcedPowerLimit(handle), 0.001),
                    clock=ask(lambda: pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)),
                    fan=None if fan is None else clamp01(fan),
                )
            )
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


def _active_clock(text: str) -> Optional[float]:
    """The clock marked current (``*``) in an amdgpu ``pp_dpm_*`` table, MHz."""
    for line in (text or "").splitlines():
        if "*" in line:
            match = re.search(r"(\d+(?:\.\d+)?)\s*[Mm][Hh]z", line)
            if match:
                return float(match.group(1))
    return None


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
        temp = power = limit = fan = None
        for hwmon in sorted(glob.glob(os.path.join(device, "hwmon", "hwmon*"))):
            milli = _number(_read(os.path.join(hwmon, "temp1_input")))
            if temp is None and milli is not None:
                temp = milli / 1000.0
            micro = _number(_read(os.path.join(hwmon, "power1_average")))
            if micro is None:
                micro = _number(_read(os.path.join(hwmon, "power1_input")))
            if power is None and micro is not None:
                power = micro / 1e6
            cap = _number(_read(os.path.join(hwmon, "power1_cap")))
            if limit is None and cap is not None:
                limit = cap / 1e6
            pwm = _number(_read(os.path.join(hwmon, "pwm1")))
            if fan is None and pwm is not None:
                fan = clamp01(pwm / 255.0)
        name = _read(os.path.join(device, "product_name")).strip() or "AMD Radeon"
        gpus.append(
            GpuReading(
                index=int(card[4:]),
                name=name,
                vendor="amd",
                utilization=None if util is None else clamp01(util / 100.0),
                memory_used=used,
                memory_total=total,
                temperature=temp,
                power=power,
                power_limit=limit,
                clock=_active_clock(_read(os.path.join(device, "pp_dpm_sclk"))),
                fan=fan,
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
                utilization=clamp01(int(util.group(1)) / 100.0),
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


# ── The system ───────────────────────────────────────────────────────────

#: The drivers whose sensors are the CPU, best first, and the sensor of
#: each that is the package (or the die) rather than one core.
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "soc_thermal")
PACKAGE_LABELS = ("package id 0", "tctl", "tdie", "cpu")


def pick_cpu_temperature(sensors: Mapping[str, Sequence[Any]]) -> Optional[float]:
    """The CPU's temperature out of ``psutil.sensors_temperatures()``.

    The package sensor of the first CPU driver present, or that driver's
    hottest reading when it labels none as the package. Another chip's
    sensors — a disk, a wireless card, the battery — are not the CPU's, and
    a reading outside 0–150 °C is a sensor that is not wired up.
    """
    for chip in CPU_SENSORS:
        readings = []
        for entry in sensors.get(chip) or ():
            value = getattr(entry, "current", None)
            if isinstance(value, (int, float)) and 0 < value < 150:
                readings.append((str(getattr(entry, "label", "") or "").strip().lower(), float(value)))
        if not readings:
            continue
        for wanted in PACKAGE_LABELS:
            for label, value in readings:
                if label == wanted:
                    return value
        return max(value for _label, value in readings)
    return None


#: How long a temperature is kept: the sensors are many small files.
_TEMPERATURE_SECONDS = 2.0
_temperature: Tuple[float, Optional[float]] = (-1e9, None)


def _cpu_temperature(psutil: Any) -> Optional[float]:
    global _temperature
    now = time.monotonic()
    if now - _temperature[0] < _TEMPERATURE_SECONDS:
        return _temperature[1]
    try:
        sensors = psutil.sensors_temperatures()
    except Exception:
        sensors = {}
    value = pick_cpu_temperature(sensors or {})
    _temperature = (now, value)
    return value


def sample_system() -> Reading:
    """Everything but the GPUs, through psutil. Each figure is on its own:
    one a platform cannot give is ``None``, and the rest still come."""
    try:
        import psutil
    except Exception:
        return Reading()

    def ask(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except Exception:
            return None

    cpu = ask(lambda: psutil.cpu_percent(interval=None) / 100.0)
    cores = ask(lambda: tuple(clamp01(v / 100.0) for v in psutil.cpu_percent(interval=None, percpu=True)))
    freq = ask(lambda: float(psutil.cpu_freq().current) or None)
    memory = ask(psutil.virtual_memory)
    total = float(memory.total) if memory is not None else None
    # Used as "not available": the figure that means "how close to running
    # out", which psutil's own ``used`` is not on every platform.
    used = total - float(memory.available) if memory is not None else None
    swap = ask(psutil.swap_memory)
    load = ask(lambda: tuple(float(v) for v in psutil.getloadavg()))
    net = ask(lambda: psutil.net_io_counters())
    disk = ask(lambda: psutil.disk_io_counters())
    boot = ask(psutil.boot_time)
    battery = ask(psutil.sensors_battery) if hasattr(psutil, "sensors_battery") else None
    return Reading(
        cpu=cpu,
        cpu_count=int(ask(psutil.cpu_count) or 0),
        memory_used=used,
        memory_total=total,
        cores=cores or (),
        cpu_freq=freq,
        cpu_temp=_cpu_temperature(psutil) if hasattr(psutil, "sensors_temperatures") else None,
        load=load if load and len(load) == 3 else None,
        swap_used=float(swap.used) if swap is not None and swap.total else None,
        swap_total=float(swap.total) if swap is not None and swap.total else None,
        processes=ask(lambda: len(psutil.pids())),
        uptime=max(0.0, time.time() - float(boot)) if boot else None,
        battery=clamp01(battery.percent / 100.0) if battery is not None and battery.percent is not None else None,
        charging=bool(battery.power_plugged) if battery is not None and battery.power_plugged is not None else None,
        net_bytes=(float(net.bytes_recv), float(net.bytes_sent)) if net is not None else None,
        disk_bytes=(float(disk.read_bytes), float(disk.write_bytes)) if disk is not None else None,
    )


def _rates(before: Optional[Tuple[float, float]], after: Optional[Tuple[float, float]],
           seconds: float) -> Tuple[Optional[float], Optional[float]]:
    """Two counters' rates between two readings, bytes a second.

    A counter that went backwards was reset (a network interface going
    down, a counter wrapping) — that interval's rate is unknown, not
    negative.
    """
    if before is None or after is None or seconds <= 0:
        return None, None
    out = []
    for old, new in zip(before, after):
        out.append((new - old) / seconds if new >= old else None)
    return out[0], out[1]


#: Readings kept: two minutes of them, at the sampler's half second.
HISTORY = 240


class ResourceSampler:
    """Takes readings on its own thread, only while someone is looking.

    :meth:`want` is called by a monitor on every frame it draws; the thread
    samples while the last call was recent and idles otherwise. A monitor
    that is hidden draws no frames, so it costs no samples. The system is
    read every :data:`SAMPLE_SECONDS`, the GPUs — a subprocess, for most of
    them — every ``gpu_interval``.

    ``system`` and ``gpus`` stand in for the machine in a test. Left out,
    they are this module's :func:`sample_system` and :func:`probe_gpus`,
    looked up on every reading, so a stand-in put on the module reaches a
    sampler made before it.
    """

    #: How long a ``want`` keeps the sampler busy.
    WANT_SECONDS = 3.0

    def __init__(
        self,
        interval: float = SAMPLE_SECONDS,
        gpu_interval: float = 2.0,
        system: Optional[Callable[[], Reading]] = None,
        gpus: Optional[Callable[[Optional[float]], Tuple[List[GpuReading], str]]] = None,
    ) -> None:
        self.interval = interval
        self.gpu_interval = gpu_interval
        self._system = system
        self._gpus = gpus
        self._lock = threading.Lock()
        self._latest: Optional[Reading] = None
        self._history: Deque[Reading] = deque(maxlen=HISTORY)
        self._counters: Optional[Tuple[float, Optional[tuple], Optional[tuple]]] = None
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

    def history(self) -> List[Reading]:
        """The readings kept, oldest first — the newest is :meth:`latest`."""
        with self._lock:
            return list(self._history)

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def sample_once(self, with_gpus: bool = True, gpus=None, note: str = "") -> Reading:
        """One reading, taken now. What the thread runs; callable directly."""
        system = (self._system or sample_system)()
        if with_gpus:
            found, note = (self._gpus or probe_gpus)(system.memory_total)
            gpus = tuple(found)
        net_rx = net_tx = disk_read = disk_write = None
        previous = self._counters
        if previous is not None:
            seconds = system.at - previous[0]
            net_rx, net_tx = _rates(previous[1], system.net_bytes, seconds)
            disk_read, disk_write = _rates(previous[2], system.disk_bytes, seconds)
        self._counters = (system.at, system.net_bytes, system.disk_bytes)
        reading = replace(
            system,
            gpus=tuple(gpus or ()),
            gpu_note=note,
            net_rx=net_rx,
            net_tx=net_tx,
            disk_read=disk_read,
            disk_write=disk_write,
        )
        with self._lock:
            self._latest = reading
            self._history.append(reading)
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


# ── Figures ──────────────────────────────────────────────────────────────


def gigabytes(value: Optional[float]) -> str:
    if value is None:
        return "?"
    figure = value / (1024 ** 3)
    # Tenths only where they are worth a column: 6.1 of 8 says something
    # that 6 of 8 does not, 20.4 of 24 says nothing that 20 of 24 does not.
    return f"{figure:.0f}" if figure >= 10 else f"{figure:.1f}"


def rate(value: Optional[float]) -> str:
    """A byte rate in four columns or fewer: ``0``, ``850``, ``12K``, ``3.4M``."""
    if value is None:
        return "—"
    for unit, size in (("G", 1024 ** 3), ("M", 1024 ** 2), ("K", 1024)):
        if value >= size:
            figure = value / size
            return f"{figure:.1f}{unit}" if figure < 10 else f"{figure:.0f}{unit}"
    return f"{value:.0f}"


def duration(seconds: Optional[float]) -> str:
    """``3d04h``, ``5h12m``, ``42m`` — an uptime in five columns."""
    if seconds is None:
        return "—"
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def clock(mhz: Optional[float]) -> str:
    if mhz is None:
        return ""
    return f"{mhz / 1000:.1f}GHz" if mhz >= 1000 else f"{mhz:.0f}MHz"


def _percent(value: Optional[float]) -> str:
    return "—%" if value is None else f"{value * 100:.0f}%"


def _short_name(name: str) -> str:
    """A GPU's name without the vendor's own words for itself."""
    text = " ".join(str(name or "").split())
    for noise in ("NVIDIA ", "GeForce ", "AMD ", "Radeon(TM) ", "(TM)", "Corporation "):
        text = text.replace(noise, "")
    return text.strip() or "GPU"


# ── The instrument ───────────────────────────────────────────────────────

#: Frames a second the animation is drawn at.
ANIMATION_HZ = 10.0

#: The longest step an animation is asked to take: a monitor coming back
#: on screen after a while away resumes, rather than jumping.
MAX_STEP = 0.5

#: The glass the optics are applied through — a kit is what knows how.
_GLASS = IndicatorKit()


class ResourceMonitor(Widget):
    """The instrument: a title, the readouts, and the animation under them.

    The title is also its switch: click it to fold the monitor to that one
    line (and stop it sampling), click again to open it. The same switch is
    on the PANEL pane, under METERS, and on Shift+F8.

    ``preview`` draws the animation alone under its style's name — the
    PANEL pane's live preview of a style before it is chosen.
    """

    DEFAULT_CSS = """
    ResourceMonitor {
        height: auto;
    }
    """

    def __init__(
        self,
        sampler: Optional[ResourceSampler] = None,
        *,
        style: Optional[str] = None,
        preview: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        #: A sampler handed in belongs to whoever handed it in; one made
        #: here is this monitor's own, and stops with it.
        self._owns_sampler = sampler is None
        self._sampler = sampler if sampler is not None else ResourceSampler()
        self._preview = bool(preview)
        self._style_name = normalise_style(style)
        self._style: Style = make_style(self._style_name, seed=11)
        self._reading: Optional[Reading] = None
        self._expanded = True
        self._tick = 0
        self._stepped_at: Optional[float] = None
        self._rows = 0

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

    @property
    def style_name(self) -> str:
        return self._style_name

    @property
    def style(self) -> Style:
        return self._style

    def set_expanded(self, expanded: bool) -> None:
        if bool(expanded) == self._expanded:
            return
        self._expanded = bool(expanded)
        self._stepped_at = None
        self.refresh(layout=True)

    def set_style(self, name: Optional[str]) -> None:
        """Draw with another style, from a standing start."""
        name = normalise_style(name)
        if name == self._style_name:
            return
        self._style_name = name
        self._style = make_style(name, seed=11)
        self._stepped_at = None
        self.refresh(layout=True)

    def set_reading(self, reading: Optional[Reading]) -> None:
        """Adopt a reading — the sampler's, or a test's."""
        self._reading = reading
        rows = self._row_count(reading)
        self.refresh(layout=rows != self._rows)
        self._rows = rows

    # ── Clock ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._rows = self._row_count(self._reading)
        self.set_interval(1.0 / ANIMATION_HZ, self._advance)

    def on_unmount(self) -> None:
        if self._owns_sampler:
            self._sampler.stop()

    def _showing(self) -> bool:
        """Drawn in the last screen update — not hidden by F8 or a narrow window."""
        size = self.size
        return size.width > 0 and size.height > 0 and self.is_on_screen

    def _advance(self) -> None:
        if not self._expanded or not self._showing():
            self._stepped_at = None
            return
        self._sampler.want()
        latest = self._sampler.latest()
        if latest is not None and latest is not self._reading:
            self.set_reading(latest)
        if self._reading is None:
            return
        now = time.monotonic()
        dt = 1.0 / ANIMATION_HZ if self._stepped_at is None else now - self._stepped_at
        self._stepped_at = now
        self.step(min(MAX_STEP, max(0.0, dt)))
        self.refresh()

    def step(self, dt: float) -> None:
        """Advance the animation ``dt`` seconds on the reading it has."""
        self._style.step(dt, self._reading, self._sampler.history() or (
            [self._reading] if self._reading is not None else []))
        self._tick += 1
        rows = self._row_count(self._reading)
        if rows != self._rows:
            self._rows = rows
            self.refresh(layout=True)

    # ── Size ─────────────────────────────────────────────────────────────

    def header_rows(self, reading: Optional[Reading]) -> int:
        """How many rows the readouts take for ``reading``."""
        if self._preview:
            return 0
        if reading is None:
            return 3
        rows = 2  # CPU, MEM
        rows += 1 if _shows_cores(reading) else 0
        rows += 1 if reading.swap_total else 0
        rows += 1  # NET and DSK
        rows += 1 if reading.load is not None or reading.processes is not None else 0
        rows += 1 if reading.uptime is not None or reading.battery is not None else 0
        if not reading.gpus:
            return rows + 1
        for gpu in reading.gpus:
            rows += 1 + (1 if gpu.has_detail else 0)
        return rows

    def _row_count(self, reading: Optional[Reading]) -> int:
        if not self._expanded:
            return 1
        rows = 1 + self.header_rows(reading)
        if reading is None:
            return rows
        return rows + 1 + self._style.rows(reading)

    def get_content_height(self, container, viewport, width: int) -> int:
        return self._row_count(self._reading)

    # ── Drawing ──────────────────────────────────────────────────────────

    def render(self) -> Text:
        width = max(8, self.size.width or 30)
        out = Text(no_wrap=True, overflow="crop")
        reading = self._reading
        lines: List[Text] = [self._title(width)]
        if self._expanded:
            if not self._preview:
                lines.extend(self._header(reading, width))
            if reading is not None:
                lines.append(self._rule(width))
                lines.extend(self._canvas(reading, width))
            elif self._preview:
                lines.append(Text(" reading the machine …", style=palette_colour(self, "dim")))
        for index, line in enumerate(lines):
            if index:
                out.append("\n")
            out.append_text(line)
        return out

    def _title(self, width: int) -> Text:
        if self._preview:
            label = f"PREVIEW · {self._style.title.upper()}"
        else:
            label = f"RESOURCES {'▾' if self._expanded else '▸'}"
        optics = widget_optics(self)
        title = Text(no_wrap=True, overflow="crop")
        if optics.get("mode") == "dos":
            ink, band = palette_colour(self, "ink"), palette_colour(self, "band")
            title.append(label.ljust(width), style=f"bold {ink} on {band}")
        else:
            title.append(label, style=f"bold {palette_colour(self, 'secondary')}")
        return title

    def _rule(self, width: int) -> Text:
        """``╌╌ ORRERY ╌╌╌╌╌`` — where the numbers stop and the picture starts."""
        name = f" {self._style.title.upper()} " if not self._preview else " "
        rule = Text(no_wrap=True, overflow="crop")
        dim = palette_colour(self, "dim")
        rule.append("╌╌", style=dim)
        rule.append(name, style=f"bold {palette_colour(self, 'secondary')}")
        rule.append("╌" * max(0, width - 2 - len(name)), style=dim)
        return rule

    def _header(self, reading: Optional[Reading], width: int) -> List[Text]:
        dim = palette_colour(self, "dim")
        if reading is None:
            return [
                self._line("CPU", "…"),
                self._line("MEM", "…"),
                Text(" reading the machine …", style=dim),
            ]
        cpu = _percent(reading.cpu)
        if reading.cpu_temp is not None:
            cpu += f" {reading.cpu_temp:.0f}°"
        memory = f"{gigabytes(reading.memory_used)}/{gigabytes(reading.memory_total)}G"
        swap = f"{gigabytes(reading.swap_used)}/{gigabytes(reading.swap_total)}G" if reading.swap_total else ""
        # Every bar's figure right-aligned in one column, so the bars start
        # and end together and read as a set rather than as ragged rows.
        figures = max(len(cpu), len(memory), len(swap))
        lines = [self._bar("CPU", reading.cpu, cpu.rjust(figures), width, heat_role(reading.cpu_temp)
                           if reading.cpu_temp is not None and reading.cpu_temp >= 70 else None)]
        if _shows_cores(reading):
            lines.append(self._cores(reading, width))
        lines.append(self._bar("MEM", _fraction(reading.memory_used, reading.memory_total),
                               memory.rjust(figures), width))
        if reading.swap_total:
            lines.append(self._bar("SWP", _fraction(reading.swap_used, reading.swap_total),
                                   swap.rjust(figures), width))
        lines.append(self._traffic(reading, width))
        if reading.load is not None or reading.processes is not None:
            lines.append(self._system(reading, width))
        if reading.uptime is not None or reading.battery is not None:
            lines.append(self._power(reading, width))
        if not reading.gpus:
            line = Text(no_wrap=True, overflow="crop")
            line.append("GPU ", style=f"bold {palette_colour(self, 'foreground')}")
            line.append(reading.gpu_note or "none found", style=dim)
            lines.append(line)
            return lines
        for gpu in reading.gpus:
            lines.append(self._gpu_caption(gpu, width))
            if gpu.has_detail:
                lines.append(self._gpu_detail(gpu, width))
        return lines

    def _line(self, label: str, value: str) -> Text:
        line = Text(no_wrap=True, overflow="crop")
        line.append(f"{label} ", style=f"bold {palette_colour(self, 'foreground')}")
        line.append(value, style=palette_colour(self, "dim"))
        return line

    def _bar(self, label: str, share: Optional[float], value: str, width: int,
             value_role: Optional[str] = None) -> Text:
        """``CPU ▕████░░░░▏ 37%`` — the tape's own ramp, in a row."""
        line = Text(no_wrap=True, overflow="crop")
        line.append(f"{label} ", style=f"bold {palette_colour(self, 'foreground')}")
        room = max(1, width - len(label) - 1 - len(value) - 2)
        if share is None:
            line.append("░" * room, style=palette_colour(self, "dim"))
        else:
            share = clamp01(share)
            filled = int(round(share * room))
            line.append("█" * filled, style=palette_colour(self, level_role(share)))
            line.append("░" * (room - filled), style=palette_colour(self, "dim"))
        line.append(f"  {value}", style=palette_colour(self, value_role or "foreground"))
        return line

    def _cores(self, reading: Reading, width: int) -> Text:
        """A column per core under the CPU bar, as tall as its load, and the clock."""
        speed = clock(reading.cpu_freq)
        room = max(1, width - 4 - (len(speed) + 1 if speed else 0))
        loads = group(reading.cores or ((reading.cpu,) if reading.cpu is not None else ()), room)
        line = Text(no_wrap=True, overflow="crop")
        line.append("    ")
        colours: Dict[str, str] = {}
        for load in loads:
            role = level_role(load) if load >= 0.03 else "dim"
            glyph = EIGHTHS[max(1, int(round(load * 8)))]
            line.append(glyph, style=colours.setdefault(role, palette_colour(self, role)))
        if speed:
            line.append(" " * max(1, room - len(loads) + 1))
            line.append(speed, style=palette_colour(self, "dim"))
        return line

    @staticmethod
    def _fitted(variants: Sequence[Sequence[Tuple[str, str]]], width: int) -> Text:
        """The first way of writing a line that fits ``width``.

        Each variant is a list of ``(text, style)`` pieces, roomiest first. A
        readout that ran past the column was cropped from the right, which
        is where its last figure is — a busy machine's disk-write rate, its
        process count — so a line gives up spacing, then words, before it
        gives up a figure. The last variant is used if none fits.
        """
        chosen = variants[-1]
        for variant in variants:
            if sum(cell_len(text) for text, _style in variant) <= width:
                chosen = variant
                break
        line = Text(no_wrap=True, overflow="crop")
        for text, style in chosen:
            line.append(text, style=style)
        return line

    def _traffic(self, reading: Reading, width: int) -> Text:
        """``NET ↓1.2M ↑34K  DSK r5.0M w1M`` — rates, bytes a second."""
        bold = f"bold {palette_colour(self, 'foreground')}"
        figure = palette_colour(self, "foreground")
        dim = palette_colour(self, "dim")
        net = [("↓", palette_colour(self, "accent")), (rate(reading.net_rx), figure), (" ", ""),
               ("↑", palette_colour(self, "success")), (rate(reading.net_tx), figure)]
        disk = [("r", dim), (rate(reading.disk_read), figure), (" ", ""),
                ("w", dim), (rate(reading.disk_write), figure)]
        return self._fitted(
            [
                [("NET ", bold), *net, ("  ", ""), ("DSK ", bold), *disk],
                [("NET ", bold), *net, (" ", ""), ("DSK ", bold), *disk],
                [("NET", bold), *net, (" ", ""), ("DSK", bold), *disk],
            ],
            width,
        )

    def _system(self, reading: Reading, width: int) -> Text:
        """``LOAD 1.23 0.98 0.76  312 PROC``."""
        bold = f"bold {palette_colour(self, 'foreground')}"
        figure = palette_colour(self, "foreground")
        dim = palette_colour(self, "dim")
        load: List[Tuple[str, str]] = []
        if reading.load is not None:
            busy = reading.load[0] / max(1, reading.cpu_count or len(reading.cores) or 1)
            role = level_role(min(1.0, busy)) if busy >= 0.75 else "foreground"
            load = [("LOAD ", bold), (" ".join(_load(v) for v in reading.load), palette_colour(self, role))]
        if reading.processes is None:
            return self._fitted([load], width)
        count = str(reading.processes)
        return self._fitted(
            [
                load + [("  ", "")] * bool(load) + [(count, figure), (" PROC", dim)],
                load + [(" ", "")] * bool(load) + [(count, figure), ("P", dim)],
            ],
            width,
        )

    def _power(self, reading: Reading, width: int) -> Text:
        """``UP 3d04h  BAT 87% ↯`` — ``↯`` while it charges."""
        bold = f"bold {palette_colour(self, 'foreground')}"
        pieces: List[Tuple[str, str]] = []
        if reading.uptime is not None:
            pieces += [("UP ", bold), (duration(reading.uptime), palette_colour(self, "foreground"))]
        if reading.battery is not None:
            if pieces:
                pieces.append(("  ", ""))
            role = "error" if reading.battery < 0.1 else "warning" if reading.battery < 0.25 else "foreground"
            pieces += [("BAT ", bold), (_percent(reading.battery), palette_colour(self, role))]
            if reading.charging:
                pieces.append((" ↯", palette_colour(self, "success")))
        return self._fitted([pieces], width)

    def _gpu_caption(self, gpu: GpuReading, width: int) -> Text:
        """``GPU0 RTX 4090     37% 61° 6.1/24G`` — the numbers beside the name."""
        figures = [_percent(gpu.utilization)]
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

    def _gpu_detail(self, gpu: GpuReading, width: int) -> Text:
        """`` ↯402/450W 2.5GHz fan 62%`` — what the card is drawing."""
        figure = palette_colour(self, "foreground")
        pieces: List[Tuple[str, str]] = [(" ", "")]
        if gpu.power is not None:
            share = gpu.power / gpu.power_limit if gpu.power_limit else None
            role = level_role(share) if share is not None and share >= 0.75 else "foreground"
            watts = f"{gpu.power:.0f}" + (f"/{gpu.power_limit:.0f}" if gpu.power_limit else "")
            pieces += [("↯", palette_colour(self, "warning")), (watts + "W ", palette_colour(self, role))]
        if gpu.clock is not None:
            pieces.append((clock(gpu.clock) + " ", figure))
        if gpu.fan is not None:
            pieces += [("fan ", palette_colour(self, "dim")), (_percent(gpu.fan), figure)]
        # Tight, the fan's word goes before its figure does.
        tight = [(text, style) if text != "fan " else ("f", style) for text, style in pieces]
        return self._fitted([pieces, tight], width)

    def _canvas(self, reading: Reading, width: int) -> List[Text]:
        """The style's frame, behind the display's glass, painted."""
        rows = self._style.rows(reading)
        frame = self._style.frame(width, rows, reading, self._sampler.history() or [reading])
        _GLASS.optics = widget_optics(self)
        lit = _GLASS.apply_optics([[(glyph, fg) for glyph, fg, _bg in row] for row in frame], self._tick)
        colours: Dict[Tuple[str, Optional[str]], str] = {}

        def colour(key: Tuple[str, Optional[str]]) -> str:
            found = colours.get(key)
            if found is None:
                fg, bg = key
                found = palette_colour(self, fg)
                if bg:
                    found = f"{found} on {palette_colour(self, bg)}"
                colours[key] = found
            return found

        lines = []
        for drawn, row in zip(lit, frame):
            line = Text(no_wrap=True, overflow="crop")
            run: List[str] = []
            key: Optional[Tuple[str, Optional[str]]] = None
            for (glyph, fg), (_g, _f, bg) in zip(drawn, row):
                if (fg, bg) != key and run:
                    line.append("".join(run), style=colour(key))
                    run = []
                key = (fg, bg)
                run.append(glyph)
            if run and key is not None:
                line.append("".join(run), style=colour(key))
            lines.append(line)
        return lines

    # ── Mouse ────────────────────────────────────────────────────────────

    def on_click(self, event: events.Click) -> None:
        """The title row is the monitor's own switch."""
        if self._preview or int(event.y) != 0:
            return
        event.stop()
        try:
            self.app.run_keyline_action("resource-monitor")
        except Exception:
            self.set_expanded(not self._expanded)


def _load(value: float) -> str:
    """A load average in four columns: ``0.98``, ``12.4``, ``128``."""
    if value < 10:
        return f"{value:.2f}"
    if value < 100:
        return f"{value:.1f}"
    return f"{value:.0f}"


def _fraction(used: Optional[float], total: Optional[float]) -> Optional[float]:
    if not total or used is None:
        return None
    return clamp01(used / total)


def _shows_cores(reading: Reading) -> bool:
    """Whether the per-core strip has anything to add to the CPU bar."""
    return len(reading.cores) > 1 or reading.cpu_freq is not None


__all__ = [
    "ANIMATION_HZ",
    "CPU_SENSORS",
    "DEFAULT_STYLE",
    "FIELDED_GPUS",
    "GpuReading",
    "HISTORY",
    "NVIDIA_SMI_QUERY",
    "NeuronNet",
    "Reading",
    "ResourceMonitor",
    "ResourceSampler",
    "STYLE_NAMES",
    "clock",
    "duration",
    "field_rows",
    "gigabytes",
    "heat_role",
    "parse_ioreg_accelerator",
    "parse_nvidia_smi",
    "pick_cpu_temperature",
    "probe_gpus",
    "rate",
    "read_amdgpu_sysfs",
    "sample_system",
]
