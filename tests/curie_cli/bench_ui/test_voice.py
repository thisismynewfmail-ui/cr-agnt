"""Voice: the two switches, the voice folder, and the refresh button.

Everything here has to work on a machine with no audio stack at all, which is
most machines the console runs on: the ``ui`` extra installs Textual, not
sounddevice. So the switches report why they cannot turn on rather than
raising, the folder listing is a directory read and nothing more, and the
settings pane draws the same either way.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import settings as bench_settings  # noqa: E402
from curie_cli.bench_ui import voice as bench_voice  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.settings import (  # noqa: E402
    BenchSettings,
    list_piper_voices,
    piper_voices_dir,
    read_settings,
)
from curie_cli.bench_ui.voice import VoiceDesk  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture
def voice_folder(tmp_path):
    """A Piper folder with one complete voice and one half-downloaded one."""
    folder = tmp_path / "piper-voices"
    folder.mkdir()
    (folder / "en_US-lessac-medium.onnx").write_bytes(b"x" * 2_500_000)
    (folder / "en_US-lessac-medium.onnx.json").write_text("{}")
    (folder / "nested").mkdir()
    (folder / "nested" / "de_DE-thorsten-high.onnx").write_bytes(b"y" * 1_000_000)
    return folder


# ── The folder ───────────────────────────────────────────────────────────

def test_the_folder_listing_finds_voices_including_nested_ones(voice_folder):
    """``piper.download_voices`` nests by language, and the reader can see it."""
    voices = list_piper_voices(BenchSettings(piper_voices_dir=str(voice_folder)))
    names = [voice.name for voice in voices]
    assert "en_US-lessac-medium" in names
    assert "de_DE-thorsten-high" in names, "a nested voice was not listed"


def test_a_voice_without_its_metadata_is_listed_as_not_ready(voice_folder):
    """Piper needs the .onnx.json too; a half-download is not a usable voice."""
    voices = {v.name: v for v in list_piper_voices(
        BenchSettings(piper_voices_dir=str(voice_folder))
    )}
    assert voices["en_US-lessac-medium"].ready
    assert voices["en_US-lessac-medium"].state == "ready"
    assert not voices["de_DE-thorsten-high"].ready
    assert voices["de_DE-thorsten-high"].state == "no metadata"


def test_sizes_are_reported_so_a_stalled_download_is_visible(voice_folder):
    voices = {v.name: v for v in list_piper_voices(
        BenchSettings(piper_voices_dir=str(voice_folder))
    )}
    assert voices["en_US-lessac-medium"].megabytes == pytest.approx(2.4, abs=0.1)


def test_a_missing_folder_is_an_empty_list_and_not_a_crash(tmp_path):
    settings = BenchSettings(piper_voices_dir=str(tmp_path / "not-there"))
    assert list_piper_voices(settings) == []


def test_an_explicit_folder_overrides_the_default(voice_folder):
    settings = BenchSettings(piper_voices_dir=str(voice_folder))
    assert piper_voices_dir(settings) == voice_folder


def test_the_default_folder_is_under_the_curie_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    resolved = piper_voices_dir(BenchSettings())
    assert "piper-voices" in str(resolved)


# ── Settings ─────────────────────────────────────────────────────────────

def test_an_unknown_indicator_set_in_config_falls_back(monkeypatch):
    monkeypatch.setattr(
        bench_settings, "_config", lambda: {"ui": {"indicators": "no-such-set"}}
    )
    assert read_settings().indicators == bench_settings.DEFAULT_KIT


def test_a_config_of_the_wrong_shape_does_not_stop_the_console(monkeypatch):
    """A hand-edited ``ui: true`` must not be able to prevent a start-up."""
    monkeypatch.setattr(bench_settings, "_config", lambda: {"ui": True})
    settings = read_settings()
    assert settings.indicators == bench_settings.DEFAULT_KIT
    assert settings.voice_input is False
    assert settings.speak_replies is False


def test_a_config_that_will_not_load_resolves_to_defaults(monkeypatch):
    def boom():
        raise RuntimeError("config.yaml is a directory")

    monkeypatch.setattr(bench_settings, "_config", boom)
    assert read_settings().indicators == bench_settings.DEFAULT_KIT


def test_the_stored_preferences_are_read_back(monkeypatch):
    monkeypatch.setattr(
        bench_settings,
        "_config",
        lambda: {
            "ui": {"indicators": "sweep", "voice_input": True, "speak_replies": True},
            "tts": {"provider": "piper", "piper": {"voice": "en_GB-alan-low"}},
        },
    )
    settings = read_settings()
    assert settings.indicators == "sweep"
    assert settings.voice_input and settings.speak_replies
    assert settings.tts_provider == "piper"
    assert settings.piper_voice == "en_GB-alan-low"


# ── The desk ─────────────────────────────────────────────────────────────

class _FakeVoiceApi:
    """Stands in for ``curie_cli.voice`` without touching an audio device."""

    def __init__(self):
        self.continuous = False
        self.spoken: list[str] = []
        self.on_transcript = None

    def start_continuous(self, on_transcript, on_status=None, on_silent_limit=None):
        self.continuous = True
        self.on_transcript = on_transcript

    def stop_continuous(self, force_transcribe=False):
        self.continuous = False

    def speak_text(self, text, stop_event=None):
        self.spoken.append(text)


@pytest.fixture
def desk(monkeypatch):
    """A VoiceDesk wired to a fake audio stack and a config that never writes."""
    api = _FakeVoiceApi()
    written: list[tuple[str, object]] = []
    monkeypatch.setattr(
        bench_voice, "write_setting", lambda k, v: (written.append((k, v)) or "")
    )
    monkeypatch.setattr(bench_voice, "read_settings", lambda: BenchSettings())
    desk = VoiceDesk()
    monkeypatch.setattr(desk, "_api", lambda: api)
    desk.api, desk.written = api, written
    return desk


def test_the_microphone_switch_starts_and_stops_dictation(desk):
    assert desk.toggle_listening() is True
    assert desk.api.continuous
    assert desk.listening
    assert desk.toggle_listening() is False
    assert not desk.api.continuous


def test_the_microphone_switch_is_remembered(desk):
    desk.toggle_listening()
    assert ("ui.voice_input", True) in desk.written
    desk.toggle_listening()
    assert ("ui.voice_input", False) in desk.written


def test_a_dictated_sentence_reaches_the_callback(desk):
    heard: list[str] = []
    desk._on_transcript = heard.append
    desk.start_listening()
    desk.api.on_transcript("  what changed in the last commit  ")
    assert heard == ["what changed in the last commit"]


def test_an_empty_transcript_is_not_forwarded(desk):
    heard: list[str] = []
    desk._on_transcript = heard.append
    desk.start_listening()
    desk.api.on_transcript("   ")
    assert heard == []


def test_the_speaker_switch_gates_speech(desk):
    assert desk.speak("nothing yet") is False, "speech ran with the switch off"
    assert desk.toggle_speaking() is True
    assert desk.speak("say this") is True
    for _ in range(200):
        if desk.api.spoken:
            break
        import time as _time

        _time.sleep(0.01)
    assert desk.api.spoken == ["say this"]


def test_the_speaker_switch_is_remembered(desk):
    desk.toggle_speaking()
    assert ("ui.speak_replies", True) in desk.written


def test_selecting_a_voice_also_selects_piper(desk):
    assert desk.select_voice("en_US-lessac-medium") == ""
    assert ("tts.piper.voice", "en_US-lessac-medium") in desk.written
    assert ("tts.provider", "piper") in desk.written


def test_selecting_no_voice_is_refused_with_a_reason(desk):
    assert desk.select_voice("  ") == "No voice selected."


def test_no_audio_stack_reports_why_rather_than_raising(monkeypatch):
    monkeypatch.setattr(bench_voice, "write_setting", lambda k, v: "")
    monkeypatch.setattr(bench_voice, "read_settings", lambda: BenchSettings())
    desk = VoiceDesk()

    def no_import(*_args, **_kwargs):
        raise ImportError("No module named 'sounddevice'")

    monkeypatch.setattr(bench_voice.VoiceDesk, "_api", lambda self: None)
    assert desk.toggle_listening() is False
    assert desk.toggle_speaking() is False
    assert desk.speak("anything") is False


def test_closing_the_console_does_not_un_remember_the_switches(desk):
    """Shutting down is not the user turning the microphone off.

    Releasing the device through the ordinary stop path wrote ``false`` to
    the config on every exit, so the switch could be turned on and would
    never once come back on.
    """
    desk.toggle_listening()
    desk.written.clear()
    desk.shutdown()
    assert not desk.api.continuous, "the recorder was left holding the device"
    assert desk.written == [], f"closing the console rewrote the switches: {desk.written}"


def test_restoring_the_stored_switch_does_not_write_it_back(desk):
    """Acting on the remembered value must not be a config save per launch."""
    desk.start_listening(remember=False)
    assert desk.listening
    assert desk.written == []


def test_a_microphone_that_will_not_open_reports_why(monkeypatch):
    monkeypatch.setattr(bench_voice, "write_setting", lambda k, v: "")
    monkeypatch.setattr(bench_voice, "read_settings", lambda: BenchSettings())

    class _Broken(_FakeVoiceApi):
        def start_continuous(self, *a, **k):
            raise OSError("no default input device")

    desk = VoiceDesk()
    monkeypatch.setattr(desk, "_api", lambda: _Broken())
    assert desk.start_listening() is False
    assert "could not be opened" in desk.reason
    assert not desk.listening


# ── The settings pane ────────────────────────────────────────────────────

def test_the_pane_shows_both_switches_and_a_refresh_button():
    async def scenario():
        from curie_cli.bench_ui.panes import PanelButton, ToggleSwitch

        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            # Asked of the voice block rather than of the pane: the display
            # block above it has switches of its own now, and this test is
            # about the two that belong to VOICE.
            switches = {
                s.switch_key
                for s in app.query_one("#voice-switches").query(ToggleSwitch)
            }
            assert switches == {"voice-listen", "voice-speak"}
            buttons = {b.button_action for b in app.query(PanelButton)}
            assert "voice-refresh" in buttons
    _run(scenario())


def test_the_refresh_button_re_reads_the_folder(monkeypatch, voice_folder):
    """The button exists because the folder changes while the console is open."""
    async def scenario():
        from textual.widgets import DataTable

        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)

            import curie_cli.bench_ui.panes as panes_module

            monkeypatch.setattr(
                panes_module,
                "read_settings",
                lambda: BenchSettings(piper_voices_dir=str(voice_folder)),
            )
            monkeypatch.setattr(
                app.voice, "voices", lambda: list_piper_voices(
                    BenchSettings(piper_voices_dir=str(voice_folder))
                )
            )
            monkeypatch.setattr(app.voice, "voices_dir", lambda: voice_folder)

            table = app.query_one("#voice-table", DataTable)
            assert table.row_count == 1, "the fixture folder was found too early"

            app.run_keyline_action("voice-refresh")
            await _settle(pilot)

            listed = [
                str(table.get_row_at(i)[0]).replace("▶", "").strip()
                for i in range(table.row_count)
            ]
            assert "en_US-lessac-medium" in listed
            assert "de_DE-thorsten-high" in listed
            assert "2 voice(s)" in str(app.query_one("#notice").render())
    _run(scenario())


def test_toggling_a_switch_from_the_pane_moves_it():
    async def scenario():
        from curie_cli.bench_ui.panes import ToggleSwitch

        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            switch = app.query_one("#switch-voice-speak", ToggleSwitch)
            assert not switch.is_on

            app.voice._api = lambda: _FakeVoiceApi()
            app.run_keyline_action("voice-speak")
            await _settle(pilot)
            assert switch.is_on, "the speaker switch did not move"
            assert "ON" in str(switch.render())
    _run(scenario())


def test_a_finished_reply_is_spoken_when_the_speaker_is_on():
    async def scenario():
        from curie_cli.bench_ui.agent_bridge import TurnEvent

        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            said: list[str] = []
            app.voice.speak = lambda text: said.append(text) or True
            app.bridge._events.put(TurnEvent("delta", "the whole answer"))
            app.bridge._events.put(TurnEvent("done", "the whole answer"))
            app._pump_agent()
            await _settle(pilot)
            assert said == ["the whole answer"]
    _run(scenario())


def test_a_dictated_sentence_becomes_a_turn():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app._dictated("summarise the last three commits")
            await _settle(pilot)
            assert bridge.submitted == ["summarise the last three commits"]
    _run(scenario())


def test_dictation_during_a_turn_is_kept_rather_than_dropped():
    class _Busy(_StubBridge):
        @property
        def busy(self):
            return True

    async def scenario():
        from curie_cli.bench_ui.panes import Composer

        bridge = _Busy()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            app._dictated("and one more thing")
            await _settle(pilot)
            assert bridge.submitted == []
            assert "and one more thing" in app.query_one("#composer", Composer).text
    _run(scenario())
