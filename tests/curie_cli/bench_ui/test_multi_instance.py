"""Two consoles open at once have to agree with each other.

Nothing in Curie stops a second ``curie ui`` — or a ``curie chat``, or the
gateway — from running beside the first, and people do it: one window for a
long turn, another to look something up. What used to happen then is that each
window read the shared state exactly once, at start-up, and believed its own
copy for the rest of the session:

* a skin, display mode or indicator set chosen in one window stayed invisible
  in the other until it was restarted;
* a conversation started in one never appeared in the other's logbook, because
  the logbook is only re-read after this console's own turns;
* a scheduled task created in one was invisible in the other, so the second
  window would happily create a duplicate of it.

The mechanism is deliberately not a protocol. Both windows already write the
same files; the fix is to notice when one of them has, which is a fingerprint
and a timer. These tests are about that noticing — that it happens, that it
happens once per real change, and that a window does not read its own writes
back and announce them to the person who made them.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.settings import (  # noqa: E402
    KEY_INDICATORS,
    KEY_SKIN_MODE,
    write_setting,
)
from curie_cli.bench_ui.sync import ConsoleSync, StoreWatch  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """A config file and a session store nobody else is writing to."""
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


def _console(mode: str = dos.MODE_BENCH) -> BenchConsole:
    app = BenchConsole(bridge=_StubBridge())
    app._display_mode = mode
    app._optics = dos.Optics(mode=mode)
    app.bench_palette = app._resolve_palette()
    return app


# ── The watch itself ─────────────────────────────────────────────────────


def test_a_watch_reports_a_change_once(tmp_path):
    target = tmp_path / "thing.json"
    watch = StoreWatch("thing", lambda: target)

    assert watch.changed() is False, "a first look is a baseline, not a change"
    target.write_text("{}", encoding="utf-8")
    assert watch.changed() is True
    assert watch.changed() is False, "the same change was reported twice"


def test_a_file_that_does_not_exist_is_not_a_change(tmp_path):
    watch = StoreWatch("missing", lambda: tmp_path / "never-written")
    assert watch.changed() is False
    assert watch.changed() is False


def test_a_resolver_that_raises_is_not_a_change():
    """This runs on a timer inside a full-screen app; it cannot raise."""

    def explode():
        raise RuntimeError("no home")

    watch = StoreWatch("broken", explode)
    assert watch.changed() is False


def test_owning_a_write_stops_it_being_read_back(tmp_path):
    target = tmp_path / "thing.json"
    watch = StoreWatch("thing", lambda: target)
    watch.changed()
    target.write_text("{}", encoding="utf-8")
    watch.sync()
    assert watch.changed() is False


def test_the_console_watches_every_store_it_shares():
    """Named, so a store added later cannot quietly go unwatched."""
    sync = ConsoleSync()
    assert {watch.name for watch in sync.watches()} == {
        "settings",
        "sessions",
        "schedule",
        "executions",
    }


# ── Settings ─────────────────────────────────────────────────────────────


def test_a_skin_mode_chosen_elsewhere_reaches_this_console():
    async def scenario():
        app = _console(dos.MODE_BENCH)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            assert not app.bench_palette.dos

            # The other window turns DOS mode on.
            assert write_setting(KEY_SKIN_MODE, dos.MODE_DOS) == ""

            app._poll_shared_stores()
            await _settle(pilot)
            assert app.display_mode == dos.MODE_DOS
            assert app.bench_palette.dos, "the palette did not follow the mode"

    _run(scenario())


def test_a_tube_chosen_elsewhere_reaches_this_console():
    from curie_cli.bench_ui.settings import KEY_DOS_PHOSPHOR

    other = next(
        name for name in dos.PHOSPHOR_NAMES if name != dos.DEFAULT_PHOSPHOR
    )

    async def scenario():
        app = _console(dos.MODE_DOS)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            assert write_setting(KEY_DOS_PHOSPHOR, other) == ""
            app._poll_shared_stores()
            await _settle(pilot)
            assert app.optics.phosphor == other

    _run(scenario())


def test_an_indicator_set_chosen_elsewhere_reaches_this_console():
    from curie_cli.bench_ui.indicators import kit_names

    wanted = next(name for name in kit_names() if name != "interference")

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            assert write_setting(KEY_INDICATORS, wanted) == ""
            app._poll_shared_stores()
            await _settle(pilot)
            assert app._kit_name == wanted
            assert app.query_one("#activity")._kit.name == wanted

    _run(scenario())


def test_an_unrelated_config_key_costs_this_console_nothing():
    """The settings file carries far more than this console's four preferences."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            before = app.bench_palette
            assert write_setting("something.unrelated", "value") == ""
            app._poll_shared_stores()
            await _settle(pilot)
            assert app.bench_palette is before, "an unrelated key caused a re-skin"

    _run(scenario())


def test_this_console_s_own_setting_is_not_announced_back_to_it():
    """Otherwise every switch the reader throws is reported to them as news."""

    async def scenario():
        app = _console(dos.MODE_BENCH)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app._toggle_display_mode()
            await _settle(pilot)
            assert "settings" not in app._sync.changes()

    _run(scenario())


def test_the_settings_are_read_from_disk_on_the_next_start():
    """A preference that only lasts as long as the process is not a preference."""
    assert write_setting(KEY_SKIN_MODE, dos.MODE_DOS) == ""

    from curie_cli.bench_ui.settings import read_settings

    assert read_settings().skin_mode == dos.MODE_DOS
    app = _console(read_settings().skin_mode)
    assert app.bench_palette.dos


# ── The logbook ──────────────────────────────────────────────────────────


def test_a_conversation_written_elsewhere_appears_in_the_logbook():
    """The logbook is only re-read after this console's own turns, so a chat
    started in another window was invisible until a restart."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            table = app.query_one("#logbook-table")
            before = table.row_count

            from curie_state import SessionDB

            db = SessionDB()
            db.create_session("made-in-the-other-window", source="cli")
            db.append_messages_batch(
                "made-in-the-other-window",
                [{"role": "user", "content": "hello from over there"}],
            )

            app._poll_shared_stores()
            await _settle(pilot)
            rows = [
                str(table.get_row_at(index)[-1]) for index in range(table.row_count)
            ]
            assert "made-in-the-other-window" in rows, (before, rows)

    _run(scenario())


# ── Every store at once ──────────────────────────────────────────────────


def test_polling_survives_a_store_that_will_not_answer(monkeypatch):
    """It runs on a timer. A store that raises must cost a readout, not the app."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)

            def explode():
                raise RuntimeError("the store is on fire")

            monkeypatch.setattr(app._sync, "changes", explode)
            app._poll_shared_stores()  # must not raise
            await _settle(pilot)
            assert app.is_running

    _run(scenario())
