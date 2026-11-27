"""Loop Detect: noticing a model that has started saying the same thing again.

A model can fall into a loop mid-stream — most often in its reasoning — and
write the same paragraph, or the same run of paragraphs, over and over until
someone presses STOP. Left alone it can go on for half an hour, and the reader
who has walked away comes back to a wall of one thought and a turn that never
got anywhere. This module is the part that notices, and it knows nothing of
who is watching. Two callers act on it — they stop the turn the way Ctrl+C
would and send the model one message telling it to break out and carry on:

* the bench console (:mod:`curie_cli.bench_ui.loop_controls`), for its own
  chat and for turns typed into a scheduled task's window;
* the scheduler (:mod:`cron.loop_guard`), for scheduled tasks' runs, which
  happen in the gateway with nobody watching at all.

Both read their settings from ``ui.loop_detect`` in ``config.yaml`` — the
PANEL's LOOP DETECT block — through :func:`read_loop_settings`.

What counts as "the same thing" is deliberately strict, because a false
alarm interrupts a model that was doing nothing wrong:

* **The unit is a whole line** — in practice a paragraph, which is what a
  looping model repeats (``loopy_01``–``03`` in the report that asked for
  this: paragraphs, and blocks of paragraphs, word for word). A line with no
  newline in sight for :data:`LONG_LINE` characters — a model that loops
  without ever starting a new paragraph — is cut at its sentence ends
  instead, so a loop that never presses Enter is still seen.
* **Short lines never count.** Fewer than ``min_words`` words is a heading,
  a "Wait.", a "Let me check.", a list item — things an honest answer
  repeats all the time.
* **Code and tables never count.** Fenced code repeats lines by nature
  (``}``, ``return None``), and a table's rows can match exactly.
* **Only verbatim repeats count**, after case, spacing and list markers are
  set aside. A paraphrase is not a loop; it is a model trying again.
* **Each model response is watched on its own**, and the thinking apart from
  the answer: the watch is reset whenever the model makes a tool call, so the
  same "Let me look at the next file." before each of a run of tool calls is
  progress, not repetition.

The work per streamed piece is a scan of that piece for newlines and a
dictionary lookup per finished line — nothing that grows with the length of
the reply, so watching costs the stream nothing. Off, nothing here runs at
all: neither caller feeds it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

#: What the console sends to set the model going again, as the report asked
#: for it. ``ui.loop_detect.message`` in ``config.yaml`` replaces it.
DEFAULT_MESSAGE = "Loop detected, break the loop and continue exactly as you were"

#: Characters of one line, with no newline, before it is cut at its sentence
#: ends and the sentences counted on their own. Long enough that an ordinary
#: paragraph — the paragraphs in the report run to about a thousand — is
#: always one unit, so the same paragraph is always the same unit.
LONG_LINE = 1600

#: Distinct lines remembered for one response before the record is started
#: again. A response that long is hundreds of thousands of words; the cap is
#: there so that no stream, however long, can grow the record without bound.
MAX_UNITS = 50_000

#: How much of the repeated passage the console quotes back.
QUOTE_CHARS = 160

#: A sentence end: the stop itself, any closing quote or bracket, and the
#: space after it. Kept with the sentence it ends.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+")

#: List markers and quote markers, set aside when lines are compared: the
#: same paragraph written once as a bullet and once as prose is still the
#: same paragraph.
_LEAD = re.compile(r"^(?:[-*+•>]+|\d{1,3}[.)])\s+")


# ── Settings ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LoopSettings:
    """``ui.loop_detect`` in ``config.yaml``, parsed by :func:`read_loop_settings`."""

    #: Whether the console watches for loops at all. Off, the console streams
    #: exactly as it did before there was such a thing.
    enabled: bool = True
    #: SCHEDULED: watch scheduled tasks' runs too — the ones the gateway
    #: fires, and turns typed into a task's window. A sub-switch: it does
    #: nothing while ``enabled`` is off. Off by default, because those runs
    #: happen where nobody is looking, and stopping one is not a thing to
    #: start doing to a reader who never asked for it.
    scheduled: bool = False
    #: REPEATS: how many times the same passage has to come round.
    repeats: int = 3
    #: MIN WORDS: how long a line has to be to count.
    min_words: int = 8
    #: RESUMES: how many times in a row the console sends the model on by
    #: itself before leaving the next move to the reader. A model that loops
    #: again straight after being told not to is not helped by a fourth
    #: telling, and a console that resumed for ever would be a loop of its
    #: own. Counted from the reader's last message; 0 stops without resuming.
    resumes: int = 3
    #: What is sent to set the model going again.
    message: str = DEFAULT_MESSAGE


#: Each numeric setting's range and the step its ◄ ► buttons move it by.
RANGES: Dict[str, Tuple[int, int, int]] = {
    "repeats": (2, 100, 1),
    "min_words": (3, 100, 1),
    "resumes": (0, 100, 1),
}


def clamp_loop_setting(name: str, value: Any) -> int:
    """A numeric Loop Detect setting, forced into its range (default if unusable)."""
    low, high, _step = RANGES[name]
    default = getattr(LoopSettings(), name)
    if isinstance(value, bool):
        return default
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def read_loop_settings(node: Any) -> LoopSettings:
    """``ui.loop_detect``, every field defaulted or clamped into range.

    Total, like every settings read in the console: a missing section, a
    section of the wrong shape and a value out of range all resolve to the
    documented default rather than raising.
    """
    node = node if isinstance(node, dict) else {}
    defaults = LoopSettings()
    message = node.get("message")
    message = " ".join(str(message).split()) if isinstance(message, str) else ""
    return LoopSettings(
        enabled=_switch(node.get("enabled"), defaults.enabled),
        scheduled=_switch(node.get("scheduled"), defaults.scheduled),
        repeats=clamp_loop_setting("repeats", node.get("repeats", defaults.repeats)),
        min_words=clamp_loop_setting(
            "min_words", node.get("min_words", defaults.min_words)
        ),
        resumes=clamp_loop_setting("resumes", node.get("resumes", defaults.resumes)),
        message=message or defaults.message,
    )


def _switch(value: Any, default: bool) -> bool:
    """A stored switch position; anything unreadable is the default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def scheduled_settings(config: Any) -> Optional[LoopSettings]:
    """The settings to watch a scheduled run with — or None, not watched.

    ``config`` is the whole of ``config.yaml``. A run is watched only when
    LOOP DETECT and its SCHEDULED switch are both on.
    """
    ui = config.get("ui") if isinstance(config, dict) else None
    node = ui.get("loop_detect") if isinstance(ui, dict) else None
    settings = read_loop_settings(node)
    return settings if settings.enabled and settings.scheduled else None


# ── Detection ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Loop:
    """A passage that has come round ``count`` times in one response."""

    #: The passage as it was written the time that tripped the detector.
    passage: str
    count: int
    #: ``thinking`` or ``answer``: which stream it was found in.
    where: str

    def quote(self, limit: int = QUOTE_CHARS) -> str:
        """The passage, cut to ``limit`` characters at a word for quoting."""
        text = " ".join(self.passage.split())
        if len(text) <= limit:
            return text
        cut = text.rfind(" ", 0, limit)
        return text[: cut if cut > limit // 2 else limit].rstrip(" ,;:") + "…"


def _key(line: str) -> str:
    """A line as it is compared: case, spacing and list markers set aside."""
    text = " ".join(line.split()).lower()
    return _LEAD.sub("", text, count=1)


def _words(key: str) -> int:
    return sum(1 for word in key.split() if any(ch.isalnum() for ch in word))


def _is_fence(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


class LoopDetector:
    """Counts the lines of one stream and says when one has come round.

    Fed the stream piece by piece, as it arrives; :meth:`feed` returns a
    :class:`Loop` the moment a line completes its ``repeats``-th appearance,
    and ``None`` otherwise. :meth:`reset` starts the count again, which the
    console does at every response boundary.
    """

    def __init__(self, where: str, repeats: int = 3, min_words: int = 8) -> None:
        self.where = where
        self.repeats = max(2, int(repeats))
        self.min_words = max(1, int(min_words))
        self._pending = ""
        #: Whether the front of the line in ``_pending`` has been cut away
        #: already (see :data:`LONG_LINE`), so what completes is a tail.
        self._clipped = False
        self._fence = False
        self._counts: Dict[str, int] = {}

    def configure(self, repeats: int, min_words: int) -> None:
        """New thresholds. The count so far is kept; it is still true."""
        self.repeats = max(2, int(repeats))
        self.min_words = max(1, int(min_words))

    def reset(self) -> None:
        self._pending = ""
        self._clipped = False
        self._fence = False
        self._counts.clear()

    def feed(self, text: str) -> Optional[Loop]:
        if not text:
            return None
        pending = self._pending + text if self._pending else text
        start = 0
        while True:
            cut = pending.find("\n", start)
            if cut < 0:
                break
            line = pending[start:cut]
            start = cut + 1
            clipped, self._clipped = self._clipped, False
            found = self._line(line, clipped)
            if found is not None:
                self._pending = pending[start:]
                return found
        pending = pending[start:] if start else pending
        if len(pending) > LONG_LINE:
            pending, found = self._cut_long(pending)
            if found is not None:
                self._pending = pending
                return found
        self._pending = pending
        return None

    # ── The units ────────────────────────────────────────────────────────

    def _line(self, line: str, clipped: bool) -> Optional[Loop]:
        """One finished line: a fence edge, code, or a unit to count."""
        if not clipped and _is_fence(line):
            self._fence = not self._fence
            return None
        if self._fence:
            return None
        return self._unit(line)

    def _cut_long(self, pending: str) -> "tuple[str, Optional[Loop]]":
        """Count the finished sentences of an overlong line; keep the rest.

        What is kept is never longer than :data:`LONG_LINE`, which is what
        keeps the work per piece bounded: a run with no newline *and* no
        sentence end — a blob of base64, a minified line — is let go of
        rather than held and re-scanned on every piece that follows. Such a
        run is not prose, so there is nothing in it to count.
        """
        self._clipped = True
        if self._fence:
            # Code is never counted, so a long line of it is only memory.
            return "", None
        pieces = _SENTENCE_END.split(pending)
        rest = pieces.pop()
        found = None
        for sentence in pieces:
            found = self._unit(sentence)
            if found is not None:
                break
        if len(rest) > LONG_LINE:
            rest = ""
        return rest, found

    def _unit(self, text: str) -> Optional[Loop]:
        key = _key(text)
        if not key or key.startswith("|") or _words(key) < self.min_words:
            return None
        if len(self._counts) >= MAX_UNITS and key not in self._counts:
            self._counts.clear()
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count >= self.repeats:
            return Loop(passage=text.strip(), count=count, where=self.where)
        return None


class LoopWatch:
    """The two streams of one turn, each watched on its own.

    The thinking and the answer are counted apart: a model that thinks a
    paragraph and then answers with it has said it twice, not looped.
    """

    #: The turn events that carry streamed text, and which stream each is.
    STREAMS = {"reasoning": "thinking", "delta": "answer"}

    def __init__(self, settings: LoopSettings) -> None:
        self.thinking = LoopDetector("thinking", settings.repeats, settings.min_words)
        self.answer = LoopDetector("answer", settings.repeats, settings.min_words)

    def configure(self, settings: LoopSettings) -> None:
        for detector in (self.thinking, self.answer):
            detector.configure(settings.repeats, settings.min_words)

    def reset(self) -> None:
        self.thinking.reset()
        self.answer.reset()

    def feed(self, kind: str, text: str) -> Optional[Loop]:
        """A turn event's text. Only ``reasoning`` and ``delta`` are watched."""
        stream = self.STREAMS.get(kind)
        if stream is None:
            return None
        detector = self.thinking if stream == "thinking" else self.answer
        return detector.feed(text)


__all__ = [
    "DEFAULT_MESSAGE",
    "LONG_LINE",
    "Loop",
    "LoopDetector",
    "LoopSettings",
    "LoopWatch",
    "RANGES",
    "clamp_loop_setting",
    "read_loop_settings",
    "scheduled_settings",
]
