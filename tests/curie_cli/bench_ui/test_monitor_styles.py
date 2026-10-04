"""The resource monitor's six styles: each drawn by the readings, and correctly.

"Abstract but correct" is the brief: every style is an animation, and every
thing that moves in one moves *because of* a reading. These tests hold each
style to what its own description promises — an idle core's planet stands
still, a dry core does not rain, a tank fills to the level — and every style
to the contract the widget relies on: a frame exactly the size it was asked
for, whatever the machine, including one that reports nothing at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import pytest

from curie_cli.bench_ui import monitor_styles as ms
from curie_cli.bench_ui.monitor_styles import (
    DEFAULT_STYLE,
    HEAT,
    STYLE_NAMES,
    BrailleCanvas,
    NeuralStyle,
    OrreryStyle,
    RainStyle,
    ScopeStyle,
    TideStyle,
    WaterfallStyle,
    heat_index,
    log_rate,
    make_style,
    normalise_style,
)

GB = 1024 ** 3


@dataclass(frozen=True)
class _Gpu:
    index: int = 0
    name: str = "Test GPU"
    vendor: str = "nvidia"
    utilization: Optional[float] = 0.5
    memory_used: Optional[float] = 6 * GB
    memory_total: Optional[float] = 24 * GB
    temperature: Optional[float] = 60.0

    @property
    def memory_fraction(self) -> Optional[float]:
        if not self.memory_total or self.memory_used is None:
            return None
        return self.memory_used / self.memory_total


@dataclass(frozen=True)
class _Reading:
    """The fields a style reads, with nothing else — as a test can set them."""

    cpu: Optional[float] = 0.5
    cores: Tuple[float, ...] = (0.5, 0.5)
    memory_used: Optional[float] = 8 * GB
    memory_total: Optional[float] = 16 * GB
    swap_used: Optional[float] = None
    swap_total: Optional[float] = None
    net_rx: Optional[float] = None
    net_tx: Optional[float] = None
    disk_read: Optional[float] = None
    disk_write: Optional[float] = None
    gpus: tuple = ()
    cpu_temp: Optional[float] = None
    at: float = 0.0


NOTHING = _Reading(cpu=None, cores=(), memory_used=None, memory_total=None)


def _run(style, reading, seconds: float, dt: float = 0.1, width: int = 30, history=None):
    history = list(history or [reading])
    frame = None
    for _ in range(int(round(seconds / dt))):
        style.step(dt, reading, history)
        frame = style.frame(width, style.rows(reading), reading, history)
    return frame


def _glyphs(frame) -> str:
    return "".join(cell[0] for row in frame for cell in row)


# ── Every style ──────────────────────────────────────────────────────────


MACHINES = {
    "nothing reported": NOTHING,
    "one core": _Reading(cpu=0.3, cores=(0.3,)),
    "a big box": _Reading(
        cpu=0.7,
        cores=tuple((i % 10) / 10 for i in range(128)),
        swap_used=1 * GB,
        swap_total=8 * GB,
        net_rx=50e6,
        net_tx=2e6,
        disk_read=200e6,
        disk_write=5e6,
        gpus=tuple(_Gpu(index=i, utilization=i / 8) for i in range(8)),
        cpu_temp=90.0,
    ),
}


@pytest.mark.parametrize("name", STYLE_NAMES)
@pytest.mark.parametrize("machine", sorted(MACHINES))
@pytest.mark.parametrize("width", [8, 30, 61])
def test_every_style_draws_exactly_its_box_whatever_the_machine(name, machine, width):
    style = make_style(name, seed=1)
    reading = MACHINES[machine]
    history = []
    for tick in range(25):
        history.append(reading)
        style.step(0.1, reading, history)
        rows = style.rows(reading)
        frame = style.frame(width, rows, reading, history)
        assert len(frame) == rows, (name, machine, tick)
        for row in frame:
            assert len(row) == width
            for glyph, role, background in row:
                assert isinstance(glyph, str) and len(glyph) == 1
                assert isinstance(role, str) and role
                assert background is None or isinstance(background, str)


def test_there_are_at_least_five_styles_each_named_and_described():
    assert len(STYLE_NAMES) >= 5
    for name in STYLE_NAMES:
        style = make_style(name)
        assert style.name == name
        assert style.title and style.blurb


def test_an_unknown_style_draws_as_the_default():
    assert normalise_style("no-such-style") == DEFAULT_STYLE
    assert normalise_style("  ORRERY ") == "orrery"
    assert normalise_style(None) == DEFAULT_STYLE
    assert make_style("no-such-style").name == DEFAULT_STYLE


@pytest.mark.parametrize("name", STYLE_NAMES)
def test_a_seeded_style_is_repeatable(name):
    reading = _Reading(cpu=0.6, cores=(0.9, 0.1, 0.5), net_rx=5e6, disk_read=1e6)

    def frame():
        return _run(make_style(name, seed=4), reading, 2.0)

    assert frame() == frame()


# ── The log scale ────────────────────────────────────────────────────────


def test_the_rate_scale_runs_from_a_kilobyte_to_a_gigabyte_a_second():
    assert log_rate(None) == log_rate(0) == log_rate(1024) == 0.0
    assert log_rate(1024 ** 3) == pytest.approx(1.0)
    assert log_rate(1024 ** 2) == pytest.approx(0.5)
    assert log_rate(1e12) == 1.0
    assert log_rate(10e3) < log_rate(10e6) < log_rate(500e6)


# ── The canvas ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("centre", [(10.0, 10.0), (10.5, 10.5), (11.5, 9.5)])
def test_a_disc_is_solid_wherever_its_centre_falls(centre):
    """Halves rounded to even left every other column of a disc empty."""
    canvas = BrailleCanvas(12, 6)
    cx, cy = centre
    canvas.disc(cx, cy, 3.0, 1.0)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            assert canvas.level_at(cx + dx, cy + dy) == 1.0, (dx, dy)


def test_the_afterglow_fades_and_then_goes():
    canvas = BrailleCanvas(4, 2)
    canvas.plot(3, 3, 1.0)
    canvas.fade(0.5)
    assert canvas.level_at(3, 3) == pytest.approx(0.5)
    for _ in range(10):
        canvas.fade(0.5)
    assert canvas.level_at(3, 3) == 0.0
    assert all(cell[0] == " " for row in canvas.render() for cell in row)


# ── NEURAL ───────────────────────────────────────────────────────────────


def _cpu_firings(load: float, seconds: float = 12.0) -> int:
    style = NeuralStyle(seed=3)
    reading = _Reading(cpu=load, cores=(load,) * 8)
    fired = 0
    for _ in range(int(seconds * 10)):
        style.step(0.1, reading, [reading])
        fired += style.cpu_net.fired_last
    return fired


def test_neural_cores_fire_harder_the_harder_they_work():
    assert _cpu_firings(0.0) < _cpu_firings(0.5) < _cpu_firings(1.0)


def test_neural_ticks_in_real_time_not_per_frame():
    """A fast display must not make a field look busier than its load."""
    slow, fast = NeuralStyle(seed=1), NeuralStyle(seed=1)
    reading = _Reading(cpu=1.0, cores=(1.0,) * 4)
    for _ in range(10):
        slow.step(0.1, reading, [reading])
    for _ in range(100):
        fast.step(0.01, reading, [reading])
    assert slow.clock == pytest.approx(fast.clock)
    assert slow.ticked == fast.ticked == int(NeuralStyle.TICK_HZ)
    assert slow.frame(30, slow.rows(reading), reading, [reading]) == fast.frame(
        30, fast.rows(reading), reading, [reading]
    )


def test_neural_gives_each_gpu_a_field_and_a_line_more_than_without():
    style = NeuralStyle()
    bare = style.rows(_Reading())
    one = style.rows(_Reading(gpus=(_Gpu(),)))
    two = style.rows(_Reading(gpus=(_Gpu(index=0), _Gpu(index=1))))
    assert bare < one < two
    frame = _run(NeuralStyle(), _Reading(gpus=(_Gpu(index=3),)), 1.0)
    assert "gpu3" in _glyphs(frame)


def test_neural_fires_in_the_cpu_temperature_colour():
    style = NeuralStyle(seed=2)
    reading = _Reading(cpu=1.0, cores=(1.0,) * 8, cpu_temp=95.0)
    roles = set()
    for _ in range(80):
        style.step(0.1, reading, [reading])
        frame = style.frame(30, style.rows(reading), reading, [reading])
        roles.update(role for row in frame for glyph, role, _bg in row if glyph == ms.FIRING)
    assert roles == {"error"}


# ── ORRERY ───────────────────────────────────────────────────────────────


def test_orrery_an_idle_core_stands_still_and_a_busy_one_moves_at_its_load():
    style = OrreryStyle(seed=1)
    reading = _Reading(cores=(0.0, 1.0, 0.5))
    style.step(0.0, reading, [reading])  # settle the loads in place
    before = list(style.angles)
    style.step(0.2, reading, [reading])
    moved = [(after - start) % (2 * math.pi) for start, after in zip(before, style.angles)]
    assert moved[0] == pytest.approx(0.0)
    for planet, load in ((1, 1.0), (2, 0.5)):
        orbit = OrreryStyle.orbit_of(planet, 3)
        assert moved[planet] == pytest.approx(
            OrreryStyle.SPEED * load / math.sqrt(1 + orbit) * 0.2, rel=1e-6
        )


def test_orrery_has_a_planet_for_every_core_up_to_its_limit():
    style = OrreryStyle()
    style.step(0.1, _Reading(cores=(0.5,) * 6), [])
    assert len(style.loads) == 6
    style.step(0.1, _Reading(cores=(0.5,) * 64), [])
    assert len(style.loads) == OrreryStyle.PLANETS


def test_orrery_comets_come_only_with_network_traffic():
    quiet = OrreryStyle(seed=5)
    _run(quiet, _Reading(), 5.0)
    assert quiet.comets == []
    busy = OrreryStyle(seed=5)
    seen = 0
    reading = _Reading(net_rx=200e6, net_tx=200e6)
    for _ in range(50):
        busy.step(0.1, reading, [reading])
        seen = max(seen, len(busy.comets))
    assert seen > 0
    inbound = [c for c in busy.comets if c.speed < 0]
    outbound = [c for c in busy.comets if c.speed > 0]
    assert all(c.role == "accent" for c in inbound)
    assert all(c.role == "success" for c in outbound)


def test_orrery_the_sun_grows_with_the_cpu():
    idle, busy = OrreryStyle(), OrreryStyle()
    _run(idle, _Reading(cpu=0.05), 3.0)
    _run(busy, _Reading(cpu=0.95), 3.0)
    assert idle.sun < 0.1 < 0.9 < busy.sun


def test_orrery_the_belt_is_lit_as_far_round_as_memory_is_used():
    def lit(memory: float) -> int:
        style = OrreryStyle()
        reading = _Reading(cpu=0.0, cores=(), memory_used=memory * 16 * GB)
        frame = style.frame(30, style.rows(reading), reading, [reading])
        # The canvas only: the legend under it keys memory in the same colour.
        return sum(role == "secondary" for row in frame[:-1] for _g, role, _b in row)

    assert lit(0.0) == 0 < lit(0.25) < lit(0.5) < lit(1.0)


# ── WATERFALL ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("width", [8, 20, 30, 61])
@pytest.mark.parametrize("cores", [1, 4, 16, 128])
def test_waterfall_columns_fill_the_width_exactly(width, cores):
    reading = _Reading(cores=(0.5,) * cores, swap_total=4 * GB, swap_used=GB, gpus=(_Gpu(),))
    channels = WaterfallStyle().channels(width, reading)
    assert sum(channel.width for channel in channels) == width
    assert all(channel.width >= 1 for channel in channels)


def test_waterfall_the_newest_sample_is_the_top_row_in_its_heat():
    style = WaterfallStyle()
    old = _Reading(cores=(0.0, 0.0), at=0.0)
    new = _Reading(cores=(1.0, 0.0), at=0.5)
    frame = style.frame(30, style.rows(new), new, [old, new])
    hot = HEAT[heat_index(1.0) - 1]
    top = frame[1][0]  # row 0 is the labels; core 0's column
    assert top[0] == "▀" and top[1] == hot
    assert frame[1][style.channels(30, new)[0].width][0] == " ", "core 1 was idle"


def test_waterfall_reads_memory_into_its_own_column():
    style = WaterfallStyle()
    reading = _Reading(cores=(0.0,), memory_used=16 * GB, memory_total=16 * GB)
    x = 0
    for channel in style.channels(30, reading):
        if channel.label == "M":
            break
        x += channel.width
    frame = style.frame(30, style.rows(reading), reading, [reading, reading])
    assert frame[0][x][0] == "M"
    assert frame[1][x] == ("█", HEAT[heat_index(1.0) - 1], None)


# ── SCOPE ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cpu", [0.0, 0.5, 1.0])
def test_scope_the_cpu_trace_head_sits_at_the_cpu_value(cpu):
    style = ScopeStyle()
    reading = _Reading(cpu=cpu)
    frame = style.frame(30, style.rows(reading), reading, [reading])
    canvas_rows = style.rows(reading) - 1
    expected = int(ScopeStyle.y_for(cpu, canvas_rows * 4) // 4)
    labels = {row_index: row[-1] for row_index, row in enumerate(frame[:-1])}
    assert labels[expected][0] == "c"


def test_scope_a_trace_with_no_readings_is_not_drawn():
    style = ScopeStyle()
    reading = _Reading(gpus=())
    frame = style.frame(30, style.rows(reading), reading, [reading])
    assert "g" not in [row[-1][0] for row in frame[:-1]]
    assert ScopeStyle.value(reading, "g") is None
    assert ScopeStyle.value(reading, "n") is None


def test_scope_rolls_between_samples_and_snaps_on_a_new_one():
    style = ScopeStyle()
    first = _Reading(at=1.0)
    style.step(0.1, first, [first])
    style.step(0.2, first, [first])
    assert style.since_sample == pytest.approx(0.2)
    second = _Reading(at=1.5)
    style.step(0.1, second, [first, second])
    assert style.since_sample == 0.0


# ── RAIN ─────────────────────────────────────────────────────────────────


def test_rain_an_idle_machine_stays_dry():
    style = RainStyle(seed=2)
    _run(style, _Reading(cpu=0.0, cores=(0.0,) * 8), 10.0)
    assert all(not drops for drops in style.columns.values())


def test_rain_falls_only_where_a_core_is_loaded():
    style = RainStyle(seed=2)
    reading = _Reading(cpu=0.5, cores=(1.0, 0.0))  # left half busy, right half idle
    _run(style, reading, 6.0)
    left = sum(len(style.columns.get(x, [])) for x in range(15))
    right = sum(len(style.columns.get(x, [])) for x in range(15, 30))
    assert left > 0 and right == 0


@pytest.mark.parametrize("memory", [0.0, 0.25, 0.5, 1.0])
def test_rain_the_pool_is_as_wide_as_memory_is_used(memory):
    style = RainStyle()
    reading = _Reading(cores=(0.0,), memory_used=memory * 16 * GB, memory_total=16 * GB)
    frame = style.frame(40, style.rows(reading), reading, [reading])
    filled = sum(cell[0] == "▄" for cell in frame[-1])
    assert filled == round(memory * 40)


def test_rain_the_falling_heads_print_the_cores_load():
    style = RainStyle(seed=6)
    reading = _Reading(cpu=0.7, cores=(0.7,))
    frame = _run(style, reading, 4.0)
    digits = {cell[0] for row in frame[:-1] for cell in row if cell[1] == "accent"}
    assert digits and digits <= {"7"}


# ── TIDE ─────────────────────────────────────────────────────────────────


def test_tide_tanks_fill_to_the_reading():
    style = TideStyle()
    reading = _Reading(cpu=0.8, memory_used=4 * GB, memory_total=16 * GB)
    _run(style, reading, 6.0)
    assert style.levels["CPU"] == pytest.approx(0.8, abs=1e-3)
    assert style.levels["MEM"] == pytest.approx(0.25, abs=1e-3)


def test_tide_a_still_level_settles_and_a_moving_one_swells():
    style = TideStyle()
    _run(style, _Reading(cpu=0.5), 8.0)
    settled = style.swell["CPU"]
    style.step(0.1, _Reading(cpu=1.0), [])
    assert settled < 0.05 < style.swell["CPU"]


def test_tide_names_each_tank_and_prints_its_level():
    style = TideStyle()
    reading = _Reading(cpu=0.42, gpus=(_Gpu(index=0, utilization=0.8),))
    frame = _run(style, reading, 1.0, width=40)
    figures = "".join(cell[0] for cell in frame[-2])
    names = "".join(cell[0] for cell in frame[-1])
    assert "42" in figures and "80" in figures
    for name in ("CPU", "MEM", "G0", "V0"):
        assert name in names
    assert "SWP" not in names, "no swap, no swap tank"


def test_tide_bubbles_rise_only_where_there_is_work():
    style = TideStyle(seed=3)
    _run(style, _Reading(cpu=0.0, memory_used=8 * GB, memory_total=16 * GB), 6.0)
    assert not style.bubbles["CPU"]
    _run(style, _Reading(cpu=1.0), 6.0)
    assert style.bubbles["CPU"]
