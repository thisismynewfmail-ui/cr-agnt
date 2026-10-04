"""BACK (Ctrl+B) must not leave the model in a loop.

Reported: "the model seems to be looping after tool calling / using the back
key". Each case here is one way a taken-back turn — or a turn that came apart
— left something behind that sent the model round in circles, run end to end:
the real agent loop and the real read and todo tools, driven through the
console's own bridge, with only the model scripted.

* The read tool blocks the fourth identical read *in a row*. The reads of a
  taken-back turn kept counting, so going back and asking again three times
  got the file refused with "You already have this information" — which the
  rewind had just taken away.
* The todo list lives on the agent. A plan the taken-back turn wrote outlived
  it, came back on the model's next todo read, and was re-injected after
  compaction as the work to carry on with.
* A turn that raised left the bridge holding the history it went in with,
  while the read tool remembered serving the turn's reads: the next read came
  back "unchanged — refer to the earlier result", with nothing to refer to.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import AgentBridge  # noqa: E402
from curie_state import SessionDB  # noqa: E402

FILE_TEXT = "line one\nline two\nTHE CONTENT\n"
PLAN_ONE = [{"id": "1", "content": "write the parser", "status": "pending"}]
PLAN_TWO = [
    {"id": "1", "content": "write the parser", "status": "completed"},
    {"id": "2", "content": "ship it", "status": "in_progress"},
]


def _definitions(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _reply(content: str = "", calls=None, finish: str = "stop"):
    message = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        model="test/model",
        usage=None,
    )


def _call(name: str, arguments: dict):
    return SimpleNamespace(
        id=f"call_{uuid.uuid4().hex[:8]}",
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


class _Model:
    """A model that does what each request asks, once, and then answers."""

    def __init__(self, path: str):
        self.path = path
        #: The tool results the model was handed, in order.
        self.results: list[str] = []

    def __call__(self, api_kwargs: dict):
        last = api_kwargs["messages"][-1]
        if last["role"] == "tool":
            self.results.append(str(last.get("content")))
            return _reply("done")
        asked = str(last.get("content"))
        if "read the file" in asked:
            return _reply(calls=[_call("read_file", {"path": self.path})], finish="tool_calls")
        if "first plan" in asked:
            return _reply(calls=[_call("todo", {"todos": PLAN_ONE})], finish="tool_calls")
        if "second plan" in asked:
            return _reply(calls=[_call("todo", {"todos": PLAN_TWO})], finish="tool_calls")
        if "show the plan" in asked:
            return _reply(calls=[_call("todo", {})], finish="tool_calls")
        return _reply("hello")


@pytest.fixture
def bench(tmp_path):
    """A bridge on a real agent, with the model scripted and reads faked."""
    from run_agent import AIAgent
    from tools.file_tools import _read_tracker

    path = tmp_path / "notes.txt"
    path.write_text(FILE_TEXT)
    reads = MagicMock()
    reads.read_file = lambda p, offset=1, limit=500: SimpleNamespace(
        content=FILE_TEXT,
        to_dict=lambda: {
            "content": FILE_TEXT,
            "total_lines": 3,
            "file_size": len(FILE_TEXT),
        },
    )
    with (
        patch("run_agent.get_tool_definitions", return_value=_definitions("read_file", "todo")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=SessionDB(),
            platform="cli",
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are a test."
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent.suppress_status_output = True
    model = _Model(str(path))
    agent._interruptible_api_call = model
    agent._interruptible_streaming_api_call = lambda kwargs, on_first_delta=None: model(kwargs)

    bridge = AgentBridge()
    bridge._agent = agent
    bridge._session_id = agent.session_id
    _read_tracker.clear()
    with patch("tools.file_tools._get_file_ops", return_value=reads):
        yield bridge, agent, model
    _read_tracker.clear()


def _turn(bridge: AgentBridge, text: str) -> list:
    bridge._run_turn(text)
    events = bridge.drain()
    errors = [event.text for event in events if event.kind == "error"]
    assert not errors, errors
    return events


def test_going_back_and_asking_again_always_gets_the_file(bench):
    bridge, _agent, model = bench
    _turn(bridge, "hello")
    for attempt in range(5):
        _turn(bridge, "please read the file")
        served = model.results[-1]
        assert "THE CONTENT" in served, f"attempt {attempt + 1} was not given the file: {served}"
        assert "BLOCKED" not in served
        assert bridge.rewind() == "please read the file"


def test_a_plan_written_in_a_taken_back_turn_goes_with_it(bench):
    bridge, agent, model = bench
    _turn(bridge, "make the first plan")
    _turn(bridge, "make the second plan")
    assert agent._todo_store.read() == PLAN_TWO, "precondition: the second plan is live"

    assert bridge.rewind() == "make the second plan"
    assert agent._todo_store.read() == PLAN_ONE
    assert "ship it" not in (agent._todo_store.format_for_injection() or "")

    _turn(bridge, "show the plan")
    assert "ship it" not in model.results[-1]
    assert "write the parser" in model.results[-1]


def test_taking_back_the_only_plan_leaves_none(bench):
    bridge, agent, model = bench
    _turn(bridge, "hello")
    _turn(bridge, "make the second plan")
    assert bridge.rewind() == "make the second plan"
    assert agent._todo_store.read() == []
    _turn(bridge, "show the plan")
    assert "ship it" not in model.results[-1]


# ── A turn that came apart ───────────────────────────────────────────────


class _ReadsThenRaises:
    """Reads the file through the real tool, then dies before handing back.

    It never re-points its live mirror, as a turn that raises before its
    first persist does not: the history the bridge holds is the one it sent.
    """

    def __init__(self, path: str):
        self.session_id = "s-raises"
        self.conversation_history: list = []
        self._session_messages: list = []
        self.path = path

    def run_conversation(self, message, conversation_history=None, task_id=None, **_kw):
        from tools.file_tools import read_file_tool

        read_file_tool(self.path, task_id=task_id)
        raise RuntimeError("the provider dropped the connection")


def test_a_turn_that_raised_does_not_leave_its_reads_marked_as_served(tmp_path):
    from tools.file_tools import _read_tracker, read_file_tool

    path = tmp_path / "notes.txt"
    path.write_text(FILE_TEXT)
    reads = MagicMock()
    reads.read_file = lambda p, offset=1, limit=500: SimpleNamespace(
        content=FILE_TEXT,
        to_dict=lambda: {"content": FILE_TEXT, "total_lines": 3, "file_size": len(FILE_TEXT)},
    )
    bridge = AgentBridge(session_id="s-raises")
    bridge._agent = _ReadsThenRaises(str(path))
    bridge.ensure_agent = lambda: True  # type: ignore[method-assign]
    before = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
    bridge._history = list(before)
    _read_tracker.clear()
    try:
        with patch("tools.file_tools._get_file_ops", return_value=reads):
            bridge._run_turn("read the file")
            assert bridge._history == before, "nothing the turn did came back"
            again = json.loads(read_file_tool(str(path), task_id="s-raises"))
        assert again.get("dedup") is not True, again
        assert "THE CONTENT" in again.get("content", "")
    finally:
        _read_tracker.clear()
