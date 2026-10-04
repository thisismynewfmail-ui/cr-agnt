"""The console's half of the chatter: its switches, dials and feeds.

:mod:`curie_cli.bench_ui.chatter` is the voice and knows nothing of the
console; this is what connects it — the PANEL pane's CHATTER block, the
settings it saves, and the turn events it is fed from. A mixin, beside
:class:`~curie_cli.bench_ui.access.AccessMixin` and the slash commands, for
the same reason: one feature with its own state, reporting through the
console's notice line and panes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple

from curie_cli.bench_ui import speakers
from curie_cli.bench_ui.chatter import (
    RANGES,
    VOICES,
    ChatterSettings,
    Chatterbox,
    get_voice,
)

#: What each dial is called on the panel.
DIAL_LABELS = {"pitch": "Tone", "volume": "Volume", "speed": "Speed", "wobble": "Wobble"}

#: What a dial or a new voice says, so the reader hears the change.
PREVIEW_LINE = "Hello there!"


class ChatterControlsMixin:
    """The CHATTER block and the feeds, for :class:`BenchConsole`."""

    def _chatter_init(self, settings) -> None:
        self._chatter_settings: ChatterSettings = settings.chatter
        self._chatter = Chatterbox(self._chatter_settings)
        #: The failure last reported, so it is reported once.
        self._chatter_failure = ""

    # ── Feeding it ───────────────────────────────────────────────────────

    def _chatter_quiet(self) -> bool:
        """Whether the chatter must keep still right now.

        Off, or the microphone is open: a voice babbling into the room while
        dictation listens would be transcribed into the composer.
        """
        if not self._chatter_settings.enabled:
            return True
        voice = getattr(self, "voice", None)
        return bool(voice is not None and getattr(voice, "listening", False))

    def _chatter_feed(self, text: str, variant: str = "answer") -> None:
        if not self._chatter_quiet():
            self._chatter.feed(text, variant)

    def _chatter_chirp(self, kind: str, text: str = "") -> None:
        if kind == "wait" and not self._chatter_settings.writing:
            # "waiting on <model>…" belongs to the WRITING switch, with the
            # other long silences, not to the tools'.
            return
        if not self._chatter_quiet():
            self._chatter.chirp(kind, text)

    def _chatter_sustain(self, tool: str) -> None:
        """A tool call has started streaming — see :meth:`Chatterbox.sustain`."""
        if not self._chatter_quiet() and self._chatter_settings.writing:
            self._chatter.sustain(tool)

    # ── The switches and dials ───────────────────────────────────────────

    def _chatter_action(self, action: str) -> None:
        """Every control in the CHATTER block, by its keyline action."""
        current = self._chatter_settings
        toggles = {
            "chatter": "enabled",
            "chatter-board": "board_speaker",
            "chatter-thinking": "thinking",
            "chatter-tools": "tools",
            "chatter-match": "match_stream",
            "chatter-writing": "writing",
        }
        if action in toggles:
            field = toggles[action]
            self._set_chatter(**{field: not getattr(current, field)})
        elif action == "chatter-test":
            self._chatter_test()
        elif action == "chatter-hush":
            self._chatter.hush()
            self._notify_panel("Hushed.", seconds=3.0)
        elif action.endswith(("-up", "-down")):
            setting = action[len("chatter-"):].rsplit("-", 1)[0]
            if setting in RANGES:
                self._step_chatter(setting, 1 if action.endswith("-up") else -1)

    def _set_chatter(self, **changes) -> str:
        """Change chatter settings: apply, save, redraw and say so.

        Returns "" or why the change could not be saved — it is applied
        either way, because a switch that did nothing until the file was
        writable would be a switch that looked broken.
        """
        before = self._chatter_settings
        after = replace(before, **changes)
        if after == before:
            return ""
        self._chatter_settings = after
        self._chatter.apply(after)
        problem = ""
        for field in changes:
            value = getattr(after, field)
            if getattr(before, field) != value:
                problem = self._save_setting(f"ui.chatter.{field}", value) or problem
        self._sync_chatter_panel()
        self._notify_panel(
            self._chatter_sentence(before, after)
            + (f"  (not saved: {problem})" if problem else ""),
            seconds=8.0,
        )
        return problem

    def _chatter_sentence(self, before: ChatterSettings, after: ChatterSettings) -> str:
        """What a change did, in a sentence for the notice line."""
        ok, where = self._chatter_output()
        if before.enabled != after.enabled:
            if not after.enabled:
                return "Chatter off."
            if not ok:
                return f"Chatter on — but there is no way to sound it: {where}."
            return f"Chatter on — replies babble as they stream, through {where}."
        if before.board_speaker != after.board_speaker:
            target = "the board speaker" if after.board_speaker else "the sound card"
            return (
                f"Chatter now goes to {target}" + (f": {where}." if ok else f" — but {where}.")
            )
        if before.thinking != after.thinking:
            return (
                "The thinking is murmured while its drawer is open."
                if after.thinking
                else "The thinking is left silent."
            )
        if before.tools != after.tools:
            return "Tools chirp as they start and finish." if after.tools else "Tools are silent."
        if before.writing != after.writing:
            if not after.writing:
                self._chatter.hush()
            return (
                "The long waits are voiced — a tool call being written, and "
                "waiting on the model."
                if after.writing
                else "The long waits are silent — writing a tool call, and "
                "waiting on the model."
            )
        if before.match_stream != after.match_stream:
            if after.match_stream:
                return (
                    "MATCH STREAM on — the voice keeps the pace the reply "
                    "streams at; SPEED is set aside while this is on."
                )
            return f"MATCH STREAM off — the voice keeps its own pace again (SPEED {after.speed}%)."
        if before.voice != after.voice:
            voice = get_voice(after.voice)
            return f"Voice: {voice.title} — {voice.blurb}."
        for setting, label in DIAL_LABELS.items():
            if getattr(before, setting) != getattr(after, setting):
                value = getattr(after, setting)
                unit = " semitones" if setting == "pitch" else "%"
                shown = f"{value:+d}" if setting == "pitch" else str(value)
                return f"{label} {shown}{unit}."
        return "Chatter settings changed."

    def _step_chatter(self, setting: str, direction: int) -> None:
        low, high, step = RANGES[setting]
        current = getattr(self._chatter_settings, setting)
        value = max(low, min(high, current + direction * step))
        if value == current:
            self._notify_panel(
                f"{DIAL_LABELS[setting]} is already at its "
                + ("lowest." if direction < 0 else "highest.")
            )
            return
        self._set_chatter(**{setting: value})
        if setting == "speed" and self._chatter_settings.match_stream:
            self._notify_panel(
                f"Speed {value}% saved — it is used when MATCH STREAM is off; "
                "while it is on, the stream sets the pace.",
                seconds=8.0,
            )
        # A dial is heard, not read.
        self._chatter_preview()

    def _chatter_select_voice(self, name: str) -> None:
        if name not in VOICES:
            return
        self._set_chatter(voice=name)
        self._chatter_preview()

    def _chatter_preview(self) -> None:
        self._chatter.hush()
        self._chatter.test(PREVIEW_LINE)

    def _chatter_test(self) -> None:
        ok, where = self._chatter_output()
        if not ok:
            self._notify_panel(f"Nothing to play it through — {where}.", seconds=10.0)
            return
        self._chatter.hush()
        self._chatter.test()
        self._notify_panel(f"Testing the chatter through {where} …", seconds=5.0)

    # ── What it says about itself ────────────────────────────────────────

    def _chatter_output(self) -> Tuple[bool, str]:
        """What the chatter would sound through — without opening anything."""
        try:
            return speakers.describe_output(self._chatter_settings.board_speaker)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, f"{type(exc).__name__}: {exc}"

    def _sync_chatter_panel(self) -> None:
        from curie_cli.bench_ui.panes import PanelPane

        pane = self._maybe("#pane-panel", PanelPane)
        if pane is not None:
            pane.refresh_chatter(
                self._chatter_settings, self._chatter_output(), self._chatter.failure
            )

    def _watch_chatter(self) -> None:
        """Report, once, a device that failed on the chatter's own thread."""
        failure = self._chatter.failure
        if failure == self._chatter_failure:
            return
        self._chatter_failure = failure
        self._sync_chatter_panel()
        if failure:
            self._notify_panel(f"The chatter fell silent — {failure}.", seconds=10.0)

    def _chatter_adopt(self, settings: ChatterSettings) -> List[str]:
        """Adopt chatter settings another window saved. Returns what moved."""
        if settings == self._chatter_settings:
            return []
        before, self._chatter_settings = self._chatter_settings, settings
        self._chatter.apply(settings)
        self._sync_chatter_panel()
        if before.enabled != settings.enabled:
            return ["chatter on" if settings.enabled else "chatter off"]
        return ["chatter settings"]


__all__ = ["ChatterControlsMixin", "DIAL_LABELS", "PREVIEW_LINE"]
