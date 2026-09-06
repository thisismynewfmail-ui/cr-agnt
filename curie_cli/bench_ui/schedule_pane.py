"""The SCHEDULE pane: automated tasks, and a window onto the one that is running.

Three surfaces in one pane, because they are three views of one subject and
putting them behind separate switches would make the reader navigate between
halves of a single decision:

* **the list** — every scheduled task, what it is waiting for, when it last
  ran and whether it is running right now;
* **the form** — one task's whole definition, with the schedule built from
  controls rather than typed as a string somebody has to know the grammar of;
* **the task window** — a small chat onto one task's most recent run, with its
  own composer and its own stop, docked under the list rather than floating
  over it.

The window is docked and not floating on purpose. A floating panel has to be
positioned, and a positioned panel is wrong at some window size — but more
than that, a floating panel *covers* the thing it was opened from, so reading
a run means losing sight of the task list it belongs to. Docked, the two share
the pane and every state reflows: the list shrinks, the form shrinks, the
window grows when asked to and gives the rows back when closed.

Nothing here schedules anything. Every read and write goes through
:mod:`curie_cli.bench_ui.schedule`, which goes through Curie's own job store —
so a task made here is fired by the gateway on the same clock as one made with
``curie cron create``, runs whether or not this console is open, and appears in
a second console the moment it is written.
"""

from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import DataTable, Input, Static, TextArea

from curie_cli.bench_ui import dos, schedule
from curie_cli.bench_ui.panes import (
    Composer,
    PanelButton,
    ToggleSwitch,
    _app_palette,
    _head,
    _is_dos,
    _note,
)

#: The task table, as ``(heading, width, keep when narrow)``. Widths are
#: fixed for every column but the name, which takes whatever is left — a task
#: table where the *name* is the truncated column is a table of tasks the
#: reader cannot tell apart, and every other column here has a known maximum
#: shape ("● ARMED", "in 1d6h") that does not need measuring.
TASK_COLUMNS: tuple[tuple[str, int, bool], ...] = (
    # Nine, because "▶ RUNNING" is nine and a truncated state is a different
    # word: "▶ RUNNIN" reads as a rendering fault in the one column whose
    # whole job is to be read at a glance.
    ("STATE", 9, True),
    ("TASK", 0, True),
    ("WHEN", 16, False),
    ("NEXT", 9, True),
    ("LAST RUN", 12, True),
    ("RUNS", 5, False),
)

#: The narrowest the name column is allowed to get before the two optional
#: columns are dropped instead. Below this a name is an abbreviation.
NAME_MIN = 16

#: Below this many columns the table drops the two columns a reader can do
#: without: the schedule (which the form shows in full, and the summary line
#: above the table counts) and the run tally. Chosen against the *pane*, not
#: the window — by the time it matters the rail and the instrument stack have
#: already collapsed — and only used as a floor: the table re-measures itself
#: against its own width on every draw, so this is the point below which even
#: that cannot save the name column.
TABLE_COMPACT_AT = 92

#: The task window's two heights, as a fraction of the pane. The small one
#: leaves the task list readable underneath; the large one is for working in a
#: run. Neither is large enough to push the action row off the pane — the row
#: is docked, so it cannot be — and neither is so small that the window is a
#: letterbox.
WINDOW_HEIGHT = "45%"
WINDOW_HEIGHT_LARGE = "72%"


class ScheduleAction(Message):
    """A control on this pane was used. Handled by the console."""

    def __init__(self, action: str, value: str = "") -> None:
        super().__init__()
        self.action = action
        self.value = value


class FieldRow(Horizontal):
    """One labelled control on the form: a stencilled name, then the control.

    A row rather than a label above the field, because the form has eleven
    controls and stacking a caption over each doubles its height — which on a
    thirty-row terminal is the difference between a form and a form that
    scrolls.
    """

    def __init__(self, label: str, *children, **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._children = children
        self.add_class("field-row")

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="field-name")
        for child in self._children:
            yield child


class Cycler(Static):
    """A control that steps through a fixed list of choices when clicked.

    Not a dropdown. A dropdown is a second surface that opens over the form,
    has to be positioned, closes on the wrong click and needs its own keyboard
    handling — for choosing between four units. A cycler shows the current
    choice, says what the next one is, and takes one click to get there.
    """

    def __init__(self, key: str, choices, value: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.cycler_key = key
        self.choices = tuple(choices)
        self._value = value or (self.choices[0][0] if self.choices else "")
        self.add_class("cycler")

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if any(value == key for key, *_rest in self.choices):
            self._value = value
            self.refresh()

    def step(self) -> str:
        keys = [key for key, *_rest in self.choices]
        if not keys:
            return self._value
        try:
            index = keys.index(self._value)
        except ValueError:
            index = -1
        self._value = keys[(index + 1) % len(keys)]
        self.refresh()
        return self._value

    def label_for(self, value: str) -> str:
        for key, label, *_rest in self.choices:
            if key == value:
                return label
        return value

    def render(self) -> Text:
        palette = _app_palette(self)
        band = _is_dos(palette)
        out = Text(no_wrap=True, overflow="ellipsis")
        # The mark is what says this is a control and not a readout — a bare
        # word in a form reads as a value somebody else set.
        out.append("[" if band else "‹ ", style=palette["dim"])
        out.append(self.label_for(self._value), style=f"bold {palette['accent']}")
        out.append("]" if band else " ›", style=palette["dim"])
        return out

    def on_click(self) -> None:
        self.step()
        self.post_message(ScheduleAction("schedule-form-changed"))


class ModeTab(Static):
    """One of the three ways to say when. Clickable; shows which is chosen."""

    def __init__(self, key: str, label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.mode_key = key
        self.mode_label = label
        self.add_class("mode-tab")

    def render(self) -> Text:
        palette = _app_palette(self)
        chosen = self.has_class("-active")
        band = _is_dos(palette)
        out = Text(no_wrap=True)
        if band:
            # On the phosphor the chosen tab is an inverse-video run, which is
            # how a text-mode program said "this one" — a colour alone is not
            # a distinction the mode has to spend.
            style = f"bold {palette['ink'] if chosen else palette['foreground']}"
        else:
            style = f"bold {palette['accent'] if chosen else palette['dim']}"
        out.append(f" {self.mode_label} ", style=style)
        return out

    def on_click(self) -> None:
        self.post_message(ScheduleAction("schedule-mode", self.mode_key))


class PreviewComposer(Composer):
    """The task window's own input. Enter sends into *that* conversation.

    A subclass rather than a second ``id`` on the same class, because the
    console's Enter handler finds the composer by type as well as by id, and
    a message typed into a task window must never be able to arrive in the
    main chat instead. The two are different objects and post different
    messages; there is no path from one to the other.
    """

    class PreviewSubmitted(Message):
        """The task window's composer asked for its contents to be sent."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key in self.NEWLINE_KEYS:
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.PreviewSubmitted())
            return
        await TextArea._on_key(self, event)


class TaskWindow(Vertical):
    """A small chat onto one scheduled task's most recent run.

    Every run of a task is its own conversation — the scheduler opens a fresh
    session for each fire, with no prior context, which is what makes a task
    reproducible. So there is a real chat to show, and this shows it: the
    messages of that run, a composer that continues it, and a stop that
    reaches the process actually running it.

    Read live. The run is happening in the gateway, not here, so the window
    polls its conversation while it is open rather than waiting to be told —
    there is nothing to tell it.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task_id = ""
        self.task_name = ""
        self.session_id = ""
        self._runs: list = []
        self._run_index = 0
        self._shown: list = []
        self._large = False
        self._status = ""
        self._busy = False
        #: Confirming scroll passes still owed. See :meth:`_to_end`.
        self._settle = 0

    # ── Layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="preview-bar"):
            yield Static("", id="preview-title")
            yield PanelButton("◂ OLDER", "schedule-preview-older")
            yield PanelButton("NEWER ▸", "schedule-preview-newer")
            yield PanelButton("STOP", "schedule-preview-stop")
            yield PanelButton("RUN", "schedule-preview-run")
            yield PanelButton("SIZE", "schedule-preview-size")
            yield PanelButton("CLOSE", "schedule-preview-close")
        yield VerticalScroll(id="preview-log")
        yield Static("", id="preview-status")
        with Horizontal(id="preview-composer-row"):
            yield Static("▶", id="preview-caret")
            editor = PreviewComposer(id="preview-composer", soft_wrap=True)
            editor.show_line_numbers = False
            yield editor

    # ── Opening and closing ──────────────────────────────────────────────

    def open_for(self, task: schedule.Task) -> None:
        """Point the window at a task and draw its newest run."""
        changed = task.id != self.task_id
        self.task_id = task.id
        self.task_name = task.name
        if changed:
            self._run_index = 0
            self._shown = []
            self._status = ""
        self.display = True
        self.reload_runs()
        self.paint_title()
        try:
            self.query_one("#preview-composer", PreviewComposer).focus()
        except Exception:
            pass

    def close(self) -> None:
        self.display = False
        self.task_id = ""
        self.session_id = ""
        self._shown = []
        self._status = ""
        # A window reopened onto another task must not inherit the last one's
        # "waiting for a reply", which would refuse the composer for a turn
        # that is not this task's and may not exist any more.
        self._busy = False

    @property
    def is_open(self) -> bool:
        return bool(self.display and self.task_id)

    def toggle_size(self) -> None:
        self._large = not self._large
        self.styles.height = WINDOW_HEIGHT_LARGE if self._large else WINDOW_HEIGHT

    # ── Content ──────────────────────────────────────────────────────────

    def reload_runs(self) -> None:
        """Re-read which runs exist, keeping the one being looked at."""
        if not self.task_id:
            return
        self._runs = schedule.task_runs(self.task_id)
        if not self._runs:
            self.session_id = ""
            self.render_messages([])
            return
        self._run_index = max(0, min(self._run_index, len(self._runs) - 1))
        self.session_id = str(self._runs[self._run_index].get("id") or "")
        self.refresh_conversation()

    def step_run(self, delta: int) -> str:
        """Move to an older (+1) or newer (-1) run of the same task."""
        if not self._runs:
            return "This task has not run yet, so there is nothing to look at."
        target = self._run_index + delta
        if target < 0:
            return "That is the newest run."
        if target >= len(self._runs):
            return "That is the oldest run this task still has."
        self._run_index = target
        self.session_id = str(self._runs[target].get("id") or "")
        self.refresh_conversation()
        self.paint_title()
        return ""

    def refresh_conversation(self) -> None:
        """Re-read the run's messages, and redraw only if they moved.

        The comparison matters: this runs on a timer while the window is open,
        and rebuilding the message widgets on every tick would reset the
        reader's scroll position twice a second — a run they were reading
        would keep snapping back to the bottom.
        """
        if not self.session_id:
            self.render_messages([])
            return
        messages, problem = schedule.read_conversation(self.session_id)
        if problem:
            self.set_status(problem)
            return
        if messages == self._shown:
            return
        self._shown = messages
        self.render_messages(messages)

    def render_messages(self, messages: list) -> None:
        log = self._log()
        if log is None:
            return
        try:
            log.remove_children()
        except Exception:
            return
        if not self.task_id:
            return
        if not messages:
            log.mount(
                Static(
                    "  This task has not produced a conversation yet. It will "
                    "when it next fires — RUN starts one now.",
                    classes="preview-empty",
                )
            )
            return
        palette = _app_palette(self)
        marks = dos.ENTRY_MARKS if _is_dos(palette) else _BENCH_MARKS
        rows = []
        for role, text in messages:
            full, _short, tone = marks.get(
                "user" if role == "user" else "reply", ("", "", "dim")
            )
            line = Text(no_wrap=False)
            line.append(f"{full} ", style=f"bold {palette[tone]}")
            line.append(text, style=palette["foreground"])
            rows.append(Static(line, classes="preview-entry"))
        log.mount_all(rows)
        # After the mount, not during: the scroll target does not exist until
        # the layout pass the mount causes has run.
        self._settle = 1
        self.call_after_refresh(self._to_end)

    def _to_end(self) -> None:
        """Put the newest message at the bottom, and confirm once.

        The confirming pass is not belt and braces. Mounting the messages
        makes the layout, and the layout is what gives the content its true
        height — so the first scroll runs against a height that is one pass
        stale and lands a row or two short. In a window three rows tall that
        is the difference between the reply and the gap above it. One extra
        pass, bounded, because a loop here is a repaint that never stops.
        """
        log = self._log()
        if log is None:
            return
        try:
            log.scroll_end(animate=False, immediate=True)
        except Exception:
            return
        if getattr(self, "_settle", 0) > 0:
            self._settle -= 1
            self.call_after_refresh(self._to_end)

    def _log(self):
        try:
            return self.query_one("#preview-log", VerticalScroll)
        except Exception:
            return None

    # ── Chrome ───────────────────────────────────────────────────────────

    def paint_title(self) -> None:
        palette = _app_palette(self)
        title = self._maybe("#preview-title")
        if title is None:
            return
        band = _is_dos(palette)
        tone = palette["ink"] if band else palette["accent"]
        faint = palette["inkdim"] if band else palette["dim"]
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append(self.task_name or "task", style=f"bold {tone}")
        if len(self._runs) > 1:
            out.append(f"  run {self._run_index + 1}/{len(self._runs)}", style=faint)
        # The run's timestamp, not its whole session id. The id is the task's
        # id with a stamp on the end, and the task's id is already said by the
        # name beside it — so all thirty-odd columns of it buy is the one part
        # that differs between two runs, which is the stamp.
        stamp = _run_stamp(self.session_id)
        if stamp:
            out.append(f"  {stamp}", style=faint)
        title.update(out)
        # Only when there is somewhere to step to. Two buttons that always
        # answer "that is the newest run" are twenty columns the title needs.
        many = len(self._runs) > 1
        for selector in ("schedule-preview-older", "schedule-preview-newer"):
            for button in self.query(PanelButton):
                if button.button_action == selector:
                    button.display = many
        self.border_title = "TASK WINDOW" if band else ""

    def set_status(self, text: str) -> None:
        self._status = text
        widget = self._maybe("#preview-status")
        if widget is None:
            return
        palette = _app_palette(self)
        widget.update(Text(f"  {text}", style=palette["warning" if text else "dim"]))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

    @property
    def busy(self) -> bool:
        return self._busy

    def restyle(self) -> None:
        """Repaint everything this window colours itself, after a skin change."""
        self.paint_title()
        self.set_status(self._status)
        self.render_messages(self._shown)
        for widget in self.query(PanelButton):
            widget.refresh()

    def _maybe(self, selector: str):
        try:
            return self.query_one(selector, Static)
        except Exception:
            return None


#: The bench palette's speaker marks for the task window, in the same shape
#: ``dos.ENTRY_MARKS`` uses so one lookup serves both modes.
_BENCH_MARKS = {
    "user": ("▶ YOU", "▶", "primary"),
    "reply": ("▮ TASK", "▮", "accent"),
}


class SchedulePane(Vertical):
    """Scheduled tasks: the list, the editor, and a window onto a run."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tasks: list = []
        self._editing: str = ""
        self._spec = schedule.ScheduleSpec()
        self._compact = False

    # ── Layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield _head("SCHEDULE — automated tasks")

        with Vertical(id="schedule-list"):
            yield Static("", id="schedule-summary")
            yield DataTable(
                id="schedule-table", cursor_type="row", zebra_stripes=False
            )
            # Docked, so a tall task window takes its rows from the table
            # and never from the controls. Undocked they were simply clipped:
            # opening the window at its larger size took the whole action row
            # off the pane, leaving a reader with no way to close the thing
            # that had just covered the buttons.
            with Vertical(id="schedule-footer"):
                with Horizontal(id="schedule-actions"):
                    yield PanelButton("NEW", "schedule-new")
                    yield PanelButton("EDIT", "schedule-edit")
                    yield PanelButton("RUN NOW", "schedule-run")
                    yield PanelButton("PAUSE", "schedule-pause")
                    yield PanelButton("STOP", "schedule-stop")
                    yield PanelButton("WINDOW", "schedule-preview")
                    yield PanelButton("DELETE", "schedule-delete")
                yield Static("", id="schedule-scheduler-note", classes="note")

        with VerticalScroll(id="schedule-form"):
            yield Static("", id="schedule-form-head", classes="section-head")
            yield FieldRow(
                "NAME",
                Input(placeholder="what this task is for", id="schedule-name"),
            )
            yield Static("PROMPT", classes="field-name-block")
            editor = TextArea(id="schedule-prompt", soft_wrap=True)
            editor.show_line_numbers = False
            yield editor
            yield _note(
                "  Each run is a fresh conversation with no prior context, so "
                "the prompt has to stand on its own."
            )

            with Horizontal(id="schedule-modes"):
                yield Static("WHEN", classes="field-name")
                for key, label, _blurb in schedule.MODES:
                    yield ModeTab(key, label, id=f"schedule-mode-{key}")

            yield FieldRow(
                "EVERY",
                Input(value="1", id="schedule-every-value"),
                Cycler("unit", schedule.UNITS, "h", id="schedule-every-unit"),
                id="schedule-row-every",
            )
            yield FieldRow(
                "ON",
                Cycler("day", schedule.DAYS, "day", id="schedule-day"),
                Input(value="09:00", id="schedule-time", placeholder="HH:MM"),
                Input(value="", id="schedule-date", placeholder="YYYY-MM-DD"),
                id="schedule-row-at",
            )
            yield FieldRow(
                "CRON",
                Input(value="0 9 * * *", id="schedule-cron", placeholder="0 9 * * 1-5"),
                id="schedule-row-cron",
            )

            yield ToggleSwitch(
                "schedule-repeat",
                "REPEATING",
                "off = run once, then retire",
                id="schedule-repeat",
            )
            yield FieldRow(
                "RUNS",
                Input(value="0", id="schedule-repeat-times"),
                Static(
                    "  0 = keep going until stopped",
                    classes="field-hint",
                ),
                id="schedule-row-runs",
            )
            yield FieldRow(
                "MODEL",
                Input(placeholder="blank = whatever is configured", id="schedule-model"),
            )
            yield FieldRow(
                "DELIVER",
                Cycler("deliver", schedule.DELIVERIES, "local", id="schedule-deliver"),
            )
            yield FieldRow(
                "FOLDER",
                Input(placeholder="blank = the scheduler's own", id="schedule-workdir"),
            )

            yield Static("", id="schedule-form-preview")
            yield Static("", id="schedule-form-problem", classes="note")
            with Horizontal(id="schedule-form-actions"):
                yield PanelButton("SAVE", "schedule-save")
                yield PanelButton("CANCEL", "schedule-cancel")

        window = TaskWindow(id="task-window")
        window.display = False
        yield window

    def on_mount(self) -> None:
        self.query_one("#schedule-form").display = False
        self.set_mode(schedule.MODE_EVERY)
        self.reload()

    def on_resize(self, event: events.Resize) -> None:
        # The name column is whatever the other columns leave, so a resize is
        # a re-measure. Only while the list is the surface on screen: the form
        # has no table, and re-reading the store on a resize the reader cannot
        # see is work for nobody.
        if not self.form_open:
            self.reload()

    # ── The list ─────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Re-read every task. Safe to call from a timer at any moment."""
        tasks, problem = schedule.list_tasks()
        keep = self.selected_id()
        self._tasks = tasks
        table = self._maybe("#schedule-table", DataTable)
        if table is None:
            return
        palette = _app_palette(self)
        columns = self._columns()
        # Rebuilt rather than cleared: the column set changes with the pane's
        # width, and a table whose columns are re-added while its rows are
        # still there raises rather than reflowing.
        table.clear(columns=True)
        for heading, width, _keep in columns:
            table.add_column(heading, width=width)
        for task in tasks:
            table.add_row(*self._row_for(task, palette, columns), key=task.id)
        if not tasks:
            table.add_row(
                "—", "no scheduled tasks yet", *["—"] * (len(columns) - 2)
            )
        elif keep:
            for index, task in enumerate(tasks):
                if task.id == keep:
                    try:
                        table.move_cursor(row=index)
                    except Exception:
                        pass
                    break
        summary = self._maybe("#schedule-summary", Static)
        if summary is not None:
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append(schedule.summarise(tasks), style=palette["foreground"])
            if problem:
                text.append(f"   {problem}", style=palette["error"])
            summary.update(text)
        note = self._maybe("#schedule-scheduler-note", Static)
        if note is not None:
            state = schedule.scheduler_note()
            tone = "success" if state.startswith("Scheduler live") else "warning"
            note.update(Text(f"  {state}", style=palette[tone]))

    def _columns(self) -> tuple:
        """Which columns fit, and how wide the name column gets.

        Measured against the table's own width rather than thresholded
        against the window's. The pane's width is the window's minus the rail
        and the instrument stack, and both of those come and go — so a
        threshold on the window is right at one combination of them and wrong
        at the others, which is how a table ends up scrolling sideways in a
        console that has room to spare.
        """
        room = 0
        table = self._maybe("#schedule-table", DataTable)
        if table is not None:
            room = table.content_size.width or table.size.width
        if not room:
            room = max(40, (self.content_size.width or self.size.width or 80))
        optional = not self._compact
        while True:
            chosen = [c for c in TASK_COLUMNS if c[2] or optional]
            # One column of padding either side of every cell, which is what
            # ``DataTable`` adds and what makes the sum come out right.
            fixed = sum(width for _h, width, _k in chosen) + 2 * len(chosen)
            name = room - fixed
            if name >= NAME_MIN or not optional:
                break
            optional = False
        return tuple(
            (heading, max(NAME_MIN, name) if width == 0 else width, keep)
            for heading, width, keep in chosen
        )

    def _row_for(self, task: schedule.Task, palette, columns) -> tuple:
        label, tone = schedule.STATE_MARKS[schedule.task_state_key(task)]
        runs = str(task.runs) if task.repeat is None else f"{task.runs}/{task.repeat}"
        last = schedule.describe_when(task.last_run_at)
        if task.last_status and task.last_status not in {"ok", "success"}:
            last = f"{last} ({task.last_status})"
        cells = {
            "STATE": Text(label, style=f"bold {palette[tone]}", no_wrap=True),
            "TASK": Text(task.name, no_wrap=True, overflow="ellipsis"),
            "WHEN": Text(task.schedule, no_wrap=True, overflow="ellipsis"),
            "NEXT": schedule.describe_when(task.next_run_at),
            "LAST RUN": last,
            "RUNS": runs,
        }
        return tuple(cells[heading] for heading, *_rest in columns)

    def set_compact(self, compact: bool) -> None:
        """Refuse the two optional columns however wide the table measures."""
        if compact == self._compact:
            return
        self._compact = compact
        self.reload()

    def selected_id(self) -> str:
        """The task under the cursor, by position rather than by a column.

        The id used to ride in a last column the way the logbook's does, and
        it cost twelve columns of a table whose *name* column is the one that
        matters — twelve columns spent on a hex string nobody reads to tell
        two tasks apart. Rows are added in ``self._tasks`` order and nothing
        else writes to the table, so the cursor's row index is the same
        answer without the column.
        """
        table = self._maybe("#schedule-table", DataTable)
        if table is None or not self._tasks:
            return ""
        try:
            index = int(table.cursor_row)
        except Exception:
            return ""
        if 0 <= index < len(self._tasks):
            return self._tasks[index].id
        return ""

    def selected_task(self) -> Optional[schedule.Task]:
        wanted = self.selected_id()
        for task in self._tasks:
            if task.id == wanted:
                return task
        return None

    def task_by_id(self, task_id: str) -> Optional[schedule.Task]:
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    @property
    def running_count(self) -> int:
        """How many tasks are running, asked of the ledger and not of the table.

        The table is only re-read while this pane is the one on screen, so
        counting its rows would answer for whenever the reader last looked —
        and the whole point of the count is to be right while they are looking
        somewhere else.
        """
        return schedule.running_count()

    @property
    def armed_count(self) -> int:
        return sum(
            1 for task in self._tasks if not task.paused and not task.terminal
        )

    # ── The form ─────────────────────────────────────────────────────────

    @property
    def form_open(self) -> bool:
        form = self._maybe("#schedule-form")
        return bool(form is not None and form.display)

    def open_form(self, task: Optional[schedule.Task] = None) -> None:
        """Show the editor, either empty or filled in from a task."""
        self._editing = task.id if task else ""
        self._spec = schedule.spec_from_job(task.raw) if task else schedule.ScheduleSpec()

        self._set_value("#schedule-name", task.name if task else "")
        prompt = self._maybe("#schedule-prompt", TextArea)
        if prompt is not None:
            prompt.text = task.prompt if task else ""
        self._set_value("#schedule-model", task.model if task else "")
        self._set_value("#schedule-workdir", task.workdir if task else "")
        self._set_cycler("#schedule-deliver", task.deliver if task else "local")

        self._set_value("#schedule-every-value", str(self._spec.every_value))
        self._set_cycler("#schedule-every-unit", self._spec.every_unit)
        self._set_cycler("#schedule-day", self._spec.day)
        self._set_value("#schedule-time", self._spec.time)
        self._set_value("#schedule-date", self._spec.date)
        self._set_value("#schedule-cron", self._spec.cron_expr)
        self._set_value("#schedule-repeat-times", str(self._spec.repeat_times))
        toggle = self._maybe("#schedule-repeat", ToggleSwitch)
        if toggle is not None:
            toggle.set_on(self._spec.repeating)

        head = self._maybe("#schedule-form-head", Static)
        if head is not None:
            head.update("EDIT TASK" if task else "NEW TASK")
        self.set_mode(self._spec.mode)
        self.set_form_problem("")

        self.query_one("#schedule-list").display = False
        self.query_one("#schedule-form").display = True
        self.refresh_form_preview()
        name = self._maybe("#schedule-name", Input)
        if name is not None:
            name.focus()

    def close_form(self) -> None:
        self._editing = ""
        self.query_one("#schedule-form").display = False
        self.query_one("#schedule-list").display = True
        table = self._maybe("#schedule-table", DataTable)
        if table is not None:
            table.focus()

    def set_mode(self, mode: str) -> None:
        """Choose how the schedule is being said, and show only that control.

        Only that control, because three visible schedule rows means three
        sets of values on screen and no way to tell which two are inert. The
        chosen tab and the one visible row say the same thing twice, which is
        exactly right for a control whose whole job is to disambiguate.
        """
        known = {key for key, *_rest in schedule.MODES}
        self._spec.mode = mode if mode in known else schedule.MODE_EVERY
        for key, *_rest in schedule.MODES:
            tab = self._maybe(f"#schedule-mode-{key}", ModeTab)
            if tab is not None:
                tab.set_class(key == self._spec.mode, "-active")
                tab.refresh()
        for key, row_id in (
            (schedule.MODE_EVERY, "#schedule-row-every"),
            (schedule.MODE_AT, "#schedule-row-at"),
            (schedule.MODE_CRON, "#schedule-row-cron"),
        ):
            row = self._maybe(row_id)
            if row is not None:
                row.display = key == self._spec.mode
        self.refresh_form_preview()

    def read_form(self) -> schedule.ScheduleSpec:
        """Take the controls' current values into the spec."""
        spec = self._spec
        spec.every_value = _as_int(self._value("#schedule-every-value"), 1)
        spec.every_unit = self._cycler("#schedule-every-unit") or "h"
        spec.day = self._cycler("#schedule-day") or "day"
        spec.time = self._value("#schedule-time") or "09:00"
        spec.date = self._value("#schedule-date")
        spec.cron_expr = self._value("#schedule-cron")
        spec.repeat_times = max(0, _as_int(self._value("#schedule-repeat-times"), 0))
        toggle = self._maybe("#schedule-repeat", ToggleSwitch)
        spec.repeating = bool(toggle.is_on) if toggle is not None else True
        # A one-shot date is not repeating whatever the switch says: the
        # schedule names one instant, and an instant cannot recur.
        if spec.mode == schedule.MODE_AT and spec.day == "once":
            spec.repeating = False
        return spec

    def refresh_form_preview(self) -> None:
        """Say, under the controls, when this schedule would actually fire."""
        widget = self._maybe("#schedule-form-preview", Static)
        if widget is None or not self.form_open:
            return
        spec = self.read_form()
        # The RUNS field is only meaningful for a repeating task.
        row = self._maybe("#schedule-row-runs")
        if row is not None:
            row.display = spec.repeating
        palette = _app_palette(self)
        described = spec.describe()
        # ``describe`` returns the problem when there is one, so the tone is
        # read off whether a fire time came back rather than tracked apart.
        good = "first run" in described or "once at" in described
        widget.update(
            Text(f"  {described}", style=palette["success" if good else "warning"])
        )

    def set_form_problem(self, text: str) -> None:
        widget = self._maybe("#schedule-form-problem", Static)
        if widget is None:
            return
        palette = _app_palette(self)
        widget.update(Text(f"  {text}" if text else "", style=palette["error"]))

    def save_form(self) -> tuple:
        """Write the form. Returns ``(task, problem)``."""
        spec = self.read_form()
        name = self._value("#schedule-name")
        prompt = self._maybe("#schedule-prompt", TextArea)
        text = prompt.text if prompt is not None else ""
        model = self._value("#schedule-model")
        workdir = self._value("#schedule-workdir")
        deliver = self._cycler("#schedule-deliver") or "local"
        if self._editing:
            return schedule.update_task(
                self._editing,
                name=name,
                prompt=text,
                spec=spec,
                model=model,
                deliver=deliver,
                workdir=workdir,
            )
        return schedule.create_task(
            name=name,
            prompt=text,
            spec=spec,
            model=model,
            deliver=deliver,
            workdir=workdir,
        )

    # ── The task window ──────────────────────────────────────────────────

    def window(self) -> Optional[TaskWindow]:
        return self._maybe("#task-window", TaskWindow)

    @property
    def window_open(self) -> bool:
        window = self.window()
        return bool(window is not None and window.is_open)

    # ── Skins ────────────────────────────────────────────────────────────

    def restyle(self) -> None:
        """Repaint everything this pane colours itself.

        The table's cells hold styled ``Text`` built when the row was added,
        so a skin change leaves them in the previous palette until they are
        rebuilt — which is what ``reload`` does.
        """
        self.reload()
        self.refresh_form_preview()
        window = self.window()
        if window is not None and window.is_open:
            window.restyle()
        for widget in list(self.query(PanelButton)) + list(self.query(ModeTab)):
            widget.refresh()
        for widget in self.query(Cycler):
            widget.refresh()
        for widget in self.query(ToggleSwitch):
            widget.refresh()

    # ── Small helpers ────────────────────────────────────────────────────

    def _maybe(self, selector: str, kind: type | None = None):
        try:
            found = self.query(selector)
        except Exception:
            return None
        for node in found:
            if kind is None or isinstance(node, kind):
                return node
        return None

    def _value(self, selector: str) -> str:
        widget = self._maybe(selector, Input)
        return str(widget.value).strip() if widget is not None else ""

    def _set_value(self, selector: str, value: str) -> None:
        widget = self._maybe(selector, Input)
        if widget is not None:
            widget.value = str(value or "")

    def _cycler(self, selector: str) -> str:
        widget = self._maybe(selector, Cycler)
        return widget.value if widget is not None else ""

    def _set_cycler(self, selector: str, value: str) -> None:
        widget = self._maybe(selector, Cycler)
        if widget is not None:
            widget.set_value(value)

    # ── Live editing ─────────────────────────────────────────────────────

    @on(Input.Changed)
    def _input_changed(self, event: Input.Changed) -> None:
        if str(event.input.id or "").startswith("schedule-"):
            self.refresh_form_preview()

    @on(ScheduleAction)
    def _form_changed(self, event: ScheduleAction) -> None:
        if event.action == "schedule-form-changed":
            event.stop()
            self.refresh_form_preview()


def _run_stamp(session_id: str) -> str:
    """The ``YYYYmmdd_HHMMSS`` tail of a cron run's session id, humanised.

    Cron sessions are named ``cron_<job id>_<stamp>``; everything before the
    stamp is the same for every run of one task, so the stamp is the whole of
    what distinguishes them.
    """
    parts = str(session_id or "").split("_")
    if len(parts) < 3:
        return ""
    date, _, clock = parts[-2], "_", parts[-1]
    if len(date) == 8 and len(clock) == 6 and date.isdigit() and clock.isdigit():
        return (
            f"{date[:4]}-{date[4:6]}-{date[6:]} "
            f"{clock[:2]}:{clock[2:4]}:{clock[4:]}"
        )
    return ""


def _as_int(text: str, fallback: int) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return fallback


__all__ = [
    "Cycler",
    "ModeTab",
    "PreviewComposer",
    "SchedulePane",
    "ScheduleAction",
    "TASK_COLUMNS",
    "TABLE_COMPACT_AT",
    "TaskWindow",
]
