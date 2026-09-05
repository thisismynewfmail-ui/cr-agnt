"""The bench console application.

Layout is a docked frame, which is what survives a resize: a title bar with
the wordmark, lamps and clock; a function-key line along the bottom; a rail of
labelled switches on the left; content in the middle; an instrument stack on
the right. Every one of those collapses in a defined order as the window
narrows, so the console stays usable from a 200-column terminal down to about
46 columns.

The visual language is a mid-century instrument panel — enamel ground,
stencilled labels, box-drawn chrome, signal-coloured lamps. Colours come from
the active skin, never from here.

There is a second one. :mod:`curie_cli.bench_ui.dos` re-skins the whole console
as an amber phosphor terminal — a one-row inverse title bar, a numbered menu
box, Norton's function-key bar, CP437 frames — and it is a *mode*, chosen on
the PANEL pane and remembered in ``config.yaml``, not a second application.
Everything below is written once and drawn twice: the same layout, the same
keys, the same events, the same instruments reading the same numbers. What the
mode changes is the palette they resolve against and the marks they paint
with, which is why turning it on cannot break a feature — there is nothing
underneath it to break.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from rich import box
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import DataTable, RichLog, Static, TextArea

from curie_cli.bench_ui import dos
from curie_cli.bench_ui.agent_bridge import AgentBridge
from curie_cli.bench_ui.indicators import (
    ERROR,
    READY,
    SAVING,
    STREAMING,
    THINKING,
    TOOL,
    WAITING,
    ActivityMonitor,
    RasterKit,
    get_kit,
    kit_names,
)
from curie_cli.bench_ui.instruments import (
    ChartLegend,
    DialGauge,
    PanelLamps,
    StripChart,
    TapeMeter,
)
from curie_cli.bench_ui.panes import (
    BenchPane,
    Composer,
    DiagnosticsPane,
    InstrumentsPane,
    LogbookPane,
    PanelPane,
    SupplyPane,
)
from curie_cli.bench_ui.settings import (
    KEY_DOS_BLOCK_CURSOR,
    KEY_DOS_GLOW,
    KEY_DOS_PHOSPHOR,
    KEY_DOS_SCANLINES,
    KEY_INDICATORS,
    KEY_SKIN,
    KEY_SKIN_MODE,
    BenchSettings,
    read_settings,
    restore_active_skin,
    write_setting,
)
from curie_cli.bench_ui.styles import BENCH_CSS
from curie_cli.bench_ui.theme import resolve_palette
from curie_cli.bench_ui.voice import VoiceDesk

# The rail. Each entry is (key, switch label, glyph, pane class).
#
# The names are the bench's own: a logbook is where you write down what you
# did, instruments are what you measure with, supply is the shelf you take
# reagents off, the panel is what you set the run up on. None of it is a
# metaphor for something else — that is the whole point.
SWITCHES: tuple[tuple[str, str, str, type], ...] = (
    ("bench", "BENCH", "▮", BenchPane),
    ("logbook", "LOGBOOK", "▤", LogbookPane),
    ("instruments", "INSTRUMENTS", "◷", InstrumentsPane),
    ("supply", "SUPPLY", "▣", SupplyPane),
    ("panel", "PANEL", "▥", PanelPane),
    ("diagnostics", "DIAGNOSTICS", "◑", DiagnosticsPane),
)

#: The function key each pane answers to. Kept beside :data:`SWITCHES`
#: because the DOS mode draws the rail as a numbered menu, and the number a
#: menu line carries has to be the key that throws it — the rail's *order* is
#: not that number (DIAGNOSTICS is sixth on the rail and F9 on the keyboard),
#: so deriving it from position would print a menu of keys that do not work.
#: ``test_dos_skin`` holds this against the console's own bindings.
PANE_KEYS: dict[str, int] = {
    "bench": 2,
    "logbook": 3,
    "instruments": 4,
    "supply": 5,
    "panel": 6,
    "diagnostics": 9,
}

# Below this many columns the rail shows glyphs only; below the second, it
# hides and the switches are reachable by their function keys alone.
RAIL_NARROW_AT = 84
RAIL_HIDDEN_AT = 58
INSTRUMENTS_HIDDEN_AT = 100

# Below this many columns the transcript drops the speaker's name to its
# glyph. Chosen against the *window*, not the reading column, because the
# rail and the instrument stack have already collapsed by the time it
# matters — and a name gutter is a sixth of a forty-column line.
ENTRY_COMPACT_AT = 96

# The function-key line: (cap, label, action, the narrowest window it is
# painted at). Every key works at every width — what a threshold removes is
# the painted reminder, not the binding — so they are ordered by how badly a
# reader needs reminding. Help and the chrome toggle stay to the end, because
# they are the two that get someone out of a state they did not mean to enter;
# stop and quit stay because they are the way out of the program.
#
# The widths are the row's own arithmetic: a cap costs len(cap) + len(label)
# + 3, and the console must never paint a key line wider than its window —
# an overhanging row does not wrap, it takes the caps after it off the end.
KEYLINE: tuple[tuple[str, str, str, int], ...] = (
    ("F1", "HELP", "help", 0),
    ("F2", "BENCH", "bench", 72),
    ("F3", "LOGBOOK", "logbook", 100),
    ("F6", "PANEL", "panel", 122),
    ("F7", "RAIL", "rail_toggle", 100),
    ("F8", "METERS", "instruments_toggle", 122),
    ("F10", "CHROME", "masthead_toggle", 0),
    # The two turn controls sit together, in the order a reader reaches for
    # them: ask again, or take it back.
    ("^G", "AGAIN", "regenerate", 72),
    ("^B", "BACK", "back", 72),
    ("F12", "NEW", "new_session", 100),
)

#: The keys on the right of the spacer — the two ways out. Stop stays at
#: every width; quit gives way at the narrowest, where the note that names
#: the pane keys is worth more than a reminder of a convention every terminal
#: program shares (and F1 lists it either way).
KEYLINE_EXITS: tuple[tuple[str, str, str, int], ...] = (
    ("^C", "STOP", "stop", 0),
    ("^Q", "QUIT", "quit", 56),
)

#: The note that advertises the pane keys when the rail is not there to show
#: them, longest first. Which one is painted depends on the room the keycaps
#: leave — measured, not guessed at, because the caps themselves come and go
#: with the width and a fixed threshold would be wrong at half of them.
KEYLINE_NOTES: tuple[str, ...] = (
    "F2-F6, F9 switch panes",
    "F2-F9 switch panes",
    "F2-F9 panes",
)

# How long a transient state holds the state instrument before it falls back
# to whatever the turn is actually doing. A write to the record is over in
# milliseconds and would never be seen at its true duration; a fault is worth
# holding for longer than that, because it is the one state a reader may have
# looked away from.
ACTIVITY_HOLD = {SAVING: 2.4, ERROR: 5.0}


# The double-line box from code page 437, which is what a text-mode program
# drew a title plate with.
_HEAVY_BOX = box.DOUBLE


class Switch(Static):
    """One labelled switch on the rail. Clickable; shows which is thrown."""

    def __init__(self, key: str, label: str, glyph: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.switch_key = key
        self.switch_label = label
        self.switch_glyph = glyph
        self.switch_number = PANE_KEYS.get(key, 0)
        self.add_class("switch")
        self.narrow = False

    def render(self) -> Text:
        palette = _palette_of(self)
        if palette is not None and palette.dos:
            # A numbered menu line, and the thrown one inverted. The rail's
            # own ``-active`` class colours a switch on the panel; on the
            # phosphor the highlight *is* the selection, so it is drawn into
            # the text where the number and the name can share it.
            return dos.rail_switch(
                self.switch_number,
                self.switch_label,
                self.switch_glyph,
                palette,
                active=self.has_class("-active"),
                narrow=self.narrow,
            )
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append(f" {self.switch_glyph} ")
        if not self.narrow:
            out.append(self.switch_label)
        return out

    def on_click(self) -> None:
        self.app.show_pane(self.switch_key)


class KeyCap(Static):
    """One clickable entry on the function-key line.

    ``min_width`` is the narrowest window the cap is painted at. It is a
    display threshold and nothing else: the key it advertises is bound at
    every width, so dropping the cap costs the reminder and never the
    control.
    """

    def __init__(
        self, cap: str, label: str, action: str, min_width: int = 0, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.cap = cap
        self.label = label
        self.key_action = action
        self.min_width = min_width
        self.add_class("keycap")

    @property
    def keycap_width(self) -> int:
        """Columns this cap occupies: its text plus one of padding a side."""
        return len(self.cap) + 1 + len(self.label) + 2

    def render(self) -> Text:
        palette = _palette_of(self)
        if palette is not None and palette.dos:
            return dos.keycap(self.cap, self.label, palette)
        out = Text(no_wrap=True)
        out.append(self.cap, style="bold")
        out.append(" ")
        out.append(self.label)
        return out

    def on_click(self) -> None:
        self.app.run_keyline_action(self.key_action)


def _palette_of(widget):
    """The console's palette, or None when there is no console to ask.

    Chrome widgets render during mount and during teardown, and both are
    moments at which ``widget.app`` can be absent or half-built. A widget that
    cannot find the palette draws its plain form rather than raising: an
    unstyled keycap is a keycap, and an exception in ``render`` is a
    traceback painted over the interface.
    """
    try:
        return widget.app.bench_palette
    except Exception:
        return None


class BenchConsole(App):
    """``curie ui`` — the bench console."""

    # One document, both modes. The DOS half is scoped under a class on the
    # screen (see :data:`curie_cli.bench_ui.dos.DOS_CSS`) rather than kept as
    # a second stylesheet, because Textual parses ``CSS`` once at start-up: a
    # mode that swapped documents could only change by rebuilding the app,
    # and two documents could drift apart. A class toggles in a frame.
    CSS = BENCH_CSS + dos.DOS_CSS
    TITLE = "Curie Agent"
    SUB_TITLE = "bench console"

    # Every one of these is ``priority``, which means the app is offered the
    # key before the focused widget. That is not belt-and-braces: the composer
    # is a ``TextArea``, and ``TextArea`` binds F6 to select-line and F7 to
    # select-all. Without priority those two keys never reached the console
    # while the cursor was in the composer — which is where it is essentially
    # always — so the PANEL switch silently did nothing and instead selected a
    # line of the message being typed. A function-key line that works only
    # when focus happens to be elsewhere is worse than none, so these keys
    # belong to the console at all times, the way a text-mode program's
    # function keys did.
    BINDINGS = [
        Binding("f1", "keyline('help')", "Help", show=False, priority=True),
        Binding("f2", "keyline('bench')", "Bench", show=False, priority=True),
        Binding("f3", "keyline('logbook')", "Logbook", show=False, priority=True),
        Binding("f4", "keyline('instruments')", "Instruments", show=False, priority=True),
        Binding("f5", "keyline('supply')", "Supply", show=False, priority=True),
        Binding("f6", "keyline('panel')", "Panel", show=False, priority=True),
        Binding("f7", "keyline('rail_toggle')", "Rail", show=False, priority=True),
        Binding("f8", "keyline('instruments_toggle')", "Meters", show=False, priority=True),
        Binding("f9", "keyline('diagnostics')", "Diagnostics", show=False, priority=True),
        Binding("f10", "keyline('masthead_toggle')", "Chrome", show=False, priority=True),
        Binding("f12", "keyline('new_session')", "New", show=False, priority=True),
        # The two turn controls are control keys, not function keys. F11 is
        # the window manager's fullscreen toggle in essentially every
        # terminal — GNOME Terminal, Konsole, xterm, Windows Terminal — so it
        # is swallowed before the application ever sees it, and a keycap
        # painted with a key that cannot arrive is worse than no keycap at
        # all. Both of these are among the few control keys ``TextArea`` does
        # not claim, so the composer keeps every editing key it had.
        Binding("ctrl+g", "keyline('regenerate')", "Again", show=False, priority=True),
        Binding("ctrl+b", "keyline('back')", "Back", show=False, priority=True),
        Binding("ctrl+c", "keyline('stop')", "Stop", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+l", "keyline('clear')", "Clear", show=False, priority=True),
        Binding(
            "ctrl+r", "keyline('reload_logbook')", "Reload logbook",
            show=False, priority=True,
        ),
    ]

    active_pane: reactive[str] = reactive("bench")

    def __init__(self, bridge: AgentBridge | None = None, **kwargs) -> None:
        # Every stored preference, read once, before anything is built from
        # it. The skin has to be put back into the skin engine here rather
        # than merely read: ``resolve_palette`` asks the engine what is
        # active, and nothing on the ``curie ui`` path had ever told it.
        settings = read_settings()
        restore_active_skin(settings)
        # Which of the two skins the console opens in, and — for the DOS one
        # — the tube it opens on. Held on the app rather than re-read from
        # config on every use: the reader can be moving the brightness
        # control while a frame is being drawn, and a control that answers
        # from the file is a control with a save between every step of it.
        self._display_mode = settings.skin_mode
        self._optics = settings.optics()
        # Resolved before ``super().__init__``: Textual builds its stylesheet
        # inside App.__init__ and calls ``get_css_variables()`` while doing
        # so, which needs the palette to already exist.
        self.bench_palette = self._resolve_palette()
        super().__init__(**kwargs)
        self.bridge = bridge if bridge is not None else AgentBridge()
        self._turn_started_at: float | None = None
        self._last_chars = 0
        self._last_reasoning_chars = 0
        self._last_tool_calls = 0
        self._chart_clicks = 0
        self._notice_until = 0.0
        self._streaming = False
        # F7. Independent of the width-driven collapse below: the rail can be
        # hidden by hand at any width, and stays hidden until asked back.
        self._rail_hidden = False
        # F10. The title plate and the key line are both chrome around the
        # conversation, so one key takes both away and gives their rows to it.
        self._chrome_hidden = False
        self._chrome_hint_shown = False

        # What the console is doing, as one of the indicator states. The base
        # is what the turn is doing; the overlay is a transient — a write to
        # the record, a fault — that holds for a moment and then gives the
        # instrument back.
        self._activity = READY
        self._activity_detail = ""
        self._activity_overlay: tuple[str, float] | None = None
        #: The tool calls running right now, oldest first, by name. A list
        #: rather than one name because a model can reach for several tools
        #: at once, and because a call that is *blocked* never reports
        #: finishing at all — a single slot held that name for the rest of
        #: the turn, and the state figure sat on TOOL, naming a tool that
        #: had already been refused, however much the turn went on to do.
        self._running_tools: list[str] = []

        self._kit_name = settings.indicators
        #: Whether the indicator set was ever actually chosen. The DOS mode
        #: offers its own native figure to a console that has never had one
        #: picked, and must not overrule a console that has.
        self._kit_chosen = settings.indicators_explicit
        self.voice = VoiceDesk(
            on_transcript=self._voice_transcript,
            on_status=self._voice_status,
        )

    # ── DOM access from timers ───────────────────────────────────────────

    def _bench(self) -> BenchPane | None:
        """The bench pane, or None when it is not mounted."""
        return self._maybe("#pane-bench", BenchPane)

    def _write(self, kind: str, text: str) -> None:
        """Append one block of conversation to the transcript.

        Entries are handed over as (kind, text) rather than as finished
        renderables so a skin change can re-render them — see
        :meth:`BenchPane.restyle`.
        """
        pane = self._bench()
        if pane is not None:
            pane.write(kind, text)

    def _maybe(self, selector: str, kind: type | None = None):
        """Return a widget, or None when it is not in the DOM right now.

        Timers keep firing after the app begins tearing down, and they keep
        firing while a pane is hidden. ``query_one`` raises ``NoMatches`` in
        both cases, and an exception from a timer callback is unhandled — it
        prints a traceback over the interface and can take the app down. The
        instruments are a readout: if the widget is gone there is simply
        nothing to update.
        """
        try:
            found = self.query(selector)
        except Exception:
            return None
        for node in found:
            if kind is None or isinstance(node, kind):
                return node
        return None

    # ── Palette injection ────────────────────────────────────────────────

    def get_css_variables(self) -> dict[str, str]:
        """Hand the palette to the stylesheet as ``$bench-*`` variables."""
        variables = super().get_css_variables()
        variables.update(self.bench_palette.as_css_variables())
        return variables

    def _resolve_palette(self):
        """The colours for whichever skin is in force.

        The two are resolved by different rules and that is the point: the
        bench palette is *derived* from whatever skin the CLI and the TUI are
        wearing, so it changes when they do; the DOS palette is *built*, from
        a tube and a brightness, so it looks the same wherever it is opened.
        A monitor is not themeable.
        """
        if self._display_mode == dos.MODE_DOS:
            return dos.resolve_palette(
                phosphor=self._optics.phosphor,
                glow=self._optics.glow,
                scanlines=self._optics.scanlines,
                block_cursor=self._optics.block_cursor,
            )
        return resolve_palette()

    @property
    def display_mode(self) -> str:
        """Which skin the console is drawing — ``bench`` or ``dos``."""
        return self._display_mode

    @property
    def optics(self):
        """The DOS mode's settings, whether or not the mode is on."""
        return self._optics

    # ── Layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # Structure, not dock ordering. Docking the rail to the left and the
        # instruments to the right of the *screen* made both claim the full
        # column height and paint straight over the top-docked title bar —
        # docks at different edges do not subtract from one another. A single
        # body row between the top and bottom docks, with the three columns as
        # ordinary siblings inside it, cannot get into that argument, and it
        # is also what makes the responsive collapse a matter of hiding one
        # sibling rather than re-deriving a layout.
        with Horizontal(id="titlebar"):
            yield Static(self._wordmark(), id="titlebar-mark")
            yield Static("", id="titlebar-subject")
            # Four lamps, and every one of them is a state the reader cannot
            # otherwise see: the console is up, an agent is loaded, a turn is
            # running, and something was written to the record (which pulses
            # and fades, because it is an event and not a condition).
            yield PanelLamps(
                lamps=(
                    ("MAINS", "on"),
                    ("BENCH", "off"),
                    ("LOG", "off"),
                    ("REC", "off"),
                ),
                id="titlebar-lamps",
            )
            yield Static("", id="titlebar-clock")

        with Horizontal(id="keyline"):
            for cap, label, action, min_width in KEYLINE:
                yield KeyCap(cap, label, action, min_width=min_width)
            yield Static("", id="keyline-spacer")
            for cap, label, action, min_width in KEYLINE_EXITS:
                yield KeyCap(cap, label, action, min_width=min_width)
            yield Static("", id="keyline-note")

        with Horizontal(id="body"):
            with Vertical(id="rail"):
                for key, label, glyph, _pane in SWITCHES:
                    switch = Switch(key, label, glyph, id=f"switch-{key}")
                    if key == self.active_pane:
                        switch.add_class("-active")
                    yield switch

            with Vertical(id="content"):
                yield Static("", id="notice", classes="hidden")
                for key, _label, _glyph, pane_cls in SWITCHES:
                    pane = pane_cls(id=f"pane-{key}", classes="pane")
                    pane.display = key == self.active_pane
                    yield pane

            with Vertical(id="instruments"):
                # State first. It is the instrument a reader looks at, and the
                # only one that answers the question they actually have while
                # a turn is running — not "how much", but "of what".
                yield Static("STATE", classes="instrument-title")
                yield ActivityMonitor(kit=get_kit(self._kit_name), id="activity")
                yield Static("OUTPUT", classes="instrument-title")
                yield StripChart("chars/s", id="chart-output")
                yield ChartLegend(id="chart-legend")
                # The gauge and the tape carry no stencilled title and no
                # label of their own. A needle over a 0-to-ceiling scale and
                # a fill bar are read from their shape; the words above them
                # were naming what the reader could already see, in a column
                # 26 columns wide where every row is spent on something.
                yield DialGauge(ceiling=100.0, id="gauge-context")
                yield TapeMeter("elapsed", id="tape-turn")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick_clock)
        self.set_interval(0.1, self._pump_agent)
        self.set_interval(0.5, self._tick_instruments)
        # Before the responsive pass: the DOS mode changes the rail's width
        # and the frames around the columns, and the collapse thresholds are
        # measured against what is actually painted.
        self._apply_display_mode()
        self._apply_responsive_layout(self.size.width)
        self._apply_kit(self._kit_name)
        self.query_one("#composer", Composer).focus()
        self.call_after_refresh(self._restore_voice)
        # Deferred one frame: the plate is drawn to the transcript's measured
        # width, which is zero until the first layout pass completes.
        self.call_after_refresh(self.paint_masthead)
        # Building the agent reads config, resolves the provider and loads
        # tools; doing it on mount would stall the first paint, so it happens
        # on a worker and the lamp lights when it lands.
        self.run_worker(self._warm_agent, thread=True, exclusive=False)

    def _warm_agent(self) -> None:
        ok = self.bridge.ensure_agent()
        self.call_from_thread(self._agent_ready, ok)

    def _agent_ready(self, ok: bool) -> None:
        # Delivered from a worker thread via call_from_thread, so it can land
        # after the app has begun tearing down.
        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.set_lamp("BENCH", "on" if ok else "blink")
        if ok:
            facts = self.bridge.describe()
            self._set_subject(facts)
            ceiling = facts.get("context_length")
            gauge = self._maybe("#gauge-context", DialGauge)
            if ceiling and gauge is not None:
                gauge.set_reading(0, ceiling)
        else:
            self._notify_panel(
                "No model is configured yet, so the bench cannot run a turn. "
                "Set one with `curie model`, then reopen this console."
                + (f"  ({self.bridge.load_error})" if self.bridge.load_error else "")
            )

    # ── Responsive layout ────────────────────────────────────────────────

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    def _apply_responsive_layout(self, width: int) -> None:
        """Collapse chrome in a fixed order as the window narrows.

        Instruments go first (they are a readout, not a control), then the
        rail's labels, then the rail itself. The transcript and composer are
        never collapsed: they are the reason the console exists.
        """
        try:
            rail = self.query_one("#rail")
            instruments = self.query_one("#instruments")
        except Exception:
            return

        instruments.set_class(width < INSTRUMENTS_HIDDEN_AT, "hidden")

        rail.display = width >= RAIL_HIDDEN_AT and not self._rail_hidden
        narrow = width < RAIL_NARROW_AT
        rail.set_class(narrow, "narrow")
        for switch in self.query(Switch):
            switch.narrow = narrow
            switch.refresh()

        pane = self._bench()
        if pane is not None:
            pane.set_compact(width < ENTRY_COMPACT_AT)

        # The key line, tier by tier. Hidden by width, not by the F10 toggle:
        # a keycap that has run off the end of the row is worse than one that
        # was never painted, because it takes the ones after it with it.
        for cap in self.query(KeyCap):
            cap.set_class(width < cap.min_width, "hidden")

        note = self.query_one("#keyline-note", Static)
        note.update(self._keyline_note(width) if not rail.display else "")

        # A frame's title has to fit the frame, and the frame's width is what
        # this pass has just changed.
        self._apply_frames()

    def _keyline_note(self, width: int) -> str:
        """The longest pane-key reminder that fits in what the caps leave.

        Measured rather than thresholded. The caps drop out one tier at a
        time as the window narrows, so the room left for the note is not a
        function of the width alone — and a note that overhangs the row takes
        the keycaps to its left off the end with it.
        """
        used = sum(
            cap.keycap_width for cap in self.query(KeyCap) if not cap.has_class("hidden")
        )
        room = width - used - 2
        for candidate in KEYLINE_NOTES:
            if len(candidate) <= room:
                return candidate
        return ""

    # ── Panes ────────────────────────────────────────────────────────────

    def show_pane(self, key: str) -> None:
        if key not in {k for k, _l, _g, _p in SWITCHES}:
            return
        self.active_pane = key
        for switch_key, _label, _glyph, _pane in SWITCHES:
            pane = self.query_one(f"#pane-{switch_key}")
            pane.display = switch_key == key
            switch = self.query_one(f"#switch-{switch_key}", Switch)
            switch.set_class(switch_key == key, "-active")
        if key == "bench":
            self.query_one("#composer", Composer).focus()

    def action_keyline(self, action: str) -> None:
        self.run_keyline_action(action)

    def run_keyline_action(self, action: str) -> None:
        if action == "quit":
            self.exit()
        elif action == "stop":
            if not self.bridge.interrupt():
                self._notify_panel("Nothing is running.")
        elif action == "clear":
            pane = self._bench()
            if pane is not None:
                pane.clear_transcript()
        elif action == "help":
            self.show_pane("bench")
            self._write_help()
        elif action == "instruments_toggle":
            instruments = self._maybe("#instruments")
            if instruments is not None:
                instruments.toggle_class("hidden")
        elif action == "rail_toggle":
            # An explicit hide, remembered across resizes: without the flag
            # the next resize event would recompute rail visibility from the
            # window width alone and put the rail straight back.
            self._rail_hidden = not self._rail_hidden
            self._apply_responsive_layout(self.size.width)
        elif action == "masthead_toggle":
            self._toggle_chrome()
        elif action == "new_session":
            self._start_new_session()
        elif action == "reload_logbook":
            self._reload_logbook()
        elif action == "regenerate":
            self._regenerate()
        elif action == "back":
            self._go_back()
        elif action == "voice-listen":
            self._toggle_listening()
        elif action == "voice-speak":
            self._toggle_speaking()
        elif action == "voice-refresh":
            self._refresh_voices()
        elif action == "dos-mode":
            self._toggle_display_mode()
        elif action == "dos-scanlines":
            self._toggle_scanlines()
        elif action == "dos-cursor":
            self._toggle_block_cursor()
        elif action == "dos-glow-up":
            self._step_glow(1)
        elif action == "dos-glow-down":
            self._step_glow(-1)
        else:
            self.show_pane(action)

    # ── The display mode ─────────────────────────────────────────────────

    def _apply_display_mode(self, announce: bool = False) -> None:
        """Redraw the console in whichever skin is now in force.

        A mode change is a re-skin, not a repaint, so it has to reach four
        different kinds of surface and every one of them is a place an earlier
        version of this got it wrong:

        * the **stylesheet**, through a class on the screen — the frames, the
          fills, the inverse-video bands;
        * the **chrome the app draws itself** — the wordmark, the clock, the
          title plate, the keycaps, the rail — none of which is styled by CSS
          because each is a ``Text`` built at render time;
        * the **conversation already on screen**, which holds its own colours
          per entry and would otherwise stay in the palette it was written in;
        * the **settings pane**, which is the one page the reader is looking
          at when they change this, and therefore the one page where an
          un-repainted widget is certain to be seen.

        Total: every step is guarded, because this runs from a click and a
        half-mounted DOM must not take the console down over an appearance
        setting.
        """
        self.bench_palette = self._resolve_palette()
        is_dos = self.bench_palette.dos

        try:
            self.screen.set_class(is_dos, "-dos")
        except Exception:
            # Called before a screen exists (tests construct the app without
            # running it). The class is applied again on mount.
            pass
        try:
            self.refresh_css()
        except Exception:
            pass

        mark = self._maybe("#titlebar-mark", Static)
        if mark is not None:
            mark.update(self._wordmark())
        self._set_subject(self.bridge.describe())
        self._tick_clock()
        self.paint_masthead()
        self._apply_frames()

        # The chrome the app draws itself. Both are ``Text`` built in
        # ``render``, so a refresh is what re-reads the palette.
        for widget in list(self.query(KeyCap)) + list(self.query(Switch)):
            widget.refresh()

        pane = self._bench()
        if pane is not None:
            pane.restyle(self.bench_palette)

        panel = self._maybe("#pane-panel", PanelPane)
        if panel is not None:
            try:
                panel.reload_display(self._settings_now())
                panel.refresh_display_switches(self._settings_now())
                panel.reload_kits(self._kit_name)
                panel.reload_voices()
            except Exception:
                pass
            self._sync_voice_switches()

        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.refresh()

        if announce:
            self._announce_mode()

    def _settings_now(self) -> BenchSettings:
        """The display settings as the console currently holds them.

        Handed to the settings pane instead of letting it re-read the file:
        the pane is redrawn on every step of the brightness control, and a
        file read per keypress would show the reader the *previous* step
        whenever a save was still in flight.
        """
        return BenchSettings(
            indicators=self._kit_name,
            indicators_explicit=self._kit_chosen,
            skin_mode=self._display_mode,
            dos_phosphor=self._optics.phosphor,
            dos_glow=self._optics.glow,
            dos_scanlines=self._optics.scanlines,
            dos_block_cursor=self._optics.block_cursor,
        )

    def _apply_frames(self) -> None:
        """Name the boxes the DOS mode draws, and un-name them when it does not.

        A double-line frame with a title set into its top rule is how a
        text-mode program said what a region was. The bench mode says it with
        a stencilled heading inside the region instead, so the border titles
        have to come *off* when the mode does — a title with no border under
        it does not render, but one left behind on a widget that gets a border
        again for another reason would.

        A title also comes off when its box is too narrow to hold it. Textual
        truncates one that does not fit, and a frame with ``…`` set into its
        top rule reads as a rendering fault rather than as a narrow window —
        the collapsed rail is five glyphs wide, and there is no four-letter
        abbreviation of a menu worth paying for that.
        """
        is_dos = self.bench_palette.dos
        for selector, title in (("#rail", "MAIN MENU"), ("#instruments", "METERS")):
            widget = self._maybe(selector)
            if widget is None:
                continue
            room = widget.outer_size.width - 4
            widget.border_title = title if is_dos and room >= len(title) else ""

    def _toggle_display_mode(self) -> None:
        """The PANEL switch: change skin, apply it, and write it down."""
        self._display_mode = (
            dos.MODE_BENCH if self._display_mode == dos.MODE_DOS else dos.MODE_DOS
        )
        self._optics = dos.Optics(
            mode=self._display_mode,
            phosphor=self._optics.phosphor,
            glow=self._optics.glow,
            scanlines=self._optics.scanlines,
            block_cursor=self._optics.block_cursor,
        )
        problem = write_setting(KEY_SKIN_MODE, self._display_mode)
        self._adopt_native_kit()
        self._apply_display_mode()
        self._announce_mode(problem)

    def _adopt_native_kit(self) -> None:
        """Offer the DOS mode its own indicator set — once, and never over a choice.

        The state figures are the one part of the console that is *chosen*
        rather than themed, so the mode cannot simply take them: a reader who
        picked the Lissajous set picked it. But a reader still on the set
        Curie ships with is on it because it is the default, and the default
        was authored for the panel. So the swap happens exactly there, and the
        moment it does the stored set stops being the default — which is why
        it happens once and not on every toggle.

        Only on the way *in*. Taking it back on the way out would be the mode
        reaching into a setting after the fact, and it would make the same
        toggle behave differently before and after a restart: by then the
        stored set is ``raster``, which is a choice as far as any later run
        can tell. Marking it chosen here is what makes the two agree.
        """
        if self._kit_chosen or self._display_mode != dos.MODE_DOS:
            return
        wanted = RasterKit.name
        if wanted == self._kit_name or wanted not in kit_names():
            return
        self._apply_kit(wanted)
        self._kit_chosen = True
        write_setting(KEY_INDICATORS, wanted)

    def _announce_mode(self, problem: str = "") -> None:
        if self._display_mode == dos.MODE_DOS:
            tube = dos.get_phosphor(self._optics.phosphor)
            message = (
                f"DOS mode on — {tube.title}, glow "
                f"{dos.glow_label(self._optics.glow)}. Every key, pane and "
                "meter is the same one; only the glass changed."
            )
        else:
            message = (
                "DOS mode off — back to the bench panel, in the skin the CLI "
                "and the TUI are wearing."
            )
        self._notify_panel(message + (f"  (not saved: {problem})" if problem else ""))

    def _set_optics(self, **changes) -> str:
        """Change one of the DOS mode's settings, apply it, and save it.

        Applied whether or not the mode is on. A reader who is choosing a tube
        before turning the mode on is choosing what it will look like when
        they do, and a setting that only takes effect later is a setting they
        cannot judge — the preview beside the control is drawn from these
        values, so it moves either way.

        Returns whatever went wrong on the way to disk, as a sentence, or ""
        — the same contract :func:`write_setting` has, because every caller of
        this is a keypress with one place to put a failure.
        """
        keys = {
            "phosphor": KEY_DOS_PHOSPHOR,
            "glow": KEY_DOS_GLOW,
            "scanlines": KEY_DOS_SCANLINES,
            "block_cursor": KEY_DOS_BLOCK_CURSOR,
        }
        self._optics = dos.Optics(
            mode=self._display_mode,
            phosphor=str(changes.get("phosphor", self._optics.phosphor)),
            glow=dos.clamp_glow(changes.get("glow", self._optics.glow)),
            scanlines=bool(changes.get("scanlines", self._optics.scanlines)),
            block_cursor=bool(
                changes.get("block_cursor", self._optics.block_cursor)
            ),
        )
        problems = []
        for name, value in changes.items():
            key = keys.get(name)
            if key is None:
                continue
            stored = getattr(self._optics, name)
            problem = write_setting(key, stored)
            if problem:
                problems.append(problem)
        self._apply_display_mode()
        return "; ".join(problems)

    def _step_glow(self, delta: int) -> None:
        level = dos.clamp_glow(self._optics.glow + delta)
        if level == self._optics.glow:
            self._notify_panel(
                "The brightness control is already at "
                + ("its dimmest." if delta < 0 else "full drive.")
            )
            return
        problem = self._set_optics(glow=level)
        self._notify_panel(
            f"Glow {dos.glow_label(level)}  {dos.glow_bar(level)}  "
            "— the haze on the glass and the bloom on the strokes move "
            "together, because on a monitor they are one knob."
            + (f"  (not saved: {problem})" if problem else "")
        )

    def _toggle_scanlines(self) -> None:
        problem = self._set_optics(scanlines=not self._optics.scanlines)
        self._notify_panel(
            (
                "Scanlines on — every other raster line is drawn dark, in the "
                "chrome and in the state figures."
                if self._optics.scanlines
                else "Scanlines off — a progressive picture, no gaps."
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    def _toggle_block_cursor(self) -> None:
        problem = self._set_optics(block_cursor=not self._optics.block_cursor)
        self._notify_panel(
            (
                "Block cursor on — the composer's cursor blinks, as a DOS "
                "prompt's did."
                if self._optics.block_cursor
                else "Block cursor off — the composer's cursor holds steady."
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    # ── F10: the chrome ──────────────────────────────────────────────────

    def _toggle_chrome(self) -> None:
        """Take everything that is not the conversation away, and give it back.

        Four things, because all four are frame rather than content and all
        four cost rows the reading column could have: the title plate, the
        key line's lettering, the pane's own top padding, and every notice.

        The notices matter most. They are the one piece of chrome that
        appears *by itself*, so leaving them live meant F10 freed four rows
        and then a status line took two of them back a moment later, over the
        top of the conversation — which is the opposite of what the key was
        pressed for. Hidden here, and refused at the source while hidden, so
        one cannot arrive a second after the toggle.

        The key line's *bar* stays either way. It is a painted edge along the
        bottom of the window in the skin's own panel colour; removing it left
        the composer sitting on the terminal's background with nothing to
        close the frame. What goes is the lettering on it.
        """
        self._chrome_hidden = not self._chrome_hidden
        self._apply_chrome()
        if self._chrome_hidden and not self._chrome_hint_shown:
            # Once per session. The key line is where the reader learns the
            # keys, so taking it away deserves saying how to get it back —
            # but the notice costs two of the rows the key just freed, and
            # charging that on every press would undo the point of it.
            self._chrome_hint_shown = True
            # Straight to the widget: _notify_panel refuses while the chrome
            # is hidden, and this is the one message whose whole subject is
            # that it now is.
            notice = self._maybe("#notice", Static)
            if notice is not None:
                notice.update(
                    Text(
                        "  Title plate and key line hidden — F10 brings them back.",
                        style=self.bench_palette["warning"],
                    )
                )
                notice.remove_class("hidden")
                self._notice_until = time.monotonic() + 6.0

    def _apply_chrome(self) -> None:
        """Put every piece of chrome in the state the F10 flag calls for."""
        hidden = self._chrome_hidden
        plate = self._maybe("#masthead", Static)
        if plate is not None:
            # The transcript is the flexible sibling, so it takes the freed
            # rows on the next layout pass with nothing else to do.
            plate.display = not hidden
        # The bar keeps its colour and its row; only the caps go.
        keyline = self._maybe("#keyline")
        if keyline is not None:
            keyline.set_class(hidden, "-blank")
        # The pane's top padding is the second of the two rows that were
        # holding the conversation off the top of the window.
        for key, *_rest in SWITCHES:
            pane = self._maybe(f"#pane-{key}")
            if pane is not None:
                pane.set_class(hidden, "-tight")
        if hidden:
            self._dismiss_notice()

    def _dismiss_notice(self) -> None:
        notice = self._maybe("#notice", Static)
        if notice is not None:
            notice.add_class("hidden")
        self._notice_until = 0.0

    # ── Going back over a turn ───────────────────────────────────────────

    def _regenerate(self) -> None:
        """Ctrl+G — ask the last question again, from a clean slate.

        Not a re-render of the stored reply: the exchange is removed from the
        conversation and the same words are sent again, so the model answers
        without its previous attempt in context. Regenerating *with* the old
        answer still in the history asks a different question — "improve on
        this" — which is not what the key says it does.
        """
        if self.bridge.busy:
            self._notify_panel("A turn is running — Ctrl+C stops it first.")
            return
        prompt = self._take_back_last_exchange()
        if not prompt:
            self._notify_panel("Nothing to send again — no request yet.")
            return
        self._notify_panel("Asking again, without the previous answer.", seconds=4.0)
        self._send(prompt)

    def _go_back(self) -> None:
        """Ctrl+B — remove the last exchange and put the words back.

        The prompt returns to the composer rather than vanishing, because the
        reason to go back one message is almost always to change it. Removing
        it and leaving the reader to retype it from memory would make the key
        a destructive one.
        """
        if self.bridge.busy:
            self._notify_panel("A turn is running — Ctrl+C stops it first.")
            return
        prompt = self._take_back_last_exchange()
        if prompt is None:
            self._notify_panel("Nothing to take back — the bench is clear.")
            return
        composer = self._maybe("#composer", Composer)
        if composer is not None:
            composer.text = prompt
            composer.focus()
        self._notify_panel(
            "Took back one message — it is in the composer, ready to edit.",
            seconds=6.0,
        )
        self._set_subject(self.bridge.describe())

    def _take_back_last_exchange(self) -> str | None:
        """Drop the newest exchange from both the screen and the history.

        The transcript's copy is what the reader sees; the bridge's is what
        the next turn is built from. Both have to go, and the transcript's is
        the one to trust for the words, because the bridge's history can be a
        repaired projection with the original phrasing summarised away.
        """
        pane = self._bench()
        if pane is None:
            return None
        shown = pane.drop_last_exchange()
        held = self.bridge.rewind()
        self._set_activity(READY)
        return shown if shown is not None else held

    # ── The bench (chat) ─────────────────────────────────────────────────

    @on(Composer.Submitted)
    def _composer_submitted(self, event: Composer.Submitted) -> None:
        """The composer asked to send. See :class:`Composer` for the binding."""
        event.stop()
        self._submit_turn()

    def _submit_turn(self) -> None:
        composer = self.query_one("#composer", Composer)
        message = composer.text.strip()
        if not message:
            return
        if self.bridge.busy:
            self._notify_panel("A turn is already running — Ctrl+C stops it.")
            return

        egg = _easter_egg(message)
        if egg is not None:
            composer.text = ""
            self._write_notebook_entry(egg)
            return

        composer.text = ""
        self._send(message)

    def _send(self, message: str) -> None:
        """Put one request to the agent and set the panel running.

        Shared by the composer, Ctrl+G and dictation, so all three produce
        exactly the same turn — an indicator that only moved for typed
        requests would be lying about the other two.

        A blank request is refused here rather than at each caller. Dictation
        is where one comes from — a breath, a cough, a door, and the
        recogniser returns an empty string — and sending it puts an empty
        user turn in front of the model, which reads it as having been
        addressed with nothing and answers by casting about for what it is
        supposed to be doing. That is a loop the reader cannot see the cause
        of, because the turn that caused it shows as an empty line.
        """
        if not message.strip():
            return
        self._write("user", message)

        if not self.bridge.submit(message):
            self._notify_panel("Could not start the turn.")
            return

        self._turn_started_at = time.monotonic()
        self._last_chars = 0
        self._last_reasoning_chars = 0
        self._last_tool_calls = 0
        self._streaming = False
        self._running_tools.clear()
        self._set_activity(WAITING, "sent")
        chart = self._maybe("#chart-output", StripChart)
        if chart is not None:
            chart.clear()
        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.set_lamp("LOG", "warn")

    def _pump_agent(self) -> None:
        events_ = self.bridge.drain()
        if not events_:
            return
        pane = self._bench()
        if pane is None:
            # Teardown, or the bench pane is not mounted. The events are
            # already drained; dropping them beats a traceback over the UI.
            return
        for event in events_:
            if event.kind == "delta":
                # The first token of the answer means thinking is over, even
                # if no explicit end-of-reasoning event ever arrives.
                pane.stop_thinking()
                pane.begin_reply()
                self._streaming = True
                # Answer text means every call the model made has been
                # answered, whether or not each reported finishing.
                self._running_tools.clear()
                pane.append_reply(event.text)
                self._set_activity(STREAMING)
            elif event.kind == "reasoning":
                # Into the fold, shut, with the pen sweeping beside it. The
                # scratch work is available on demand and never in the way.
                pane.start_thinking(THINKING)
                pane.fold().add_reasoning(event.text)
                # Thinking again means the model has its tool results, even
                # if a blocked call never reported finishing.
                self._running_tools.clear()
                self._set_activity(THINKING)
            elif event.kind == "wait":
                # The agent explaining a slow provider. It is not reasoning
                # and it is not an answer; it is the reason for the silence,
                # and it is the one thing a stalled turn can say for itself.
                self._set_activity(WAITING, _shorten(event.text, 24))
                self._notify_panel(event.text, seconds=6.0)
            elif event.kind == "tool_gen":
                # The model is writing the call but has not made it. Worth
                # the indicator — composing a large payload is a long
                # silence — and nothing else: it is not a call yet, so it
                # goes neither into the fold nor into the count, and it does
                # not open a block of workings. A call written and then
                # abandoned is a real case (it is what the agent's
                # dropped-tool-call nudge exists for), and opening one here
                # would split the answer around a block that turned out to
                # have nothing in it. An open block gets its pen re-aimed;
                # nothing else changes.
                if pane.in_workings:
                    pane.start_thinking(TOOL)
                self._set_activity(TOOL, _shorten(event.text, 22))
            elif event.kind == "tool":
                # A tool reached for after an answer opens a *new* block of
                # workings, below that answer — ``fold()`` gives one because
                # ``begin_reply`` sealed the last.
                pane.fold().add_tool(event.text)
                self._running_tools.append(event.text)
                pane.start_thinking(TOOL)
                self._set_activity(TOOL, _shorten(event.text, 22))
            elif event.kind == "tool_done":
                if event.text in self._running_tools:
                    # One occurrence, not every one: two calls to the same
                    # tool at once are two calls, and clearing both on the
                    # first completion would report the turn as done with
                    # tools while the second was still running.
                    self._running_tools.remove(event.text)
                if not self._running_tools:
                    # Back to whatever the turn was doing before the tool
                    # ran. Asked of the block that is open right now, not of
                    # whether any answer text has arrived this turn — which
                    # stays true for the rest of it, so a tool the model
                    # reached for *after* answering left the state figure
                    # claiming to be streaming an answer while it ran.
                    if pane.in_workings:
                        pane.start_thinking(THINKING)
                        self._set_activity(THINKING)
                    else:
                        # No block open: either the answer is already
                        # streaming, or the bench was cleared out from under
                        # the turn and there is nothing to show but the wait.
                        self._set_activity(
                            STREAMING if self._streaming else WAITING
                        )
            elif event.kind == "record":
                self._record_written(event.text)
            elif event.kind == "warn":
                self._notify_panel(event.text)
            elif event.kind == "status":
                self._notify_panel(event.text)
            elif event.kind == "error":
                pane.write("error", event.text)
                self._pulse_activity(ERROR, _shorten(event.text, 24))
            elif event.kind == "done":
                if not self._streaming and event.text:
                    pane.begin_reply()
                    pane.append_reply(event.text)
                spoken = event.text or pane.reply_text
                pane.end_turn()
                self._finish_turn()
                self._speak(spoken)

    def _finish_turn(self) -> None:
        self._streaming = False
        self._turn_started_at = None
        self._running_tools.clear()
        self._set_activity(READY)
        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.set_lamp("LOG", "off")
        tape = self._maybe("#tape-turn", TapeMeter)
        if tape is not None:
            tape.set_fraction(0.0)
        facts = self.bridge.describe()
        self._set_subject(facts)
        gauge = self._maybe("#gauge-context", DialGauge)
        ceiling = facts.get("context_length")
        if ceiling and gauge is not None:
            gauge.set_reading(gauge.value, ceiling)
        # The conversation reaches the store as part of the turn, so this is
        # the first moment a bench conversation can appear in the logbook.
        self._reload_logbook()

    # ── Instruments ──────────────────────────────────────────────────────

    def _tick_clock(self) -> None:
        clock = self._maybe("#titlebar-clock", Static)
        if clock is not None:
            # The DOS bar carries the long date beside the time, because that
            # is what a program with a title bar and no desktop around it had
            # to do: there was nothing else on the screen to ask.
            clock.update(
                dos.clock(self.bench_palette, datetime.now())
                if self.bench_palette.dos
                else Text(
                    datetime.now().strftime("%H:%M:%S"),
                    style=self.bench_palette["secondary"],
                )
            )
        if self._notice_until and time.monotonic() > self._notice_until:
            notice = self._maybe("#notice", Static)
            if notice is not None:
                notice.add_class("hidden")
            self._notice_until = 0.0

    def _tick_instruments(self) -> None:
        self._push_output_sample()
        self._refresh_activity()

        tape = self._maybe("#tape-turn", TapeMeter)
        if tape is None:
            return
        if self._turn_started_at is None:
            # No turn: the tape sits at its start rather than wherever the
            # last one left it.
            tape.set_fraction(0.0)
            return
        elapsed = time.monotonic() - self._turn_started_at
        # A minute of travel: long enough that the common turn shows
        # movement, short enough that it does not look stuck.
        tape.set_fraction((elapsed % 60.0) / 60.0)

    def _push_output_sample(self) -> None:
        """One sample onto the recorder, on the channel that produced it.

        Three counters are read and the largest movement wins the sample.
        The alternative — three samples per tick — would draw three columns
        for one half-second and make the time axis a lie; a turn only ever
        does one of these things at a time anyway.
        """
        chart = self._maybe("#chart-output", StripChart)
        if chart is None:
            return
        answer = self.bridge.chars_this_turn
        reasoning = getattr(self.bridge, "reasoning_chars_this_turn", 0)
        tools = getattr(self.bridge, "tool_calls_this_turn", 0)
        answer_delta = max(0, answer - self._last_chars)
        reasoning_delta = max(0, reasoning - self._last_reasoning_chars)
        tool_delta = max(0, tools - self._last_tool_calls)
        self._last_chars = answer
        self._last_reasoning_chars = reasoning
        self._last_tool_calls = tools

        if tool_delta and not answer_delta and not reasoning_delta:
            # A tool call is one event, not a character count, so it is drawn
            # at a fixed height against whatever the scale currently is —
            # enough to be visible beside a stream, never enough to reset it.
            chart.push(max(1.0, chart.peak * 0.45), channel="tool")
            return
        # Sampled twice a second, so the rate is the delta doubled.
        if reasoning_delta > answer_delta:
            chart.push(reasoning_delta * 2, channel="think")
        else:
            chart.push(answer_delta * 2, channel="text")

    # ── The state instrument ─────────────────────────────────────────────

    def _set_activity(self, state: str, detail: str = "") -> None:
        """Set what the console is doing. Transient states go through
        :meth:`_pulse_activity` instead, so they cannot get stuck on."""
        self._activity = state
        self._activity_detail = detail
        self._refresh_activity()

    def _pulse_activity(self, state: str, detail: str = "") -> None:
        """Show a state for a moment, then hand the instrument back.

        A write to the record takes a millisecond and a reader would never
        catch it at its true duration; a fault is over the moment it is
        reported and still needs to be seen. Both are events rather than
        conditions, so they borrow the instrument rather than owning it.
        """
        self._activity_overlay = (
            state,
            time.monotonic() + ACTIVITY_HOLD.get(state, 2.0),
        )
        self._overlay_detail = detail
        self._refresh_activity()

    def _refresh_activity(self) -> None:
        """Paint the effective state, expiring a spent overlay on the way."""
        state, detail = self._activity, self._activity_detail
        overlay = self._activity_overlay
        if overlay is not None:
            if time.monotonic() < overlay[1]:
                state, detail = overlay[0], getattr(self, "_overlay_detail", "")
            else:
                self._activity_overlay = None
        # No character or second count beside the figure. The figure already
        # says what is happening and the recorder below it says how much; a
        # running number said neither, and it was the one thing in the column
        # that changed every frame without ever meaning anything different.
        # Whatever the state itself has to say — the tool that is running,
        # why the provider is slow — stays.
        monitor = self._maybe("#activity", ActivityMonitor)
        if monitor is not None:
            monitor.set_state(state)
            monitor.set_detail(detail)

    def _record_written(self, what: str) -> None:
        """Something reached the record — a session name, a compression.

        Two indicators, because it is two facts: the lamp says *that* the
        record was written and fades, and the state figure says *what* for
        as long as anyone is likely to be looking.
        """
        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.flash("REC")
        self._pulse_activity(SAVING, _shorten(what, 24))
        self._notify_panel(f"Recorded — {what}.", seconds=5.0)
        # A name is exactly what the logbook lists, so it is stale the moment
        # one is written.
        self._reload_logbook()

    @on(events.Click, "#chart-output")
    def _chart_clicked(self, event: events.Click) -> None:
        """Five clicks on the recorder runs its calibration sweep.

        A chart recorder has a test button for exactly this reason: a flat
        line and a dead pen look identical until you drive the pen through
        its full travel. Five is deliberate — it is not something you hit
        by accident while scrolling.
        """
        self._chart_clicks += 1
        if self._chart_clicks < 5:
            return
        self._chart_clicks = 0
        chart = self._maybe("#chart-output", StripChart)
        if chart is None:
            return
        on_now = chart.toggle_calibration()
        self._notify_panel(
            "Calibration sweep running — the pen is driven through full "
            "travel so a flat trace can be told from a dead pen. "
            "Click five more times to stop."
            if on_now
            else "Calibration sweep off. Back to live output rate."
        )

    # ── Voice ────────────────────────────────────────────────────────────

    def _restore_voice(self) -> None:
        """Put the voice switches back the way they were left.

        Speech is restored silently — it changes nothing until a turn ends.
        The microphone is restored *and announced*, because a console that
        quietly opens an input device on start-up is a console nobody should
        trust, and the notice is the difference between a restored preference
        and a surprise.
        """
        settings = self.voice.reload()
        if settings.voice_input:
            # remember=False: this *is* the remembered value being acted on,
            # and writing it back would put a config save on every start-up.
            if self.voice.start_listening(remember=False):
                self._notify_panel(
                    "Dictation is on, as you left it — F6 opens PANEL to "
                    "turn it off.",
                    seconds=8.0,
                )
            else:
                self._notify_panel(self.voice.reason or "Dictation is unavailable.")
        self._sync_voice_switches()

    def _toggle_listening(self) -> None:
        listening = self.voice.toggle_listening()
        if listening:
            self._notify_panel("Dictation on — speak, and it goes to the composer.")
        elif self.voice.reason:
            self._notify_panel(self.voice.reason)
        else:
            self._notify_panel("Dictation off.")
        self._sync_voice_switches()

    def _toggle_speaking(self) -> None:
        speaking = self.voice.toggle_speaking()
        if speaking:
            voice = self.voice.selected_voice()
            self._notify_panel(
                "Replies will be read aloud"
                + (f" in {voice}." if voice else " by the configured provider.")
            )
        elif self.voice.reason:
            self._notify_panel(self.voice.reason)
        else:
            self._notify_panel("Spoken replies off.")
        self._sync_voice_switches()

    def _refresh_voices(self) -> None:
        pane = self._maybe("#pane-panel", PanelPane)
        if pane is None:
            return
        self.voice.reload()
        pane.reload_voices()
        found = len(self.voice.voices())
        self._notify_panel(
            f"Voice folder re-read — {found} voice(s) in "
            f"{self.voice.voices_dir()}.",
            seconds=8.0,
        )
        self._sync_voice_switches()

    def _sync_voice_switches(self) -> None:
        pane = self._maybe("#pane-panel", PanelPane)
        if pane is None:
            return
        pane.refresh_switches(
            listening=self.voice.listening,
            speaking=self.voice.speaking_enabled,
            note=self.voice.reason and f"  {self.voice.reason}" or "",
        )

    def _speak(self, text: str) -> None:
        """Read a finished reply aloud, if the speaker switch is on."""
        if text and text.strip():
            self.voice.speak(text)

    def _voice_transcript(self, text: str) -> None:
        """A dictated sentence, arriving from the recogniser's own thread."""
        try:
            self.call_from_thread(self._dictated, text)
        except Exception:
            # The app is tearing down, or the loop is not running yet. A lost
            # transcript is better than an exception on an audio thread.
            pass

    def _voice_status(self, text: str) -> None:
        try:
            self.call_from_thread(self._notify_panel, text)
        except Exception:
            pass

    def _dictated(self, text: str) -> None:
        """What to do with a dictated sentence.

        It goes into the composer and is sent, which is what dictation means.
        A turn already running is the exception: the words are left in the
        composer instead of being dropped, so the speaker can send them when
        the bench is free rather than having to say them again.
        """
        composer = self._maybe("#composer", Composer)
        if composer is None:
            return
        if not text.strip():
            # Silence, a cough, a door. The recogniser returns something for
            # all of them and none of them is a request.
            return
        if self.bridge.busy:
            composer.text = (composer.text + " " + text).strip()
            self._notify_panel(
                "Heard that, but a turn is running — it is in the composer."
            )
            return
        self.show_pane("bench")
        composer.text = ""
        self._send(text)

    # ── Indicator sets ───────────────────────────────────────────────────

    def _apply_kit(self, name: str) -> None:
        """Adopt an indicator set everywhere at once.

        Every kit-driven widget is handed the same set by name rather than
        the same instance: an automaton carries a generation counter, and two
        indicators sharing one would drag each other's pattern sideways every
        time either drew.
        """
        if name not in kit_names():
            return
        self._kit_name = name
        monitor = self._maybe("#activity", ActivityMonitor)
        if monitor is not None:
            monitor.set_kit(name)
        pane = self._bench()
        if pane is not None:
            pane.set_kit(name)

    @on(DataTable.RowSelected, "#indicator-table")
    def _kit_selected(self, event: DataTable.RowSelected) -> None:
        table = event.data_table
        try:
            row = table.get_row_at(event.cursor_row)
        except Exception:
            return
        name = str(row[0]).replace("▶", "").strip()
        if name not in kit_names():
            return
        self._apply_kit(name)
        # From here on the mode does not get to pick: a set chosen by hand is
        # a set chosen by hand, whichever skin it was chosen under.
        self._kit_chosen = True
        problem = write_setting(KEY_INDICATORS, name)
        panel = self._maybe("#pane-panel", PanelPane)
        if panel is not None:
            panel.reload_kits(name)
        kit = get_kit(name)
        self._notify_panel(
            f"Indicator set {name!r} — {kit.blurb}"
            + (f"  (not saved: {problem})" if problem else "")
        )

    @on(DataTable.RowSelected, "#voice-table")
    def _voice_selected(self, event: DataTable.RowSelected) -> None:
        try:
            row = event.data_table.get_row_at(event.cursor_row)
        except Exception:
            return
        name = str(row[0]).replace("▶", "").strip()
        if not name or name == "—":
            return
        problem = self.voice.select_voice(name)
        panel = self._maybe("#pane-panel", PanelPane)
        if panel is not None:
            panel.reload_voices(selected=name)
            self._sync_voice_switches()
        self._notify_panel(
            f"Voice {name!r} selected — Piper is now the TTS provider."
            if not problem
            else f"Could not select {name!r}: {problem}"
        )

    # ── Panel switching from the appearance table ────────────────────────

    @on(DataTable.RowSelected, "#panel-table")
    def _skin_selected(self, event: DataTable.RowSelected) -> None:
        table = event.data_table
        try:
            row = table.get_row_at(event.cursor_row)
        except Exception:
            return
        name = str(row[0]).replace("▶", "").strip()
        if not name or name == "—":
            return
        try:
            from curie_cli.skin_engine import set_active_skin

            set_active_skin(name)
        except Exception as exc:  # noqa: BLE001
            self._notify_panel(f"Could not apply skin {name!r}: {exc}")
            return
        # Through the mode, not straight to ``resolve_palette``: while the
        # DOS mode is on, the skin belongs to the CLI and the TUI and this
        # console keeps its phosphor. Repainting from the skin here would have
        # dropped the console out of the mode without the mode being turned
        # off — and left the switch on the same pane saying it was still on.
        self._apply_display_mode()
        self.query_one("#titlebar-mark", Static).update(self._wordmark())
        # The chrome follows the stylesheet, but the conversation does not:
        # every entry was rendered with the previous skin's colours baked in,
        # so without this the chat stayed in the old palette — a reply left
        # dark grey and a name left orange under a skin that uses neither.
        pane = self._bench()
        if pane is not None:
            pane.restyle(self.bench_palette)
        self.paint_masthead()
        # The settings pane paints its own notes and previews from the
        # palette, so they have to be redrawn too — otherwise the page the
        # skin was chosen on is the one page still showing the old one.
        panel = self._maybe("#pane-panel", PanelPane)
        if panel is not None:
            panel.reload_kits(self._kit_name)
            panel.reload_voices()
            self._sync_voice_switches()
        # Written down, not merely applied. `set_active_skin` changes only
        # this process's idea of the active skin, so without the save the
        # choice lasted exactly as long as the console was open — reapplied
        # by hand on every launch, which is not a setting at all.
        problem = write_setting(KEY_SKIN, name)
        self._notify_panel(
            (
                f"Skin {name!r} applied to the CLI and the TUI, and saved. "
                "This console is in DOS mode, so it keeps the phosphor — turn "
                "DOS MODE off above and the skin takes it back."
                if self.bench_palette.dos
                else f"Skin {name!r} applied — here, and in the CLI and TUI "
                "as well."
            )
            + ("" if not problem else f"  (not saved: {problem})")
        )
        for index in range(table.row_count):
            existing = table.get_row_at(index)
            plain = str(existing[0]).replace("▶", "").strip()
            table.update_cell_at(
                (index, 0), ("▶ " if plain == name else "  ") + plain
            )

    @on(DataTable.RowSelected, "#phosphor-table")
    def _phosphor_selected(self, event: DataTable.RowSelected) -> None:
        """Change the tube. Applies and saves whether the mode is on or not."""
        try:
            row = event.data_table.get_row_at(event.cursor_row)
        except Exception:
            return
        name = str(row[0]).replace("▶", "").strip()
        if name not in dos.PHOSPHOR_NAMES:
            return
        problem = self._set_optics(phosphor=name)
        tube = dos.get_phosphor(name)
        self._notify_panel(
            f"{tube.title} — {tube.blurb}"
            + (
                ""
                if self.bench_palette.dos
                else "  Turn DOS MODE on above to see it."
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    @on(DataTable.RowSelected, "#logbook-table")
    def _session_selected(self, event: DataTable.RowSelected) -> None:
        """Load the chosen conversation onto the bench and carry on in it."""
        try:
            row = event.data_table.get_row_at(event.cursor_row)
        except Exception:
            return
        sid = str(row[-1]).strip()
        if not sid or sid == "—":
            return
        if sid == self.bridge.session_id:
            self.show_pane("bench")
            self._notify_panel("That conversation is already on the bench.")
            return
        if self.bridge.busy:
            self._notify_panel(
                "A turn is running — Ctrl+C stops it, then the conversation "
                "can be swapped."
            )
            return
        # Reading the transcript and rebuilding the agent both touch the
        # database and the provider resolver, so they go on a worker; the
        # console stays responsive and says what it is doing meanwhile.
        self.show_pane("bench")
        self._notify_panel(f"Loading {sid} …", seconds=30.0)
        self.run_worker(
            lambda: self._load_session(sid), thread=True, exclusive=True
        )

    def _start_new_session(self) -> None:
        """F12 — begin a fresh conversation on the bench.

        A new conversation means a new agent bound to a new session id, not
        just a cleared screen: keeping the old agent would append the next
        turn to the conversation the reader thinks they just left.
        """
        if self.bridge.busy:
            self._notify_panel(
                "A turn is running — Ctrl+C stops it, then a new "
                "conversation can be started."
            )
            return
        pane = self._bench()
        if pane is not None:
            pane.clear_transcript()
        self.show_pane("bench")
        self._notify_panel("Starting a new conversation …", seconds=30.0)
        self.run_worker(self._new_session, thread=True, exclusive=True)

    def _new_session(self) -> None:
        """Worker half of :meth:`_start_new_session`."""
        ok = self.bridge.new_session()
        self.call_from_thread(self._new_session_ready, ok)

    def _new_session_ready(self, ok: bool) -> None:
        if not ok:
            self._notify_panel(
                "Could not start a new conversation"
                + (f" — {self.bridge.load_error}" if self.bridge.load_error else "")
            )
            return
        self._set_subject(self.bridge.describe())
        self._reload_logbook()
        gauge = self._maybe("#gauge-context", DialGauge)
        if gauge is not None:
            gauge.set_reading(0, self.bridge.describe().get("context_length") or 1)
        self._notify_panel("New conversation. The bench is clear.")
        composer = self._maybe("#composer", Composer)
        if composer is not None:
            composer.focus()

    def _load_session(self, session_id: str) -> None:
        """Worker half of :meth:`_session_selected`."""
        history = self.bridge.load_session(session_id)
        self.call_from_thread(self._session_loaded, session_id, history)

    def _session_loaded(self, session_id: str, history: "list[dict] | None") -> None:
        if history is None:
            self._notify_panel(
                f"Could not open {session_id}"
                + (f" — {self.bridge.load_error}" if self.bridge.load_error else "")
            )
            return
        pane = self._bench()
        if pane is None:
            return
        pane.clear_transcript()
        self._replay(history)
        self._set_subject(self.bridge.describe())
        self._reload_logbook(mark=session_id)
        self._notify_panel(
            f"{session_id} is on the bench — {len(history):,} message(s) "
            "loaded. The next turn continues it."
        )

    def _replay(self, history: "list[dict]") -> None:
        """Redraw a stored conversation into the transcript.

        Stored messages carry no reasoning or tool detail (the transcript is
        what was said, not how), so this renders the two roles that were and
        skips everything else — system prompts, pivot markers and tool
        plumbing are not conversation.
        """
        pane = self._bench()
        if pane is None:
            return
        shown = 0
        for message in history:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if message.get("display_kind"):
                continue
            text = message.get("content") or message.get("text") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            if role == "user":
                pane.write("user", text.strip())
                shown += 1
            elif role == "assistant":
                pane.write("reply", text.strip())
                shown += 1
        if not shown:
            pane.write(
                "note", "  This conversation has no stored messages to show."
            )

    def _reload_logbook(self, mark: str | None = None) -> None:
        """Re-read the logbook table, keeping the bench's row flagged."""
        pane = self._maybe("#pane-logbook", LogbookPane)
        if pane is None:
            return
        try:
            pane.reload()
            pane.mark_active(mark or self.bridge.session_id or "")
        except Exception:
            # The logbook is a readout; a store that will not answer must not
            # take the console down with it.
            pass

    # ── Rendering helpers ────────────────────────────────────────────────

    def _wordmark(self) -> Text:
        if self.bench_palette.dos:
            return dos.wordmark(self.bench_palette)
        out = Text(no_wrap=True)
        out.append("▮ ", style=self.bench_palette["primary"])
        out.append("CURIE", style=f"bold {self.bench_palette['accent']}")
        return out

    def _set_subject(self, facts: dict) -> None:
        subject = self._maybe("#titlebar-subject", Static)
        if subject is None:
            return
        model = facts.get("model") or "no model configured"
        provider = facts.get("provider") or ""
        # The DOS title bar is inverse video, so the two weights here are the
        # two weights of ink that go *on* a band — the body colour would be a
        # hole in it rather than a word.
        band = self.bench_palette.dos
        strong = self.bench_palette["ink" if band else "foreground"]
        weak = self.bench_palette["inkdim" if band else "dim"]
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(model, style=strong)
        if provider:
            text.append(f"  ·  {provider}", style=weak)
        subject.update(text)

    def paint_masthead(self) -> None:
        """Draw the title plate into its own widget above the transcript.

        A widget rather than a transcript entry, because F10 has to be able
        to take it away and give the rows to the conversation. As an entry it
        would have scrolled with the chat and could not be hidden without
        rewriting the log.

        A ``Panel`` rather than hand-drawn box characters: the plate has to
        be correct at every width, and Panel sizes itself to whatever width
        it is rendered at, so the rewrap is free.
        """
        from rich.panel import Panel

        plate = self._maybe("#masthead", Static)
        if plate is None:
            return
        if self.bench_palette.dos:
            # Drawn to a measured width rather than by a self-sizing Panel,
            # because the title is set *into* the top rule and the two runs of
            # rule either side of it are what has to add up. Measured off the
            # widget where it can be, off the window where the first layout
            # pass has not happened yet.
            width = plate.content_size.width or plate.size.width
            if not width:
                width = max(24, self.size.width - 4)
            plate.update(
                dos.masthead(self.bench_palette, width, self._plate_subject())
            )
            plate.display = not self._chrome_hidden
            return
        dim, accent = self.bench_palette["dim"], self.bench_palette["accent"]
        plate.update(
            Panel(
                Text("CURIE AGENT — BENCH TERMINAL", style=f"bold {accent}"),
                border_style=dim,
                box=_HEAVY_BOX,
                padding=(0, 1),
            )
        )
        plate.display = not self._chrome_hidden

    def _plate_subject(self) -> str:
        """What the DOS plate's second row says on its left: the model."""
        facts = self.bridge.describe()
        model = str(facts.get("model") or "").strip()
        return model or "no model configured"

    def _write_help(self) -> None:
        self._write("note", "")
        self._write("head", "▮ COMMAND INDEX")
        rows = [
            ("Enter", "send the composed request"),
            ("Shift+Enter", "newline inside the composer"),
            ("Ctrl+J", "newline — works in every terminal"),
            ("Ctrl+C", "stop the running turn"),
            ("Ctrl+L", "clear the transcript"),
            ("Ctrl+Q", "close the console"),
            ("F1", "this index"),
            ("F2 … F6, F9", "throw a switch on the rail"),
            ("F7", "show or hide the rail"),
            ("F8", "show or hide the instrument stack"),
            ("F10", "show or hide the title plate and the key line"),
            ("F6 → DISPLAY", "re-skin as a DOS phosphor terminal"),
            ("Ctrl+G", "ask the last request again, without the old answer"),
            ("Ctrl+B", "take back one message, into the composer"),
            ("F12", "start a new conversation"),
            ("Ctrl+R", "re-read the logbook"),
            ("right-click", "paste the clipboard into the composer"),
            ("mouse", "click switches, keycaps, folds, table rows"),
        ]
        for key, what in rows:
            self._write("kv", f"{key}\t{what}")
        self._write("note", "")
        self._write(
            "note",
            "  The rail: BENCH runs turns · LOGBOOK lists every past "
            "conversation, and selecting one loads it here · INSTRUMENTS "
            "shows the model and route · SUPPLY lists toolsets, skills "
            "and MCP servers · PANEL sets the display mode, the skin, the "
            "indicator set and voice · DIAGNOSTICS reports install health.",
        )
        self._write("note", "")
        self._write(
            "note",
            "  The instrument stack: STATE names what the console is doing "
            "and draws it as a moving figure — waiting, thinking, a tool, an "
            "answer, a write to the record — in whichever indicator set is "
            "chosen on PANEL. OUTPUT traces those apart by channel: the "
            "answer, reasoning, and tool calls.",
        )
        self._write("note", "")

    def _write_notebook_entry(self, entry: tuple[str, str]) -> None:
        """Write a page from the bench notebook. See :func:`_easter_egg`."""
        title, body = entry
        self._write("note", "")
        self._write("head", f"▮ {title}")
        for line in _wrap(body, 72):
            self._write("note", f"  {line}")
        self._write("note", "")

    def _notify_panel(self, message: str, seconds: float = 8.0) -> None:
        # Refused while the chrome is hidden. A notice is a two-row banner
        # over the top of the conversation, and it arrives on its own clock —
        # a status line landing a second after F10 would take back the rows
        # the key was pressed to get, which is worse than never showing it.
        # Nothing is lost that the panel does not say another way: the state
        # instrument names what is happening, the lamps say what was written,
        # and an error is written into the transcript itself.
        if self._chrome_hidden:
            return
        notice = self._maybe("#notice", Static)
        if notice is None:
            return
        notice.update(Text(f"  {message}", style=self.bench_palette["warning"]))
        notice.remove_class("hidden")
        self._notice_until = time.monotonic() + seconds


#: Marks the agent puts in front of a status line for a terminal that has
#: nothing else to draw with. The panel does: it has a lamp, a state name and
#: a moving figure for exactly this, so a pictogram beside them is a fourth
#: thing saying what three already said.
_STATUS_PICTOGRAMS = "⏳⌛⚠️⚠✗✓●◐→⚙️⚙🔄💭🛠️🛠"


def _shorten(text: str, width: int) -> str:
    """One line of at most ``width`` columns, for an instrument caption."""
    flat = " ".join(str(text or "").split())
    flat = flat.lstrip(_STATUS_PICTOGRAMS + " ")
    if len(flat) <= width:
        return flat
    return flat[: max(1, width - 1)].rstrip() + "…"


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. Used for the notebook facsimile's fixed-width page."""
    out: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if len(candidate) <= width:
                line = candidate
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


# ── Notebook entries ─────────────────────────────────────────────────────
# Typing one of these words opens a page from the bench notebook instead of
# sending a turn. They are real notes about real practice — the joke, such as
# it is, is that the console keeps a notebook at all.

_NOTEBOOK: dict[str, tuple[str, str]] = {
    "radium": (
        "NOTEBOOK — handling, entry 1",
        "Four years of processing eleven tonnes of pitchblende residue "
        "yielded about a tenth of a gram of radium chloride. The notebooks "
        "from that work are still radioactive and are kept in lead-lined "
        "boxes; readers sign a waiver.\n"
        "The lesson worth keeping: the measurement that mattered was the "
        "one nobody could reproduce yet, and it took four years of grinding "
        "rock to make it reproducible.",
    ),
    "polonium": (
        "NOTEBOOK — handling, entry 2",
        "Named for Poland, which did not appear on the map of Europe at the "
        "time. Naming a discovery after a country that officially did not "
        "exist was not an accident.",
    ),
    "notebook": (
        "NOTEBOOK — practice",
        "Write down what you did, not what you meant to do. A run you "
        "cannot reconstruct from the page did not happen.\n"
        "Date every entry. Note the instrument, the settings, and anything "
        "that went wrong — especially anything that went wrong. The failed "
        "runs are the ones you will want later.",
    ),
    "calibrate": (
        "NOTEBOOK — calibration",
        "Before trusting a reading, measure something you already know the "
        "answer to. A flat trace and a dead pen look identical.\n"
        "Click the output recorder five times to run its sweep.",
    ),
    "aperture": (
        "NOTEBOOK — panel construction",
        "The chrome on this console is drawn with the box-drawing and "
        "shading characters of IBM code page 437, which is what a text-mode "
        "program had to build an interface out of between roughly 1981 and "
        "the late nineties. Four shading densities, a handful of line "
        "weights, sixteen colours.\n"
        "It is a real constraint, not a stylistic one, and it is why panels "
        "from that era are still legible at any size.",
    ),
}


def _easter_egg(message: str) -> tuple[str, str] | None:
    """Return a notebook page when the composer holds just its keyword."""
    key = message.strip().lower().strip(".!?")
    return _NOTEBOOK.get(key)


def run(**kwargs) -> int:
    """Launch the console. Returns a process exit code."""
    app = BenchConsole(**kwargs)
    try:
        app.run()
    finally:
        # The recorder and the speaker both run on daemon threads that hold
        # an audio device. Leaving either open on the way out gives the shell
        # back a terminal with the microphone still live.
        try:
            app.voice.shutdown()
        except Exception:
            pass
    return 0


__all__ = ["BenchConsole", "run", "SWITCHES"]
