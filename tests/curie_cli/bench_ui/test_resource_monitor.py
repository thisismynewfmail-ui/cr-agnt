"""The resource monitor under the elapsed tape: CPU, memory, GPUs as neurons.

Three layers, tested at their own seams: the driver readers (pure text →
readings), the neuron field (seeded, so a figure can be held to), and the
instrument on the console — its rule, its switch, and that it reads the
machine only while it is open.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import resources  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.instruments import TapeMeter  # noqa: E402
from curie_cli.bench_ui.panes import PanelPane, ToggleSwitch  # noqa: E402
from curie_cli.bench_ui.resources import (  # noqa: E402
    DORMANT,
    FIRING,
    LIVE,
    WIRE,
    GpuReading,
    NeuronNet,
    Reading,
    ResourceMonitor,
    ResourceSampler,
    field_rows,
    heat_role,
    parse_ioreg_accelerator,
    parse_nvidia_smi,
    probe_gpus,
    read_amdgpu_sysfs,
)
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_RESOURCE_MONITOR,
    KEY_RESOURCE_STYLE,
    read_settings,
    write_setting,
)

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402

GB = 1024 ** 3
MIB = 1024 * 1024

#: Every glyph the field may draw: neurons, wiring at rest and lit, air.
FIELD_GLYPHS = (
    {resources.DORMANT, resources.RESTING, resources.CHARGED, resources.FIRING, resources.RECOVERING, " "}
    | set(WIRE.values())
    | set(LIVE.values())
)


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


def _gpu(index=0, util=0.5, used=6 * GB, total=24 * GB, temp=60.0, name="NVIDIA GeForce RTX 4090"):
    return GpuReading(
        index=index, name=name, vendor="nvidia",
        utilization=util, memory_used=used, memory_total=total, temperature=temp,
    )


class _Machine:
    """Stands in for psutil and the GPU drivers; counts how often it is read."""

    def __init__(self, gpus=(), note=""):
        self.gpus = list(gpus)
        self.note = note
        self.system_reads = 0
        self.gpu_reads = 0
        self._lock = threading.Lock()

    def system(self) -> Reading:
        with self._lock:
            self.system_reads += 1
        return Reading(cpu=0.25, cpu_count=8, memory_used=8 * GB, memory_total=32 * GB)

    def probe(self, memory_total):
        with self._lock:
            self.gpu_reads += 1
        return list(self.gpus), ("" if self.gpus else (self.note or "no GPU found"))


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def machine(monkeypatch):
    """A machine with one busy GPU, installed under every monitor made."""
    fake = _Machine(gpus=[_gpu(util=0.9, used=20 * GB, temp=81.0)])
    monkeypatch.setattr(resources, "sample_system", fake.system)
    monkeypatch.setattr(resources, "probe_gpus", fake.probe)
    return fake


def _monitor(app) -> ResourceMonitor:
    return app.query_one("#resources", ResourceMonitor)


def _plain(monitor: ResourceMonitor) -> str:
    return monitor.render().plain


# ── Reading the drivers ──────────────────────────────────────────────────


def test_nvidia_smi_csv_becomes_readings_in_bytes_and_fractions():
    output = (
        "0, NVIDIA GeForce RTX 4090, 37, 6144, 24564, 61\n"
        "1, NVIDIA A100-SXM4-80GB, 100, 81000, 81920, 77\n"
    )
    first, second = parse_nvidia_smi(output)
    assert (first.index, first.vendor, first.name) == (0, "nvidia", "NVIDIA GeForce RTX 4090")
    assert first.utilization == pytest.approx(0.37)
    assert first.memory_used == 6144 * MIB
    assert first.memory_total == 24564 * MIB
    assert first.temperature == 61
    assert second.utilization == pytest.approx(1.0)
    assert second.memory_fraction == pytest.approx(81000 / 81920)


def test_nvidia_smi_not_available_fields_are_none_not_zero():
    """A card that will not say is drawn as not saying, never as idle."""
    (gpu,) = parse_nvidia_smi("0, Tesla K80, [N/A], [N/A], 11441, [Not Supported]\n")
    assert gpu.utilization is None
    assert gpu.memory_used is None
    assert gpu.memory_fraction is None
    assert gpu.temperature is None
    assert gpu.memory_total == 11441 * MIB


def test_nvidia_smi_noise_lines_are_skipped():
    assert parse_nvidia_smi("") == []
    assert parse_nvidia_smi("No devices were found\n") == []


def _amd_card(root, card, busy="42", used=str(2 * GB), total=str(8 * GB), temp="55000", name=None):
    device = root / card / "device"
    (device / "hwmon" / "hwmon3").mkdir(parents=True)
    (device / "gpu_busy_percent").write_text(busy + "\n")
    (device / "mem_info_vram_used").write_text(used + "\n")
    (device / "mem_info_vram_total").write_text(total + "\n")
    (device / "hwmon" / "hwmon3" / "temp1_input").write_text(temp + "\n")
    if name:
        (device / "product_name").write_text(name + "\n")


def test_amdgpu_sysfs_is_read_without_any_tool(tmp_path):
    _amd_card(tmp_path, "card1", name="Radeon RX 7900 XTX")
    # A connector node beside it is not a card.
    (tmp_path / "card1-DP-1" / "device").mkdir(parents=True)
    (gpu,) = read_amdgpu_sysfs(str(tmp_path))
    assert (gpu.index, gpu.vendor, gpu.name) == (1, "amd", "Radeon RX 7900 XTX")
    assert gpu.utilization == pytest.approx(0.42)
    assert gpu.memory_fraction == pytest.approx(0.25)
    assert gpu.temperature == pytest.approx(55.0)


def test_amdgpu_sysfs_with_no_cards_is_empty(tmp_path):
    assert read_amdgpu_sysfs(str(tmp_path / "missing")) == []


def test_apple_ioreg_performance_statistics_are_parsed():
    output = (
        '+-o AGXAcceleratorG14X  <class AGXAcceleratorG14X>\n'
        '    {\n'
        '      "model" = "Apple M2 Pro"\n'
        '      "PerformanceStatistics" = {"In use system memory"=3221225472,'
        '"Device Utilization %"=23,"Renderer Utilization %"=20}\n'
        '    }\n'
    )
    (gpu,) = parse_ioreg_accelerator(output, memory_total=32 * GB)
    assert (gpu.vendor, gpu.name) == ("apple", "Apple M2 Pro")
    assert gpu.utilization == pytest.approx(0.23)
    # Unified memory: the GPU's share is measured against the machine's.
    assert gpu.memory_fraction == pytest.approx(3 / 32)


def test_a_machine_with_no_readable_gpu_says_so(monkeypatch):
    monkeypatch.setattr(resources, "_nvml_gpus", lambda: None)
    monkeypatch.setattr(resources, "_nvidia_smi_gpus", lambda timeout=3.0: [])
    monkeypatch.setattr(resources, "read_amdgpu_sysfs", lambda root="": [])
    monkeypatch.setattr(resources, "_apple_gpus", lambda total, timeout=3.0: [])
    gpus, note = probe_gpus(16 * GB)
    assert gpus == []
    assert note


def test_every_vendor_found_is_reported_together(monkeypatch):
    """An NVIDIA card beside an AMD iGPU shows both, not the first found."""
    monkeypatch.setattr(resources, "_nvml_gpus", lambda: None)
    monkeypatch.setattr(resources, "_nvidia_smi_gpus", lambda timeout=3.0: [_gpu()])
    amd = GpuReading(index=1, name="Radeon 780M", vendor="amd", utilization=0.1)
    monkeypatch.setattr(resources, "read_amdgpu_sysfs", lambda root="": [amd])
    monkeypatch.setattr(resources, "_apple_gpus", lambda total, timeout=3.0: [])
    gpus, note = probe_gpus()
    assert [g.vendor for g in gpus] == ["nvidia", "amd"]
    assert note == ""


@pytest.mark.parametrize(
    "temperature, role",
    [(None, "accent"), (40.0, "accent"), (75.0, "warning"), (90.0, "error")],
)
def test_spikes_burn_hotter_as_the_card_does(temperature, role):
    assert heat_role(temperature) == role


# ── The neuron field ─────────────────────────────────────────────────────


def _firings(activity: float, recruited: float = 1.0, ticks: int = 120, seed: int = 3) -> int:
    net = NeuronNet(seed=seed)
    total = 0
    for _ in range(ticks):
        net.step(31, 5, activity, recruited)
        total += net.fired_last
    return total


def test_a_busier_gpu_fires_more():
    idle, half, full = _firings(0.0), _firings(0.5), _firings(1.0)
    assert idle < half < full


def test_an_idle_gpu_still_sparks_rather_than_looking_dead():
    assert _firings(0.0, ticks=400) > 0


def test_less_memory_in_use_leaves_more_of_the_field_dormant():
    def dormant(recruited: float) -> int:
        net = NeuronNet(seed=5)
        net.step(31, 5, 0.5, recruited)
        frame = net.frame(31, 5, recruited)
        return sum(glyph == DORMANT for row in frame for glyph, _role in row)

    assert dormant(0.25) > dormant(0.75) >= dormant(1.0)
    assert dormant(1.0) == 0


@pytest.mark.parametrize("width, height", [(31, 5), (30, 3), (12, 1), (8, 5), (44, 7)])
def test_the_field_is_exactly_its_box_and_only_its_own_glyphs(width, height):
    net = NeuronNet(seed=1)
    for _ in range(40):
        net.step(width, height, 1.0)
    frame = net.frame(width, height)
    assert len(frame) == height
    assert all(len(row) == width for row in frame)
    assert {glyph for row in frame for glyph, _role in row} <= FIELD_GLYPHS


def test_pulses_never_bend_the_wiring_they_travel_along():
    """The rows between neurons are bus runs; a pulse doubles them, lit or
    not, but never turns one sideways — even where two pulses overlap."""
    net = NeuronNet(seed=11)
    layers, per_layer, offset = NeuronNet.shape_for(31, 5)
    buses = [offset + layer * resources.SPACING + resources.SPACING // 2 for layer in range(layers - 1)]
    for _ in range(200):
        net.step(31, 5, 1.0)
        frame = net.frame(31, 5)
        for y in range(1, 2 * (per_layer - 1), 2):
            for x in buses:
                assert frame[y][x][0] in (WIRE["v"], LIVE["v"]), (x, y, frame[y][x])


def test_a_firing_neuron_is_drawn_in_the_heat_colour():
    net = NeuronNet(seed=2)
    seen = set()
    for _ in range(60):
        net.step(31, 5, 1.0)
        for row in net.frame(31, 5, heat="error"):
            seen.update(role for glyph, role in row if glyph == FIRING)
    assert seen == {"error"}


def test_a_seeded_field_is_repeatable():
    def run():
        net = NeuronNet(seed=9)
        for _ in range(30):
            net.step(31, 5, 0.6, 0.8)
        return net.frame(31, 5, 0.8)

    assert run() == run()


def test_one_gpu_gets_a_roomier_field_than_several():
    assert field_rows(1) > field_rows(2) == field_rows(4)


# ── The sampler ──────────────────────────────────────────────────────────


def test_the_sampler_takes_nothing_until_asked():
    fake = _Machine(gpus=[_gpu()])
    sampler = ResourceSampler(interval=0.01, system=fake.system, gpus=fake.probe)
    try:
        threading.Event().wait(0.1)
        assert fake.system_reads == 0
        assert sampler.latest() is None
    finally:
        sampler.stop()


def test_the_sampler_reads_while_wanted():
    fake = _Machine(gpus=[_gpu()])
    sampler = ResourceSampler(interval=0.01, gpu_interval=0.01, system=fake.system, gpus=fake.probe)
    try:
        sampler.want()
        deadline = threading.Event()
        for _ in range(200):
            if sampler.latest() is not None:
                break
            deadline.wait(0.01)
        latest = sampler.latest()
        assert latest is not None
        assert latest.cpu == pytest.approx(0.25)
        assert [g.index for g in latest.gpus] == [0]
    finally:
        sampler.stop()


def test_gpu_probes_run_on_their_own_slower_clock():
    """A GPU query is a subprocess; CPU and memory are an attribute read."""
    fake = _Machine(gpus=[_gpu()])
    sampler = ResourceSampler(system=fake.system, gpus=fake.probe)
    first = sampler.sample_once(with_gpus=True)
    again = sampler.sample_once(with_gpus=False, gpus=first.gpus, note=first.gpu_note)
    assert fake.system_reads == 2
    assert fake.gpu_reads == 1
    assert again.gpus == first.gpus


def test_a_stopped_sampler_does_not_start_again():
    fake = _Machine()
    sampler = ResourceSampler(interval=0.01, system=fake.system, gpus=fake.probe)
    sampler.stop()
    sampler.want()
    threading.Event().wait(0.05)
    assert fake.system_reads == 0


# ── On the console ───────────────────────────────────────────────────────


def test_the_monitor_sits_under_the_elapsed_tape_behind_a_rule(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            tape = app.query_one("#tape-turn", TapeMeter)
            monitor = _monitor(app)
            column = app.query_one("#instruments")
            assert monitor.parent is column
            children = list(column.children)
            assert children.index(monitor) == children.index(tape) + 1
            assert monitor.region.y > tape.region.y
            # The rule: a border on the monitor's top edge, in both skins.
            assert monitor.styles.border_top[0] not in ("", "none", "hidden")

    _run(scenario())


def test_the_monitor_draws_the_machine_and_each_gpu(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            monitor = _monitor(app)
            for _ in range(40):
                await pilot.pause(0.05)
                if monitor.reading is not None:
                    break
            await _settle(pilot)
            text = _plain(monitor)
            assert "RESOURCES" in text
            assert "CPU" in text and "25%" in text
            assert "MEM" in text and "8.0/32G" in text
            assert "GPU0" in text and "RTX 4090" in text and "90%" in text and "81°" in text
            # The readouts, a rule naming the style, and the style's frame —
            # every row of it accounted for in the monitor's own height.
            rows = text.split("\n")
            reading = monitor.reading
            assert len(rows) == 1 + monitor.header_rows(reading) + 1 + monitor.style.rows(reading)
            assert monitor.size.height == len(rows)
            assert monitor.style.title.upper() in rows[1 + monitor.header_rows(reading)]

    _run(scenario())


def test_no_gpu_is_said_rather_than_drawn_idle(monkeypatch):
    fake = _Machine(gpus=[], note="no GPU found")
    monkeypatch.setattr(resources, "sample_system", fake.system)
    monkeypatch.setattr(resources, "probe_gpus", fake.probe)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            monitor = _monitor(app)
            for _ in range(40):
                await pilot.pause(0.05)
                if monitor.reading is not None:
                    break
            text = _plain(monitor)
            assert "no GPU found" in text
            # The CPU still has its field; no GPU gets one, or a caption.
            assert "GPU0" not in text and "▸ gpu" not in text

    _run(scenario())


def test_shift_f8_folds_the_monitor_and_remembers(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            monitor = _monitor(app)
            assert monitor.expanded is True
            await pilot.press("shift+f8")
            await _settle(pilot)
            assert monitor.expanded is False
            assert monitor.size.height == 1
            assert _plain(monitor).strip().startswith("RESOURCES")
            assert read_settings().resource_monitor is False
            switch = app.query_one(PanelPane).query_one("#switch-resource-monitor", ToggleSwitch)
            assert switch.is_on is False
            await pilot.press("shift+f8")
            await _settle(pilot)
            assert monitor.expanded is True
            assert read_settings().resource_monitor is True
            assert switch.is_on is True

    _run(scenario())


def test_the_panel_switch_and_the_title_are_the_same_switch(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            monitor = _monitor(app)
            app.run_keyline_action("resource-monitor")
            await _settle(pilot)
            assert monitor.expanded is False
            await pilot.click("#resources", offset=(1, 0))
            await _settle(pilot)
            assert monitor.expanded is True
            assert read_settings().resource_monitor is True

    _run(scenario())


def test_a_folded_monitor_opens_folded_and_reads_nothing(monkeypatch):
    write_setting(KEY_RESOURCE_MONITOR, False)
    fake = _Machine(gpus=[_gpu()])
    monkeypatch.setattr(resources, "sample_system", fake.system)
    monkeypatch.setattr(resources, "probe_gpus", fake.probe)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            for _ in range(6):
                await pilot.pause(0.05)
            assert _monitor(app).expanded is False
            assert fake.system_reads == 0
            assert fake.gpu_reads == 0

    _run(scenario())


def test_hiding_the_instrument_stack_stops_the_readings(machine):
    """F8 takes the column away; a monitor nobody can see costs nothing."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            monitor = _monitor(app)
            await pilot.press("f8")
            await _settle(pilot)
            assert not monitor.is_on_screen
            sampler = monitor.sampler
            sampler._wanted_until = 0.0
            for _ in range(6):
                await pilot.pause(0.05)
            assert sampler._wanted_until == 0.0

    _run(scenario())


# ── More of the machine ──────────────────────────────────────────────────


def test_nvidia_smi_power_clock_and_fan_are_read_when_the_driver_has_them():
    (gpu,) = parse_nvidia_smi(
        "0, NVIDIA GeForce RTX 4090, 97, 20480, 24564, 81, 402.15, 450.00, 2520, 62\n"
    )
    assert gpu.power == pytest.approx(402.15)
    assert gpu.power_limit == pytest.approx(450.0)
    assert gpu.clock == pytest.approx(2520.0)
    assert gpu.fan == pytest.approx(0.62)
    assert gpu.has_detail


def test_nvidia_smi_without_power_clock_or_fan_still_reads_the_rest():
    (gpu,) = parse_nvidia_smi("0, Tesla T4, 10, 100, 15360, 40, [N/A], [N/A], 585, [N/A]\n")
    assert gpu.utilization == pytest.approx(0.10)
    assert gpu.power is None and gpu.power_limit is None and gpu.fan is None
    assert gpu.clock == pytest.approx(585.0)
    (old,) = parse_nvidia_smi("0, Old Driver GPU, 10, 100, 15360, 40\n")
    assert old.power is None and not old.has_detail


def test_amdgpu_power_clock_and_fan_come_from_its_own_files(tmp_path):
    _amd_card(tmp_path, "card0", name="Radeon RX 7900 XTX")
    device = tmp_path / "card0" / "device"
    hwmon = device / "hwmon" / "hwmon3"
    (hwmon / "power1_average").write_text("212000000\n")
    (hwmon / "power1_cap").write_text("327000000\n")
    (hwmon / "pwm1").write_text("128\n")
    (device / "pp_dpm_sclk").write_text("0: 500Mhz\n1: 1950Mhz *\n2: 2500Mhz\n")
    (gpu,) = read_amdgpu_sysfs(str(tmp_path))
    assert gpu.power == pytest.approx(212.0)
    assert gpu.power_limit == pytest.approx(327.0)
    assert gpu.clock == pytest.approx(1950.0)
    assert gpu.fan == pytest.approx(128 / 255)


class _Sensor:
    def __init__(self, label, current):
        self.label, self.current = label, current


@pytest.mark.parametrize(
    "sensors, expected",
    [
        ({"coretemp": [_Sensor("Core 0", 61.0), _Sensor("Package id 0", 64.0)]}, 64.0),
        ({"k10temp": [_Sensor("Tccd1", 70.0), _Sensor("Tctl", 72.5)]}, 72.5),
        ({"coretemp": [_Sensor("Core 0", 61.0), _Sensor("Core 1", 66.0)]}, 66.0),
        # Not the CPU: a drive, a wireless card — never mistaken for it.
        ({"nvme": [_Sensor("Composite", 45.0)], "iwlwifi_1": [_Sensor("", 50.0)]}, None),
        # A sensor that is not wired up reads nonsense; it is not a reading.
        ({"coretemp": [_Sensor("Package id 0", -273.0)]}, None),
        ({}, None),
    ],
)
def test_the_cpu_temperature_is_the_cpu_package_and_nothing_else(sensors, expected):
    assert resources.pick_cpu_temperature(sensors) == expected


def _counting_machine():
    """A machine whose counters run at 1 MiB/s down and 4 MiB/s of writes."""
    clock = {"t": 100.0, "rx": 0.0, "w": 0.0}

    def system():
        clock["t"] += 0.5
        clock["rx"] += 512 * 1024
        clock["w"] += 2 * 1024 * 1024
        return Reading(
            cpu=0.5, cpu_count=4, memory_used=GB, memory_total=4 * GB, at=clock["t"],
            net_bytes=(clock["rx"], 1000.0), disk_bytes=(0.0, clock["w"]),
        )

    return clock, system


def test_rates_are_worked_out_from_the_counters_between_readings():
    _clock, system = _counting_machine()
    sampler = ResourceSampler(system=system, gpus=lambda total: ([], "no GPU found"))
    first = sampler.sample_once()
    assert first.net_rx is None, "one reading is not a rate"
    second = sampler.sample_once()
    assert second.net_rx == pytest.approx(1024 * 1024)
    assert second.net_tx == pytest.approx(0.0)
    assert second.disk_write == pytest.approx(4 * 1024 * 1024)
    assert second.disk_read == pytest.approx(0.0)


def test_a_counter_that_goes_backwards_is_an_unknown_rate_not_a_negative_one():
    readings = iter([
        Reading(at=1.0, net_bytes=(5_000_000.0, 10.0)),
        Reading(at=1.5, net_bytes=(1_000.0, 20.0)),  # the interface was reset
    ])
    sampler = ResourceSampler(system=lambda: next(readings), gpus=lambda total: ([], ""))
    sampler.sample_once()
    reset = sampler.sample_once()
    assert reset.net_rx is None
    assert reset.net_tx == pytest.approx(20.0)


def test_the_history_is_kept_and_kept_bounded():
    _clock, system = _counting_machine()
    sampler = ResourceSampler(system=system, gpus=lambda total: ([], ""))
    for _ in range(resources.HISTORY + 25):
        sampler.sample_once(with_gpus=False)
    history = sampler.history()
    assert len(history) == resources.HISTORY
    assert history[-1] is sampler.latest()
    assert [r.at for r in history] == sorted(r.at for r in history)


@pytest.mark.parametrize(
    "value, shown",
    [(None, "—"), (0, "0"), (850, "850"), (12_345, "12K"), (3.4 * 1024 ** 2, "3.4M"), (2 * 1024 ** 3, "2.0G")],
)
def test_a_rate_fits_its_column(value, shown):
    assert resources.rate(value) == shown


@pytest.mark.parametrize(
    "seconds, shown",
    [(None, "—"), (59, "0m"), (42 * 60, "42m"), (5 * 3600 + 12 * 60, "5h12m"), (3 * 86400 + 4 * 3600, "3d04h")],
)
def test_an_uptime_fits_its_column(seconds, shown):
    assert resources.duration(seconds) == shown


class _BusyMachine(_Machine):
    """The whole machine reporting: cores, swap, traffic, load, battery."""

    def __init__(self, gpus=(), swap=True):
        super().__init__(gpus=gpus)
        self.swap = swap
        self._t = 0.0

    def system(self) -> Reading:
        with self._lock:
            self.system_reads += 1
            self._t += 0.5
            t = self._t
        return Reading(
            cpu=0.5, cpu_count=8, memory_used=8 * GB, memory_total=32 * GB,
            cores=(0.1, 0.9, 0.5, 0.5, 0.2, 1.0, 0.0, 0.3), cpu_freq=3600.0, cpu_temp=71.0,
            load=(2.5, 1.75, 1.0), processes=321, uptime=90061.0, battery=0.42, charging=True,
            swap_used=GB if self.swap else None, swap_total=4 * GB if self.swap else None,
            net_bytes=(t * 2e6, t * 1e4), disk_bytes=(t * 3e6, t * 5e5), at=t,
        )


def _busy(monkeypatch, **kwargs):
    gpu = GpuReading(
        index=0, name="NVIDIA GeForce RTX 4090", vendor="nvidia", utilization=0.9,
        memory_used=20 * GB, memory_total=24 * GB, temperature=81.0,
        power=402.0, power_limit=450.0, clock=2520.0, fan=0.62,
    )
    fake = _BusyMachine(gpus=[gpu], **kwargs)
    monkeypatch.setattr(resources, "sample_system", fake.system)
    monkeypatch.setattr(resources, "probe_gpus", fake.probe)
    return fake


async def _read(pilot, monitor, rates: bool = True):
    for _ in range(80):
        await pilot.pause(0.05)
        reading = monitor.reading
        if reading is not None and (not rates or reading.net_rx is not None):
            break
    await _settle(pilot)


def test_the_readouts_cover_the_whole_machine(monkeypatch):
    _busy(monkeypatch)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 80)) as pilot:
            monitor = _monitor(app)
            await _read(pilot, monitor)
            text = _plain(monitor)
            for figure in (
                "50% 71°", "3.6GHz", "8.0/32G", "SWP", "1.0/4.0G",
                "NET ↓", "DSK r", "LOAD 2.50 1.75 1.00", "321 PROC",
                "UP 1d01h", "BAT 42%", "↯",
                "GPU0", "90% 81° 20/24G", "402/450W", "2.5GHz", "fan 62%",
            ):
                assert figure in text, figure
            # One column per core under the CPU bar.
            strip = text.split("\n")[2]
            assert sum(ch in resources.EIGHTHS[1:] for ch in strip) == 8

    _run(scenario())


def test_a_machine_without_swap_gets_no_swap_line(monkeypatch):
    _busy(monkeypatch, swap=False)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 80)) as pilot:
            monitor = _monitor(app)
            await _read(pilot, monitor, rates=False)
            assert "SWP" not in _plain(monitor)
            assert monitor.size.height == len(_plain(monitor).split("\n"))

    _run(scenario())


# ── Choosing a style ─────────────────────────────────────────────────────


def _styles_table(app):
    from textual.widgets import DataTable

    return app.query_one("#monitor-style-table", DataTable)


def test_the_panel_lists_every_style_and_marks_the_one_in_force(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            table = _styles_table(app)
            names = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
            assert [n.replace("▶", "").strip() for n in names] == list(resources.STYLE_NAMES)
            assert [n for n in names if n.startswith("▶")] == ["▶ neural"]

    _run(scenario())


def test_selecting_a_style_draws_the_monitor_in_it_and_remembers(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            app.show_pane("panel")
            await _settle(pilot)
            table = _styles_table(app)
            row = list(resources.STYLE_NAMES).index("orrery")
            table.focus()
            table.move_cursor(row=row)
            await pilot.press("enter")
            await _settle(pilot)
            assert _monitor(app).style_name == "orrery"
            assert read_settings().resource_style == "orrery"
            assert str(table.get_row_at(row)[0]).startswith("▶")

    _run(scenario())


def test_the_preview_follows_the_cursor_and_the_monitor_does_not(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            app.show_pane("panel")
            await _settle(pilot)
            preview = app.query_one("#monitor-preview", ResourceMonitor)
            table = _styles_table(app)
            table.move_cursor(row=list(resources.STYLE_NAMES).index("rain"))
            await _settle(pilot)
            assert preview.style_name == "rain"
            assert _monitor(app).style_name == "neural"
            assert read_settings().resource_style == "neural"
            # One sampler serves both, so the machine is not read twice.
            assert preview.sampler is _monitor(app).sampler

    _run(scenario())


def test_a_style_chosen_in_another_window_is_followed(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            write_setting(KEY_RESOURCE_STYLE, "tide")
            app._adopt_external_settings()
            await _settle(pilot)
            assert _monitor(app).style_name == "tide"

    _run(scenario())


def test_a_style_this_version_does_not_know_opens_as_the_default(machine):
    write_setting(KEY_RESOURCE_STYLE, "hologram")
    assert read_settings().resource_style == resources.DEFAULT_STYLE

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            assert _monitor(app).style_name == resources.DEFAULT_STYLE

    _run(scenario())


def test_the_console_stops_the_shared_sampler_when_it_closes(machine):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            await _settle(pilot)
            sampler = app.resource_sampler
            assert not sampler.stopped
        assert sampler.stopped

    _run(scenario())


def test_the_preview_opens_on_the_style_in_force(machine):
    write_setting(KEY_RESOURCE_STYLE, "scope")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 60)) as pilot:
            app.show_pane("panel")
            await _settle(pilot)
            preview = app.query_one("#monitor-preview", ResourceMonitor)
            assert preview.style_name == "scope"
            assert _styles_table(app).cursor_row == list(resources.STYLE_NAMES).index("scope")

    _run(scenario())


def test_every_readout_fits_its_column_even_on_a_busy_box(machine):
    """Text past the column is cropped from the right — where the last figure is.

    A line gives up its spacing and its words before a figure: a busy
    machine's disk-write rate and process count are the ends of their lines.
    """
    from rich.cells import cell_len

    big = GpuReading(
        index=7, name="NVIDIA GeForce RTX 5090 Founders Edition", vendor="nvidia",
        utilization=1.0, memory_used=31.9 * GB, memory_total=32 * GB, temperature=100.0,
        power=1000.0, power_limit=1000.0, clock=2520.0, fan=1.0,
    )
    busy = Reading(
        cpu=1.0, cpu_count=128, memory_used=1000 * GB, memory_total=1024 * GB,
        cores=(1.0,) * 128, cpu_freq=5800.0, cpu_temp=99.0, load=(64.12, 60.0, 155.31),
        swap_used=63 * GB, swap_total=64 * GB, processes=12345, uptime=400 * 86400.0,
        battery=1.0, charging=True, net_rx=9.9 * 1024 ** 2, net_tx=9.9 * 1024 ** 2,
        disk_read=9.9 * 1024 ** 2, disk_write=9.9 * 1024 ** 2, gpus=(big,),
    )

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(160, 80)) as pilot:
            await _settle(pilot)
            for mode in ("bench", "dos"):
                if mode == "dos":
                    app._display_mode = "dos"
                    app._apply_display_mode()
                    await _settle(pilot)
                monitor = _monitor(app)
                monitor.set_reading(busy)
                width = monitor.size.width
                lines = monitor.render().plain.split("\n")
                header = lines[: 1 + monitor.header_rows(busy)]
                for line in header:
                    assert cell_len(line) <= width, (mode, width, line)
                text = "\n".join(header)
                for figure in ("w9.9M", "12345", "155", "BAT 100%", "↯1000/1000W", "100%"):
                    assert figure in text, (mode, figure)

    _run(scenario())
