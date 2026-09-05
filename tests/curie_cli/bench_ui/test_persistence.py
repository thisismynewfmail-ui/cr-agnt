"""Every setting the console offers has to survive closing it.

A preference that is applied and then forgotten is not a preference — it is a
per-session toggle the reader has to find and set again on every launch. The
skin was exactly that: ``set_active_skin`` changes only the running process's
idea of the active skin, and the console reached it through ``curie ui``,
which is not the path that reads ``display.skin`` back at start-up. So a skin
could be picked, applied everywhere, saved nowhere, and read nowhere.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import settings as bench_settings  # noqa: E402
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_INDICATORS,
    KEY_SKIN,
    KEY_SPEAK_REPLIES,
    KEY_VOICE_INPUT,
    read_settings,
    restore_active_skin,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def curie_home(tmp_path, monkeypatch):
    """An isolated CURIE_HOME with a config the console can write to."""
    home = tmp_path / ".curie"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  default: test/model\n", encoding="utf-8")
    monkeypatch.setenv("CURIE_HOME", str(home))
    # config.py caches on the file's (mtime, size) keyed by path, so a fresh
    # home is enough; nothing else needs resetting between tests.
    return home


# ── What the console reads back ──────────────────────────────────────────

def test_every_stored_preference_is_read_back(curie_home):
    curie_home.joinpath("config.yaml").write_text(
        "display:\n"
        "  skin: curie-vga\n"
        "ui:\n"
        "  indicators: sweep\n"
        "  voice_input: true\n"
        "  speak_replies: true\n"
        "tts:\n"
        "  provider: piper\n"
        "  piper:\n"
        "    voice: en_GB-alan-low\n",
        encoding="utf-8",
    )
    settings = read_settings()
    assert settings.skin == "curie-vga"
    assert settings.indicators == "sweep"
    assert settings.voice_input is True
    assert settings.speak_replies is True
    assert settings.tts_provider == "piper"
    assert settings.piper_voice == "en_GB-alan-low"


def test_the_saved_skin_is_put_back_into_the_skin_engine(curie_home):
    """The half that was missing: nothing on this path ever loaded it.

    ``curie ui`` does not go through the classic CLI's start-up, which is
    where ``init_skin_from_config`` runs — so the console opened on the
    built-in default however many times a skin had been chosen.
    """
    from curie_cli.skin_engine import get_active_skin_name, set_active_skin

    set_active_skin("default")
    curie_home.joinpath("config.yaml").write_text(
        "display:\n  skin: curie-amber\n", encoding="utf-8"
    )
    assert restore_active_skin() == "curie-amber"
    assert get_active_skin_name() == "curie-amber"


def test_the_console_opens_in_the_saved_skin(curie_home):
    """End to end: the palette the console paints with comes from config."""
    from curie_cli.bench_ui.app import BenchConsole
    from curie_cli.skin_engine import set_active_skin

    from tests.curie_cli.bench_ui.test_console import _StubBridge

    set_active_skin("default")
    curie_home.joinpath("config.yaml").write_text(
        "display:\n  skin: curie-vga\n", encoding="utf-8"
    )
    app = BenchConsole(bridge=_StubBridge())
    assert app.bench_palette.skin_name == "curie-vga"


def test_a_skin_that_has_since_been_deleted_does_not_stop_the_console(curie_home):
    """An appearance setting must never be a reason not to start."""
    from curie_cli.skin_engine import set_active_skin

    set_active_skin("default")
    curie_home.joinpath("config.yaml").write_text(
        "display:\n  skin: a-skin-that-was-removed\n", encoding="utf-8"
    )
    restore_active_skin()  # must not raise

    from curie_cli.bench_ui.app import BenchConsole

    from tests.curie_cli.bench_ui.test_console import _StubBridge

    app = BenchConsole(bridge=_StubBridge())
    assert app.bench_palette.values, "the console came up with no palette"


def test_an_unreadable_config_resolves_to_defaults(curie_home, monkeypatch):
    def boom():
        raise RuntimeError("config.yaml is a directory")

    monkeypatch.setattr(bench_settings, "_config", boom)
    settings = read_settings()
    assert settings.skin == ""
    assert settings.indicators == bench_settings.DEFAULT_KIT
    assert restore_active_skin(settings) is not None


# ── What the console writes ──────────────────────────────────────────────

def test_choosing_a_skin_writes_it_down():
    """Applied *and* saved. Applied alone lasts one session."""
    from textual.widgets import DataTable

    from curie_cli.bench_ui.app import BenchConsole

    from tests.curie_cli.bench_ui.test_console import _StubBridge

    saved: list[tuple[str, object]] = []

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("panel")
            for _ in range(3):
                await pilot.pause()

            import curie_cli.bench_ui.app as app_module

            original = app_module.write_setting
            app_module.write_setting = lambda key, value: (
                saved.append((key, value)) or ""
            )
            try:
                table = app.query_one("#panel-table", DataTable)
                target = None
                for index in range(table.row_count):
                    name = str(table.get_row_at(index)[0]).replace("▶", "").strip()
                    if name and name != "default" and name != "—":
                        target = (index, name)
                        break
                assert target, "no second skin to select"
                index, name = target
                table.move_cursor(row=index)
                await pilot.pause()
                table.post_message(
                    DataTable.RowSelected(
                        table,
                        index,
                        table.coordinate_to_cell_key(table.cursor_coordinate).row_key,
                    )
                )
                for _ in range(3):
                    await pilot.pause()
            finally:
                app_module.write_setting = original
            return name

    name = _run(scenario())
    assert (KEY_SKIN, name) in saved, f"the skin was applied but not saved: {saved}"


@pytest.mark.parametrize(
    "action,key",
    [("voice-listen", KEY_VOICE_INPUT), ("voice-speak", KEY_SPEAK_REPLIES)],
)
def test_the_voice_switches_are_written_down(action, key, monkeypatch):
    from curie_cli.bench_ui import voice as bench_voice
    from curie_cli.bench_ui.app import BenchConsole

    from tests.curie_cli.bench_ui.test_console import _StubBridge

    class _FakeApi:
        continuous = False

        def start_continuous(self, **kwargs):
            self.continuous = True

        def stop_continuous(self, **kwargs):
            self.continuous = False

        def speak_text(self, text, stop_event=None):
            pass

    saved: list[tuple[str, object]] = []
    monkeypatch.setattr(
        bench_voice, "write_setting", lambda k, v: (saved.append((k, v)) or "")
    )

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app.voice, "_api", lambda: _FakeApi())
            app.run_keyline_action(action)
            for _ in range(2):
                await pilot.pause()

    _run(scenario())
    assert (key, True) in saved, f"{action} was not remembered: {saved}"


def test_the_indicator_set_is_read_at_start_up(curie_home):
    from curie_cli.bench_ui.app import BenchConsole

    from tests.curie_cli.bench_ui.test_console import _StubBridge

    curie_home.joinpath("config.yaml").write_text(
        "ui:\n  indicators: cells\n", encoding="utf-8"
    )

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(3):
                await pilot.pause()
            from curie_cli.bench_ui.indicators import ActivityMonitor

            assert app.query_one("#activity", ActivityMonitor).kit.name == "cells"

    _run(scenario())


def test_a_stored_speak_preference_reaches_the_desk(curie_home):
    """Speech is restored silently — it changes nothing until a turn ends."""
    from curie_cli.bench_ui.voice import VoiceDesk

    curie_home.joinpath("config.yaml").write_text(
        "ui:\n  speak_replies: true\n", encoding="utf-8"
    )
    assert VoiceDesk().speaking_enabled is True


def test_the_settings_keys_are_the_ones_the_cli_uses():
    """One store, not two. `curie config set ui.indicators scope` must work."""
    assert KEY_SKIN == "display.skin"
    assert KEY_INDICATORS == "ui.indicators"
    assert KEY_VOICE_INPUT == "ui.voice_input"
    assert KEY_SPEAK_REPLIES == "ui.speak_replies"

    from curie_cli.config_defaults import DEFAULT_CONFIG

    assert "indicators" in DEFAULT_CONFIG["ui"]
    assert "skin" in DEFAULT_CONFIG["display"]
