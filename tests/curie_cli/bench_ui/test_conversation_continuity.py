"""A turn has to arrive at the model with the conversation attached.

``run_conversation`` builds a turn's message list from the history it is
*handed* — ``messages = list(conversation_history) if conversation_history
else []`` — and never reads ``agent.conversation_history``. ``agent.chat()``
forwards with ``conversation_history=None``.

So the console, which used ``chat()``, sent every turn as a first turn: a
resumed conversation was silently dropped, and even within one session the
model could not see what it had just said. Setting the agent attribute (which
the resume path did) changed nothing, because nothing reads it.
"""

from __future__ import annotations

import pytest

import curie_cli.bench_ui.agent_bridge as bridge_mod
from curie_cli.bench_ui.agent_bridge import AgentBridge


class _RecordingAgent:
    """Records the history each turn is handed, and grows it like the real one."""

    def __init__(self, **_kwargs):
        self.session_id = "sid"
        self.model = "test/model"
        self.provider = "test"
        self.conversation_history = []
        self.handed: list[list] = []

    def run_conversation(
        self, message, conversation_history=None, stream_callback=None, **_kw
    ):
        self.handed.append(list(conversation_history or []))
        if stream_callback:
            stream_callback(f"reply to {message}")
        return {
            "final_response": f"reply to {message}",
            "messages": list(conversation_history or [])
            + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": f"reply to {message}"},
            ],
        }


@pytest.fixture
def bridge(monkeypatch):
    agents: list[_RecordingAgent] = []

    def _build(**kwargs):
        agent = _RecordingAgent(**kwargs)
        agents.append(agent)
        return agent

    monkeypatch.setattr(bridge_mod, "_build_agent", _build)
    b = AgentBridge()
    assert b.ensure_agent()
    b.agents = agents  # type: ignore[attr-defined]
    return b


def _texts(messages) -> list[str]:
    return [m["content"] for m in messages]


# ── Within one conversation ──────────────────────────────────────────────

def test_the_first_turn_starts_from_nothing(bridge):
    bridge._run_turn("hello")
    assert bridge.agents[0].handed == [[]]


def test_the_second_turn_can_see_the_first(bridge):
    """The whole point. Without this the model repeats itself every turn."""
    bridge._run_turn("my name is Marie")
    bridge.drain()
    bridge._run_turn("what is my name")

    handed = bridge.agents[0].handed[1]
    assert _texts(handed) == ["my name is Marie", "reply to my name is Marie"], (
        f"the second turn was handed {_texts(handed)!r}"
    )


def test_history_keeps_growing_over_a_long_conversation(bridge):
    for index in range(5):
        bridge._run_turn(f"turn {index}")
        bridge.drain()
    handed = bridge.agents[0].handed
    assert [len(h) for h in handed] == [0, 2, 4, 6, 8], (
        f"history did not accumulate: {[len(h) for h in handed]}"
    )


def test_the_agent_is_never_asked_through_chat(bridge, monkeypatch):
    """``chat()`` drops the history, so the bridge must not use it."""
    called: list[str] = []
    monkeypatch.setattr(
        _RecordingAgent, "chat",
        lambda self, *a, **k: called.append("chat") or "",
        raising=False,
    )
    bridge._run_turn("hello")
    assert called == [], "the bridge went through chat() and lost the history"


def test_a_failed_turn_does_not_corrupt_the_history(bridge, monkeypatch):
    """A conversation must not be truncated by one provider error."""
    bridge._run_turn("first")
    bridge.drain()
    good = list(bridge._history)

    def _boom(self, *a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(_RecordingAgent, "run_conversation", _boom)
    bridge._run_turn("second")
    kinds = [e.kind for e in bridge.drain()]
    assert "error" in kinds and kinds[-1] == "done"
    assert bridge._history == good, "a failed turn ate the conversation"


def test_a_turn_returning_no_messages_keeps_what_we_had(bridge, monkeypatch):
    bridge._run_turn("first")
    bridge.drain()
    good = list(bridge._history)

    monkeypatch.setattr(
        _RecordingAgent, "run_conversation",
        lambda self, *a, **k: {"final_response": "terse", "messages": []},
    )
    bridge._run_turn("second")
    assert bridge._history == good


# ── Across a resume ──────────────────────────────────────────────────────

STORED_MODEL = [
    {"role": "user", "content": "what did we decide"},
    {"role": "assistant", "content": "we decided on pitchblende"},
]
STORED_DISPLAY = STORED_MODEL + [
    {"role": "user", "content": "[System: model switched]",
     "display_kind": "model_switch"},
]


@pytest.fixture
def resumable(monkeypatch):
    class _Db:
        def resolve_resume_session_id(self, sid):
            return sid

        def get_resume_conversations(self, _sid):
            return (STORED_MODEL, STORED_DISPLAY)

    import curie_state

    monkeypatch.setattr(curie_state, "SessionDB", _Db)
    agents: list[_RecordingAgent] = []
    monkeypatch.setattr(
        bridge_mod, "_build_agent",
        lambda **kw: agents.append(_RecordingAgent(**kw)) or agents[-1],
    )
    b = AgentBridge()
    b.agents = agents  # type: ignore[attr-defined]
    return b


def test_loading_a_conversation_returns_its_display_history(resumable):
    display = resumable.load_session("old-sid")
    assert display == STORED_DISPLAY


def test_a_resumed_conversation_reaches_the_model_on_the_next_turn(resumable):
    """This is the reported bug: the conversation came back on screen but
    the model was answering as though the session were new."""
    resumable.load_session("old-sid")
    resumable._run_turn("and where did we get to")

    handed = resumable.agents[-1].handed[0]
    assert handed == STORED_MODEL, (
        f"the resumed conversation did not reach the model: {_texts(handed)!r}"
    )


def test_a_resume_seeds_the_model_history_not_the_display_one(resumable):
    """Display history carries the whole lineage plus markers.

    Replaying that at the model would re-send turns the compression already
    summarised, and feed it rows no client renders as conversation.
    """
    resumable.load_session("old-sid")
    assert resumable._history == STORED_MODEL
    assert all("display_kind" not in m for m in resumable._history)


def test_turns_reported_after_a_resume_count_the_restored_conversation(resumable):
    resumable.load_session("old-sid")
    assert resumable.describe()["turns"] == 1
    resumable._run_turn("another")
    assert resumable.describe()["turns"] == 2


def test_a_new_conversation_after_a_resume_starts_empty(resumable):
    """F12 must not carry the resumed conversation into the new one."""
    resumable.load_session("old-sid")
    assert resumable._history
    assert resumable.new_session() is True
    assert resumable._history == []

    resumable._run_turn("fresh start")
    assert resumable.agents[-1].handed[0] == []
