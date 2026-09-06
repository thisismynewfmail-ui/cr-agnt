"""Six reported faults in the console, and what each of them actually was.

* **The context gauge never moved.** Its needle was written once per turn
  from ``gauge.value`` — its own reading — with only the *ceiling* coming
  from anywhere real. So it was set on every turn and could never change.
* **The workings had no switch.** The fold is shut by default on purpose, and
  a reader who would rather watch the work than the answer had no way to say
  so.
* **F1 opened the command index.** It is the lettering key now; the index
  moved to ^O rather than going away.
* **The console did not reach the bottom of the window**, and the taller the
  window the more of the old terminal showed underneath it.
* **The gauge and the tape were welded together**, so a numeric scale and a
  fill bar read as one instrument.
* **The chat window's scroll bar could not be turned off.**

Grouped in one file because five of the six are the same kind of change — a
preference the console reads, writes, applies and syncs — and the sixth (the
gauge) shares the instrument stack with the fifth.
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui import indicators  # noqa: E402
from curie_cli.bench_ui.agent_bridge import AgentBridge  # noqa: E402
from curie_cli.bench_ui.app import (  # noqa: E402
    KEYLINE,
    KEYLINE_EXITS,
    BenchConsole,
    SIZE_ENV,
    live_terminal_size,
)
from curie_cli.bench_ui.instruments import DialGauge, TapeMeter  # noqa: E402
from curie_cli.bench_ui.panes import ToggleSwitch  # noqa: E402
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_SCROLLBARS,
    KEY_WORKINGS_OPEN,
    read_settings,
    write_setting,
)
from curie_cli.bench_ui.typeface import (  # noqa: E402
    CP437,
    DEFAULT_TYPEFACE,
    is_default_typeface,
    resolve_typeface,
    typeface_names,
)

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 5) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """A config file nobody else is writing to."""
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


def _console(mode: str = dos.MODE_BENCH, bridge=None) -> BenchConsole:
    app = BenchConsole(bridge=bridge if bridge is not None else _StubBridge())
    app._display_mode = mode
    app._optics = dos.Optics(mode=mode)
    app.bench_palette = app._resolve_palette()
    return app


# ── The context gauge ────────────────────────────────────────────────────


class _Compressor:
    def __init__(self, last_prompt_tokens=0, context_length=200_000):
        self.last_prompt_tokens = last_prompt_tokens
        self.context_length = context_length


class _Agent:
    """Just enough agent for the three places the reading comes from."""

    def __init__(self, compressor=None, messages=None, anchor=None):
        self.context_compressor = compressor
        self._session_messages = messages if messages is not None else []
        self._turn_base_usage_anchor = anchor
        self.context_length = 200_000


def _bridge_with(agent, history=None) -> AgentBridge:
    bridge = AgentBridge()
    bridge._agent = agent
    if history is not None:
        bridge._history = history
    return bridge


def test_a_bridge_with_no_agent_has_nothing_to_report():
    """``None`` rather than zero: an unbuilt agent is not an empty context."""
    assert AgentBridge().context_usage() is None


def test_the_reading_is_the_provider_s_own_count():
    bridge = _bridge_with(_Agent(_Compressor(48_000, 128_000)))
    assert bridge.context_usage() == (48_000, 128_000)


def test_the_post_compaction_sentinel_is_never_rendered():
    """``last_prompt_tokens`` is parked at -1 for one turn after a compression.

    Rendered raw it drew a needle below the scale's own zero and a readout of
    ``-1``, which reads as a broken instrument rather than as a conversation
    that has just been made smaller.
    """
    bridge = _bridge_with(_Agent(_Compressor(-1, 128_000)))
    tokens, ceiling = bridge.context_usage()
    assert tokens == 0 and ceiling == 128_000


def test_the_anchored_figure_wins_over_the_last_request():
    """The sawtooth case, and the reason the anchor exists.

    On a reasoning model a long tool loop replays the whole turn's thinking on
    every request, so the last request's prompt count can run far above the
    durable transcript — all of which evaporates at the turn boundary. A
    needle driven from the raw count climbs and then slams back, which reads
    as a compaction that failed.
    """
    messages = [{"role": "user", "content": "hello"}]
    anchor = {
        "prompt_tokens": 12_000,
        "completion_tokens": 500,
        "base_count": 1,
        "base_last_id": id(messages[0]),
        "base_last_role": "user",
    }
    bridge = _bridge_with(
        _Agent(_Compressor(410_000, 128_000), messages=messages, anchor=anchor)
    )
    tokens, _ceiling = bridge.context_usage()
    assert tokens == 12_500, "the replayed request was rendered, not the turn"


def test_a_stale_anchor_falls_back_rather_than_lying():
    """A compaction replaces the message the anchor fingerprinted."""
    messages = [{"role": "user", "content": "hello"}]
    anchor = {
        "prompt_tokens": 12_000,
        "completion_tokens": 500,
        "base_count": 1,
        "base_last_id": 1,  # not this list's
        "base_last_role": "user",
    }
    bridge = _bridge_with(
        _Agent(_Compressor(90_000, 128_000), messages=messages, anchor=anchor)
    )
    assert bridge.context_usage() == (90_000, 128_000)


def test_a_provider_that_reports_no_usage_still_moves_the_needle():
    """Some OpenAI-compatible endpoints omit usage entirely.

    A rough estimate is the least accurate of the three sources and the only
    one that beats a needle pinned at zero for the whole conversation.
    """
    history = [
        {"role": "user", "content": "x" * 4_000},
        {"role": "assistant", "content": "y" * 4_000},
    ]
    bridge = _bridge_with(_Agent(_Compressor(0, 128_000)), history=history)
    tokens, ceiling = bridge.context_usage()
    assert ceiling == 128_000
    assert tokens > 1_000, tokens


def test_the_rough_estimate_is_taken_once_per_shape_of_history():
    """It is read on a one-second timer and it walks the whole conversation."""
    history = [{"role": "user", "content": "x" * 4_000}]
    bridge = _bridge_with(_Agent(_Compressor(0, 128_000)), history=history)
    first = bridge.context_usage()
    calls = []
    import agent.model_metadata as meta

    real = meta.estimate_messages_tokens_rough
    meta.estimate_messages_tokens_rough = lambda *a, **k: (
        calls.append(1) or real(*a, **k)
    )
    try:
        assert bridge.context_usage() == first
        assert calls == [], "the walk was repeated for an unchanged history"
        history.append({"role": "assistant", "content": "y" * 4_000})
        assert bridge.context_usage()[0] > first[0]
        assert calls, "the walk was skipped for a history that had grown"
    finally:
        meta.estimate_messages_tokens_rough = real


def test_a_ceiling_nobody_can_name_is_not_a_reading():
    """A gauge with no scale has nothing to say, so it says nothing."""
    agent = _Agent(_Compressor(9_000, 0))
    agent.context_length = 0
    bridge = _bridge_with(agent)
    assert bridge.context_usage() is None


class _MeteredBridge(_StubBridge):
    """A stub whose context reading the test drives directly."""

    def __init__(self, reading=None):
        super().__init__()
        self.reading = reading

    def context_usage(self):
        return self.reading


def test_the_needle_follows_the_conversation():
    """The reported fault: it was written every turn and never moved."""

    async def scenario():
        bridge = _MeteredBridge((0, 200_000))
        app = _console(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            gauge = app.query_one("#gauge-context", DialGauge)
            bridge.reading = (50_000, 200_000)
            app._tick_context()
            await pilot.pause()
            assert gauge.value == 50_000
            assert "25%" in gauge.render().plain
            bridge.reading = (150_000, 200_000)
            app._tick_context()
            await pilot.pause()
            assert gauge.value == 150_000
            assert "75%" in gauge.render().plain

    _run(scenario())


def test_a_reading_that_cannot_be_taken_holds_the_needle():
    """There is no moment where the context becomes unknown *and* empty."""

    async def scenario():
        bridge = _MeteredBridge((80_000, 200_000))
        app = _console(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            gauge = app.query_one("#gauge-context", DialGauge)
            app._tick_context()
            await pilot.pause()
            assert gauge.value == 80_000
            bridge.reading = None
            app._tick_context()
            await pilot.pause()
            assert gauge.value == 80_000, "the needle was dropped to zero"

    _run(scenario())


def test_a_bridge_that_raises_does_not_take_the_console_with_it():
    """It is called on a timer while a worker thread writes what it reads."""

    class _Broken(_StubBridge):
        def context_usage(self):
            raise RuntimeError("mid-turn")

    async def scenario():
        app = _console(bridge=_Broken())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app._tick_context()
            await pilot.pause()
            assert app.is_running

    _run(scenario())


def test_the_gauge_is_read_on_its_own_clock():
    """Not on the instrument tick: its reading costs more than the others'."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            assert any(
                getattr(timer, "_callback", None)
                and getattr(timer._callback, "__name__", "") == "_tick_context"
                for timer in app._timers
            ), "nothing is driving the context gauge"

    _run(scenario())


# ── The gap between the gauge and the tape ───────────────────────────────


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_gauge_and_the_tape_are_two_instruments(mode):
    """Flush, they read as one: a fill bar sitting under a numeric scale.

    Every other pair in the stack is separated by a stencilled title. These
    two deliberately carry none — a needle and a fill bar are read from their
    shape — so the row of air is what has to do the separating.
    """

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            gauge = app.query_one("#gauge-context", DialGauge).region
            tape = app.query_one("#tape-turn", TapeMeter).region
            gap = tape.y - (gauge.y + gauge.height)
            assert gap == 1, f"{gap} rows between the two instruments"

    _run(scenario())


# ── The workings switch ──────────────────────────────────────────────────


def test_the_workings_are_shut_out_of_the_box():
    """The fold's whole argument. The switch is the deliberate act."""
    assert read_settings().workings_open is False


def test_a_fold_opens_shut_by_default():
    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            fold = app._bench().fold()
            await pilot.pause()
            assert fold.collapsed is True

    _run(scenario())


def test_the_switch_opens_the_fold_the_reader_is_looking_at():
    """A switch whose effect waits for the next tool call did nothing."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            standing = pane.fold()
            standing.add_tool("read_file")
            await pilot.pause()
            assert standing.collapsed is True

            app.run_keyline_action("workings-open")
            await _settle(pilot)
            assert standing.collapsed is False, "the fold on screen did not move"

            pane._seal_fold()
            later = pane.fold()
            await pilot.pause()
            assert later.collapsed is False, "the next fold opened shut"

            app.run_keyline_action("workings-open")
            await _settle(pilot)
            assert standing.collapsed is True and later.collapsed is True

    _run(scenario())


def test_the_switch_is_written_down_and_read_back():
    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("workings-open")
            await _settle(pilot)
        assert read_settings().workings_open is True

        second = _console()
        async with second.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            assert second._workings_open is True
            assert second._bench().fold().collapsed is False

    _run(scenario())


def test_a_fold_built_before_the_setting_arrives_still_honours_it():
    """Start-up order: the pane is composed before ``on_mount`` runs."""
    write_setting(KEY_WORKINGS_OPEN, True)

    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            assert app._bench()._workings_open is True

    _run(scenario())


# ── The scroll-bar switch ────────────────────────────────────────────────


def test_the_scroll_bar_is_on_out_of_the_box():
    """Hiding it is the deliberate act: it is what says how much is above."""
    assert read_settings().scrollbars is True


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_switch_takes_the_bar_away_and_gives_the_column_back(mode):
    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            # Filled, because ``overflow-y: auto`` means a short conversation
            # has no bar for the switch to take away — and the column the
            # switch is spending is only spent once there is one.
            pane = app._bench()
            for index in range(60):
                pane.write("reply", f"line {index} of the conversation body")
            await _settle(pilot)
            transcript = app.query_one("#transcript")
            assert transcript.show_vertical_scrollbar, "precondition: a bar"
            wide = transcript.scrollable_content_region.width
            assert transcript.styles.scrollbar_size_vertical > 0

            app.run_keyline_action("scrollbars")
            await _settle(pilot)
            assert transcript.has_class("-no-scrollbar")
            assert transcript.styles.scrollbar_size_vertical == 0
            assert transcript.scrollable_content_region.width > wide, (
                "the column the bar was using was not given back"
            )

            app.run_keyline_action("scrollbars")
            await _settle(pilot)
            assert not transcript.has_class("-no-scrollbar")
            assert transcript.scrollable_content_region.width == wide

    _run(scenario())


def test_hiding_the_bar_does_not_stop_the_window_scrolling():
    """``scrollbar-size: 0`` rather than ``overflow: hidden``.

    Hidden would strand a reader at the bottom of a long conversation, which
    is not what the switch says it does.
    """

    async def scenario():
        app = _console()
        async with app.run_test(size=(110, 30)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for index in range(60):
                pane.write("reply", f"line {index} of the conversation body")
            app.run_keyline_action("scrollbars")
            await _settle(pilot)
            transcript = app.query_one("#transcript")
            assert transcript.max_scroll_y > 0
            transcript.scroll_to(y=0, animate=False)
            await _settle(pilot)
            assert transcript.scroll_offset.y == 0
            transcript.scroll_to(y=transcript.max_scroll_y, animate=False)
            await _settle(pilot)
            assert transcript.scroll_offset.y > 0

    _run(scenario())


def test_the_scroll_bar_switch_survives_a_restart():
    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("scrollbars")
            await _settle(pilot)
        assert read_settings().scrollbars is False

        second = _console()
        async with second.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            assert second.query_one("#transcript").has_class("-no-scrollbar")

    _run(scenario())


# ── Both switches, on the panel and across two windows ───────────────────


def test_the_panel_shows_both_switches_where_they_actually_are():
    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            thrown = {
                switch.switch_key: switch.is_on
                for switch in app.query(ToggleSwitch)
            }
            assert thrown["workings-open"] is False
            assert thrown["scrollbars"] is True

            app.run_keyline_action("workings-open")
            app.run_keyline_action("scrollbars")
            await _settle(pilot)
            thrown = {
                switch.switch_key: switch.is_on
                for switch in app.query(ToggleSwitch)
            }
            assert thrown["workings-open"] is True
            assert thrown["scrollbars"] is False

    _run(scenario())


def test_a_second_window_catches_up_with_both():
    """The same sync path the skin and the indicator set already ride."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 44)) as pilot:
            await _settle(pilot)
            assert app._workings_open is False and app._scrollbars is True

            write_setting(KEY_WORKINGS_OPEN, True)
            write_setting(KEY_SCROLLBARS, False)
            app._adopt_external_settings()
            await _settle(pilot)

            assert app._workings_open is True
            assert app._scrollbars is False
            assert app.query_one("#transcript").has_class("-no-scrollbar")
            assert app._bench().fold().collapsed is False

    _run(scenario())


# ── F1, the lettering, and where the index went ──────────────────────────


def test_f1_is_no_longer_the_command_index():
    actions = {binding.key: binding.action for binding in BenchConsole.BINDINGS}
    assert actions["f1"] == "keyline('typeface_default')"
    assert actions["ctrl+o"] == "keyline('help')"
    assert "keyline('help')" not in {
        action for key, action in actions.items() if key == "f1"
    }


def test_the_key_line_advertises_both_where_they_now_are():
    caps = {cap: (label, action) for cap, label, action, _w in KEYLINE}
    assert caps["F1"] == ("TYPE", "typeface_default")
    assert caps["^O"] == ("HELP", "help")


def test_the_key_line_still_fits_the_window_that_shows_every_cap():
    """A row wider than its window does not wrap; it loses the caps after it."""
    # Every width the console reflows through, not three samples of it: the
    # row is a ladder of thresholds and a cap added in the middle of it is
    # paid for by the caps around it, so the arithmetic has to hold at every
    # rung rather than at the two ends.
    for width in range(46, 241):
        used = sum(
            len(cap) + len(label) + 3
            for cap, label, _a, min_width in KEYLINE
            if width >= min_width
        )
        used += sum(
            len(cap) + len(label) + 3
            for cap, label, _a, min_width in KEYLINE_EXITS
            if width >= min_width
        )
        assert used <= width, f"the key line overhangs a {width}-column window"


def test_the_index_is_still_reachable_and_says_where_it_lives():
    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            await pilot.press("ctrl+o")
            await _settle(pilot)
            assert app.active_pane == "bench"
            from tests.curie_cli.bench_ui.test_console import _transcript_text

            written = _transcript_text(app)
            assert "COMMAND INDEX" in written
            assert "Ctrl+O" in written, "the index does not name its own key"
            assert "lettering" in written, "F1 is not described"

    _run(scenario())


def test_the_face_describes_the_alphabet_the_console_actually_draws_with():
    """Otherwise "put the lettering back" puts back something else."""
    assert CP437.shades == indicators.SHADE
    assert CP437.columns == indicators.COLUMNS
    assert CP437.quadrants == indicators.QUADRANTS


def test_a_face_this_build_cannot_letter_with_resolves_to_the_one_it_can():
    for junk in (None, "", "  ", 7, "helvetica", ["default"]):
        assert resolve_typeface(junk).name == DEFAULT_TYPEFACE
    assert typeface_names() == [DEFAULT_TYPEFACE]
    assert is_default_typeface("DEFAULT ") is True
    # A name that *resolves* to the default is not the default: it is the
    # wrong thing to have written down, and F1 is what puts it right.
    assert is_default_typeface("helvetica") is False


def test_f1_puts_a_hand_edited_face_back():
    """The case the key exists for: a name in config.yaml nothing can letter.

    Stored verbatim rather than normalised away — see
    :func:`curie_cli.bench_ui.settings.read_settings` — because a font is
    named by a path or by a file name and neither is a value this build can
    hold a list of. Which is exactly why F1 has to be able to undo one.
    """
    write_setting("ui.typeface", "helvetica")
    assert read_settings().typeface == "helvetica", "the spec is kept as written"
    assert read_settings().face().problem, "and it does not resolve to a font"

    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app._typeface = "helvetica"
            await pilot.press("f1")
            await _settle(pilot)
            assert app._typeface == DEFAULT_TYPEFACE
            from curie_cli.bench_ui.settings import _config, _dig

            assert _dig(_config(), "ui", "typeface") == DEFAULT_TYPEFACE

    _run(scenario())


def test_f1_says_what_it_did():
    async def scenario():
        app = _console()
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("f1")
            await _settle(pilot)
            notice = app.query_one("#notice")
            assert not notice.has_class("hidden")

    _run(scenario())


# ── The strip of terminal along the bottom ───────────────────────────────


def _measurable(monkeypatch, yes: bool = True) -> None:
    monkeypatch.setattr(
        "curie_cli.bench_ui.app.terminal_answers_for_itself", lambda: yes
    )


def test_a_stale_size_in_the_environment_is_taken_out_of_the_way(monkeypatch):
    """The fault: Textual reads ``COLUMNS``/``LINES`` before the ioctl.

    ``shutil.get_terminal_size`` — which is what Textual's Linux driver asks,
    at start-up *and* on every SIGWINCH — prefers those two variables and only
    falls through to the terminal when they are unset. Exported anywhere up
    the chain, they pin the console at whatever size was current then: grow
    the window and the console lays out to the same frozen numbers, leaving
    the rows it never claimed showing whatever was on the screen before it
    started, and more of them the taller the window gets.
    """
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    _measurable(monkeypatch)
    with live_terminal_size():
        assert "COLUMNS" not in os.environ
        assert "LINES" not in os.environ
    assert os.environ["COLUMNS"] == "80"
    assert os.environ["LINES"] == "24"


def test_the_size_textual_would_read_is_the_live_one(monkeypatch):
    """Stated as the question Textual actually asks, not as an env check."""
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    _measurable(monkeypatch)
    monkeypatch.setattr(
        os, "get_terminal_size", lambda fd=1: os.terminal_size((203, 61))
    )
    assert shutil.get_terminal_size() == (80, 24), "precondition: the stale read"
    with live_terminal_size():
        assert shutil.get_terminal_size() == (203, 61)


def test_the_variables_come_back_even_when_the_console_comes_apart(monkeypatch):
    """They are the shell's variables, not this program's."""
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    _measurable(monkeypatch)
    with pytest.raises(RuntimeError):
        with live_terminal_size():
            assert "LINES" not in os.environ
            raise RuntimeError("the console fell over")
    assert os.environ["LINES"] == "24"


def test_nothing_is_taken_away_when_the_terminal_cannot_answer(monkeypatch):
    """With no tty, ``shutil`` falls back to a flat 80×24.

    There the environment is the only real size information there is, and
    dropping it would trade a stale size for a wrong one.
    """
    monkeypatch.setenv("COLUMNS", "132")
    monkeypatch.setenv("LINES", "50")
    _measurable(monkeypatch, yes=False)
    with live_terminal_size():
        assert os.environ["COLUMNS"] == "132"
        assert os.environ["LINES"] == "50"


def test_a_resize_nobody_reported_is_noticed_anyway(monkeypatch):
    """Belt and braces: every other way the notification can go missing.

    A SIGWINCH that does not arrive, a multiplexer that swallows it, a
    terminal that negotiates in-band resize reporting and then sends none —
    the symptom is the one that was reported either way.
    """

    async def scenario():
        app = _console()
        async with app.run_test(size=(110, 30)) as pilot:
            await _settle(pilot)
            assert app.size == (110, 30)
            monkeypatch.setattr(
                "curie_cli.bench_ui.app.terminal_answers_for_itself", lambda: True
            )
            monkeypatch.setattr(
                os, "get_terminal_size", lambda fd=1: os.terminal_size((110, 48))
            )
            app._tick_window()
            await _settle(pilot)
            assert app.size == (110, 48), "the console kept the size it started at"
            # And the rows it has just been given are laid out, rather than
            # left as the strip of old terminal the fault was.
            assert app.query_one("#keyline").region.y == 47

    _run(scenario())


def test_the_watchdog_is_silent_when_nothing_has_moved(monkeypatch):
    """It posts only on a real difference, so applying one cannot start another."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(110, 30)) as pilot:
            await _settle(pilot)
            monkeypatch.setattr(
                "curie_cli.bench_ui.app.terminal_answers_for_itself", lambda: True
            )
            monkeypatch.setattr(
                os, "get_terminal_size", lambda fd=1: os.terminal_size((110, 30))
            )
            posted = []
            original = app.post_message
            app.post_message = lambda message: (
                posted.append(message) or original(message)
            )
            app._tick_window()
            await _settle(pilot)
            from textual import events as textual_events

            resizes = [
                message
                for message in posted
                if isinstance(message, textual_events.Resize)
            ]
            assert resizes == []

    _run(scenario())


def test_the_watchdog_says_nothing_when_the_terminal_cannot_be_asked(monkeypatch):
    """Under a test harness or a piped stdout there is no size to disagree with."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(110, 30)) as pilot:
            await _settle(pilot)
            _measurable(monkeypatch, yes=False)

            def _boom(*_a, **_k):
                raise AssertionError("the terminal was asked anyway")

            monkeypatch.setattr(os, "get_terminal_size", _boom)
            app._tick_window()
            await _settle(pilot)
            assert app.size == (110, 30)

    _run(scenario())


def test_an_environment_that_says_nothing_is_left_alone(monkeypatch):
    for name in SIZE_ENV:
        monkeypatch.delenv(name, raising=False)
    _measurable(monkeypatch)
    with live_terminal_size():
        assert not any(name in os.environ for name in SIZE_ENV)
    assert not any(name in os.environ for name in SIZE_ENV)


def test_only_one_of_the_two_being_set_is_still_the_same_fault(monkeypatch):
    """``LINES`` alone is enough: ``shutil`` needs *both* to skip the ioctl.

    It is the pair that has to go, because a run left holding one of them is
    a run where the next thing to export the other re-freezes the size.
    """
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.setenv("LINES", "24")
    _measurable(monkeypatch)
    with live_terminal_size():
        assert "LINES" not in os.environ
    assert os.environ["LINES"] == "24"
