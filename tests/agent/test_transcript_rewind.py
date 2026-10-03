"""What a rewind (and a compaction) must take out with the turns it removes.

The tools remember having shown the model a skill or a file, per task id, so
a repeat can be answered with a short "unchanged since it was loaded earlier
in this conversation" stub. That stub is only true while the earlier result
is still in the transcript. These tests hold the shared rewind to forgetting
it, and to rewinding the session store only when the store provably holds
the same conversation.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.context_compressor import (
    HISTORICAL_TASK_HEADING,
    SUMMARY_PREFIX,
    _SUMMARY_END_MARKER,
    user_originated_turn_view,
)
from agent.transcript_rewind import (
    forget_served_content,
    install_rewound_history,
    rewind_user_turn,
    user_turn_count,
)
from curie_state import SessionDB
from tools.file_tools import _read_tracker, read_file_tool
from tools.skills_tool import _skill_view_with_bump, reset_skill_view_dedup

SKILL = "demo-rewind-skill"


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    home = tmp_path / ".curie"
    folder = home / "skills" / SKILL
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: A skill for rewind tests.\n---\n"
        "# Demo\n\nStep one: follow the whole procedure.\n"
    )
    monkeypatch.setenv("CURIE_HOME", str(home))
    reset_skill_view_dedup()
    yield home
    reset_skill_view_dedup()


def _view(task: str) -> dict:
    return json.loads(_skill_view_with_bump({"name": SKILL}, task_id=task))


def _composite_carrier(ask: str = "REAL ASK") -> dict:
    """A compaction summary and a person's ask in one user row."""
    return {
        "role": "user",
        "content": (
            f"{SUMMARY_PREFIX}\n{HISTORICAL_TASK_HEADING}\nold task\n\n"
            f"{_SUMMARY_END_MARKER}\n\n{ask}"
        ),
    }


# ── Forgetting what was served ───────────────────────────────────────────


def test_a_skill_is_served_in_full_again_once_forgotten(skills_home):
    assert "Step one" in _view("task-a").get("content", "")
    assert _view("task-a").get("dedup") is True, "precondition: a repeat is a stub"
    forget_served_content("task-a")
    again = _view("task-a")
    assert "Step one" in again.get("content", "")
    assert again.get("dedup") is None


def test_forgetting_one_task_leaves_another_alone(skills_home):
    _view("task-a")
    _view("task-b")
    forget_served_content("task-a")
    assert _view("task-b").get("dedup") is True
    assert _view("task-a").get("dedup") is None


def test_a_file_is_read_in_full_again_once_forgotten():
    folder = tempfile.mkdtemp(prefix="curie-rewind-", dir=os.getcwd())
    path = os.path.join(folder, "notes.txt")
    with open(path, "w") as handle:
        handle.write("line one\nline two\n")
    text = "line one\nline two\n"
    fake_ops = MagicMock()
    fake_ops.read_file = lambda p, offset=1, limit=500: SimpleNamespace(
        content=text,
        to_dict=lambda: {"content": text, "total_lines": 2, "file_size": len(text)},
    )
    _read_tracker.clear()
    try:
        with patch("tools.file_tools._get_file_ops", return_value=fake_ops):
            json.loads(read_file_tool(path, task_id="task-f"))
            assert json.loads(read_file_tool(path, task_id="task-f")).get("dedup") is True
            forget_served_content("task-f")
            again = json.loads(read_file_tool(path, task_id="task-f"))
        assert again.get("dedup") is None
        assert "line one" in json.dumps(again)
    finally:
        _read_tracker.clear()
        os.unlink(path)
        os.rmdir(folder)


def test_installing_a_rewound_history_forgets_and_moves_the_cursor(skills_home):
    _view("task-i")
    agent = SimpleNamespace(
        _session_messages=["old"] * 6,
        _last_flushed_db_idx=6,
        _db_flush_scan_prefix=["old"] * 6,
    )
    kept = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "a"}]
    install_rewound_history(agent, kept, persisted=True, task_id="task-i")
    assert agent._session_messages == kept
    assert agent._last_flushed_db_idx == 2
    assert agent._db_flush_scan_prefix == kept
    assert _view("task-i").get("dedup") is None

    install_rewound_history(agent, kept, persisted=False, task_id="task-i")
    assert agent._last_flushed_db_idx == 0, "an unstored rewind must re-scan on the next flush"
    assert agent._db_flush_scan_prefix is None


# ── The rewind itself, in memory ─────────────────────────────────────────


HISTORY = [
    {"role": "user", "content": "one"},
    {"role": "assistant", "content": "first"},
    {"role": "user", "content": "two"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
    {"role": "tool", "tool_call_id": "c1", "content": "result"},
    {"role": "assistant", "content": "second"},
]


def test_the_last_turn_and_everything_after_it_is_taken_back():
    turns = user_turn_count(HISTORY)
    rewound = rewind_user_turn(HISTORY, turns - 1)
    assert [m["content"] for m in rewound.history] == ["one", "first"]
    assert rewound.live_view["content"] == "two"
    assert rewound.rewound_count == 4
    assert rewound.persisted is False
    assert len(HISTORY) == 6, "the caller's history was modified"


def test_an_out_of_range_turn_is_refused_before_the_store_is_opened():
    @contextmanager
    def never():
        raise AssertionError("the store was opened for an impossible rewind")
        yield  # pragma: no cover

    with pytest.raises(ValueError):
        rewind_user_turn(HISTORY, 5, session_id="s", db_scope=never)


def test_a_compaction_summary_sharing_the_row_is_kept():
    history = [_composite_carrier(), {"role": "assistant", "content": "answer"}]
    rewound = rewind_user_turn(history, user_turn_count(history) - 1)
    assert rewound.live_view["content"].strip() == "REAL ASK"
    assert len(rewound.history) == 1
    head = rewound.history[0]
    assert "old task" in head["content"], "the only copy of the summarised turns was lost"
    assert user_originated_turn_view(head) is None, "the kept summary still carries the ask"


# ── The rewind, against the store ────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    yield db
    db.close()


def _stored(db: SessionDB, session_id: str, history: list) -> list:
    db.create_session(session_id, source="cli")
    for message in history:
        db.append_message(session_id, message["role"], message.get("content"))
    return db.get_messages_as_conversation(session_id)


def _scope(db):
    @contextmanager
    def scope():
        yield db

    return scope


def test_the_store_is_rewound_with_the_history(store):
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "second"},
    ]
    durable = _stored(store, "sess-store", history)
    rewound = rewind_user_turn(
        durable, user_turn_count(durable) - 1, session_id="sess-store", db_scope=_scope(store)
    )
    assert rewound.persisted is True
    active = store.get_messages_as_conversation("sess-store")
    assert [m["content"] for m in active] == ["one", "first"]


def test_a_store_that_does_not_match_is_left_alone(store):
    _stored(store, "sess-diff", [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "something else entirely"},
    ])
    warm = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "two"},
    ]
    before = store.get_active_message_ids("sess-diff")
    with pytest.raises(RuntimeError):
        rewind_user_turn(warm, 1, session_id="sess-diff", db_scope=_scope(store))
    assert store.get_active_message_ids("sess-diff") == before


def test_no_store_at_all_is_an_error_not_a_silent_skip():
    @contextmanager
    def nothing():
        yield None

    with pytest.raises(RuntimeError):
        rewind_user_turn(HISTORY, 1, session_id="s", db_scope=nothing)
