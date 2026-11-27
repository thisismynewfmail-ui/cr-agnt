"""The console's half of Loop Detect: the PANEL block and the stop-and-resume.

:mod:`agent.loop_detect` notices a model repeating itself and
knows nothing of the console; this is what acts on it. When a passage comes
round REPEATS times in one response the turn is stopped exactly as Ctrl+C
stops it, a LOOP entry says so in the transcript, and once the turn has
*fully* stopped — its last event drained and its worker thread gone, so the
conversation the next turn is built from is the finished one — the console
sends the model the resume message, as an AUTO entry, and the work carries
on.

With SCHEDULED on as well, the same happens to a turn typed into a
scheduled task's window (the window's own turn, run by this console — the
runs the gateway fires are watched by :mod:`cron.loop_guard`). The window has
no stream on screen to stop drawing, so there it is its status line that
says what happened, and the run's conversation shows the resume as AUTO.

A mixin, beside :class:`~curie_cli.bench_ui.chatter_controls.ChatterControlsMixin`,
for the same reason: one feature with its own state, reporting through the
console's notice line and panes.

Off, none of it runs: the stream is not fed to anything, no turn is stopped,
nothing is sent, and every entry point below returns at its first line —
which is how "off" keeps the console exactly as it was before.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Optional

from agent.loop_detect import (
    RANGES,
    Loop,
    LoopSettings,
    LoopWatch,
)

#: The PANEL's dials: (dial id, label, LoopSettings field). The id is what
#: the ◄ ► buttons' actions carry — ``loop-<id>-up`` / ``loop-<id>-down``.
DIALS = (
    ("repeats", "REPEATS", "repeats"),
    ("words", "MIN WORDS", "min_words"),
    ("resumes", "RESUMES", "resumes"),
)

_DIAL_FIELDS = {dial: field for dial, _label, field in DIALS}
_FIELD_LABELS = {field: label for _dial, label, field in DIALS}

#: Turn events that carry the stream a stopped turn has left in flight. Once
#: the console has stopped a looping turn, more of the loop is not news: it
#: is dropped rather than drawn under the LOOP entry that just explained why
#: the turn was stopped.
_RESIDUE = frozenset({"delta", "reasoning", "tool_gen"})

#: Turn events that end one model response and start another. The model has
#: made a tool call (or the conversation has been compacted), so whatever it
#: writes next is a new response, and is counted afresh.
BOUNDARIES = frozenset({"tool", "compacting", "compacted"})


def times(count: int) -> str:
    """``once``, ``twice``, ``3 times`` — how a count reads in a sentence."""
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


class LoopControlsMixin:
    """The LOOP DETECT block and the turn-side logic, for :class:`BenchConsole`."""

    def _loop_init(self, settings) -> None:
        self._loop_settings: LoopSettings = settings.loop_detect
        self._loop_watch = LoopWatch(self._loop_settings)
        #: The loop the console stopped the running turn for, while that turn
        #: is still unwinding. None the rest of the time.
        self._loop_stopping: Optional[Loop] = None
        #: Where the automatic resume is: "" (none), "planned" (the stopped
        #: turn is still unwinding) or "due" (it has ended; sent once its
        #: worker thread is gone).
        self._loop_resume = ""
        #: Automatic resumes since the reader's own last message.
        self._loop_resumes = 0
        # The same four, for a turn typed into a scheduled task's window —
        # its own bridge, its own turn, so its own watch. ``_task_loop_for``
        # is the run the resume belongs to: the window can be stepped to
        # another run while the stopped turn unwinds.
        self._task_loop_watch = LoopWatch(self._loop_settings)
        self._task_loop_stopping: Optional[Loop] = None
        self._task_loop_resume = ""
        self._task_loop_resumes = 0
        self._task_loop_for = ""

    # ── Turn side ────────────────────────────────────────────────────────

    def _loop_turn_started(self, auto: bool) -> None:
        """A turn has been put to the agent. ``auto``: Loop Detect sent it."""
        self._loop_watch.reset()
        self._loop_stopping = None
        self._loop_resume = ""
        if not auto:
            # The reader has spoken: the count of resumes starts again.
            self._loop_resumes = 0

    def _loop_suppresses(self, kind: str) -> bool:
        """Whether a turn event is the stream of a turn Loop Detect stopped."""
        return self._loop_stopping is not None and kind in _RESIDUE

    def _loop_boundary(self, kind: str) -> None:
        """A tool call or a compaction: the next response is counted afresh."""
        if self._loop_settings.enabled and kind in BOUNDARIES:
            self._loop_watch.reset()

    def _loop_check(self, kind: str, text: str) -> None:
        """Watch one turn event's text; stop the turn if it has looped."""
        if not self._loop_settings.enabled or self._loop_stopping is not None:
            return
        loop = self._loop_watch.feed(kind, text)
        if loop is not None:
            self._loop_caught(loop)

    def _loop_caught(self, loop: Loop) -> None:
        """Stop the turn the way Ctrl+C does, and say why in the transcript."""
        settings = self._loop_settings
        resume = self._loop_resumes < settings.resumes
        # STOP's own steps, not a lookalike: an approval waiting is refused,
        # the chatter is hushed and the agent is asked to unwind.
        if not self._stop_turn():
            # The turn ended on its own as the loop completed — there is
            # nothing left to stop, and nothing to resume.
            self._loop_watch.reset()
            return
        self._loop_stopping = loop
        self._loop_resume = "planned" if resume else ""
        if resume:
            then = "The model will be told to break the loop and carry on once it has stopped."
        elif settings.resumes == 0:
            then = "RESUMES is 0, so nothing is sent — the next move is yours."
        else:
            then = (
                f"It has been sent on {times(self._loop_resumes)} since your last "
                "message, so it is not sent on again — the next move is yours."
            )
        self._write(
            "loop",
            f"The same passage came round {times(loop.count)} in the {loop.where} — "
            f"the turn was stopped, as Ctrl+C would. {then}\n“{loop.quote()}”",
        )

    def _loop_turn_ended(self) -> bool:
        """The turn's last event. True if Loop Detect had stopped it.

        A turn the console stopped ends without its closing words: they are
        the loop, or the agent's note that it was interrupted, and the LOOP
        entry above has already said what happened.
        """
        if self._loop_stopping is None:
            return False
        self._loop_stopping = None
        if self._loop_resume == "planned":
            self._loop_resume = "due"
        return True

    def _loop_resume_if_due(self) -> None:
        """Send the resume, once the stopped turn has fully finished.

        The turn's ``done`` event is put before its worker has finished
        putting the conversation back — the history is adopted and the hooks
        restored on the way out — so "due" waits for the thread itself to be
        gone. Checked on every tick of the pump, which is a flag test while
        nothing is due.
        """
        if self._loop_resume != "due" or self.bridge.busy:
            return
        self._loop_resume = ""
        if not self._loop_settings.enabled:
            return
        self._loop_resumes += 1
        self._begin_turn(self._loop_settings.message, kind="auto")

    def _loop_cancel(self) -> bool:
        """Call off a resume not yet sent. Returns whether there was one.

        The reader stopping the turn themselves, speaking, going back or
        changing conversation all mean the next move is theirs.
        """
        had = bool(self._loop_resume)
        self._loop_resume = ""
        return had

    def _loop_forget(self) -> None:
        """A different conversation: no resume, and the count starts again."""
        self._loop_cancel()
        self._loop_resumes = 0
        self._loop_watch.reset()

    # ── A scheduled task's window ────────────────────────────────────────

    def _task_loop_on(self) -> bool:
        """Whether turns in a task's window are watched: both switches on."""
        settings = self._loop_settings
        return settings.enabled and settings.scheduled

    def _task_loop_turn_started(self, auto: bool) -> None:
        """A turn is being put into the window's run."""
        self._task_loop_watch.reset()
        self._task_loop_stopping = None
        self._task_loop_resume = ""
        if not auto:
            self._task_loop_resumes = 0

    def _task_loop_event(self, bridge, window, event) -> None:
        """Watch one event of the window's turn; stop the turn if it loops."""
        if not self._task_loop_on():
            return
        if event.kind in BOUNDARIES:
            self._task_loop_watch.reset()
            return
        if self._task_loop_stopping is not None:
            return
        loop = self._task_loop_watch.feed(event.kind, event.text)
        if loop is None:
            return
        settings = self._loop_settings
        resume = self._task_loop_resumes < settings.resumes
        if not bridge.interrupt():
            # Already over: nothing to stop and nothing to resume.
            self._task_loop_watch.reset()
            return
        self._task_loop_stopping = loop
        self._task_loop_resume = "planned" if resume else ""
        self._task_loop_for = str(getattr(window, "session_id", "") or "")
        window.set_status(
            f"Loop detected — the same passage came round {times(loop.count)} "
            f"in the {loop.where}. Stopping"
            + (", then sending it on." if resume else "; the next move is yours.")
        )

    def _task_loop_turn_ended(self) -> bool:
        """The window's turn is over. True if Loop Detect had stopped it."""
        if self._task_loop_stopping is None:
            return False
        self._task_loop_stopping = None
        if self._task_loop_resume == "planned":
            self._task_loop_resume = "due"
        return True

    def _task_loop_holds_status(self) -> bool:
        """Whether the window's status line is Loop Detect's right now."""
        return self._task_loop_stopping is not None

    def _task_loop_resume_if_due(self, bridge, window) -> None:
        """Send the window's run on, once its stopped turn has fully finished."""
        if self._task_loop_resume != "due" or bridge.busy:
            return
        self._task_loop_resume = ""
        if not self._task_loop_on() or window is None or not window.is_open:
            return
        if str(getattr(window, "session_id", "") or "") != self._task_loop_for:
            return
        self._task_loop_resumes += 1
        self._send_into_task(self._loop_settings.message, auto=True)

    def _task_loop_cancel(self) -> bool:
        """Call off the window's resume. Returns whether there was one."""
        had = bool(self._task_loop_resume)
        self._task_loop_resume = ""
        return had

    def _task_loop_forget(self) -> None:
        """The window closed, or its run changed hands: start again."""
        self._task_loop_cancel()
        self._task_loop_resumes = 0
        self._task_loop_watch.reset()

    # ── The switch and dials ─────────────────────────────────────────────

    def _loop_action(self, action: str) -> None:
        """Every control in the LOOP DETECT block, by its keyline action."""
        if action == "loop-detect":
            self._set_loop(enabled=not self._loop_settings.enabled)
        elif action == "loop-scheduled":
            self._set_loop(scheduled=not self._loop_settings.scheduled)
        elif action.endswith(("-up", "-down")):
            dial = action[len("loop-"):].rsplit("-", 1)[0]
            field = _DIAL_FIELDS.get(dial)
            if field is not None:
                self._step_loop(field, 1 if action.endswith("-up") else -1)

    def _set_loop(self, **changes) -> str:
        """Change Loop Detect settings: apply, save, redraw and say so.

        Returns "" or why the change could not be saved — it is applied
        either way, because a switch that did nothing until the file was
        writable would look broken.
        """
        before = self._loop_settings
        after = replace(before, **changes)
        if after == before:
            return ""
        self._loop_apply(before, after)
        problem = ""
        for field in changes:
            value = getattr(after, field)
            if getattr(before, field) != value:
                problem = self._save_setting(f"ui.loop_detect.{field}", value) or problem
        self._notify_panel(
            self._loop_sentence(before, after)
            + (f"  (not saved: {problem})" if problem else ""),
            seconds=8.0,
        )
        return problem

    def _loop_apply(self, before: LoopSettings, after: LoopSettings) -> None:
        self._loop_settings = after
        self._loop_watch.configure(after)
        self._task_loop_watch.configure(after)
        if before.enabled != after.enabled:
            # Switched on mid-reply, it counts from here; switched off, it
            # lets go of everything — including a resume not yet sent.
            self._loop_watch.reset()
            if not after.enabled:
                self._loop_cancel()
        was = before.enabled and before.scheduled
        now = after.enabled and after.scheduled
        if was != now:
            # The same for the task window, which SCHEDULED gates as well.
            self._task_loop_watch.reset()
            if not now:
                self._task_loop_cancel()
        self._sync_loop_panel()

    def _loop_sentence(self, before: LoopSettings, after: LoopSettings) -> str:
        """What a change did, in a sentence for the notice line."""
        if before.enabled != after.enabled:
            if not after.enabled:
                return "Loop Detect off — replies stream exactly as they always have."
            return (
                f"Loop Detect on — a passage that comes round {times(after.repeats)} "
                "in one response stops the turn"
                + (", and the model is told to carry on." if after.resumes else ".")
            )
        if before.scheduled != after.scheduled:
            if not after.scheduled:
                return (
                    "SCHEDULED off — scheduled tasks' runs are left alone; "
                    "this chat is still watched."
                    if after.enabled
                    else "SCHEDULED off."
                )
            if not after.enabled:
                return (
                    "SCHEDULED on — it takes effect when LOOP DETECT itself "
                    "is switched on."
                )
            return (
                "SCHEDULED on — a scheduled task's run that loops is stopped "
                "and sent on too, from its next turn."
            )
        if before.repeats != after.repeats:
            return f"REPEATS {after.repeats} — the same passage {times(after.repeats)} stops the turn."
        if before.min_words != after.min_words:
            return (
                f"MIN WORDS {after.min_words} — lines shorter than that are never counted."
            )
        if before.resumes != after.resumes:
            if after.resumes == 0:
                return "RESUMES 0 — a loop is stopped and left for you; nothing is sent."
            return (
                f"RESUMES {after.resumes} — the model is sent on up to "
                f"{times(after.resumes)} after each of your messages."
            )
        return "Loop Detect settings changed."

    def _step_loop(self, field: str, direction: int) -> None:
        low, high, step = RANGES[field]
        current = getattr(self._loop_settings, field)
        value = max(low, min(high, current + direction * step))
        if value == current:
            self._notify_panel(
                f"{_FIELD_LABELS[field]} is already at its "
                + ("lowest." if direction < 0 else "highest.")
            )
            return
        self._set_loop(**{field: value})

    # ── What it says about itself ────────────────────────────────────────

    def _sync_loop_panel(self) -> None:
        from curie_cli.bench_ui.panes import PanelPane

        pane = self._maybe("#pane-panel", PanelPane)
        if pane is not None:
            pane.refresh_loop(self._loop_settings)

    def _loop_adopt(self, settings: LoopSettings) -> List[str]:
        """Adopt Loop Detect settings another window saved. Returns what moved."""
        if settings == self._loop_settings:
            return []
        before = self._loop_settings
        self._loop_apply(before, settings)
        if before.enabled != settings.enabled:
            return ["Loop Detect on" if settings.enabled else "Loop Detect off"]
        if before.scheduled != settings.scheduled:
            return [
                "Loop Detect for scheduled tasks on"
                if settings.scheduled
                else "Loop Detect for scheduled tasks off"
            ]
        return ["Loop Detect settings"]


__all__ = ["BOUNDARIES", "DIALS", "LoopControlsMixin", "times"]
