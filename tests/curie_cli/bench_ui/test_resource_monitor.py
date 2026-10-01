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
            # The field itself: title, two bars, a caption, then the neurons.
            rows = text.split("\n")
            assert len(rows) == 4 + field_rows(1)
            assert monitor.size.height == len(rows)

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
            assert not any(glyph in text for glyph in (FIRING, DORMANT))

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
