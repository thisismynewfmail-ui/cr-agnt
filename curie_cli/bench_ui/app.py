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

import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from rich import box
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Size
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
from curie_cli.bench_ui.schedule_pane import (
    TABLE_COMPACT_AT,
    PreviewComposer,
    ScheduleAction,
    SchedulePane,
    TaskWindow,
)
from curie_cli.bench_ui import schedule as schedule_store
from curie_cli.bench_ui.fonts import (
    FontFace,
    clamp_rows,
    forget_rendered,
    render_wordmark,
    resolve_face,
    system_fonts,
)
from curie_cli.bench_ui.settings import (
    KEY_DOS_BLOCK_CURSOR,
    KEY_DOS_GLOW,
    KEY_DOS_PHOSPHOR,
    KEY_DOS_SCANLINES,
    KEY_INDICATORS,
    KEY_SCROLLBARS,
    KEY_SKIN,
    KEY_SKIN_MODE,
    KEY_TYPEFACE,
    KEY_TYPEFACE_ROWS,
    KEY_WORKINGS_OPEN,
    BenchSettings,
    read_settings,
    restore_active_skin,
    write_setting,
)
from curie_cli.bench_ui.styles import BENCH_CSS
from curie_cli.bench_ui.sync import ConsoleSync
from curie_cli.bench_ui.theme import resolve_palette
from curie_cli.bench_ui.typeface import CP437, DEFAULT_TYPEFACE, is_default_typeface
from curie_cli.bench_ui.voice import VoiceDesk

# The rail. Each entry is (key, switch label, glyph, pane class).
#
# The names are the bench's own: a logbook is where you write down what you
# did, instruments are what you measure with, supply is the shelf you take
# reagents off, the panel is what you set the run up on. None of it is a
# metaphor for something else — that is the whole point.
SWITCHES: tuple[tuple[str, str, str, type], ...] = (
    ("bench", "BENCH", "▮", BenchPane),
    ("schedule", "SCHEDULE", "◴", SchedulePane),
    ("logbook", "LOGBOOK", "▤", LogbookPane),
    ("instruments", "INSTRUMENTS", "◷", InstrumentsPane),
    ("supply", "SUPPLY", "▣", SupplyPane),
    ("panel", "PANEL", "▥", PanelPane),
    ("diagnostics", "DIAGNOSTICS", "◑", DiagnosticsPane),
)

#: The key each pane answers to. Kept beside :data:`SWITCHES` because the DOS
#: mode draws the rail as a keyed menu, and the key a menu line carries has to
#: be the one that throws it — the rail's *order* is not that key (DIAGNOSTICS
#: is seventh on the rail and F9 on the keyboard), so deriving it from position
#: would print a menu of keys that do not work. ``test_dos_skin`` holds this
#: against the console's own bindings.
#:
#: SCHEDULE is the one control key here, and it is a control key because there
#: was no function key left. F1 to F10 are all spoken for, F12 starts a new
#: conversation, and F11 is the window manager's fullscreen toggle in
#: essentially every terminal — GNOME Terminal, Konsole, xterm, Windows
#: Terminal — so it never reaches the application at all. The console already
#: made this trade once, for the two turn controls; a key that works and is
#: printed with a caret beats a function key that is silently swallowed.
PANE_KEYS: dict[str, str] = {
    "bench": "f2",
    "schedule": "ctrl+t",
    "logbook": "f3",
    "instruments": "f4",
    "supply": "f5",
    "panel": "f6",
    "diagnostics": "f9",
}


def pane_key_label(key: str) -> str:
    """How a pane's key is printed on the rail: ``3``, ``^T``.

    The ``F`` comes off a function key — the bar it sits on is what says
    these are function keys, and a column of ``F``s is a column not spent on
    the pane names. A control key keeps its caret, because it is not found by
    counting along the top row of the keyboard.
    """
    binding = PANE_KEYS.get(key, "")
    if binding.startswith("ctrl+"):
        return f"^{binding[5:].upper()}"
    if binding.startswith("f") and binding[1:].isdigit():
        return binding[1:]
    return binding.upper()

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
    # F1 is the lettering key. It used to be HELP, which is the convention a
    # text-mode program set — and the index it opened has moved to ^O rather
    # than gone, because a console whose only documentation is unreachable is
    # a console with no documentation. It keeps F1's *place* at the head of
    # the row: the first key is the one that puts the interface back the way
    # it ships, which is what a reader who has changed something they cannot
    # name reaches for first.
    ("F1", "TYPE", "typeface_default", 0),
    ("F2", "BENCH", "bench", 72),
    # Second only to the bench, and with a lower width threshold than the
    # logbook, because it is the one pane that can be *doing something* while
    # the reader is looking at another: its cap carries a live count of the
    # tasks running, and a count nobody can see is a count that may as well
    # not be kept.
    ("^T", "TASKS", "schedule", 86),
    ("F3", "LOGBOOK", "logbook", 100),
    # These four moved up a tier when TASKS was added, and the arithmetic is
    # the reason: the row must never be wider than the window, so a cap added
    # in the middle of the ladder is paid for by the caps around it. Which
    # ones give way is a judgement about what a reader at that width still
    # needs reminding of — the pane keys and the two turn controls stay,
    # because they are what the console is for; the two chrome toggles and
    # the new-conversation key give way, because F1 lists all three and
    # neither is reached for mid-thought.
    ("F6", "PANEL", "panel", 120),
    ("F7", "RAIL", "rail_toggle", 120),
    ("F8", "METERS", "instruments_toggle", 132),
    ("F10", "CHROME", "masthead_toggle", 0),
    # The two turn controls sit together, in the order a reader reaches for
    # them: ask again, or take it back.
    ("^G", "AGAIN", "regenerate", 72),
    ("^B", "BACK", "back", 72),
    ("F12", "NEW", "new_session", 120),
    # Last in the row and first to go, because it is the one key whose whole
    # job is to tell you what the others do — which is worth least to the
    # reader who is already looking at them.
    ("^O", "HELP", "help", 144),
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
    "F2-F6, F9 and ^T switch panes",
    "F2-F9, ^T switch panes",
    "F2-F9, ^T panes",
    "F2-F9, ^T",
)

#: How often an animated tube is repainted, in hertz. The breath it draws is
#: eleven seconds long, so this is eighty-odd samples per cycle — far more
#: than the eye needs, and still nothing: one ``resolve_palette`` is a third
#: of a millisecond and the widgets it repaints are the ones already drawing
#: themselves on a frame clock.
#:
#: What this must never do is touch the stylesheet. ``refresh_css`` re-parses
#: the whole document and costs ~170 ms on this console — a thousand times the
#: palette. That is the reason the pulse moves the *drive* and not the glass:
#: everything the animation touches is resolved by a widget at render time,
#: and everything the stylesheet paints holds still.
PHOSPHOR_PULSE_HZ = 8.0

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
        self.switch_number = pane_key_label(key)
        self.add_class("switch")
        self.narrow = False
        #: A live count the pane wants shown on its own switch. Empty for
        #: every pane but SCHEDULE, which is the only one that can be doing
        #: something while the reader is looking at a different one.
        self.badge = ""

    def set_badge(self, badge: str) -> None:
        if badge != self.badge:
            self.badge = badge
            self.refresh()

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
                badge=self.badge,
            )
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append(f" {self.switch_glyph} ")
        if not self.narrow:
            out.append(self.switch_label)
        if self.badge:
            # In the signal colour, and only when there is something to say.
            # A permanent "0" beside a pane name is a number the eye learns to
            # stop reading, which is the opposite of what an indicator is for.
            tone = palette["primary"] if palette is not None else ""
            out.append(f" {self.badge}", style=f"bold {tone}" if tone else "bold")
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
        #: A live count appended to the label. See :meth:`Switch.set_badge` —
        #: same idea, and the same one pane uses it.
        self.badge = ""

    def set_badge(self, badge: str) -> None:
        if badge != self.badge:
            self.badge = badge
            # ``layout=True`` because this cap is ``width: auto``: a plain
            # repaint draws the new text into the *old* width, so the badge
            # is painted and then clipped off the end of the cap — present in
            # the widget, invisible on the bar, which is the one place it was
            # added to be seen.
            self.refresh(layout=True)
            # The row is measured to decide which caps fit and which note
            # goes on the end, and this cap just changed width.
            try:
                self.app.remeasure_keyline()
            except Exception:
                pass

    @property
    def keycap_width(self) -> int:
        """Columns this cap occupies: its text plus one of padding a side."""
        return len(self.cap) + 1 + len(self.label) + len(self.badge) + 2

    def render(self) -> Text:
        palette = _palette_of(self)
        if palette is not None and palette.dos:
            out = dos.keycap(self.cap, self.label, palette)
            if self.badge:
                out.append(self.badge, style=f"bold {palette['primary']}")
            return out
        out = Text(no_wrap=True)
        out.append(self.cap, style="bold")
        out.append(" ")
        out.append(self.label)
        if self.badge:
            tone = palette["primary"] if palette is not None else ""
            out.append(self.badge, style=f"bold {tone}" if tone else "bold")
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
        # F1 restores the console's lettering — see the KEYLINE note above and
        # :mod:`curie_cli.bench_ui.typeface`.
        Binding(
            "f1", "keyline('typeface_default')", "Typeface",
            show=False, priority=True,
        ),
        Binding("f2", "keyline('bench')", "Bench", show=False, priority=True),
        Binding("f3", "keyline('logbook')", "Logbook", show=False, priority=True),
        # The schedule pane. A control key, not a function key — see PANE_KEYS.
        # Ctrl+T is one of the few TextArea does not claim, so the composer
        # keeps every editing key it had.
        Binding("ctrl+t", "keyline('schedule')", "Schedule", show=False, priority=True),
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
        # The command index, moved off F1. ^O rather than one of the keys a
        # reader's fingers already own: ``TextArea`` does not claim it, so the
        # composer keeps every editing key it had, and it is not ^S or ^Q,
        # which terminals still swallow for flow control.
        Binding("ctrl+o", "keyline('help')", "Help", show=False, priority=True),
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
        #: Where an animated tube is in its breath, 0…1. Set before the first
        #: ``_resolve_palette`` below, which reads it.
        self._phosphor_phase = 0.0
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
        #: Set between the agent's two compaction edges. Compaction is a
        #: phase of the turn, not a status line: it can take tens of seconds,
        #: it produces no tokens while it runs, and the console must say so
        #: for the whole of it. Held here rather than pulsed, because a
        #: notice that expires halfway through a pause is worse than none —
        #: it teaches the reader the console has stopped.
        self._compacting = False
        self._compaction_detail = ""
        #: The tool calls running right now, oldest first, by name. A list
        #: rather than one name because a model can reach for several tools
        #: at once, and because a call that is *blocked* never reports
        #: finishing at all — a single slot held that name for the rest of
        #: the turn, and the state figure sat on TOOL, naming a tool that
        #: had already been refused, however much the turn went on to do.
        self._running_tools: list[str] = []

        self._kit_name = settings.indicators
        # The two reading switches on PANEL, and the lettering F1 restores.
        # Held on the app for the same reason the optics are: they are read on
        # every repaint and the reader can be throwing one while a frame is
        # being drawn, and a control that answers from the file is a control
        # with a save between every step of it.
        self._workings_open = settings.workings_open
        self._scrollbars = settings.scrollbars
        #: The lettering, as stored, and how tall the plate it letters is.
        self._typeface = settings.typeface
        self._typeface_rows = settings.typeface_rows
        #: The face that spec resolves to — the loaded font, or the built-in
        #: alphabet carrying the sentence saying why not. Held rather than
        #: re-resolved: resolving opens a file, and the plate is repainted on
        #: every resize.
        self._face: FontFace = settings.face()
        #: Whether the indicator set was ever actually chosen. The DOS mode
        #: offers its own native figure to a console that has never had one
        #: picked, and must not overrule a console that has.
        self._kit_chosen = settings.indicators_explicit
        self.voice = VoiceDesk(
            on_transcript=self._voice_transcript,
            on_status=self._voice_status,
        )
        #: The stores a second copy of this console writes to as well. See
        #: :mod:`curie_cli.bench_ui.sync`.
        self._sync = ConsoleSync()
        #: A second agent, bound to whichever task run the task window is
        #: showing. Built lazily and thrown away when the window closes: it
        #: loads a whole conversation and resolves a provider, which is not
        #: work to do for a window nobody has opened.
        self._task_bridge: AgentBridge | None = None
        self._task_bridge_session = ""
        #: What the badge on the SCHEDULE switch and keycap currently says,
        #: so a redraw is only paid for when the count actually moves.
        self._task_badge = ""
        #: Whether the logbook has been written to since it was last drawn,
        #: and which conversation its ▶ belongs to. See ``_reload_logbook``.
        self._logbook_stale = True
        self._logbook_mark = ""
        #: The task DELETE is armed for, if any. Deleting a scheduled task
        #: takes its schedule and its whole run history with it and cannot be
        #: taken back, so it takes two presses — and walking away disarms it,
        #: because the arming is per task rather than a mode.
        self._delete_armed = ""

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
                phase=self._phosphor_phase,
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
        # The context gauge, on its own clock rather than on the instrument
        # tick. It is the one readout whose reading costs more than an
        # attribute read — the anchored figure walks whatever the turn has
        # appended since the last response — and one second is finer than the
        # thing it measures moves: context grows a message at a time.
        self.set_interval(1.0, self._tick_context)
        # The window's own size, checked rather than waited for — see
        # :meth:`_tick_window`.
        self.set_interval(1.0, self._tick_window)
        # Two seconds is a compromise between two things a reader notices:
        # a setting changed in the other window that takes visible seconds to
        # arrive here, and a console that stats four files ten times a second
        # forever. Four stat calls every two seconds is nothing; the same
        # four at 10 Hz on a network home is not.
        self._sync.prime()
        self.set_interval(2.0, self._poll_shared_stores)
        # Always running, and doing nothing at all on a tube that holds a
        # steady picture — which is every tube but one. A timer that is
        # started and stopped as the setting changes is a timer that can be
        # left running by a path nobody thought of, or left stopped by one;
        # an unconditional interval whose callback returns on its first line
        # cannot be either, and costs an attribute read.
        self.set_interval(1.0 / PHOSPHOR_PULSE_HZ, self._pulse_phosphor)
        # The scheduling readouts. Every one of them is a *countdown* — "next
        # in 4m", "12s ago" — so they are wrong the moment they are drawn and
        # have to be redrawn on a clock rather than on an event. One second,
        # because that is the resolution the numbers are printed at.
        self.set_interval(1.0, self._tick_schedule)
        # Before the responsive pass: the DOS mode changes the rail's width
        # and the frames around the columns, and the collapse thresholds are
        # measured against what is actually painted.
        self._apply_display_mode()
        self._apply_responsive_layout(self.size.width)
        self._apply_kit(self._kit_name)
        # The two reading preferences, pushed at the bench pane now that it
        # exists. Both are read at start-up rather than on first use: a fold
        # that opened shut and then sprang open on the second turn would be
        # the setting arriving late, not the setting working.
        self._apply_workings_open()
        self._apply_scrollbars()
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

        # The task table has its own threshold: it is a six-column table, not
        # a reading column, and it runs out of room before the transcript
        # does. Measured against the pane rather than the window would be
        # better still, but the rail and the instrument stack have both
        # already collapsed by the time this matters — so the two agree.
        tasks = self._maybe("#pane-schedule", SchedulePane)
        if tasks is not None:
            tasks.set_compact(width < TABLE_COMPACT_AT)

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
        elif key == "logbook" and self._logbook_stale:
            # The deferred read, paid at the one moment it is worth paying:
            # the reader is now looking at the table.
            self._reload_logbook()
        elif key == "schedule":
            # Re-read on the way in rather than only on the timer: the pane
            # may have been off screen for an hour, and a table of countdowns
            # an hour stale is a table of wrong numbers.
            tasks = self._schedule()
            if tasks is not None:
                tasks.reload()
                table = tasks._maybe("#schedule-table", DataTable)
                if table is not None and not tasks.form_open:
                    table.focus()

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
        elif action == "workings-open":
            self._toggle_workings_open()
        elif action == "scrollbars":
            self._toggle_scrollbars()
        elif action == "typeface_default":
            self._restore_default_typeface()
        elif action == "typeface-taller":
            self._step_plate_rows(1)
        elif action == "typeface-shorter":
            self._step_plate_rows(-1)
        elif action == "typeface-rescan":
            self._rescan_fonts()
        elif action.startswith("schedule-"):
            self._schedule_keyline_action(action)
        else:
            self.show_pane(action)

    # ── The SCHEDULE pane's own controls ─────────────────────────────────

    def _schedule_keyline_action(self, action: str) -> None:
        """Every button on the schedule pane and its task window.

        One handler rather than one per control, because the pane's buttons
        and its switches both route through ``run_keyline_action`` — the same
        dispatcher the function keys use — so every control on the console is
        reachable the same way, from a click, a key, or a test.
        """
        pane = self._schedule()
        if pane is None:
            return
        window = pane.window()

        if action == "schedule-new":
            pane.open_form(None)
            return
        if action == "schedule-cancel":
            pane.close_form()
            return
        if action == "schedule-save":
            self._save_task(pane)
            return
        if action == "schedule-edit":
            task = pane.selected_task()
            if task is None:
                self._notify_panel("Pick a task first, then EDIT opens it.")
                return
            if task.running:
                self._notify_panel(
                    "That task is running right now. Editing it would change "
                    "the definition under the run — STOP it first, or wait."
                )
                return
            pane.open_form(task)
            return

        task = pane.selected_task()
        if action == "schedule-run":
            self._task_command(pane, task, schedule_store.run_now,
                               "armed for the next tick")
            return
        if action == "schedule-pause":
            if task is None:
                self._notify_panel("Pick a task first.")
                return
            wanted = not task.paused
            problem = schedule_store.set_paused(task.id, wanted)
            self._after_task_change(
                pane, problem,
                f"{task.name} {'paused' if wanted else 'armed again'}.",
            )
            return
        if action == "schedule-stop":
            self._stop_task(pane, task)
            return
        if action == "schedule-delete":
            self._delete_task(pane, task)
            return
        if action == "schedule-preview":
            if task is None:
                self._notify_panel("Pick a task first, then WINDOW opens its run.")
                return
            if window is not None:
                window.open_for(task)
            return

        # ── The task window's own controls ───────────────────────────────
        if window is None or not window.is_open:
            return
        held = pane.task_by_id(window.task_id)
        if action == "schedule-preview-close":
            self._close_task_window()
        elif action == "schedule-preview-size":
            window.toggle_size()
        elif action == "schedule-preview-older":
            window.set_status(window.step_run(1))
        elif action == "schedule-preview-newer":
            window.set_status(window.step_run(-1))
        elif action == "schedule-preview-run":
            self._task_command(pane, held, schedule_store.run_now,
                               "armed for the next tick", window=window)
        elif action == "schedule-preview-stop":
            self._stop_task(pane, held, window=window)

    def _task_command(self, pane, task, command, done: str, window=None) -> None:
        if task is None:
            self._notify_panel("Pick a task first.")
            return
        problem = command(task.id)
        self._after_task_change(pane, problem, f"{task.name} {done}.", window)

    def _stop_task(self, pane, task, window=None) -> None:
        """STOP: end the run, wherever it is actually happening.

        Two different runs can be in flight for one task and they are stopped
        differently, which is why this is not one call:

        * the *scheduled* run belongs to another process — the gateway — and
          is stopped by leaving it a request it picks up within a couple of
          seconds;
        * a turn the reader started in this window belongs to this process and
          is interrupted directly.

        Asked in that order, because the scheduled run is the one a person
        pressing STOP on a task almost always means.
        """
        if task is None:
            self._notify_panel("Pick a task first.")
            return
        if task.running:
            problem = schedule_store.stop_run(task.id)
            self._after_task_change(
                pane, problem,
                f"Asked the scheduler to stop {task.name}. It unwinds the run "
                "at its next checkpoint.",
                window,
            )
            return
        bridge = self._task_bridge
        if bridge is not None and bridge.busy and bridge.interrupt():
            if window is not None:
                window.set_status("Stopping this window's turn …")
            return
        self._notify_panel(f"{task.name} is not running.")
        if window is not None:
            window.set_status("Nothing is running for this task.")

    def _delete_task(self, pane, task) -> None:
        """Two presses to delete, because one press cannot be taken back.

        Not a modal. A confirmation dialog over a task list is a second
        surface to build, position and dismiss for a question with two
        answers; arming the button says the same thing in the row the reader
        is already looking at, and walking away disarms it.
        """
        if task is None:
            self._notify_panel("Pick a task first.")
            return
        if self._delete_armed != task.id:
            self._delete_armed = task.id
            self._notify_panel(
                f"DELETE again to remove {task.name} for good — its schedule "
                "and its run history go with it.",
                seconds=6.0,
            )
            return
        self._delete_armed = ""
        problem = schedule_store.remove_task(task.id)
        window = pane.window()
        if window is not None and window.task_id == task.id:
            self._close_task_window()
        self._after_task_change(pane, problem, f"{task.name} removed.")

    def _save_task(self, pane) -> None:
        task, problem = pane.save_form()
        if problem:
            pane.set_form_problem(problem)
            return
        pane.close_form()
        self._after_task_change(
            pane, "", f"{task.name} saved — {task.schedule}."
        )

    def _after_task_change(self, pane, problem: str, done: str, window=None) -> None:
        """Report one task change, and put every readout back in step.

        The write is this console's, so the shared-store watch is told as
        much: without that, the change this reader just made comes back on
        the next poll as somebody else's and is announced to them a second
        time.
        """
        try:
            self._sync.mine("schedule", "executions")
        except Exception:
            pass
        pane.reload()
        self._refresh_task_badge(pane)
        if window is not None and window.is_open:
            window.reload_runs()
            window.paint_title()
        if problem:
            self._notify_panel(problem)
            if window is not None:
                window.set_status(problem)
        else:
            self._notify_panel(done, seconds=6.0)
            if window is not None:
                window.set_status("")

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

        # The task table's cells and the task window's messages both hold
        # styled ``Text`` built when they were added, so like the transcript
        # they stay in the previous palette until they are rebuilt.
        tasks = self._maybe("#pane-schedule", SchedulePane)
        if tasks is not None:
            try:
                tasks.restyle()
            except Exception:
                pass

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

    # ── The tube that breathes ───────────────────────────────────────────

    @property
    def phosphor_is_animated(self) -> bool:
        """Whether the tube in force is one that does not hold still."""
        return bool(
            self._display_mode == dos.MODE_DOS
            and dos.get_phosphor(self._optics.phosphor).animated
        )

    def _pulse_phosphor(self) -> None:
        """Advance an animated tube's breath by one frame.

        Two lines of real work, and no repainting at all. The palette is
        rebuilt with a new phase, which changes exactly one thing — the
        ``bloom`` in its optics, the tube's beam current — and the indicator
        kits read that on every frame they draw, on their own clock. So the
        state figure, the fold's pen and the indicator sampler breathe, and
        nothing has to be told to.

        Nothing else moves, and that is deliberate rather than a limitation.
        The palette's *colours* reach the screen by two routes — widgets that
        render their own ``Text``, and the stylesheet, where they are frozen
        until a ``refresh_css`` that costs ~170 ms — so a pulsed colour would
        drift on one route and not the other, on surfaces that sit next to
        each other. The drive has only one consumer and no stylesheet can read
        it, so it cannot fall out of step with anything.

        Guarded whole. It runs on a timer inside a full-screen application,
        where an exception is a traceback painted across the interface.
        """
        try:
            if not self.phosphor_is_animated:
                return
            tube = dos.get_phosphor(self._optics.phosphor)
            period = max(0.5, float(tube.pulse.period))
            self._phosphor_phase = (
                self._phosphor_phase + 1.0 / (period * PHOSPHOR_PULSE_HZ)
            ) % 1.0
            self.bench_palette = self._resolve_palette()
        except Exception:
            # An appearance setting must never be able to take the console
            # down, least of all from a timer eight times a second.
            pass

    # ── Scheduled tasks ──────────────────────────────────────────────────

    def _schedule(self) -> SchedulePane | None:
        return self._maybe("#pane-schedule", SchedulePane)

    def _task_window(self) -> TaskWindow | None:
        pane = self._schedule()
        return pane.window() if pane is not None else None

    def _tick_schedule(self) -> None:
        """Redraw the scheduling readouts, and pump the task window.

        Split from the sync poller on purpose. The poller answers "has
        somebody else changed something", which is a question about files;
        this answers "how long until the next fire", which is a question
        about the clock and has a different answer every second even when
        nothing at all has changed.

        Cheap by construction: the table is only re-read while its pane is
        the one on screen, and the task window's conversation only while the
        window is open. A console sitting on the bench pays for one attribute
        read a second.
        """
        try:
            pane = self._schedule()
            if pane is None:
                return
            # The badge first and always: it is the one readout that has to be
            # right while the reader is looking at a different pane, and it
            # costs one query against the execution ledger.
            self._refresh_task_badge(pane)
            # A turn taken in the task window belongs to this process, so its
            # events are drained wherever the reader happens to be — otherwise
            # switching panes mid-reply leaves the window saying it is waiting
            # for an answer that has already arrived.
            self._pump_task_bridge()
            if self.active_pane != "schedule":
                return
            pane.reload()
            window = pane.window()
            if window is not None and window.is_open:
                window.refresh_conversation()
        except Exception:
            # A readout on a timer. A store that will not answer costs the
            # countdown, never the console.
            pass

    def _refresh_task_badge(self, pane: SchedulePane) -> None:
        """Put the running-task count on the SCHEDULE switch and keycap.

        The one thing a scheduling pane has to say from *outside itself*: a
        task is running now, in another process, and the reader is looking at
        a different pane. Without it the only way to find out is to go and
        look, which is the state of affairs the indicator exists to end.
        """
        running = pane.running_count
        badge = f"●{running}" if running else ""
        if badge == self._task_badge:
            return
        self._task_badge = badge
        switch = self._maybe("#switch-schedule", Switch)
        if switch is not None:
            switch.set_badge(badge)
        for cap in self.query(KeyCap):
            if cap.key_action == "schedule":
                cap.set_badge(badge)

    def remeasure_keyline(self) -> None:
        """Re-decide which keycaps fit, after one of them changed width."""
        self._apply_responsive_layout(self.size.width)

    # ── The task window's chat ───────────────────────────────────────────

    def _task_bridge_for(self, session_id: str) -> AgentBridge:
        """The agent bound to one task run, built the first time it is asked.

        A second bridge rather than the bench's own, because the bench's is
        holding the conversation the reader is having and a task window must
        not be able to append to it. Two bridges, two histories, two session
        ids — the windows cannot cross.
        """
        if self._task_bridge is None or self._task_bridge_session != session_id:
            self._task_bridge = AgentBridge(session_id=session_id)
            self._task_bridge_session = session_id
        return self._task_bridge

    def _pump_task_bridge(self) -> None:
        """Drain a local turn taken inside the task window."""
        bridge = self._task_bridge
        window = self._task_window()
        if bridge is None or window is None or not window.is_open:
            return
        events_ = bridge.drain()
        if not events_:
            if window.busy and not bridge.busy:
                window.set_busy(False)
            return
        for event in events_:
            if event.kind == "error":
                window.set_status(event.text)
            elif event.kind == "done":
                window.set_busy(False)
                window.set_status("")
                # The reply is written to the store as part of the turn, so
                # re-reading the conversation is what shows it — the window
                # is a *view* of the run, not a second copy of it.
                window.refresh_conversation()
            elif event.kind in {"status", "warn"}:
                window.set_status(_shorten(event.text, 96))

    def _send_into_task(self, message: str) -> None:
        """Put one message into the task run the window is showing.

        Refused while the scheduler is running that task. The run holds a
        durable turn lease on its session, and a second turn against the same
        session waits for it — for up to half an hour, with no way to tell
        from the outside that anything is happening. Saying so and offering
        STOP is the honest answer; a composer that swallows a message and
        goes quiet is not.
        """
        window = self._task_window()
        pane = self._schedule()
        if window is None or pane is None or not window.is_open:
            return
        if not message.strip():
            return
        if not window.session_id:
            window.set_status(
                "This task has not run yet, so there is no conversation to "
                "continue. RUN starts one."
            )
            return
        task = pane.task_by_id(window.task_id)
        if task is not None and task.running:
            window.set_status(
                "The scheduler is running this task right now — STOP ends "
                "that run, then this window can carry it on."
            )
            return
        if window.busy:
            window.set_status("This window is already waiting on a reply.")
            return

        bridge = self._task_bridge_for(window.session_id)
        window.set_busy(True)
        window.set_status("Sending into this run …")
        self.run_worker(
            lambda: self._task_turn(bridge, window.session_id, message),
            thread=True,
            exclusive=False,
        )

    def _task_turn(self, bridge: AgentBridge, session_id: str, message: str) -> None:
        """Worker half of :meth:`_send_into_task`.

        The conversation is loaded first and every time, not cached: the run
        this window is showing belongs to another process, which may have
        appended to it since the window was opened. Sending against a stale
        history would silently drop whatever the scheduled run said.
        """
        if bridge.session_id != session_id or not bridge.describe():
            if bridge.load_session(session_id) is None:
                self.call_from_thread(
                    self._task_turn_failed,
                    bridge.load_error or "could not open that run",
                )
                return
        if not bridge.submit(message):
            self.call_from_thread(self._task_turn_failed, "a turn is already running")

    def _task_turn_failed(self, why: str) -> None:
        window = self._task_window()
        if window is not None:
            window.set_busy(False)
            window.set_status(why)

    def _close_task_window(self) -> None:
        window = self._task_window()
        if window is not None:
            window.close()
        # The bridge holds an agent, a loaded conversation and a provider
        # client, and a window nobody has open should not be holding any of
        # them — *unless* a turn is still running on it. That turn is writing
        # into the task's own session, which is where the reader wanted it;
        # dropping the reference would not stop it, it would only mean nobody
        # is left to notice when it finishes or fails. So it is kept until it
        # is done, and released on the next close.
        if self._task_bridge is not None and self._task_bridge.busy:
            return
        self._task_bridge = None
        self._task_bridge_session = ""

    @on(PreviewComposer.PreviewSubmitted)
    def _task_composer_submitted(self, event) -> None:
        """The task window's composer asked to send."""
        event.stop()
        composer = self._maybe("#preview-composer", PreviewComposer)
        if composer is None:
            return
        message = composer.text.strip()
        composer.text = ""
        # A slash command in the task window acts on the *task*, not on the
        # console: this is the window onto one scheduled run, and "stop" here
        # can only sensibly mean "stop this run".
        lowered = message.lower().lstrip("/")
        if lowered in {"stop", "cancel", "halt"}:
            self.run_keyline_action("schedule-preview-stop")
            return
        if lowered in {"run", "go", "fire"}:
            self.run_keyline_action("schedule-preview-run")
            return
        if lowered in {"close", "quit", "exit"}:
            self.run_keyline_action("schedule-preview-close")
            return
        self._send_into_task(message)

    @on(ScheduleAction)
    def _schedule_action(self, event: ScheduleAction) -> None:
        """A control on the schedule pane was used."""
        if event.action == "schedule-mode":
            event.stop()
            pane = self._schedule()
            if pane is not None:
                pane.set_mode(event.value)

    @on(DataTable.RowSelected, "#schedule-table")
    def _task_selected(self, event: DataTable.RowSelected) -> None:
        """Selecting a task opens its window — the row's obvious meaning."""
        event.stop()
        self.run_keyline_action("schedule-preview")

    # ── Two consoles open at once ────────────────────────────────────────

    def _poll_shared_stores(self) -> None:
        """Adopt anything another copy of this console has changed.

        Everything the console shows that it does not own outright is here:
        the display preferences, the logbook, the scheduled tasks and which
        of them are running. All four live in files that a second console —
        or the gateway, or a plain ``curie`` in another terminal — writes to
        as freely as this one does, and until this existed the console read
        each of them exactly once and then believed its own copy for the rest
        of the session.

        Guarded as a whole. It runs on a timer, and a store that will not
        parse must degrade to a stale readout rather than to a traceback
        painted across the interface.
        """
        try:
            changed = self._sync.changes()
        except Exception:
            return
        if not changed:
            return
        if "settings" in changed:
            try:
                self._adopt_external_settings()
            except Exception:
                pass
        if "sessions" in changed:
            try:
                self._reload_logbook()
            except Exception:
                pass
        if changed & {"schedule", "executions"}:
            try:
                self._reload_schedule()
            except Exception:
                pass

    def _adopt_external_settings(self) -> None:
        """Re-read the display preferences and apply whatever moved.

        Compared field by field against what this console is *currently
        showing*, not applied wholesale. Two reasons, and both are visible
        faults rather than tidiness:

        * this console's own writes come back through the same file, so an
          unconditional re-apply would repaint the screen and print a notice
          every time the reader touched a switch;
        * the settings file carries far more than this console's four
          preferences, and something else writing an unrelated key must not
          cost a re-skin here.
        """
        settings = read_settings()
        moved: list[str] = []

        if settings.skin_mode != self._display_mode:
            self._display_mode = settings.skin_mode
            moved.append(
                "DOS mode on" if settings.dos_mode else "DOS mode off"
            )
        optics = settings.optics()
        if optics != self._optics:
            if optics.phosphor != self._optics.phosphor:
                moved.append(dos.get_phosphor(optics.phosphor).title.lower())
            elif optics.glow != self._optics.glow:
                moved.append(f"glow {dos.glow_label(optics.glow)}")
            elif optics.scanlines != self._optics.scanlines:
                moved.append(
                    "scanlines on" if optics.scanlines else "scanlines off"
                )
            elif optics.block_cursor != self._optics.block_cursor:
                moved.append(
                    "block cursor on" if optics.block_cursor
                    else "block cursor off"
                )
            self._optics = optics

        if settings.indicators != self._kit_name:
            self._apply_kit(settings.indicators)
            self._kit_chosen = settings.indicators_explicit
            moved.append(f"indicator set {settings.indicators!r}")

        if settings.workings_open != self._workings_open:
            self._workings_open = settings.workings_open
            self._apply_workings_open()
            moved.append(
                "workings open" if settings.workings_open else "workings shut"
            )
        if settings.scrollbars != self._scrollbars:
            self._scrollbars = settings.scrollbars
            self._apply_scrollbars()
            moved.append(
                "scroll bars on" if settings.scrollbars else "scroll bars off"
            )
        if settings.typeface != self._typeface:
            self._typeface = settings.typeface
            self._face = settings.face()
            moved.append(f"lettering {self._face.label()}")
        if settings.typeface_rows != self._typeface_rows:
            self._typeface_rows = settings.typeface_rows
            moved.append(f"title plate {settings.typeface_rows} rows")

        # The skin belongs to the CLI and the TUI as much as to this console,
        # so it is put back into the skin engine rather than merely noted:
        # ``resolve_palette`` asks the engine what is active, and the engine
        # in *this* process still holds whatever was active at start-up.
        try:
            from curie_cli.skin_engine import get_active_skin_name
        except Exception:
            get_active_skin_name = None  # type: ignore[assignment]
        if settings.skin and get_active_skin_name is not None:
            try:
                current_skin = str(get_active_skin_name() or "")
            except Exception:
                current_skin = ""
            if settings.skin != current_skin:
                if restore_active_skin(settings) == settings.skin:
                    moved.append(f"skin {settings.skin!r}")

        if not moved:
            return
        self._apply_display_mode()
        self._sync_reading_switches()
        self.call_after_refresh(self.paint_masthead)
        self._notify_panel(
            "Display settings changed in another Curie window — "
            + ", ".join(moved)
            + ". This console has caught up.",
            seconds=6.0,
        )

    def _save_setting(self, key: str, value) -> str:
        """Write one preference, and record the write as this console's.

        The recording is what keeps the poller quiet: without it, every
        switch the reader throws lands in the settings file, comes back on
        the next poll as somebody else's change, and gets announced to the
        person who just made it.
        """
        problem = write_setting(key, value)
        try:
            self._sync.mine("settings")
        except Exception:
            pass
        return problem

    def _reload_schedule(self) -> None:
        """Re-read the scheduled tasks. A no-op until the pane is mounted."""
        pane = self._maybe("#pane-schedule", SchedulePane)
        if pane is not None:
            try:
                pane.reload()
            except Exception:
                pass

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
            workings_open=self._workings_open,
            scrollbars=self._scrollbars,
            typeface=self._typeface,
            typeface_rows=self._typeface_rows,
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
        problem = self._save_setting(KEY_SKIN_MODE, self._display_mode)
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
        self._save_setting(KEY_INDICATORS, wanted)

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
            problem = self._save_setting(key, stored)
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
        held = (
            not self._optics.scanlines
            and dos.get_phosphor(self._optics.phosphor).animated
        )
        self._notify_panel(
            (
                "Scanlines on — every other raster line is drawn dark, in the "
                "chrome and in the state figures."
                if self._optics.scanlines
                else "Scanlines off — a progressive picture, no gaps."
            )
            # The one case where the switch does not get the last word, said
            # at the moment it is thrown rather than left to be discovered.
            + (
                "  The state figures keep theirs while this tube is on: its "
                "drift is a raster artefact, and a raster is what drifts. "
                "Choose another tube on the table above and the switch takes "
                "them back."
                if held
                else ""
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

    # ── The reading switches ─────────────────────────────────────────────

    def _toggle_workings_open(self) -> None:
        """Whether a turn's workings open with the drawer already down.

        Applied to the folds already on the transcript as well as to the ones
        still to come. A switch whose effect only shows up on the *next* tool
        call reads as a switch that did nothing — the reader throws it while
        looking at a fold, and the fold is what has to answer.
        """
        self._workings_open = not self._workings_open
        problem = self._save_setting(KEY_WORKINGS_OPEN, self._workings_open)
        self._apply_workings_open()
        self._sync_reading_switches()
        self._notify_panel(
            (
                "Workings open — the reasoning and tool calls behind an "
                "answer are shown as they happen, and the fold's heading "
                "still shuts them."
                if self._workings_open
                else "Workings shut — the fold's heading says work is "
                "running and how much of it there was; opening it shows the "
                "whole run."
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    def _apply_workings_open(self) -> None:
        """Push the setting at the bench pane, now and for every later fold."""
        pane = self._maybe("#pane-bench", BenchPane)
        if pane is not None:
            pane.set_workings_open(self._workings_open)

    def _toggle_scrollbars(self) -> None:
        """Whether the chat window carries a scroll bar."""
        self._scrollbars = not self._scrollbars
        problem = self._save_setting(KEY_SCROLLBARS, self._scrollbars)
        self._apply_scrollbars()
        self._sync_reading_switches()
        self._notify_panel(
            (
                "Scroll bars on — the chat window says how much conversation "
                "is above and below what you are reading."
                if self._scrollbars
                else "Scroll bars off — the chat window gives the column back "
                "to the conversation. The wheel, the keys and the mouse still "
                "scroll it."
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    def _apply_scrollbars(self) -> None:
        """Put the chat window's scroll bar where the setting says.

        A class rather than a stylesheet edit: ``refresh_css`` re-parses the
        whole document and costs a sixth of a second on this console, which
        is a visible stall for a switch. Toggling a class is a frame.
        """
        pane = self._maybe("#pane-bench", BenchPane)
        if pane is not None:
            pane.set_scrollbars(self._scrollbars)

    def _sync_reading_switches(self) -> None:
        """Put the two reading switches where this console currently is."""
        pane = self._maybe("#pane-panel", PanelPane)
        if pane is not None:
            pane.refresh_display_switches(self._settings_now())

    # ── F1: the lettering ────────────────────────────────────────────────

    def _restore_default_typeface(self) -> None:
        """F1 — put the console's lettering back to the face it ships with.

        The one key that undoes a font, whatever went wrong with it. A face
        that loaded and is not wanted, one that half-loaded, one that came in
        from another window, one hand-edited into ``config.yaml`` that this
        machine has no file for: all of them are the same keystroke, and after
        it the stored value is ``default`` with no case where it is not.

        It repaints either way, which is the half worth pressing when nothing
        is stored wrong: the chrome is redrawn and every entry on the
        transcript is restyled from the built-in alphabet.
        """
        was = self._face
        already = is_default_typeface(self._typeface)
        self._typeface = DEFAULT_TYPEFACE
        # Written every time, not only when the console can see something is
        # wrong. A spec it cannot resolve still resolves *to* the built-in
        # face, so "the console is already lettering in CP437" is not the same
        # question as "the file says default" — and the contract a restore key
        # keeps is about the file.
        problem = self._save_setting(KEY_TYPEFACE, DEFAULT_TYPEFACE)
        self._apply_typeface()
        self._notify_panel(
            f"Lettering: {CP437.title} — {CP437.blurb}."
            + (
                "  Already the console's own face; redrawn from it."
                if already
                else f"  {was.title} put away."
                if was.custom
                else "  Put back and written down."
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    def _set_typeface(self, spec: str) -> str:
        """Letter with ``spec``, apply it and write it down. Returns a problem.

        The problem returned is the *save's*, not the font's: a font that will
        not load is not a failure to record the choice, and the reader has to
        be told the two apart. What the font did is on ``self._face`` after
        this returns.
        """
        self._typeface = str(spec or DEFAULT_TYPEFACE).strip() or DEFAULT_TYPEFACE
        problem = self._save_setting(KEY_TYPEFACE, self._typeface)
        self._apply_typeface()
        return problem

    def _step_plate_rows(self, delta: int) -> None:
        """Make the lettered plate taller or shorter, a row at a time."""
        rows = clamp_rows(self._typeface_rows + delta)
        if rows == self._typeface_rows:
            self._notify_panel(
                "The plate is already at its "
                + ("shortest." if delta < 0 else "tallest.")
            )
            return
        self._typeface_rows = rows
        problem = self._save_setting(KEY_TYPEFACE_ROWS, rows)
        self._apply_typeface()
        self._notify_panel(
            f"Title plate {rows} rows."
            + (
                "  It letters at this height once a font is set — F6 → FONT."
                if not self._face.custom
                else ""
            )
            + (f"  (not saved: {problem})" if problem else "")
        )

    def _apply_typeface(self) -> None:
        """Re-resolve the face and redraw everything the lettering reaches."""
        self._face = resolve_face(self._typeface, builtin_title=CP437.title)
        self._apply_display_mode()
        self.call_after_refresh(self.paint_masthead)
        pane = self._maybe("#pane-panel", PanelPane)
        if pane is not None:
            try:
                pane.reload_fonts(self._settings_now(), self._face)
            except Exception:
                pass

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
        self._compacting = False
        self._compaction_detail = ""
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
            elif event.kind == "compacting":
                self._compaction_started(pane, event.text)
            elif event.kind == "compacted":
                self._compaction_finished(pane, event.text)
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
        # A turn that ended while a compaction was still open (interrupted,
        # or a compaction that failed outright) must not leave the hold on:
        # the instrument would sit on RECORDING with no turn behind it and
        # the console would look busy forever.
        self._compacting = False
        self._compaction_detail = ""
        self._set_activity(READY)
        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.set_lamp("LOG", "off")
        tape = self._maybe("#tape-turn", TapeMeter)
        if tape is not None:
            tape.set_fraction(0.0)
        facts = self.bridge.describe()
        self._set_subject(facts)
        # The reading the turn just earned, taken now rather than up to a
        # second later: the end of a turn is the moment the needle is most
        # worth being right, because it is the figure the reader is left
        # looking at until they ask something else.
        self._tick_context()
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

    def _tick_window(self) -> None:
        """Notice a resize the terminal never told us about.

        Belt and braces on :func:`live_terminal_size`. That takes away the
        stale ``COLUMNS``/``LINES`` a resize would be read through; this
        covers every *other* way the notification can go missing — a SIGWINCH
        that does not arrive, a multiplexer that swallows it, a terminal that
        negotiates in-band resize reporting and then does not send any. The
        symptom is the same in all of them and it is the one that was
        reported: the console holds the size it started at while the window
        grows around it, and the rows it never claimed keep showing whatever
        was on the screen before it opened.

        An ioctl a second, and only when the terminal can answer at all — so
        it is silent under a test harness and on a piped stdout, where there
        is no size to disagree with. It posts the same event the driver posts,
        with the same arguments, so nothing downstream can tell where the
        resize came from; and it posts only on a real difference, so applying
        one cannot start another.
        """
        if not terminal_answers_for_itself():
            return
        try:
            columns, rows = os.get_terminal_size(sys.__stdout__.fileno())
        except (AttributeError, ValueError, OSError):
            return
        if columns <= 0 or rows <= 0:
            return
        size = Size(columns, rows)
        if size == self.size:
            return
        self.post_message(events.Resize(size, size))

    def _tick_context(self) -> None:
        """Put the context gauge where the conversation actually is.

        The needle used to be set from ``gauge.value`` — its own reading —
        with only the ceiling coming from anywhere real, so it was written
        every turn and never moved off zero. The figure comes from the bridge
        now (see :meth:`AgentBridge.context_usage`), which takes it from the
        same places the CLI's status bar does.

        A reading the bridge cannot take leaves the needle where it is rather
        than dropping it to zero. There is no moment at which a conversation's
        context becomes unknown *and* becomes empty, so a gauge that fell back
        to zero would be reporting something that never happens.
        """
        gauge = self._maybe("#gauge-context", DialGauge)
        if gauge is None:
            return
        try:
            reading = self.bridge.context_usage()
        except Exception:
            return
        if reading is None:
            return
        tokens, ceiling = reading
        gauge.set_reading(tokens, ceiling)

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
        if self._compacting:
            # A compaction owns the instrument for as long as it runs. Any
            # other event landing mid-pause — a tool that reports finishing,
            # a status line — would otherwise take the figure off compaction
            # and leave the longest silence in the turn unexplained.
            state, detail = SAVING, self._compaction_detail or "compacting"
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

    # ── Compaction ───────────────────────────────────────────────────────

    def _compaction_started(self, pane, what: str) -> None:
        """The conversation is being compacted; the turn continues after.

        Written into the transcript as well as onto the instruments, and
        that is the point of it. A compaction is the longest silence a turn
        can contain — a second model call over the whole conversation — and
        every other way the console had of reporting it expired: the notice
        after eight seconds, the state figure the moment any other event
        landed. What the reader was left with was a console that stopped for
        half a minute and then carried on from a conversation that had
        visibly changed, with nothing anywhere saying why.

        The line is written once per compaction, not once per progress
        status: the agent emits several (preflight, retry, context reduced)
        and they are one event to the person reading them.
        """
        # A caption, not the sentence. The agent's own line is a sentence
        # with a pictogram on the front, and the instrument's caption row is
        # twenty-four columns beside a bold state name — the sentence arrives
        # there as its own first four words and an ellipsis, which says less
        # than one word would. The sentence is in the transcript, in full,
        # where there is room for it.
        detail = _compaction_caption(what)
        self._compaction_detail = detail
        if not self._compacting:
            self._compacting = True
            if pane is not None:
                pane.write("note", f"  ▤ {what.strip()}")
            lamps = self._maybe("#titlebar-lamps", PanelLamps)
            if lamps is not None:
                lamps.set_lamp("REC", "warn")
        # Held, not pulsed: this state owns the instrument until the done
        # edge arrives, so a stray tool_done cannot hand it back mid-pause.
        self._set_activity(SAVING, detail)

    def _compaction_finished(self, pane, what: str) -> None:
        """Compaction is over and the turn is resuming.

        The done edge is the half that was missing. Without it the console
        had no way to know the pause had ended, so the state figure stayed on
        RECORDING for the rest of the turn — which is what "once it does, the
        agent doesn't continue" looks like from the outside, whether or not
        the agent is in fact answering.
        """
        was_compacting = self._compacting
        self._compacting = False
        self._compaction_detail = ""
        if was_compacting and pane is not None:
            pane.write("note", f"  ▤ {what.strip()}")
        lamps = self._maybe("#titlebar-lamps", PanelLamps)
        if lamps is not None:
            lamps.flash("REC")
            # Back to whatever the turn's own lamp state is: lit while a turn
            # is running, dark when it is not.
            lamps.set_lamp("LOG", "warn" if self.bridge.busy else "off")
        # Straight back to what the turn is actually doing. Asked of the
        # console's own record of the turn rather than assumed to be
        # THINKING: compaction can be triggered before the first token
        # (preflight) or between two tool calls, and each of those resumes
        # into a different state.
        self._set_activity(*self._state_after_compaction())
        # The compacted transcript is written to the store as part of the
        # rotation, so the logbook's row for this conversation is stale the
        # moment compaction lands.
        self._reload_logbook()

    def _state_after_compaction(self) -> tuple:
        """The state the turn goes back to once the pause is over."""
        if not self.bridge.busy:
            return (READY, "")
        if self._running_tools:
            return (TOOL, _shorten(self._running_tools[-1], 22))
        if self._streaming:
            return (STREAMING, "")
        return (WAITING, "resuming")

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
        problem = self._save_setting(KEY_INDICATORS, name)
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
        tasks = self._maybe("#pane-schedule", SchedulePane)
        if tasks is not None:
            try:
                tasks.restyle()
            except Exception:
                pass
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
        problem = self._save_setting(KEY_SKIN, name)
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

    @on(DataTable.RowSelected, "#font-table")
    def _font_selected(self, event: DataTable.RowSelected) -> None:
        """Letter with the chosen font. DEFAULT is the built-in alphabet."""
        try:
            row = event.data_table.get_row_at(event.cursor_row)
        except Exception:
            return
        name = str(row[0]).replace("▶", "").strip()
        where = str(row[1]).strip() if len(row) > 1 else ""
        if not name:
            return
        if name.lower() == DEFAULT_TYPEFACE:
            self._restore_default_typeface()
            return
        # The name rather than the path in the second column, when the name is
        # enough: a name is what the reader can type into config.yaml
        # themselves and what another machine with the same font in a
        # different folder still resolves. The path is the fallback, and it is
        # the only thing that works for the one row that came *from* a path —
        # a font outside the font folders is not findable by name at all.
        spec = name
        if resolve_face(name, builtin_title=CP437.title).problem and where:
            spec = where
        problem = self._set_typeface(spec)
        face = self._face
        if face.problem:
            self._notify_panel(
                f"{name} could not be lettered with — {face.problem}.  "
                "The console is drawing its own alphabet until one loads.",
                seconds=10.0,
            )
            return
        self._notify_panel(
            f"Lettering: {face.label()} — the title plate above the "
            "conversation, and the sample here.  The body text stays in your "
            "terminal's own font; nothing running inside a terminal can "
            "change that.  F1 puts the built-in lettering back."
            + (f"  (not saved: {problem})" if problem else ""),
            seconds=10.0,
        )

    def _rescan_fonts(self) -> None:
        """Re-read the font folders. This is what RESCAN runs.

        A fresh listing every time rather than a cache, for the reason the
        voice folder's REFRESH exists: the button is pressed because the
        folder has just changed, and a cached answer would make it do nothing
        visible.
        """
        pane = self._maybe("#pane-panel", PanelPane)
        if pane is None:
            return
        forget_rendered()
        self._face = resolve_face(self._typeface, builtin_title=CP437.title)
        pane.reload_fonts(self._settings_now(), self._face)
        found = len(system_fonts())
        self._notify_panel(
            f"Font folders re-read — {found} font(s) found.  "
            "Anything not listed can still be set by path:  "
            "curie config set ui.typeface /path/to/Font.ttf",
            seconds=8.0,
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
        """Re-read the logbook table, keeping the bench's row flagged.

        Deferred while the pane is off screen, and that is not an
        optimisation for its own sake. Every write to the conversation store
        is a reason to re-read this — and with a second console open, or a
        gateway running scheduled tasks, writes arrive continuously. Reading
        three hundred sessions out of SQLite on the UI thread twice a second
        while a reply is streaming is a stutter in the one surface that must
        not have one, to refresh a table nobody is looking at.

        The mark is carried rather than dropped: it names the conversation on
        the bench, and the point of it is to be right when the reader next
        looks at the pane.
        """
        if mark:
            self._logbook_mark = mark
        pane = self._maybe("#pane-logbook", LogbookPane)
        if pane is None:
            return
        if not pane.display:
            self._logbook_stale = True
            return
        self._logbook_stale = False
        try:
            pane.reload()
            pane.mark_active(
                self._logbook_mark or self.bridge.session_id or ""
            )
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
        lettering = self._plate_lettering(plate)
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
                dos.masthead(
                    self.bench_palette,
                    width,
                    self._plate_subject(),
                    lettering,
                )
            )
            plate.display = not self._chrome_hidden
            return
        dim, accent = self.bench_palette["dim"], self.bench_palette["accent"]
        if lettering:
            # The wordmark as a picture rather than as a string, centred on
            # the *block* and never on each row: the rows are slices of one
            # rendered image and share an origin, so centring them one at a
            # time shears the word into a diagonal.
            block = max(len(row) for row in lettering)
            room = (plate.content_size.width or plate.size.width or block + 4) - 4
            lead = " " * max(0, (room - block) // 2)
            body = Text(no_wrap=True, overflow="crop")
            for index, row in enumerate(lettering):
                if index:
                    body.append("\n")
                body.append(lead)
                body.append(row.ljust(block), style=f"bold {accent}")
        else:
            body = Text("CURIE AGENT — BENCH TERMINAL", style=f"bold {accent}")
        plate.update(
            Panel(
                body,
                border_style=dim,
                box=_HEAVY_BOX,
                padding=(0, 1),
            )
        )
        plate.display = not self._chrome_hidden

    def _plate_lettering(self, plate: Static) -> "list[str]":
        """The wordmark rendered in the chosen font, or ``[]`` for the built-in.

        Measured off the plate rather than the window: the rail and the
        instrument stack take columns off this widget and a wordmark sized to
        the window would run past the frame. Off the window only where the
        first layout pass has not happened yet and the widget's own width is
        still zero.

        The wordmark shortens before it shrinks. A long string fitted to a
        narrow plate comes out as a line of grey specks — the renderer will
        happily letter "CURIE AGENT — BENCH TERMINAL" at two pixels tall — so
        the longest title that still lands above a legible size is the one
        drawn, which is the same ladder the DOS plate already walks for its
        own title.
        """
        if not self._face.custom:
            return []
        width = plate.content_size.width or plate.size.width
        if not width:
            width = max(24, self.size.width - 4)
        # Two for the frame, two for the padding inside it.
        columns = width - 4
        if columns < 12:
            return []
        return render_wordmark(
            self._face,
            dos.MASTHEAD_TITLES,
            rows=self._typeface_rows,
            columns=columns,
        )

    def _plate_subject(self) -> str:
        """What the DOS plate's second row says on its left: the model."""
        facts = self.bridge.describe()
        model = str(facts.get("model") or "").strip()
        return model or "no model configured"

    def _write_help(self) -> None:
        self._write("note", "")
        self._write("head", "▮ COMMAND INDEX")
        # Where the reader should be looking once the whole thing is written.
        # The index is longer than the pane, and a transcript that follows its
        # tail would otherwise open it at the *end* — heading gone, first
        # dozen keys gone, and no sign that there was a beginning.
        pane = self._bench()
        opening = pane.mark() if pane is not None else None
        rows = [
            ("Enter", "send the composed request"),
            ("Shift+Enter", "newline inside the composer"),
            ("Ctrl+J", "newline — works in every terminal"),
            ("Ctrl+C", "stop the running turn"),
            ("Ctrl+L", "clear the transcript"),
            ("Ctrl+Q", "close the console"),
            ("Ctrl+O", "this index"),
            ("F1", "put the lettering back to the font the console ships with"),
            ("F2 … F6, F9", "throw a switch on the rail"),
            ("Ctrl+T", "the SCHEDULE pane — automated tasks"),
            ("F7", "show or hide the rail"),
            ("F8", "show or hide the instrument stack"),
            ("F10", "show or hide the title plate and the key line"),
            ("F6 → DISPLAY", "re-skin as a DOS phosphor terminal"),
            ("F6 → READING", "open the workings by default; the scroll bar"),
            ("F6 → FONT", "letter the title plate with a font of your own"),
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
            "  The rail: BENCH runs turns · SCHEDULE is the automated tasks "
            "— EVERY counts down (45s, 2h), AT is a wall clock (Mondays at "
            "19:32), CRON is anything else; each run is a conversation of "
            "its own and WINDOW opens it, with its own composer and its own "
            "STOP · LOGBOOK lists every past conversation, and selecting one "
            "loads it here · INSTRUMENTS shows the model and route · SUPPLY "
            "lists toolsets, skills and MCP servers · PANEL sets the display "
            "mode, the skin, the reading switches, the font, the indicator "
            "set and voice · DIAGNOSTICS reports install health.",
        )
        self._write("note", "")
        self._write(
            "note",
            "  The font: PANEL → FONT letters the title plate with any font "
            "on this machine — pick one from the list, or give a path with "
            "curie config set ui.typeface /path/to/Font.ttf. It reaches the "
            "console's display type and not its body text: those glyphs are "
            "painted by your terminal out of the font the terminal is set "
            "to, and no program running inside one can change that. F1 puts "
            "the built-in lettering back.",
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
        if pane is not None:
            # After the last write, and deferred: the block's position is not
            # settled until the layout pass that mounting the rest caused.
            self.call_after_refresh(lambda: pane.reveal(opening))

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


#: What the state instrument says beside RECORDING while a compaction runs,
#: keyed on which of the agent's compaction phases produced the line. Short
#: enough for the caption row, and different enough between phases that a
#: reader watching a long pause can see it move.
_COMPACTION_CAPTIONS: tuple[tuple[str, str], ...] = (
    ("preflight", "preflight"),
    ("pre-api", "pre-API"),
    ("idle", "after idle"),
    ("too large", "oversize"),
    ("retrying", "retrying"),
    ("context reduced", "reduced"),
)


def _compaction_caption(text: str) -> str:
    """One short word for the compaction phase a status line reports."""
    lowered = " ".join(str(text or "").split()).lower()
    for needle, caption in _COMPACTION_CAPTIONS:
        if needle in lowered:
            return f"compacting · {caption}"
    return "compacting"


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


#: The two environment variables that can lie about the terminal's size.
#: Named here rather than inline because :func:`live_terminal_size` and its
#: tests both need to agree on exactly which ones are dropped.
SIZE_ENV = ("COLUMNS", "LINES")


def terminal_answers_for_itself() -> bool:
    """Whether the terminal can be measured without the environment.

    Asked of ``sys.__stdout__`` specifically, because that is the stream
    ``shutil.get_terminal_size`` falls back to — checking a different one
    would answer a question nobody is going to ask.
    """
    stream = sys.__stdout__
    try:
        os.get_terminal_size(stream.fileno())
    except (AttributeError, ValueError, OSError):
        return False
    return True


@contextmanager
def live_terminal_size() -> "Iterator[None]":
    """Take ``COLUMNS``/``LINES`` out of the way while the console is up.

    The reported fault: the console does not reach the bottom of the window,
    and the taller the window is made the more of the old terminal shows
    underneath it — a strip of the shell's own scrollback along the bottom
    that grows every time the window grows.

    The cause is a stale environment. Textual asks
    ``shutil.get_terminal_size()`` for the size, at start-up *and* on every
    SIGWINCH; that function reads ``COLUMNS`` and ``LINES`` first and only
    falls through to the ioctl when they are unset. Anything that exports
    them — a shell with them exported, a wrapper script, ``script``, a job
    runner — therefore pins the console at whatever size was current when
    they were set, for the whole run. Grow the window and Textual re-reads
    the same frozen numbers, lays out to them, and paints nothing below: the
    rows the app never claimed are still showing what was on the screen
    before it started, and there are more of them the taller the window gets.

    So they go, for the duration, and come back on the way out — restored
    rather than dropped because they are the shell's variables, not this
    program's, and something downstream may be reading them.

    Not dropped when the terminal *cannot* answer for itself: with no tty on
    stdout, ``shutil`` falls back to a flat 80×24 and the environment is the
    only real size information there is. Taking it away there would trade a
    stale size for a wrong one.
    """
    saved = {name: os.environ[name] for name in SIZE_ENV if name in os.environ}
    if not saved or not terminal_answers_for_itself():
        yield
        return
    for name in saved:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        os.environ.update(saved)


def run(**kwargs) -> int:
    """Launch the console. Returns a process exit code."""
    app = BenchConsole(**kwargs)
    try:
        with live_terminal_size():
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


__all__ = [
    "BenchConsole",
    "live_terminal_size",
    "run",
    "SIZE_ENV",
    "SWITCHES",
    "terminal_answers_for_itself",
]
