"""LOGBOOK → DUPLICATE, and ``/branch``: copy a conversation into a new chat.

Exercised against a real session store in a temporary home, because every
property worth holding here is a property of rows in that store: the copy is
a new conversation (its own id, its own logbook row, a branch marker saying it
owns its transcript), it holds the original's turns, and the original is
exactly as it was before.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import BenchPane, LogbookPane, _read_sessions  # noqa: E402
from curie_cli.bench_ui.sessions import (  # noqa: E402
    COPY_SUFFIX,
    DuplicateError,
    copy_title,
    duplicate_session,
    visible_turns,
)

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 5) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def db():
    from curie_state import SessionDB

    return SessionDB()


def _conversation(db, sid="20260101_090000_aaaaaa", title="Fix the parser"):
    """A conversation with the plumbing a real one has: a tool call and its
    result, an assistant turn that is only a call, and reasoning."""
    db.create_session(sid, "cli", model="test/model")
    if title:
        db.set_session_title(sid, title)
    db.append_message(sid, "user", "the parser drops the last token")
    db.append_message(
        sid, "assistant", "",
        tool_calls=[{"id": "c1", "type": "function",
                     "function": {"name": "read_file", "arguments": "{}"}}],
    )
    db.append_message(sid, "tool", "def parse(): ...", tool_call_id="c1",
                      tool_name="read_file")
    db.append_message(sid, "assistant", "Found it: an off-by-one in the loop.",
                      reasoning="the range stops one short")
    db.append_message(sid, "user", "fix it")
    db.append_message(sid, "assistant", "Done — the loop now runs to len(tokens).")
    return sid


# ── What a copy is called ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "original, opening, expected",
    [
        ("Fix the parser", "", "Fix the parser (copy)"),
        ("Fix the parser (copy)", "", "Fix the parser (copy)"),
        ("Fix the parser (copy) #3", "", "Fix the parser (copy)"),
        ("Fix the parser #2", "", "Fix the parser (copy)"),
        ("", "the parser drops the last token", "the parser drops the last token (copy)"),
        ("", "", "conversation (copy)"),
    ],
)
def test_a_copy_is_named_after_its_original(original, opening, expected):
    assert copy_title(original, opening) == expected


def test_a_long_opening_line_is_shortened_before_it_is_marked():
    title = copy_title("", "word " * 40)
    assert title.endswith(COPY_SUFFIX)
    assert len(title) < 64, "longer than the logbook's subject column"


def test_only_turns_that_say_something_are_kept():
    kept = visible_turns([
        {"role": "system", "content": "you are curie"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
        {"role": "tool", "content": "output"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": "   "},
    ])
    assert [m["role"] for m in kept] == ["user", "assistant"]


# ── The copy, in the store ───────────────────────────────────────────────

def test_the_copy_is_a_new_conversation_and_the_original_is_untouched(db):
    sid = _conversation(db)
    before = [dict(m) for m in db.get_messages(sid)]
    before_row = dict(db.get_session(sid))

    made = duplicate_session(sid, db=db)

    assert made.session_id != sid
    assert made.source_id == sid
    assert made.title == "Fix the parser (copy)"
    assert db.get_session_title(made.session_id) == made.title
    # The original: same rows, same title, not ended, not renamed.
    assert [dict(m) for m in db.get_messages(sid)] == before
    after_row = dict(db.get_session(sid))
    assert after_row.get("title") == before_row.get("title")
    assert after_row.get("end_reason") == before_row.get("end_reason")


def test_the_copy_holds_the_turns_and_not_the_tool_plumbing(db):
    sid = _conversation(db)
    made = duplicate_session(sid, db=db)

    copied = db.get_messages(made.session_id)
    assert [m["role"] for m in copied] == ["user", "assistant", "user", "assistant"]
    assert made.messages == 4
    assert copied[0]["content"] == "the parser drops the last token"
    assert copied[-1]["content"] == "Done — the loop now runs to len(tokens)."
    assert not any(m.get("tool_calls") for m in copied)
    # Reasoning rides along with the turn it belongs to.
    assert any(m.get("reasoning") == "the range stops one short" for m in copied)


def test_the_copy_owns_its_transcript(db):
    """Nothing written to the original afterwards can appear in the copy."""
    sid = _conversation(db)
    made = duplicate_session(sid, db=db)
    db.append_message(sid, "user", "a later message to the ORIGINAL")

    model, display = db.get_resume_conversations(made.session_id)
    texts = [m.get("content") for m in display]
    assert "a later message to the ORIGINAL" not in texts
    assert db.is_explicit_fork_child(made.session_id)


def test_both_rows_are_in_the_logbook_and_can_be_told_apart(db):
    sid = _conversation(db, title="")
    made = duplicate_session(sid, db=db)

    rows = {row[-1]: row for row in _read_sessions()}
    assert sid in rows and made.session_id in rows
    assert rows[sid][3] != rows[made.session_id][3], (
        "the copy and its original read the same in the logbook"
    )
    assert rows[made.session_id][3].endswith(COPY_SUFFIX.strip())


def test_copies_of_copies_are_numbered_not_stacked(db):
    sid = _conversation(db)
    first = duplicate_session(sid, db=db)
    second = duplicate_session(sid, db=db)
    third = duplicate_session(first.session_id, db=db)
    assert first.title == "Fix the parser (copy)"
    assert second.title == "Fix the parser (copy) #2"
    assert third.title == "Fix the parser (copy) #3"
    assert len({first.session_id, second.session_id, third.session_id}) == 3


def test_a_compressed_conversation_is_copied_whole(db):
    """The visible transcript, not the compacted working set: turns compacted
    away before the copy was taken must not be lost from it."""
    db.create_session("root_1", "cli", model="test/model")
    db.append_message("root_1", "user", "the first question, long ago")
    db.append_message("root_1", "assistant", "the first answer")
    db.end_session("root_1", "compression")
    db.create_session("tip_1", "cli", model="test/model", parent_session_id="root_1")
    db.append_message("tip_1", "user", "the latest question")
    db.append_message("tip_1", "assistant", "the latest answer")

    made = duplicate_session("root_1", db=db)

    assert made.source_id == "tip_1", "the stale root was copied, not the live tip"
    texts = [m["content"] for m in db.get_messages(made.session_id)]
    assert "the first question, long ago" in texts
    assert "the latest answer" in texts


def test_a_conversation_with_nothing_to_copy_is_refused_cleanly(db):
    db.create_session("empty_1", "cli")
    before = {row[-1] for row in _read_sessions()}
    with pytest.raises(DuplicateError, match="nothing in it"):
        duplicate_session("empty_1", db=db)
    assert {row[-1] for row in _read_sessions()} == before, "a half-made copy was left behind"


def test_an_unknown_conversation_is_refused(db):
    with pytest.raises(DuplicateError, match="not in the session store"):
        duplicate_session("no_such_session", db=db)


def test_a_copy_that_fails_part_way_is_removed(db, monkeypatch):
    sid = _conversation(db)
    before = {row[-1] for row in _read_sessions()}

    def broken(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "append_messages_batch", broken)
    with pytest.raises(DuplicateError, match="could not be copied"):
        duplicate_session(sid, db=db)
    assert {row[-1] for row in _read_sessions()} == before


# ── The console ──────────────────────────────────────────────────────────

class _StoreBridge(_StubBridge):
    """A bridge that opens conversations from the real store, no provider."""

    def load_session(self, session_id):
        from curie_state import SessionDB

        _model, display = SessionDB().get_resume_conversations(session_id)
        self._agent = SimpleNamespace(session_id=session_id)
        self._session_id = session_id
        return list(display)


def test_duplicate_on_the_logbook_opens_the_copy_on_the_bench(db):
    sid = _conversation(db)

    async def scenario():
        bridge = _StoreBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            logbook = app.query_one("#pane-logbook", LogbookPane)
            assert logbook.highlight(sid)
            await _settle(pilot)
            await pilot.click("#logbook-duplicate")
            await _settle(pilot, 10)

            copy_id = bridge.session_id
            assert copy_id and copy_id != sid, "the bench is not on a new conversation"
            assert db.get_session_title(copy_id) == "Fix the parser (copy)"
            assert app.active_pane == "bench"
            shown = app.query_one("#pane-bench", BenchPane).transcript_text()
            assert "the parser drops the last token" in shown
            assert "Done — the loop now runs to len(tokens)." in shown
            assert "Duplicated as" in str(app.query_one("#notice").render())
            # The original is still there, unchanged, beside it.
            assert len(db.get_messages(sid)) == 6
    _run(scenario())


def test_the_d_key_duplicates_the_highlighted_row(db):
    sid = _conversation(db)

    async def scenario():
        bridge = _StoreBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            app.query_one("#pane-logbook", LogbookPane).highlight(sid)
            app.query_one("#logbook-table").focus()
            await pilot.press("d")
            await _settle(pilot, 10)
            assert bridge.session_id not in (None, sid)
    _run(scenario())


def test_duplicate_is_refused_while_a_turn_is_running(db):
    sid = _conversation(db)

    class _Busy(_StoreBridge):
        @property
        def busy(self):
            return True

    async def scenario():
        app = BenchConsole(bridge=_Busy())
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            before = {row[-1] for row in _read_sessions()}
            app._duplicate(sid)
            await _settle(pilot)
            assert "turn is running" in str(app.query_one("#notice").render())
            assert {row[-1] for row in _read_sessions()} == before
    _run(scenario())


def test_the_readout_names_the_highlighted_conversation(db):
    sid = _conversation(db)

    async def scenario():
        app = BenchConsole(bridge=_StoreBridge())
        async with app.run_test(size=(140, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("logbook")
            await _settle(pilot)
            app.query_one("#pane-logbook", LogbookPane).highlight(sid)
            await _settle(pilot)
            readout = str(app.query_one("#logbook-selected").render())
            assert sid in readout and "Fix the parser" in readout
    _run(scenario())
