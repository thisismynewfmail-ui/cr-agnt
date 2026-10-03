"""BACK (Ctrl+B) and AGAIN (Ctrl+G) take the turn out of everything.

The reported bug: after Ctrl+B, a skill the taken-back turn had loaded could
never be loaded again in that conversation. ``skill_view`` remembers, per task
id, which skills it has served, and answers a repeat with "Skill content
unchanged since it was loaded earlier in this conversation — refer to the
earlier skill_view result". Ctrl+B removed that earlier result from the
history the model is sent, and nothing told the tool — so the model was
pointed at a skill load in a conversation that, for all it could see, never
happened, and never got the skill.

The agent here is a stand-in whose turn loads a skill through the *real*
``skill_view`` handler, under the task id the console hands it — the same
cache the real agent's tool calls fill.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from agent.context_compressor import (  # noqa: E402
    HISTORICAL_TASK_HEADING,
    SUMMARY_PREFIX,
    _SUMMARY_END_MARKER,
)
from curie_cli.bench_ui.agent_bridge import AgentBridge  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import Composer  # noqa: E402
from curie_state import SessionDB  # noqa: E402
from tools.skills_tool import _skill_view_with_bump, reset_skill_view_dedup  # noqa: E402

SKILL = "demo-bench-skill"
SESSION = "bench-rewind-session"


@pytest.fixture(autouse=True)
def skills_home(tmp_path, monkeypatch):
    folder = tmp_path / "skills" / SKILL
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {SKILL}\ndescription: A skill for rewind tests.\n---\n"
        "# Demo\n\nStep one: follow the whole procedure.\n"
    )
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    reset_skill_view_dedup()
    yield tmp_path
    reset_skill_view_dedup()


class _Memory:
    def __init__(self):
        self.switches: list = []

    def on_session_switch(self, session_id, **kwargs):
        self.switches.append((session_id, kwargs))


class _SkillAgent:
    """Stands in for AIAgent: every turn loads the demo skill, really."""

    model = "test/model"
    provider = "test"
    base_url = ""

    def __init__(self, session_id: str = SESSION, rotate_to: str = ""):
        self.session_id = session_id
        self.conversation_history: list = []
        self._interrupt_requested = False
        self._memory_manager = _Memory()
        self._session_messages: list = []
        self.views: list[dict] = []
        self._rotate_to = rotate_to

    def run_conversation(self, message, conversation_history=None,
                         stream_callback=None, task_id=None, **_kw):
        view = json.loads(_skill_view_with_bump({"name": SKILL}, task_id=task_id))
        self.views.append(view)
        if self._rotate_to:
            # A compaction that rotates the session mid-turn: the turn's task
            # id is the old id, the conversation continues under the new one.
            self.session_id, self._rotate_to = self._rotate_to, ""
        call = f"call-{len(self.views)}"
        reply = "Loaded the skill."
        if stream_callback is not None:
            stream_callback(reply)
        messages = list(conversation_history or []) + [
            {"role": "user", "content": message},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": call,
                    "type": "function",
                    "function": {"name": "skill_view", "arguments": json.dumps({"name": SKILL})},
                }],
            },
            {"role": "tool", "tool_call_id": call, "content": json.dumps(view)},
            {"role": "assistant", "content": reply},
        ]
        return {"messages": messages, "final_response": reply}


def _bridge(agent) -> AgentBridge:
    bridge = AgentBridge()
    bridge._agent = agent
    return bridge


def _turn(bridge: AgentBridge, message: str) -> None:
    assert bridge.submit(message)
    bridge._worker.join(10)
    assert not bridge.busy


def _loaded_in_full(view: dict) -> bool:
    return "Step one" in view.get("content", "") and not view.get("dedup")


# ── The reported bug ─────────────────────────────────────────────────────


def test_without_going_back_a_repeat_load_is_a_stub():
    """The cache is real — which is why going back has to clear it."""
    agent = _SkillAgent()
    bridge = _bridge(agent)
    _turn(bridge, "use the demo skill")
    _turn(bridge, "use it again")
    assert _loaded_in_full(agent.views[0])
    assert agent.views[1].get("dedup") is True


def test_a_skill_loaded_in_a_taken_back_turn_loads_in_full_again():
    agent = _SkillAgent()
    bridge = _bridge(agent)
    _turn(bridge, "use the demo skill")
    assert bridge.rewind() == "use the demo skill"
    _turn(bridge, "use the demo skill")
    assert _loaded_in_full(agent.views[-1]), (
        "the skill came back as 'loaded earlier in this conversation' for a "
        "load that was taken back"
    )


def test_ctrl_b_in_the_console_takes_the_skill_load_back_too():
    agent = _SkillAgent()
    bridge = _bridge(agent)

    async def scenario():
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            for _ in range(4):
                await pilot.pause()
            app._send("use the demo skill")
            for _ in range(80):
                await pilot.pause(0.05)
                if not bridge.busy and bridge._history:
                    break
            await pilot.pause()
            await pilot.press("ctrl+b")
            for _ in range(4):
                await pilot.pause()
            assert app.query_one("#composer", Composer).text == "use the demo skill"
            assert bridge._history == []
            app._send("use the demo skill")
            for _ in range(80):
                await pilot.pause(0.05)
                if not bridge.busy and len(agent.views) == 2:
                    break

    asyncio.run(scenario())
    assert len(agent.views) == 2
    assert _loaded_in_full(agent.views[1])


def test_ctrl_g_asks_again_with_the_skill_served_in_full():
    agent = _SkillAgent()
    bridge = _bridge(agent)

    async def scenario():
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            for _ in range(4):
                await pilot.pause()
            app._send("use the demo skill")
            for _ in range(80):
                await pilot.pause(0.05)
                if not bridge.busy and bridge._history:
                    break
            await pilot.pause()
            await pilot.press("ctrl+g")
            for _ in range(80):
                await pilot.pause(0.05)
                if not bridge.busy and len(agent.views) == 2:
                    break

    asyncio.run(scenario())
    assert len(agent.views) == 2
    assert _loaded_in_full(agent.views[1])


def test_a_session_rotated_by_compaction_is_forgotten_under_both_ids():
    agent = _SkillAgent(session_id="before-compaction", rotate_to="after-compaction")
    bridge = _bridge(agent)
    _turn(bridge, "use the demo skill")
    assert bridge.session_id == "after-compaction"
    stale = json.loads(_skill_view_with_bump({"name": SKILL}, task_id="before-compaction"))
    assert stale.get("dedup") is True, "precondition: the turn's own task id holds the load"
    bridge.rewind()
    again = json.loads(_skill_view_with_bump({"name": SKILL}, task_id="before-compaction"))
    assert _loaded_in_full(again)


# ── The rest of the agent hears about it ─────────────────────────────────


def test_memory_providers_are_told_the_session_was_rewound():
    agent = _SkillAgent()
    bridge = _bridge(agent)
    _turn(bridge, "use the demo skill")
    bridge.rewind()
    assert agent._memory_manager.switches == [
        (SESSION, {"parent_session_id": "", "reset": False, "rewound": True})
    ]
    assert agent._session_messages == [], "the agent's live mirror kept the turn"
    assert agent.conversation_history == []


def test_a_compaction_summary_in_the_same_row_survives_going_back():
    bridge = _bridge(SimpleNamespace(session_id="", conversation_history=[]))
    bridge._history = [
        {
            "role": "user",
            "content": (
                f"{SUMMARY_PREFIX}\n{HISTORICAL_TASK_HEADING}\nold task\n\n"
                f"{_SUMMARY_END_MARKER}\n\nREAL ASK"
            ),
        },
        {"role": "assistant", "content": "answer"},
    ]
    assert bridge.rewind().strip() == "REAL ASK"
    assert len(bridge._history) == 1
    assert "old task" in bridge._history[0]["content"], (
        "going back one message deleted the only copy of every compacted turn"
    )


# ── The stored copy ──────────────────────────────────────────────────────


def _store(session_id: str, history: list) -> SessionDB:
    db = SessionDB()
    db.create_session(session_id, source="cli")
    for message in history:
        db.append_message(session_id, message["role"], message.get("content"))
    return db


TWO_TURNS = [
    {"role": "user", "content": "one"},
    {"role": "assistant", "content": "first"},
    {"role": "user", "content": "two"},
    {"role": "assistant", "content": "second"},
]


def test_the_stored_conversation_is_rewound_with_the_bench():
    db = _store("sess-stored", TWO_TURNS)
    bridge = _bridge(SimpleNamespace(session_id="sess-stored", conversation_history=[]))
    bridge._history = db.get_messages_as_conversation("sess-stored")
    assert bridge.rewind() == "two"
    assert [m["content"] for m in db.get_messages_as_conversation("sess-stored")] == [
        "one", "first",
    ]
    assert bridge.last_rewind_note == ""


def test_a_stored_copy_that_does_not_match_is_left_and_said_so():
    db = _store("sess-diverged", TWO_TURNS[:2] + [{"role": "user", "content": "other"}])
    before = db.get_active_message_ids("sess-diverged")
    bridge = _bridge(SimpleNamespace(session_id="sess-diverged", conversation_history=[]))
    bridge._history = [dict(m) for m in TWO_TURNS]
    assert bridge.rewind() == "two", "the bench must still go back"
    assert [m["content"] for m in bridge._history] == ["one", "first"]
    assert db.get_active_message_ids("sess-diverged") == before
    assert "saved copy" in bridge.last_rewind_note


def test_a_conversation_never_stored_goes_back_without_a_note():
    bridge = _bridge(SimpleNamespace(session_id="sess-never", conversation_history=[]))
    bridge._history = [dict(m) for m in TWO_TURNS]
    assert bridge.rewind() == "two"
    assert bridge.last_rewind_note == ""
