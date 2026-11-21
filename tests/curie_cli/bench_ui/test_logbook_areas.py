"""The logbook keeps chats, Telegram and scheduled runs in separate areas.

Reported: every conversation in the store sat in one table, so the ones held
on the bench were buried under scheduled runs and Telegram chats. The pane now
opens on the chats held on this machine, and a menu on the pane switches to
Telegram, Scheduled, and any other platform or automated source the store
holds — each with its count.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Select, Static  # noqa: E402

import curie_cli.bench_ui.panes as panes  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.logbook import (  # noqa: E402
    AREA_CHATS,
    AREA_SCHEDULED,
    AREA_TELEGRAM,
    area_of,
    find_area,
    logbook_areas,
    source_label,
)
from curie_cli.bench_ui.panes import LogbookPane  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge, _flatten  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


# ── Filing ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source,area",
    [
        ("cli", AREA_CHATS),  # the bench itself runs as the cli surface
        ("tui", AREA_CHATS),
        ("desktop", AREA_CHATS),
        ("", AREA_CHATS),  # a row from before sources were recorded
        ("CLI", AREA_CHATS),
        ("telegram", AREA_TELEGRAM),
        ("cron", AREA_SCHEDULED),
        ("discord", "discord"),
        ("kanban", "kanban"),
        ("some_plugin_platform", "some_plugin_platform"),
    ],
)
def test_every_source_is_filed_under_one_area(source, area):
    assert area_of(source) == area


def test_chats_telegram_and_scheduled_are_always_offered_first():
    """Even empty: an area that vanishes when empty reads as a lost feature."""
    areas = logbook_areas({})
    assert [a.key for a in areas] == [AREA_CHATS, AREA_TELEGRAM, AREA_SCHEDULED]
    assert all(a.count == 0 for a in areas)


def test_every_other_source_gets_an_area_of_its_own_largest_first():
    areas = logbook_areas({
        "cli": 10, "tui": 2, "": 1, "telegram": 4, "cron": 40,
        "discord": 3, "slack": 7, "kanban": 3,
    })
    keys = [a.key for a in areas]
    assert keys[:3] == [AREA_CHATS, AREA_TELEGRAM, AREA_SCHEDULED]
    # Largest first, ties by name — the menu does not reshuffle between reads.
    assert keys[3:] == ["slack", "discord", "kanban"]
    chats = find_area(areas, AREA_CHATS)
    assert chats.count == 13
    assert find_area(areas, AREA_SCHEDULED).count == 40
    # Nothing is listed twice and nothing is dropped.
    assert sum(a.count for a in areas) == 10 + 2 + 1 + 4 + 40 + 3 + 7 + 3


def test_an_area_names_itself_and_its_count():
    area = find_area(logbook_areas({"telegram": 1234}), AREA_TELEGRAM)
    assert area.menu_label == "Telegram · 1,234"
    assert source_label("bluebubbles") == "iMessage"
    assert source_label("my_platform") == "My Platform"


def test_a_count_that_is_not_a_number_is_ignored():
    areas = logbook_areas({"cli": "lots", "telegram": None, "cron": 2})
    assert find_area(areas, AREA_CHATS).count == 0
    assert find_area(areas, AREA_SCHEDULED).count == 2


# ── Reading one area ─────────────────────────────────────────────────────


class _RecordingDb:
    def __init__(self):
        self.calls: list[dict] = []

    def list_sessions_rich(self, **kwargs):
        self.calls.append(kwargs)
        return []


def test_each_area_is_read_with_the_stores_own_filter(monkeypatch):
    """So a busy scheduler cannot fill the chats' window by itself."""
    db = _RecordingDb()
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    panes._read_sessions(sources=("cron",))
    assert db.calls[-1]["sources"] == ["cron"]
    panes._read_sessions()
    assert "sources" not in db.calls[-1]


# ── The pane, against a real store ───────────────────────────────────────


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A session store with chats, Telegram, scheduled runs and Discord."""
    from curie_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")

    def add(sid, source, text):
        db.create_session(sid, source=source, model="test/model")
        db.append_message(sid, "user", content=text)
        db.append_message(sid, "assistant", content="ok")

    for index in range(3):
        add(f"chat{index}", "cli", f"bench chat {index}")
    add("tui1", "tui", "a tui chat")
    for index in range(2):
        add(f"tg{index}", "telegram", f"telegram chat {index}")
    for index in range(5):
        add(f"cron{index}", "cron", f"scheduled run {index}")
    add("dc1", "discord", "a discord chat")
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    yield db
    db.close()


def _ids(app) -> list[str]:
    table = app.query_one("#logbook-table", DataTable)
    return [str(table.get_row_at(i)[-1]) for i in range(table.row_count)]


@pytest.mark.parametrize("mode", ["bench", "dos"])
def test_the_logbook_opens_on_the_chats_and_the_menu_switches_area(store, mode):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            if (mode == "dos") != app.bench_palette.dos:
                app.run_keyline_action("dos-mode")
                await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            pane = app.query_one("#pane-logbook", LogbookPane)
            menu = app.query_one("#logbook-area", Select)

            # Chats only: no scheduled run, no Telegram chat among them.
            assert pane.area == AREA_CHATS
            assert sorted(_ids(app)) == ["chat0", "chat1", "chat2", "tui1"]
            assert [a.menu_label for a in pane.areas] == [
                "Chats · 4", "Telegram · 2", "Scheduled · 5", "Discord · 1",
            ]

            # The menu is a real pull-down: open it, move down, choose.
            await pilot.click("#logbook-area")
            await _settle(pilot)
            assert menu.expanded
            await pilot.press("down", "enter")
            await _settle(pilot)
            assert pane.area == AREA_TELEGRAM
            assert sorted(_ids(app)) == ["tg0", "tg1"]

            pane.show_area(AREA_SCHEDULED)
            await _settle(pilot)
            assert sorted(_ids(app)) == [f"cron{i}" for i in range(5)]
            assert menu.value == AREA_SCHEDULED

            pane.show_area("discord")
            await _settle(pilot)
            assert _ids(app) == ["dc1"]
    _run(scenario())


def test_the_chosen_area_survives_a_reload_and_its_count_moves(store):
    """A scheduled run landing must not throw the reader out of their area."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            pane = app.query_one("#pane-logbook", LogbookPane)
            pane.show_area(AREA_TELEGRAM)
            await _settle(pilot)

            store.create_session("tg9", source="telegram", model="test/model")
            store.append_message("tg9", "user", content="a new telegram chat")
            app._reload_logbook()
            await _settle(pilot)

            assert pane.area == AREA_TELEGRAM
            assert "tg9" in _ids(app)
            assert find_area(pane.areas, AREA_TELEGRAM).count == 3
            # The shut menu shows the new count, not the one it was built with.
            label = app.query_one("#logbook-area").query_one("#label", Static)
            assert "Telegram · 3" in _flatten(label.render())
    _run(scenario())


def test_an_empty_area_says_so(store):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            pane = app.query_one("#pane-logbook", LogbookPane)
            # Archive every Telegram chat: the area stays on the menu, empty.
            for sid in ("tg0", "tg1"):
                assert store.set_session_archived(sid, True)
            app._reload_logbook()
            pane.show_area(AREA_TELEGRAM)
            await _settle(pilot)
            table = app.query_one("#logbook-table", DataTable)
            assert find_area(pane.areas, AREA_TELEGRAM).count == 0
            assert table.row_count == 1
            assert "nothing in Telegram yet" in str(table.get_row_at(0)[3])
            assert pane.selected_session_id() is None
    _run(scenario())


def test_the_letter_d_still_duplicates_with_the_menu_on_the_pane(store, monkeypatch):
    """The menu does not read letters as a search, so "d" stays DUPLICATE."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        thrown: list[str] = []
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            monkeypatch.setattr(app, "run_keyline_action", lambda action: thrown.append(action))
            app.query_one("#logbook-area").focus()
            await pilot.press("d")
            await _settle(pilot)
            assert thrown == ["logbook-duplicate"]
    _run(scenario())
