"""The console's voice controls: dictation in, speech out.

Two switches and a voice list, wrapped around the process-wide voice API in
:mod:`curie_cli.voice` so the console never talks to an audio device itself.
That module already owns the hard parts — recorder lifecycle, the
voice-activity loop, barge-in, the TTS dispatcher — and re-implementing any
of it here would mean the console's voice mode behaved differently from the
CLI's for no reason anyone could explain.

Everything is optional and everything degrades. Audio brings in
``sounddevice``, ``numpy`` and a speech model, none of which the ``ui`` extra
installs; a console on a machine without them has to keep working with the
switches turned off and a sentence saying why, not fail to start. So the
import is lazy, every entry point returns a reason instead of raising, and
the widgets that show the switches read :attr:`VoiceDesk.reason` when a
toggle refuses.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from curie_cli.bench_ui.settings import (
    KEY_PIPER_VOICE,
    KEY_SPEAK_REPLIES,
    KEY_TTS_PROVIDER,
    KEY_VOICE_INPUT,
    BenchSettings,
    PiperVoice,
    list_piper_voices,
    piper_voices_dir,
    read_settings,
    write_setting,
)

#: The provider selecting a voice from the folder implies. Piper is the only
#: local, free, no-key backend Curie ships, which is what makes a *folder of
#: voices* the right way to choose one — every other provider's voices live
#: behind an API.
PIPER = "piper"


class VoiceDesk:
    """Owns the console's microphone and speaker state.

    Deliberately not a widget. The settings pane draws the switches, the app
    routes transcripts into the composer, and both of those are about the
    interface; what is on and what it is bound to has to outlive any one pane
    being unmounted, so it lives here and is handed in.
    """

    def __init__(
        self,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_transcript = on_transcript
        self._on_status = on_status
        self._listening = False
        self._speaking_enabled = False
        self._reason = ""
        self._speech_stop: Optional[threading.Event] = None
        self._lock = threading.Lock()
        settings = read_settings()
        self._settings = settings
        self._speaking_enabled = settings.speak_replies

    # ── What the console knows ───────────────────────────────────────────

    @property
    def settings(self) -> BenchSettings:
        return self._settings

    @property
    def listening(self) -> bool:
        """Whether dictation is running right now."""
        return self._listening

    @property
    def speaking_enabled(self) -> bool:
        """Whether finished replies are read aloud."""
        return self._speaking_enabled

    @property
    def reason(self) -> str:
        """Why the last request could not be honoured, or ""."""
        return self._reason

    def reload(self) -> BenchSettings:
        """Re-read the stored preferences (after an external config edit)."""
        self._settings = read_settings()
        return self._settings

    # ── Availability ─────────────────────────────────────────────────────

    def _api(self):
        """The process-wide voice module, or None with a reason recorded.

        Imported on demand and never cached as "unavailable": a user who
        installs the audio extra and comes back to the settings pane should
        find the switch works, without restarting the console.
        """
        try:
            from curie_cli import voice as voice_api

            return voice_api
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._reason = (
                "Voice needs the audio extra — install it with "
                f"`curie update --ensure voice`.  ({type(exc).__name__})"
            )
            return None

    def available(self) -> bool:
        """Whether the audio stack can be imported at all."""
        return self._api() is not None

    # ── Dictation ────────────────────────────────────────────────────────

    def toggle_listening(self) -> bool:
        """Start or stop dictation. Returns the state it ended up in."""
        return self.stop_listening() if self._listening else self.start_listening()

    # (``shutdown`` below is the way to release the devices on the way out.)

    def start_listening(self, remember: bool = True) -> bool:
        """Begin continuous dictation. False (with a reason) if it cannot.

        ``remember`` writes the switch position to config. It is off when the
        console is *restoring* the stored position at start-up, which would
        otherwise be a config save on every launch.
        """
        if self._listening:
            return True
        api = self._api()
        if api is None:
            return False
        self._reason = ""
        try:
            api.start_continuous(
                on_transcript=self._transcript,
                on_status=self._status,
                on_silent_limit=self._silent_limit,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._reason = f"The microphone could not be opened ({type(exc).__name__}: {exc})"
            return False
        self._listening = True
        if remember:
            write_setting(KEY_VOICE_INPUT, True)
            self._settings = read_settings()
        return True

    def stop_listening(self, remember: bool = True) -> bool:
        """End dictation. Always returns False — the state it leaves behind."""
        api = self._api()
        if api is not None:
            try:
                api.stop_continuous()
            except Exception:
                # A recorder that will not close is not a reason to leave the
                # switch showing "on"; the loop is a daemon thread either way.
                pass
        self._listening = False
        if remember:
            write_setting(KEY_VOICE_INPUT, False)
            self._settings = read_settings()
        return False

    def shutdown(self) -> None:
        """Release the audio devices without changing what is remembered.

        Closing the console is not the user turning the microphone off. Doing
        this through :meth:`stop_listening` wrote ``false`` to the config on
        every exit, so the switch could be turned on and would never once
        come back on.
        """
        self.stop_listening(remember=False)
        self.stop_speaking()

    def _transcript(self, text: str) -> None:
        if self._on_transcript and text and text.strip():
            self._on_transcript(text.strip())

    def _status(self, text: str) -> None:
        if self._on_status and text:
            self._on_status(str(text))

    def _silent_limit(self) -> None:
        self._listening = False
        self._status("Dictation stopped — three turns with nothing said.")

    # ── Speech ───────────────────────────────────────────────────────────

    def toggle_speaking(self) -> bool:
        """Turn reading replies aloud on or off. Returns the new state."""
        if self._speaking_enabled:
            self._speaking_enabled = False
            self.stop_speaking()
        else:
            if self._api() is None:
                return False
            self._reason = ""
            self._speaking_enabled = True
        write_setting(KEY_SPEAK_REPLIES, self._speaking_enabled)
        self._settings = read_settings()
        return self._speaking_enabled

    def speak(self, text: str) -> bool:
        """Read ``text`` aloud on a worker thread. False if speech is off.

        Threaded because synthesis blocks for as long as the audio plays, and
        the console has a frame clock to keep. One utterance at a time: the
        previous one is cut rather than queued, so a reply that arrives while
        the last is still playing does not stack up minutes of backlog.
        """
        if not self._speaking_enabled or not text or not text.strip():
            return False
        api = self._api()
        if api is None:
            return False
        self.stop_speaking()
        stop = threading.Event()
        with self._lock:
            self._speech_stop = stop
        threading.Thread(
            target=self._speak_worker,
            args=(text, stop, api),
            name="bench-ui-tts",
            daemon=True,
        ).start()
        return True

    def _speak_worker(self, text: str, stop: threading.Event, api) -> None:
        try:
            api.speak_text(text, stop_event=stop)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._status(f"Speech failed ({type(exc).__name__}: {exc})")
        finally:
            with self._lock:
                if self._speech_stop is stop:
                    self._speech_stop = None

    def stop_speaking(self) -> None:
        """Cut whatever is playing. Safe to call when nothing is."""
        with self._lock:
            stop, self._speech_stop = self._speech_stop, None
        if stop is not None:
            stop.set()

    @property
    def speaking(self) -> bool:
        """Whether an utterance is playing right now."""
        with self._lock:
            return self._speech_stop is not None

    # ── The voice folder ─────────────────────────────────────────────────

    def voices(self) -> List[PiperVoice]:
        """Every Piper voice on disk, re-read from the folder each time.

        Not cached. The refresh button exists precisely because the folder
        changes under the console — a download finishing, a model copied in
        by hand — and a cache would make that button a lie.
        """
        return list_piper_voices(self._settings)

    def voices_dir(self):
        return piper_voices_dir(self._settings)

    def selected_voice(self) -> str:
        return self._settings.piper_voice

    def select_voice(self, name: str) -> str:
        """Make ``name`` the voice. Returns "" or why it could not be set.

        Selecting a voice from the folder also selects Piper as the provider:
        the folder *is* Piper's, so a voice picked here that then came out in
        an Edge accent would be the console ignoring the only instruction it
        was given.
        """
        name = (name or "").strip()
        if not name:
            return "No voice selected."
        problem = write_setting(KEY_PIPER_VOICE, name)
        if problem:
            return problem
        problem = write_setting(KEY_TTS_PROVIDER, PIPER)
        if problem:
            return problem
        self._settings = read_settings()
        return ""


__all__ = ["PIPER", "VoiceDesk"]
