"""Tests for /undo handling in tui_gateway.

The TUI routes ``/undo`` through ``command.dispatch`` (it's in
``_PENDING_INPUT_COMMANDS`` because the CLI handler queues input the
slash-worker subprocess can't read). The server handles it directly,
mutates SessionDB to soft-delete rows, refreshes the in-memory session
history, fires the memory-provider hook with ``rewound=True``, and
returns ``{"type": "prefill", "message": <text>, "notice": ...}`` so
the Ink client drops the message into the composer for editing.

``/undo N`` backs up N user turns at once (default 1). See issue #21910.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from curie_state import SessionDB


@pytest.fixture()
def curie_home(tmp_path, monkeypatch):
    home = tmp_path / ".curie"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CURIE_HOME", str(home))
    yield home


@pytest.fixture()
def server(curie_home):
    # Mocks are scoped to the initial import only (see
    # tests/tui_gateway/test_protocol.py for the rationale).
    with patch.dict(
        "sys.modules",
        {
            "curie_cli.env_loader": MagicMock(),
            "curie_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")

    methods = dict(mod._methods)
    yield mod
    # Restore in place instead of clear+reload: importlib.reload
    # re-registers atexit hooks (duplicate ThreadPoolExecutor shutdowns
    # race the stderr buffer at interpreter exit — same class as PR #34217)
    # and re-captures module-level paths like _curie_home against this
    # test's soon-deleted tmpdir, breaking later files in the same process.
    mod._methods.clear()
    mod._methods.update(methods)
    mod._sessions.clear()
    mod._pending.clear()
    mod._answers.clear()
    mod._db = None


@pytest.fixture()
def db(curie_home):
    return SessionDB(db_path=curie_home / "state.db")


@pytest.fixture()
def session_with_history(server, db):
    """Build a session with 3 user turns + assistant replies persisted in DB."""
    sid = "sid-undo"
    session_key = "tui-undo-1"
    db.create_session(session_key, source="tui")
    for i in range(1, 4):
        db.append_message(session_key, "user", f"question {i}")
        db.append_message(session_key, "assistant", f"answer {i}")
    history = db.get_messages_as_conversation(session_key)
    agent = MagicMock()
    agent._memory_manager = MagicMock()
    agent._last_flushed_db_idx = len(history)
    s = {
        "session_key": session_key,
        "history": list(history),
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "agent": agent,
        "attached_images": [],
        "cols": 120,
    }
    server._sessions[sid] = s
    # Wire the DB cache so _get_db() returns our fixture.
    server._db = db
    return sid, session_key, s, agent


def _call(server, method, **params):
    return server._methods[method](1, params)


def test_undo_returns_prefill_with_target_text(server, session_with_history):
    sid, session_key, s, agent = session_with_history
    resp = _call(server, "command.dispatch", session_id=sid, name="undo", arg="")
    result = resp["result"]
    assert result["type"] == "prefill"
    # Default /undo backs up one user turn — "question 3"
    assert result["message"] == "question 3"
    assert "Undid" in result["notice"]
    assert s["history"]
    assert all("_row_id" in message for message in s["history"])




def test_undo_serves_a_skill_from_the_undone_turn_in_full_again(
    server, session_with_history, curie_home
):
    """The TUI runs turns under the session key; /undo must forget what the
    tools served under it, or a skill loaded in the undone turn comes back
    as a stub pointing at a result the model no longer has."""
    import json

    from tools.skills_tool import _skill_view_with_bump, reset_skill_view_dedup

    folder = curie_home / "skills" / "rewind-skill"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: rewind-skill\ndescription: Rewind test skill.\n---\n# Demo\n\nStep one.\n"
    )
    reset_skill_view_dedup()
    sid, session_key, _s, _agent = session_with_history

    def view():
        return json.loads(_skill_view_with_bump({"name": "rewind-skill"}, task_id=session_key))

    view()
    assert view().get("dedup") is True, "precondition: a repeat is a stub"
    resp = _call(server, "command.dispatch", session_id=sid, name="undo", arg="")
    assert resp["result"]["type"] == "prefill"
    again = view()
    assert again.get("dedup") is None
    assert "Step one" in again.get("content", "")
    reset_skill_view_dedup()
