"""What the SCHEDULE pane knows about scheduled tasks, and nothing about how
they are drawn.

The console does not own scheduling. Curie already has a scheduler — a durable
job store under ``~/.curie/cron``, a ticker in the gateway that fires due jobs,
an execution ledger that records every attempt, and a session per run in the
conversation store. This module is the seam between that machinery and a
console pane: it reads those four sources into one flat record a table can
render, and turns a form's worth of switches back into the one schedule string
the store parses.

Two consequences of building it that way, and both were requirements:

* a task created here is a *real* scheduled task. It fires from the gateway,
  on the same clock as every other job, whether or not this console is open —
  because it is written to the same store ``curie cron create`` writes to;
* a second console sees it immediately, because there is nothing to
  synchronise: both windows are reading one file, and
  :mod:`curie_cli.bench_ui.sync` tells each of them when it has changed.

Every function here is total. The pane calls them from click handlers and
timers, where an exception is a traceback painted over the interface, so a
store that will not open, a schedule that will not parse and a ledger that is
locked all come back as a value plus a sentence — never as a raise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

# ── Schedule vocabulary ──────────────────────────────────────────────────

#: The two ways a person describes when something should happen, plus the one
#: a machine does. ``every`` is a countdown — "every forty-five seconds",
#: "every two hours" — and ``at`` is a wall clock — "every Monday at 7:32 pm".
#: They are separate modes rather than one free-text field because they are
#: answered by different controls, and a single box that accepts both is a box
#: whose rules the reader has to learn before it will take an answer.
MODE_EVERY = "every"
MODE_AT = "at"
MODE_CRON = "cron"

MODES: tuple[tuple[str, str, str], ...] = (
    (MODE_EVERY, "EVERY", "a countdown — fires this often, from now"),
    (MODE_AT, "AT", "a wall clock — fires on these days, at this time"),
    (MODE_CRON, "CRON", "a five-field cron expression, for anything else"),
)

#: Units the countdown offers, longest first in the cycle so a reader stepping
#: through the control meets the common ones early.
UNITS: tuple[tuple[str, str], ...] = (
    ("m", "minutes"),
    ("h", "hours"),
    ("d", "days"),
    ("s", "seconds"),
)

#: Day specs the wall clock offers, in the phrasing ``cron.jobs`` parses.
#: ``once`` is not a day at all — it is the one-time case, and it takes a date
#: instead of a repeating day, which is why it lives in this list rather than
#: in a separate switch: the reader picks *when*, once, in one control.
DAYS: tuple[tuple[str, str], ...] = (
    ("day", "every day"),
    ("weekdays", "weekdays"),
    ("weekends", "weekends"),
    ("monday", "Mondays"),
    ("tuesday", "Tuesdays"),
    ("wednesday", "Wednesdays"),
    ("thursday", "Thursdays"),
    ("friday", "Fridays"),
    ("saturday", "Saturdays"),
    ("sunday", "Sundays"),
    ("once", "once, on a date"),
)

#: Where a finished run is delivered. ``local`` writes it to the job's output
#: folder, which is what the task preview reads back; the rest hand it to a
#: chat platform the gateway is connected to.
DELIVERIES: tuple[tuple[str, str], ...] = (
    ("local", "keep the output here"),
    ("origin", "back where the task was made"),
    ("telegram", "Telegram"),
    ("discord", "Discord"),
    ("slack", "Slack"),
)

_TIME_PATTERN = re.compile(r"^\s*(\d{1,2})\s*:\s*(\d{2})\s*$")
_DATE_PATTERN = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")


@dataclass
class ScheduleSpec:
    """A schedule as the form holds it, before it becomes a string.

    Kept as fields rather than as the string itself so the form can round-trip
    an existing task back into its own controls. Reconstructing the controls
    by re-parsing "every monday at 19:32" would work until it did not, and the
    case where it does not — a task the reader opens to edit and finds every
    control reset to its default — loses their schedule silently.
    """

    mode: str = MODE_EVERY
    every_value: int = 1
    every_unit: str = "h"
    day: str = "day"
    time: str = "09:00"
    date: str = ""
    cron_expr: str = "0 9 * * *"
    #: Whether the task keeps firing. A one-shot date is repeating=False by
    #: construction; everything else is the reader's choice.
    repeating: bool = True
    #: How many times a repeating task runs before it retires. 0 = forever.
    repeat_times: int = 0

    # ── To the store's own vocabulary ────────────────────────────────────

    def to_schedule(self) -> str:
        """The one string ``cron.jobs.parse_schedule`` takes.

        Raises ``ValueError`` with a sentence a person can act on. The caller
        is a click handler and puts it straight on the notice line, so it has
        to name the control that is wrong, not the parser that noticed.
        """
        if self.mode == MODE_CRON:
            expr = " ".join(str(self.cron_expr or "").split())
            if not expr:
                raise ValueError("Enter a cron expression, e.g. 0 9 * * 1-5.")
            return expr

        if self.mode == MODE_AT:
            hour, minute = self._clock()
            if self.day == "once":
                date = self._date()
                return f"{date}T{hour:02d}:{minute:02d}:00"
            spec = self.day if self.day in {"day", "weekdays", "weekends"} else self.day
            return f"every {spec} at {hour:02d}:{minute:02d}"

        value = int(self.every_value or 0)
        if value <= 0:
            raise ValueError("The countdown needs a number greater than zero.")
        unit = self.every_unit if self.every_unit in dict(UNITS) else "m"
        # A one-time countdown is "in 45m", a repeating one is "every 45m".
        # Same number, same unit, different word — and the store reads the
        # word, which is why the repeat switch is part of the schedule and not
        # a separate field written beside it.
        lead = "every" if self.repeating else "in"
        return f"{lead} {value}{unit}"

    def repeat_argument(self) -> Optional[int]:
        """What ``create_job`` should be told about repetition.

        ``None`` means forever. One means once. Anything else is a count, and
        a count on a one-shot is meaningless — a schedule that fires at one
        instant cannot fire at it three times — so it is dropped rather than
        stored as a promise the scheduler cannot keep.
        """
        if not self.repeating:
            return 1
        times = int(self.repeat_times or 0)
        return times if times > 0 else None

    # ── Reading the controls ─────────────────────────────────────────────

    def _clock(self) -> tuple[int, int]:
        match = _TIME_PATTERN.match(str(self.time or ""))
        if not match:
            raise ValueError("Enter the time as HH:MM, e.g. 19:32.")
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(f"There is no {hour:02d}:{minute:02d} on a clock.")
        return hour, minute

    def _date(self) -> str:
        match = _DATE_PATTERN.match(str(self.date or ""))
        if not match:
            raise ValueError("Enter the date as YYYY-MM-DD, e.g. 2026-09-10.")
        try:
            datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            raise ValueError(f"{self.date.strip()} is not a date on any calendar.")
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    # ── What the form says under the controls ────────────────────────────

    def describe(self) -> str:
        """The schedule in words, with the next fire time worked out.

        Shown live under the controls, because the controls are the *inputs*
        to a schedule and not the schedule itself: "every 2, hours, Mondays,
        19:32" has four values on screen and only two of them are in force,
        and which two depends on a mode switch three rows up. One sentence
        saying when it will actually fire is the only way to check a schedule
        before committing to it.
        """
        try:
            schedule = self.to_schedule()
        except ValueError as exc:
            return str(exc)
        try:
            from cron.jobs import compute_next_run, parse_schedule

            parsed = parse_schedule(schedule)
            nxt = compute_next_run(parsed)
        except ValueError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - surfaced to the reader
            return f"{type(exc).__name__}: {exc}"
        display = str(parsed.get("display") or schedule)
        when = describe_when(nxt)
        repeat = self.repeat_argument()
        if repeat == 1:
            tail = "once"
        elif repeat:
            tail = f"{repeat} times"
        else:
            tail = "until stopped"
        return f"{display} · {tail} · first run {when}"


def spec_from_job(job: dict) -> ScheduleSpec:
    """Recover the form's controls from a stored task, as best they can be.

    Best-effort by nature: the store keeps a *parsed* schedule, and several
    different sets of controls produce the same parse. What matters is that
    editing a task and changing only its prompt leaves its schedule exactly as
    it was, which this gives — the recovered controls re-serialise to the same
    string they came from.
    """
    spec = ScheduleSpec()
    schedule = job.get("schedule") if isinstance(job, dict) else None
    schedule = schedule if isinstance(schedule, dict) else {}
    repeat = job.get("repeat") if isinstance(job, dict) else None
    times = repeat.get("times") if isinstance(repeat, dict) else None
    spec.repeat_times = int(times) if isinstance(times, int) and times > 0 else 0
    spec.repeating = spec.repeat_times != 1

    kind = str(schedule.get("kind") or "")
    if kind == "interval":
        spec.mode = MODE_EVERY
        minutes = schedule.get("minutes") or 0
        value, unit = _split_interval(float(minutes))
        spec.every_value, spec.every_unit = value, unit
        return spec

    if kind == "once":
        spec.mode = MODE_AT
        spec.day = "once"
        spec.repeating = False
        spec.repeat_times = 0
        run_at = str(schedule.get("run_at") or "")
        try:
            when = datetime.fromisoformat(run_at)
            spec.date = when.strftime("%Y-%m-%d")
            spec.time = when.strftime("%H:%M")
        except (TypeError, ValueError):
            pass
        return spec

    if kind == "cron":
        expr = str(schedule.get("expr") or "").strip()
        spec.cron_expr = expr or spec.cron_expr
        # A cron expression the wall-clock controls could have produced is
        # shown *in* those controls, because that is where the reader put it.
        # Only a plain "minute hour * * <days>" qualifies; anything with a
        # step, a range in the hour field or a day-of-month is genuinely a
        # cron expression and is left as one.
        recovered = _wall_clock_from_cron(expr)
        if recovered is not None:
            spec.mode = MODE_AT
            spec.day, spec.time = recovered
        else:
            spec.mode = MODE_CRON
        return spec

    return spec


def _split_interval(minutes: float) -> tuple[int, str]:
    """The largest whole unit an interval can be stated in."""
    seconds = int(round(minutes * 60))
    if seconds <= 0:
        return 1, "h"
    if seconds % 86400 == 0:
        return seconds // 86400, "d"
    if seconds % 3600 == 0:
        return seconds // 3600, "h"
    if seconds % 60 == 0:
        return seconds // 60, "m"
    return seconds, "s"


#: Cron weekday fields the wall-clock control can produce, and the day spec
#: each came from. Inverted from ``cron.jobs``' own table rather than copied
#: as literals, so a weekday numbering change there cannot leave this mapping
#: quietly wrong.
def _cron_dow_to_day() -> dict:
    try:
        from cron.jobs import _DAYSPEC_TO_CRON_DOW, _WEEKDAY_TO_CRON_DOW
    except Exception:
        return {}
    table = {"*": "day", "1-5": "weekdays", "0,6": "weekends"}
    for name, dow in _DAYSPEC_TO_CRON_DOW.items():
        table.setdefault(dow, name if name != "everyday" else "day")
    for name, dow in _WEEKDAY_TO_CRON_DOW.items():
        if len(name) > 3:  # the full word, not the abbreviation
            table.setdefault(dow, name)
    return table


def _wall_clock_from_cron(expr: str) -> Optional[tuple[str, str]]:
    """``(day spec, "HH:MM")`` when a cron expression is one, else None."""
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    minute, hour, dom, month, dow = parts
    if dom != "*" or month != "*":
        return None
    if not (minute.isdigit() and hour.isdigit()):
        return None
    day = _cron_dow_to_day().get(dow)
    if day is None:
        return None
    return day, f"{int(hour):02d}:{int(minute):02d}"


# ── Tasks ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Task:
    """One scheduled task, flattened from four different stores.

    Frozen because the pane holds these across a redraw and a table row that
    can be mutated in place is a table row that disagrees with the store it
    came from.
    """

    id: str
    name: str
    prompt: str
    schedule: str
    state: str
    enabled: bool
    running: bool
    next_run_at: str
    last_run_at: str
    last_status: str
    runs: int
    repeat: Optional[int]
    model: str
    deliver: str
    workdir: str
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def paused(self) -> bool:
        return self.state == "paused" or not self.enabled

    @property
    def terminal(self) -> bool:
        return self.state in {"completed", "error"}

    @property
    def lamp(self) -> str:
        """Which lamp state stands for this task, for the rail's indicator."""
        if self.running:
            return "blink"
        if self.state == "error":
            return "warn"
        if self.paused or self.terminal:
            return "off"
        return "on"


#: What the STATE column says, and the palette role it is painted in. A word
#: and a mark, because the marks carry at a glance down a column of ten rows
#: and the word carries when two of the marks are close in the active skin.
STATE_MARKS: dict = {
    "running": ("▶ RUNNING", "primary"),
    "scheduled": ("● ARMED", "success"),
    "paused": ("‖ PAUSED", "dim"),
    "completed": ("✓ DONE", "secondary"),
    "error": ("✗ FAULT", "error"),
}


def task_state_key(task: Task) -> str:
    """The key into :data:`STATE_MARKS` for one task."""
    if task.running:
        return "running"
    if task.state in STATE_MARKS:
        return task.state
    return "scheduled"


def list_tasks() -> tuple[list, str]:
    """Every scheduled task, armed first, then paused, then finished.

    Returns ``(tasks, problem)``. A problem is a sentence, and the task list
    that comes with it is whatever could still be read — a ledger that will
    not open costs the RUNNING column, not the pane.
    """
    try:
        from cron.jobs import list_jobs
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return [], f"the scheduler is not available here ({type(exc).__name__})"

    try:
        jobs = list_jobs(include_disabled=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return [], f"could not read the task store ({type(exc).__name__}: {exc})"

    running = _running_ids()
    tasks = [_task_from_job(job, running) for job in jobs or []]
    tasks.sort(key=_ordering)
    return tasks, ""


def _ordering(task: Task) -> tuple:
    """Running first, then armed by next fire, then paused, then finished."""
    if task.running:
        rank = 0
    elif task.state == "error":
        rank = 1
    elif not task.paused and not task.terminal:
        rank = 2
    elif task.paused:
        rank = 3
    else:
        rank = 4
    return (rank, task.next_run_at or "9999", task.name.lower())


def _task_from_job(job: dict, running: set) -> Task:
    repeat = job.get("repeat") if isinstance(job.get("repeat"), dict) else {}
    times = repeat.get("times")
    return Task(
        id=str(job.get("id") or ""),
        name=str(job.get("name") or "").strip() or "untitled task",
        prompt=str(job.get("prompt") or ""),
        schedule=str(job.get("schedule_display") or "?"),
        state=str(job.get("state") or "scheduled"),
        enabled=bool(job.get("enabled", True)),
        running=str(job.get("id") or "") in running,
        next_run_at=str(job.get("next_run_at") or ""),
        last_run_at=str(job.get("last_run_at") or ""),
        last_status=str(job.get("last_status") or ""),
        runs=int(repeat.get("completed") or 0),
        repeat=times if isinstance(times, int) else None,
        model=str(job.get("model") or job.get("model_snapshot") or ""),
        deliver=str(job.get("deliver") or "local"),
        workdir=str(job.get("workdir") or ""),
        raw=job if isinstance(job, dict) else {},
    )


def _running_ids() -> set:
    """The tasks executing right now, according to the durable ledger.

    The ledger rather than the in-process running set, because the process
    running them is the gateway and this is the console: an in-process answer
    here is always "nothing is running", which is exactly the readout a
    scheduling pane must not give.
    """
    try:
        from cron.executions import list_executions

        rows = list_executions(status="running", limit=200)
    except Exception:
        return set()
    ids = {str(row.get("job_id") or "") for row in rows or []}
    try:
        from cron.executions import list_executions

        claimed = list_executions(status="claimed", limit=200)
        ids |= {str(row.get("job_id") or "") for row in claimed or []}
    except Exception:
        pass
    ids.discard("")
    return ids


def running_count() -> int:
    """How many tasks are executing right now. One small query, and no more.

    Deliberately not "the length of ``list_tasks()`` filtered": the switch and
    the key line carry this count *while the reader is on another pane*, which
    is exactly when the reader cannot see the table and exactly when a task
    starting is worth knowing about. Answering it by rebuilding the whole task
    list would mean reading the job store once a second forever, for a number
    the execution ledger already holds.
    """
    return len(_running_ids())


def latest_session_id(job_id: str) -> str:
    """The newest conversation this task produced, or "".

    Each run of a task is its own conversation — the scheduler opens a session
    named ``cron_<job id>_<timestamp>`` and the run happens inside it, with no
    prior context. That is what makes the task window possible at all: there
    is a real chat to open, and it belongs to this task and to nothing else.

    Asked for by name rather than resolved into every ``Task``, because it is
    a database query per task and the table is redrawn once a second: twenty
    tasks on screen meant twenty queries a second to fill a column the table
    does not have. The one caller that needs it is the window, and it needs
    one task's answer.
    """
    if not job_id:
        return ""
    try:
        from curie_state import SessionDB

        rows = SessionDB().list_cron_job_runs(job_id, limit=1)
    except Exception:
        return ""
    for row in rows or []:
        if isinstance(row, dict):
            found = str(row.get("id") or "").strip()
            if found:
                return found
    return ""


def task_runs(job_id: str, limit: int = 12) -> list:
    """Every conversation this task has produced, newest first."""
    if not job_id:
        return []
    try:
        from curie_state import SessionDB

        rows = SessionDB().list_cron_job_runs(job_id, limit=limit)
    except Exception:
        return []
    return [row for row in rows or [] if isinstance(row, dict)]


def read_conversation(session_id: str) -> tuple[list, str]:
    """One task run's conversation, as ``[(role, text)]``, newest last.

    The display projection, not the model's working history: the preview is a
    chat window, and system prompts, pivot markers and tool plumbing are not
    chat. Returns ``(messages, problem)``.
    """
    if not str(session_id or "").strip():
        return [], ""
    try:
        from curie_state import SessionDB

        db = SessionDB()
        resolved = session_id
        resolver = getattr(db, "resolve_resume_session_id", None)
        if callable(resolver):
            resolved = str(resolver(session_id) or session_id)
        _model, display = db.get_resume_conversations(resolved)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return [], f"could not read the run ({type(exc).__name__}: {exc})"

    out: list = []
    for message in display or []:
        if not isinstance(message, dict) or message.get("display_kind"):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = message.get("content") or message.get("text") or ""
        if isinstance(text, list):
            text = " ".join(
                str(part.get("text", ""))
                for part in text
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if isinstance(text, str) and text.strip():
            out.append((role, text.strip()))
    return out, ""


# ── Changing a task ──────────────────────────────────────────────────────


def create_task(
    *,
    name: str,
    prompt: str,
    spec: ScheduleSpec,
    model: str = "",
    deliver: str = "local",
    workdir: str = "",
) -> tuple[Optional[Task], str]:
    """Write one new scheduled task. Returns ``(task, problem)``.

    The problem is a sentence for the notice line, and every way this can fail
    produces one: an empty prompt, a schedule the store will not parse, a
    one-shot time already in the past, a home that cannot be written.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return None, "A task needs a prompt — it is what the agent is asked to do."
    try:
        schedule = spec.to_schedule()
    except ValueError as exc:
        return None, str(exc)

    try:
        from cron.jobs import create_job
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return None, f"the scheduler is not available here ({type(exc).__name__})"

    try:
        job = create_job(
            prompt=prompt,
            schedule=schedule,
            name=str(name or "").strip() or None,
            repeat=spec.repeat_argument(),
            deliver=str(deliver or "local").strip() or "local",
            model=str(model or "").strip() or None,
            workdir=str(workdir or "").strip() or None,
            origin={"surface": "bench-console"},
        )
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return None, f"could not create the task ({type(exc).__name__}: {exc})"
    return _task_from_job(job, set()), ""


def update_task(
    job_id: str,
    *,
    name: str,
    prompt: str,
    spec: ScheduleSpec,
    model: str = "",
    deliver: str = "local",
    workdir: str = "",
) -> tuple[Optional[Task], str]:
    """Rewrite one task in place. Returns ``(task, problem)``.

    In place rather than delete-and-recreate, so the task keeps its id, its
    run count and its history — the run log a reader opens in the preview
    belongs to the task, and a task that loses it every time its prompt is
    corrected has no history at all.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return None, "A task needs a prompt — it is what the agent is asked to do."
    try:
        schedule = spec.to_schedule()
    except ValueError as exc:
        return None, str(exc)

    try:
        from cron.jobs import parse_schedule, update_job
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return None, f"the scheduler is not available here ({type(exc).__name__})"

    try:
        parsed = parse_schedule(schedule)
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return None, f"{type(exc).__name__}: {exc}"

    updates = {
        "name": str(name or "").strip() or None,
        "prompt": prompt,
        "schedule": parsed,
        "schedule_display": parsed.get("display", schedule),
        "model": str(model or "").strip() or None,
        "deliver": str(deliver or "local").strip() or "local",
        "workdir": str(workdir or "").strip() or None,
        "repeat": {"times": spec.repeat_argument()},
    }
    try:
        job = update_job(job_id, updates)
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return None, f"could not save the task ({type(exc).__name__}: {exc})"
    if job is None:
        return None, "That task is no longer in the store — another window may have removed it."
    return _task_from_job(job, _running_ids()), ""


def set_paused(job_id: str, paused: bool) -> str:
    """Pause or arm one task. Returns "" or why it could not."""
    try:
        from cron.jobs import pause_job, resume_job
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the scheduler is not available here ({type(exc).__name__})"
    try:
        job = (
            pause_job(job_id, reason="paused from the bench console")
            if paused
            else resume_job(job_id)
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"{type(exc).__name__}: {exc}"
    if job is None:
        return "That task is no longer in the store."
    return ""


def run_now(job_id: str) -> str:
    """Arm one task for the scheduler's next tick. Returns "" or a problem.

    Not "run it here". The task belongs to the scheduler, and running it in
    this process would give it this console's working directory, this
    console's environment and no fire claim — two copies of the same task
    could then run at once, which is the failure the claim exists to prevent.
    What this does is bring its next occurrence forward to now.
    """
    try:
        from cron.jobs import trigger_job
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the scheduler is not available here ({type(exc).__name__})"
    try:
        job = trigger_job(job_id)
    except ValueError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"{type(exc).__name__}: {exc}"
    if job is None:
        return "That task is no longer in the store."
    return ""


def stop_run(job_id: str) -> str:
    """Ask whichever process is running this task to stop. "" or a problem."""
    try:
        from cron.jobs import request_job_stop
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the scheduler is not available here ({type(exc).__name__})"
    if not request_job_stop(job_id):
        return "Could not record the stop request — the task store is not writable."
    return ""


def remove_task(job_id: str) -> str:
    """Delete one task for good. Returns "" or why it could not."""
    try:
        from cron.jobs import remove_job
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the scheduler is not available here ({type(exc).__name__})"
    try:
        if not remove_job(job_id):
            return "That task is no longer in the store."
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"{type(exc).__name__}: {exc}"
    return ""


# ── Readouts ─────────────────────────────────────────────────────────────


def describe_when(value: Any) -> str:
    """A timestamp as a person reads it: "in 4m", "12s ago", "—".

    Relative, because every question a reader has about a scheduled task is
    relative — *how long until this fires*, *how long ago did it last run* —
    and an absolute timestamp makes them do the subtraction. The absolute time
    is one column over for the cases where it matters.
    """
    when = _parse_stamp(value)
    if when is None:
        return "—"
    try:
        from curie_time import now as _now

        current = _now()
    except Exception:
        current = datetime.now(when.tzinfo) if when.tzinfo else datetime.now()
    try:
        delta = (when - current).total_seconds()
    except TypeError:
        return when.strftime("%Y-%m-%d %H:%M")
    ahead = delta >= 0
    span = _span(abs(delta))
    return f"in {span}" if ahead else f"{span} ago"


def describe_clock(value: Any) -> str:
    """The same timestamp as a wall clock, for the column beside it."""
    when = _parse_stamp(value)
    return when.strftime("%H:%M:%S") if when else "—"


def _parse_stamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _span(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        hours, minutes = divmod(int(seconds // 60), 60)
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    return f"{days}d{hours}h" if hours else f"{days}d"


def summarise(tasks: Iterable) -> str:
    """The one line above the table: how many, how many of them are live.

    A count of tasks alone answers nothing a reader wants to know — the
    questions are "is anything running right now" and "when does the next
    thing happen", and both are here.
    """
    tasks = list(tasks)
    if not tasks:
        return "No scheduled tasks yet."
    running = sum(1 for t in tasks if t.running)
    paused = sum(1 for t in tasks if t.paused and not t.terminal)
    armed = sum(1 for t in tasks if not t.paused and not t.terminal and not t.running)
    parts = [f"{len(tasks)} task{'s' if len(tasks) != 1 else ''}"]
    if running:
        parts.append(f"{running} running")
    if armed:
        parts.append(f"{armed} armed")
    if paused:
        parts.append(f"{paused} paused")
    upcoming = sorted(
        (t.next_run_at for t in tasks if t.next_run_at and not t.paused and not t.terminal)
    )
    if upcoming:
        parts.append(f"next {describe_when(upcoming[0])}")
    return " · ".join(parts)


def scheduler_note() -> str:
    """Whether anything is actually going to fire these, in one sentence.

    The single most useful thing this pane can say, and the one a table of
    tasks cannot: scheduled tasks are fired by the gateway, so a store full of
    perfectly good tasks and no gateway running is a page of things that will
    never happen. Said here rather than discovered at 2am.
    """
    try:
        from cron.jobs import get_ticker_heartbeat_age
    except Exception:
        return "The scheduler could not be reached from here."
    try:
        age = get_ticker_heartbeat_age()
    except Exception:
        age = None
    if age is None:
        return "No scheduler running — nothing fires. Start:  curie gateway install"
    if age > 300:
        return (
            f"Scheduler silent for {_span(age)} — check:  curie gateway status"
        )
    return f"Scheduler live · last tick {_span(age)} ago."


__all__ = [
    "DAYS",
    "DELIVERIES",
    "MODES",
    "MODE_AT",
    "MODE_CRON",
    "MODE_EVERY",
    "STATE_MARKS",
    "UNITS",
    "ScheduleSpec",
    "Task",
    "create_task",
    "describe_clock",
    "describe_when",
    "latest_session_id",
    "list_tasks",
    "running_count",
    "read_conversation",
    "remove_task",
    "run_now",
    "scheduler_note",
    "set_paused",
    "spec_from_job",
    "stop_run",
    "summarise",
    "task_runs",
    "task_state_key",
    "update_task",
]
