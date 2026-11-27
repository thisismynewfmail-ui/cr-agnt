"""Loop Detect for scheduled tasks' runs: LOOP DETECT → SCHEDULED on the PANEL.

A scheduled run happens in the gateway, with nobody watching. A model that
falls into a loop there — the same paragraph of reasoning, word for word,
over and over — is a run the inactivity watchdog never stops, because a
looping stream *is* activity: it goes on until the provider's own output cap,
or for hours, and spends tokens the whole time.

With LOOP DETECT and its SCHEDULED switch both on, :func:`run_watched` runs
the job's turn the way the bench console runs one of its own: the stream is
fed to :class:`agent.loop_detect.LoopWatch`, and when a passage comes round
REPEATS times the agent is interrupted the way Ctrl+C interrupts it in the CLI.
Once the turn has returned — fully stopped, its conversation in hand — the run
is continued in the same session with the resume message, up to RESUMES times.
A run that loops again after that is ended and reported as failed, with the
loop as its reason, rather than delivered as if it had finished.

Off — either switch — the scheduler calls ``run_conversation`` exactly as it
always has; nothing here is imported into the run at all.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from agent.loop_detect import Loop, LoopSettings, LoopWatch

logger = logging.getLogger(__name__)

#: The agent hooks the guard listens on, chained to whatever was there.
_HOOKS = ("reasoning_callback", "tool_start_callback")


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def describe(loop: Loop) -> str:
    """One line saying what was caught, for the log and a failed run's error."""
    return (
        f"the same passage came round {_times(loop.count)} in the {loop.where}: "
        f"“{loop.quote()}”"
    )


class _TurnWatch:
    """Watches one turn's stream and stops the turn when it loops.

    The interrupt is not called from the stream's own callback: that runs on
    whichever thread the provider stream delivers on, possibly inside the
    agent's own locks. A small thread per turn waits to be told, and calls
    ``agent.interrupt()`` from outside — as the CLI's input thread does.
    """

    def __init__(self, agent: Any, settings: LoopSettings) -> None:
        self.agent = agent
        self.watch = LoopWatch(settings)
        self.caught: Optional[Loop] = None
        #: Whether the interrupt was sent while the turn was still running.
        self.stopped = False
        self._lock = threading.Lock()
        self._finished = True
        self._signal = threading.Event()
        self._stopper: Optional[threading.Thread] = None

    # ── One turn ─────────────────────────────────────────────────────────

    def arm(self) -> None:
        with self._lock:
            self.caught = None
            self.stopped = False
            self._finished = False
            self.watch.reset()
            self._signal = threading.Event()
        self._stopper = threading.Thread(
            target=self._stop_when_told,
            args=(self._signal,),
            name="cron-loop-guard",
            daemon=True,
        )
        self._stopper.start()

    def disarm(self) -> None:
        """The turn has returned: let the stopper go, and wait for it."""
        with self._lock:
            self._finished = True
        self._signal.set()
        if self._stopper is not None:
            self._stopper.join(timeout=10.0)
            self._stopper = None

    def _stop_when_told(self, signal: threading.Event) -> None:
        signal.wait()
        with self._lock:
            if self.caught is None or self._finished:
                return
            self.stopped = True
        try:
            self.agent.interrupt()
        except Exception:
            logger.debug("Loop Detect could not interrupt the run", exc_info=True)

    # ── The stream ───────────────────────────────────────────────────────

    def feed(self, kind: str, text: Any) -> None:
        if not text:
            return
        with self._lock:
            if self.caught is not None or self._finished:
                return
            loop = self.watch.feed(kind, str(text))
            if loop is None:
                return
            self.caught = loop
        self._signal.set()

    def boundary(self) -> None:
        """A tool call: whatever the model writes next is a new response."""
        with self._lock:
            self.watch.reset()


def run_watched(
    agent: Any,
    prompt: str,
    *,
    settings: LoopSettings,
    task_id: Optional[str] = None,
    job_name: str = "",
) -> Dict[str, Any]:
    """``agent.run_conversation(prompt)``, stopped and sent on when it loops.

    Returns the last turn's result. When the run loops more often than
    RESUMES allows, that result is marked ``failed`` with the loop as its
    ``error``, so the scheduler records the run as what it was.
    """
    turn = _TurnWatch(agent, settings)
    saved: Dict[str, Any] = {name: getattr(agent, name, None) for name in _HOOKS}

    def on_reasoning(text: str = "", *args, **kwargs) -> None:
        turn.feed("reasoning", text)
        _chain(saved["reasoning_callback"], text, *args, **kwargs)

    def on_tool_start(*args, **kwargs) -> None:
        turn.boundary()
        _chain(saved["tool_start_callback"], *args, **kwargs)

    def on_delta(text: str) -> None:
        turn.feed("delta", text)

    label = job_name or "scheduled run"
    message = prompt
    history: Optional[list] = None
    resumes = 0
    try:
        agent.reasoning_callback = on_reasoning
        agent.tool_start_callback = on_tool_start
        while True:
            turn.arm()
            try:
                result = agent.run_conversation(
                    message,
                    conversation_history=history,
                    stream_callback=on_delta,
                    task_id=task_id,
                )
            finally:
                turn.disarm()
            loop = turn.caught
            if loop is None or not turn.stopped or not isinstance(result, dict):
                return result
            messages = result.get("messages")
            if resumes >= settings.resumes or not isinstance(messages, list) or not messages:
                logger.warning(
                    "Job '%s': Loop Detect stopped the run — %s. Not sent on "
                    "(%d of %d resumes used).",
                    label, describe(loop), resumes, settings.resumes,
                )
                stopped = dict(result)
                stopped["failed"] = True
                stopped["error"] = (
                    f"Loop Detect stopped the run: {describe(loop)}"
                    + (
                        f" It had already been sent on {_times(resumes)}."
                        if resumes else ""
                    )
                )
                return stopped
            resumes += 1
            logger.warning(
                "Job '%s': Loop Detect stopped the run — %s. Sending it on "
                "(%d of %d).",
                label, describe(loop), resumes, settings.resumes,
            )
            # The stop has landed and the turn returned; nothing of it may
            # carry into the next turn, which would otherwise end at once.
            try:
                agent.clear_interrupt()
            except Exception:
                agent._interrupt_requested = False
            history = list(messages)
            message = settings.message
    finally:
        for name, value in saved.items():
            try:
                setattr(agent, name, value)
            except Exception:
                pass


def _chain(callback: Optional[Callable[..., Any]], *args, **kwargs) -> None:
    if callback is None:
        return
    try:
        callback(*args, **kwargs)
    except Exception:
        logger.debug("chained agent callback failed", exc_info=True)


__all__ = ["describe", "run_watched"]
