"""The logbook is the console's record of work, so it has to be complete.

Two things were wrong and both made it useless. It read through a helper that
does not exist (``session_listing.list_sessions_for_display``), so the import
failed, the reader swallowed it, and the pane said "no saved sessions yet"
however much work had been done. And the bench built its agent with no
``session_db`` at all, so a conversation held there was never written anywhere
— it could not have appeared in any listing because it did not exist after the
console closed.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable  # noqa: E402

import curie_cli.bench_ui.agent_bridge as bridge_mod  # noqa: E402
import curie_cli.bench_ui.panes as panes  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import LOGBOOK_COLUMNS, LogbookPane  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_text,
)


def _run(coro):
    return asyncio.run(coro)


def _row(sid, *, title="", preview="", turns=4, model="test/model",
         last_active="2026-09-01T10:30:00"):
    return {
        "id": sid,
        "title": title,
        "preview": preview,
        "message_count": turns,
        "model": model,
        "last_active": last_active,
        "started_at": last_active,
        "source": "cli",
    }


class _FakeDb:
    """Just enough SessionDB for the logbook and the load path."""

    def __init__(self, rows=(), histories=None):
        self.rows = list(rows)
        self.histories = histories or {}
        self.calls: list[dict] = []

    def list_sessions_rich(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.rows)[: kwargs.get("limit", 20)]

    def resolve_resume_session_id(self, sid):
        return sid

    def get_resume_conversations(self, sid):
        return self.histories.get(sid, ([], []))


# ── Reading the store ────────────────────────────────────────────────────

def test_untitled_conversations_are_listed_too(monkeypatch):
    """A chat someone just opened and typed into never gets a title.

    Filtering on one — which the CLI's own picker does, to keep its list
    short — hid the majority of real conversations from a pane whose whole
    job is to be the complete record.
    """
    db = _FakeDb([
        _row("s-named", title="Assay writeup"),
        _row("s-bare", preview="what does this traceback mean"),
    ])
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    rows = panes._read_sessions()
    ids = [r[-1] for r in rows]
    assert ids == ["s-named", "s-bare"], (
        f"an untitled conversation was dropped: {ids}"
    )


def test_an_untitled_conversation_is_listed_under_its_opening_line(monkeypatch):
    db = _FakeDb([_row("s-bare", preview="what does this traceback mean")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    (row,) = panes._read_sessions()
    assert "what does this traceback mean" in row[3]


def test_a_conversation_with_neither_title_nor_preview_still_appears(monkeypatch):
    db = _FakeDb([_row("s-blank")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    (row,) = panes._read_sessions()
    assert row[-1] == "s-blank"
    assert row[3] == "untitled"


def test_rows_are_read_most_recently_active_first(monkeypatch):
    db = _FakeDb([_row("s1"), _row("s2")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    panes._read_sessions()
    assert db.calls[0]["order_by_last_active"] is True, (
        "the record of work should open at the most recent entry"
    )


def test_a_row_with_no_id_is_skipped(monkeypatch):
    """An id-less row cannot be selected, so listing it offers a dead handle."""
    db = _FakeDb([_row(""), _row("s-real")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    assert [r[-1] for r in panes._read_sessions()] == ["s-real"]


def test_a_repeated_id_is_listed_once(monkeypatch):
    """The id is the table's row key, and a duplicate key raises.

    Compression chains are projected forward to their live tip, which is the
    shape that can surface the same id twice — and one repeat would have taken
    the whole pane down rather than being quietly ignored.
    """
    db = _FakeDb([_row("s-dup", title="first"), _row("s-dup", title="again")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    assert [r[-1] for r in panes._read_sessions()] == ["s-dup"]


def test_a_repeated_id_does_not_take_the_pane_down(monkeypatch):
    db = _FakeDb([_row("s-dup", title="first"), _row("s-dup", title="again")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("logbook")
            await pilot.pause()
            assert app.query_one("#logbook-table", DataTable).row_count == 1
    _run(scenario())


def test_an_unopenable_store_reads_as_empty_not_a_crash(monkeypatch):
    monkeypatch.setattr(panes, "_session_db", lambda: None)
    assert panes._read_sessions() == []


def test_a_store_that_raises_reads_as_empty(monkeypatch):
    class _Broken:
        def list_sessions_rich(self, **_kwargs):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(panes, "_session_db", lambda: _Broken())
    assert panes._read_sessions() == []


def test_the_reader_calls_a_method_the_store_actually_has():
    """The original read went through a helper that does not exist.

    It failed on import every time, the reader swallowed it, and the pane
    reported an empty logbook forever. Pin the call against the real class so
    a rename upstream fails here rather than silently emptying the pane.
    """
    from curie_state import SessionDB

    assert hasattr(SessionDB, "list_sessions_rich")
    assert hasattr(SessionDB, "get_resume_conversations")
    assert hasattr(SessionDB, "resolve_resume_session_id")


# ── Timestamps ───────────────────────────────────────────────────────────

def test_an_epoch_timestamp_is_rendered_as_a_date():
    """Rows carry epoch seconds, not an ISO string, in the common case.

    Printed raw it reached the column as ``1788407323.60770``, which tells the
    reader nothing about when anything happened.
    """
    rendered = panes._when(1788407323.6077)
    assert rendered[:2] == "20" and len(rendered) == 16, rendered
    assert "." not in rendered, f"an epoch float leaked into the column: {rendered}"


def test_an_iso_timestamp_is_trimmed_to_the_minute():
    assert panes._when("2026-09-01T10:30:00") == "2026-09-01 10:30"


def test_a_missing_timestamp_reads_as_a_dash():
    assert panes._when(None) == "—"
    assert panes._when("") == "—"


def test_an_unparseable_timestamp_is_shown_rather_than_swallowed():
    """Better a value the reader can puzzle over than a silent blank."""
    assert panes._when("not-a-date") == "not-a-date"


def test_an_out_of_range_epoch_does_not_crash_the_pane():
    """A number no calendar can hold raises OSError, not ValueError."""
    for absurd in (10**20, -(10**20), float("inf"), float("nan")):
        assert isinstance(panes._when(absurd), str)


def test_a_real_session_store_lists_readable_rows(tmp_path, monkeypatch):
    """End to end against the actual database, not a stand-in.

    The fakes in this file return ISO strings because that is what the column
    is documented to hold; the real store returns epoch floats. Only a real
    round-trip catches the difference.
    """
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    import curie_constants
    import importlib

    importlib.reload(curie_constants)
    from curie_state import SessionDB

    db = SessionDB()
    db.create_session(session_id="sess-real", source="cli", model="test/model")
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    rows = panes._read_sessions()
    assert [r[-1] for r in rows] == ["sess-real"]
    when = rows[0][0]
    assert when != "—" and "." not in when, f"unreadable timestamp: {when!r}"
    assert len(when) == 16, f"expected a trimmed date-time, got {when!r}"


# ── The pane ─────────────────────────────────────────────────────────────

def test_the_pane_lists_every_conversation(monkeypatch):
    db = _FakeDb([_row(f"s{i}", title=f"run {i}") for i in range(6)])
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("logbook")
            await pilot.pause()
            table = app.query_one("#logbook-table", DataTable)
            assert table.row_count == 6
            assert [c.label.plain for c in table.columns.values()] == list(
                LOGBOOK_COLUMNS
            )
    _run(scenario())


def test_an_empty_store_says_so_rather_than_looking_broken(monkeypatch):
    monkeypatch.setattr(panes, "_session_db", lambda: _FakeDb([]))

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("logbook")
            await pilot.pause()
            table = app.query_one("#logbook-table", DataTable)
            assert table.row_count == 1
            assert "no conversations recorded" in str(table.get_row_at(0)[3])
    _run(scenario())


def test_reloading_keeps_the_cursor_on_the_same_conversation(monkeypatch):
    db = _FakeDb([_row(f"s{i}", title=f"run {i}") for i in range(5)])
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("logbook")
            await pilot.pause()
            pane = app.query_one("#pane-logbook", LogbookPane)
            table = app.query_one("#logbook-table", DataTable)
            table.move_cursor(row=3)
            chosen = pane.selected_session_id()

            # A new conversation arrives at the top, shifting every index.
            db.rows.insert(0, _row("s-new", title="newest"))
            pane.reload()
            await pilot.pause()
            assert pane.selected_session_id() == chosen, (
                "the cursor followed the index instead of the conversation"
            )
    _run(scenario())


def test_the_conversation_on_the_bench_is_flagged_in_the_list(monkeypatch):
    db = _FakeDb([_row("s1", title="one"), _row("s2", title="two")])
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("logbook")
            await pilot.pause()
            pane = app.query_one("#pane-logbook", LogbookPane)
            pane.mark_active("s2")
            await pilot.pause()
            table = app.query_one("#logbook-table", DataTable)
            subjects = [str(table.get_row_at(i)[3]) for i in range(2)]
            assert subjects[1].startswith("▶ ")
            assert not subjects[0].startswith("▶ ")

            # Marking a different one must not leave two flagged.
            pane.mark_active("s1")
            await pilot.pause()
            subjects = [str(table.get_row_at(i)[3]) for i in range(2)]
            assert subjects[0].startswith("▶ ")
            assert not subjects[1].startswith("▶ ")
    _run(scenario())


# ── Loading a conversation onto the bench ────────────────────────────────

def test_selecting_a_row_loads_the_conversation_into_the_bench(monkeypatch):
    history = [
        {"role": "user", "content": "did the assay finish"},
        {"role": "assistant", "content": "yes — the residue came back clean"},
    ]
    db = _FakeDb(
        [_row("s-load", title="assay")],
        histories={"s-load": (history, history)},
    )
    monkeypatch.setattr(panes, "_session_db", lambda: db)
    monkeypatch.setattr(bridge_mod, "SessionDB", None, raising=False)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._session_loaded("s-load", history)
            for _ in range(2):
                await pilot.pause()

            rendered = _transcript_text(app)
            assert "did the assay finish" in rendered
            assert "the residue came back clean" in rendered
            assert app.active_pane == "bench"
    _run(scenario())


def test_a_conversation_that_will_not_open_says_so(monkeypatch):
    monkeypatch.setattr(panes, "_session_db", lambda: _FakeDb([]))

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._session_loaded("s-missing", None)
            await pilot.pause()
            notice = app.query_one("#notice")
            assert not notice.has_class("hidden")
            assert "s-missing" in str(notice.render())
    _run(scenario())


def test_a_conversation_with_no_stored_messages_is_not_a_blank_screen(monkeypatch):
    monkeypatch.setattr(panes, "_session_db", lambda: _FakeDb([]))

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._session_loaded("s-empty", [])
            for _ in range(2):
                await pilot.pause()
            assert "no stored messages" in _transcript_text(app)
    _run(scenario())


def test_replay_skips_plumbing_that_is_not_conversation(monkeypatch):
    """Pivot markers and system rows are not things anyone said."""
    monkeypatch.setattr(panes, "_session_db", lambda: _FakeDb([]))
    history = [
        {"role": "system", "content": "you are a helpful assistant"},
        {
            "role": "user",
            "content": "[System: model switched]",
            "display_kind": "model_switch",
        },
        {"role": "user", "content": "the real question"},
        {"role": "assistant", "content": "the real answer"},
        {"role": "tool", "content": '{"ok": true}'},
    ]

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._session_loaded("s", history)
            for _ in range(2):
                await pilot.pause()
            rendered = _transcript_text(app)
            assert "the real question" in rendered
            assert "the real answer" in rendered
            assert "helpful assistant" not in rendered
            assert "model switched" not in rendered
            assert '{"ok": true}' not in rendered
    _run(scenario())


def test_loading_is_refused_while_a_turn_is_running():
    """Swapping history under a live turn loses whichever lands second."""
    bridge = _StubBridge()
    bridge.ensure_agent()

    class _Busy(type(bridge)):
        @property
        def busy(self):
            return True

    bridge.__class__ = _Busy
    assert bridge.load_session("anything") is None


def test_loading_the_conversation_already_on_the_bench_is_a_no_op(monkeypatch):
    """It would otherwise rebuild the agent and redraw for no change."""
    history = [{"role": "user", "content": "hello"}]
    db = _FakeDb(
        [_row("s-open", title="open")],
        histories={"s-open": (history, history)},
    )
    monkeypatch.setattr(panes, "_session_db", lambda: db)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.show_pane("logbook")
            await pilot.pause()

            loaded: list[str] = []
            app._load_session = lambda sid: loaded.append(sid)
            app.bridge._session_id = "s-open"

            table = app.query_one("#logbook-table", DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            for _ in range(2):
                await pilot.pause()
            assert loaded == [], "reloaded a conversation that was already open"
    _run(scenario())


# ── Persistence ──────────────────────────────────────────────────────────


def _build_with_stub_provider(monkeypatch, **kwargs) -> dict:
    """Build the bench's agent against a stubbed provider, and report the
    keywords it was constructed with.

    The resolver is stubbed rather than skipped-around: these tests are about
    the keywords the bridge passes, and a test that skips wherever no
    inference provider happens to be configured would not run in CI — which
    is precisely where the wiring needs guarding.
    """
    captured: dict = {}

    class _Recorder:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("run_agent.AIAgent", _Recorder)
    monkeypatch.setattr(
        "curie_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kw: {
            "provider": "openai",
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "api_mode": "chat",
            "model": "test/model",
        },
    )
    bridge_mod._build_agent(**kwargs)
    return captured


def test_the_bench_builds_its_agent_with_a_session_store(monkeypatch):
    """Without this the bench's conversations were never written anywhere.

    ``AIAgent._flush_messages_to_session_db`` has nothing to flush to when
    ``session_db`` is None, so a turn held on the bench vanished with the
    console — which is also why it could never appear in the logbook.
    """
    captured = _build_with_stub_provider(monkeypatch)
    assert captured.get("session_db") is not None, (
        "the bench builds an agent that cannot persist a conversation"
    )


def test_a_requested_session_id_is_passed_to_the_agent(monkeypatch):
    captured = _build_with_stub_provider(monkeypatch, session_id="s-resume")
    assert captured.get("session_id") == "s-resume"


def test_a_store_that_will_not_open_still_leaves_a_usable_console(monkeypatch):
    """A console that cannot persist should run turns, not refuse to start."""
    monkeypatch.setattr(bridge_mod, "_open_session_db", lambda: None)
    captured = _build_with_stub_provider(monkeypatch)
    assert "session_db" not in captured
    assert captured.get("platform") == "cli"
