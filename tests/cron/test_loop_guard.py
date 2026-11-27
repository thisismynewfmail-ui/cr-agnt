"""LOOP DETECT → SCHEDULED: a scheduled run that loops is stopped and sent on.

Scheduled runs happen in the gateway with nobody watching, and a looping
stream is activity, so the inactivity watchdog never ends one. With the
bench console's LOOP DETECT and its SCHEDULED switch both on, the scheduler
watches the run's stream, interrupts the agent when a passage comes round
REPEATS times (the CLI's Ctrl+C), and continues the same conversation with
the resume message — up to RESUMES times, after which the run is ended and
reported as failed. With either switch off, ``run_job`` calls
``run_conversation`` exactly as it always has.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from agent.loop_detect import DEFAULT_MESSAGE, LoopSettings, scheduled_settings
from cron.loop_guard import run_watched

PARAGRAPH = (
    "The bot daemon runs as an asyncio task in the server process, managing "
    "bot state in memory with persona tags and presence tracking."
)
ANSWER = "Carrying on with the report from where I was."


class _LoopingAgent:
    """An agent whose turns loop in their reasoning until interrupted.

    It honours ``interrupt()`` the way the real agent does — between streamed
    pieces — and refuses to start a turn while an interrupt is still pending,
    which is what a stale interrupt does to the real agent's next turn.
    """

    def __init__(self, plan, cap: int = 60) -> None:
        self.plan = list(plan)
        self.cap = cap
        self._interrupt_requested = False
        self.reasoning_callback = None
        self.tool_start_callback = None
        self.calls: list = []
        self.interrupts = 0
        self.interrupt_threads: list = []
        self.stream_threads: list = []

    def interrupt(self, message=None, **_kw) -> bool:
        self.interrupts += 1
        self.interrupt_threads.append(threading.current_thread().name)
        self._interrupt_requested = True
        return True

    def clear_interrupt(self, **_kw) -> bool:
        self._interrupt_requested = False
        return True

    def run_conversation(self, user_message, conversation_history=None,
                         stream_callback=None, task_id=None, **_kw):
        assert not self._interrupt_requested, "a turn began with a stale interrupt"
        self.calls.append({
            "message": user_message,
            "history": list(conversation_history or []),
            "task_id": task_id,
        })
        history = list(conversation_history or []) + [
            {"role": "user", "content": user_message}
        ]
        index = len(self.calls) - 1
        if not (self.plan[index] if index < len(self.plan) else False):
            stream_callback(ANSWER)
            return {
                "final_response": ANSWER,
                "messages": history + [{"role": "assistant", "content": ANSWER}],
                "completed": True,
            }
        for _count in range(self.cap):
            if self._interrupt_requested:
                return {
                    "final_response": "Operation interrupted.",
                    "messages": history,
                    "interrupted": True,
                    "completed": False,
                }
            self.stream_threads.append(threading.current_thread().name)
            self.reasoning_callback(PARAGRAPH + "\n\n")
            time.sleep(0.005)
        return {"final_response": "gave up", "messages": history, "completed": True}


def test_a_looping_run_is_stopped_and_sent_on_in_the_same_conversation():
    agent = _LoopingAgent(plan=(True, False))
    result = run_watched(agent, "write the report", settings=LoopSettings(), task_id="t1")
    assert result["final_response"] == ANSWER
    assert agent.interrupts == 1
    # The agent's own interrupt, from outside the stream's thread.
    assert agent.interrupt_threads == ["cron-loop-guard"]
    assert [c["message"] for c in agent.calls] == ["write the report", DEFAULT_MESSAGE]
    # Continued in the conversation as the stopped turn left it.
    assert agent.calls[1]["history"] == [{"role": "user", "content": "write the report"}]
    assert agent.calls[1]["task_id"] == "t1"


def test_the_stop_lands_on_the_third_repeat():
    agent = _LoopingAgent(plan=(True, False))
    run_watched(agent, "go", settings=LoopSettings(repeats=3))
    # Three paragraphs to trip it, and at most a couple more before the
    # interrupt is seen — not the sixty the model would have written.
    assert 3 <= len(agent.stream_threads) <= 8


def test_a_run_that_keeps_looping_is_ended_and_reported_as_failed():
    agent = _LoopingAgent(plan=(True, True, True, True))
    result = run_watched(agent, "go", settings=LoopSettings(resumes=2), job_name="nightly")
    assert len(agent.calls) == 3
    assert result["failed"] is True
    assert "Loop Detect stopped the run" in result["error"]
    assert "came round 3 times in the thinking" in result["error"]
    assert "sent on twice" in result["error"]


def test_resumes_zero_stops_without_sending_anything():
    agent = _LoopingAgent(plan=(True,))
    result = run_watched(agent, "go", settings=LoopSettings(resumes=0))
    assert len(agent.calls) == 1
    assert result["failed"] is True


def test_a_run_that_does_not_loop_is_returned_untouched():
    agent = _LoopingAgent(plan=(False,))
    result = run_watched(agent, "go", settings=LoopSettings())
    assert result["final_response"] == ANSWER
    assert agent.interrupts == 0 and len(agent.calls) == 1


def test_a_tool_call_starts_the_count_again():
    calls = []

    class _ToolAgent(_LoopingAgent):
        def run_conversation(self, user_message, conversation_history=None,
                             stream_callback=None, task_id=None, **_kw):
            calls.append(user_message)
            for _ in range(5):
                self.reasoning_callback(PARAGRAPH + "\n" + PARAGRAPH + "\n")
                self.tool_start_callback("call-1", "read_file", {})
            return {"final_response": "done", "messages": [], "completed": True}

    agent = _ToolAgent(plan=())
    result = run_watched(agent, "go", settings=LoopSettings())
    assert result["final_response"] == "done"
    assert agent.interrupts == 0 and calls == ["go"]


def test_the_agents_own_callbacks_still_fire_and_are_put_back():
    seen = []
    agent = _LoopingAgent(plan=(False,))
    agent.reasoning_callback = original = lambda text, *a, **k: seen.append(text)
    run_watched(agent, "go", settings=LoopSettings())
    assert agent.reasoning_callback is original
    assert agent.tool_start_callback is None


def test_only_both_switches_on_watches_a_scheduled_run():
    on = {"ui": {"loop_detect": {"enabled": True, "scheduled": True}}}
    assert scheduled_settings(on) is not None
    assert scheduled_settings({"ui": {"loop_detect": {"enabled": False, "scheduled": True}}}) is None
    assert scheduled_settings({"ui": {"loop_detect": {"enabled": True}}}) is None
    assert scheduled_settings({}) is None
    assert scheduled_settings(None) is None


# ── run_job ──────────────────────────────────────────────────────────────


def _run_job_with(tmp_path, config: str, agent):
    from cron.scheduler import run_job

    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    job = {"id": "loop-guard-job", "name": "loop-guard", "prompt": "write the report"}
    with patch("cron.scheduler._curie_home", tmp_path), \
         patch("cron.scheduler._resolve_origin", return_value=None), \
         patch("dotenv.load_dotenv"), \
         patch("curie_state.get_shared_session_db", return_value=MagicMock()), \
         patch(
             "curie_cli.runtime_provider.resolve_runtime_provider",
             return_value={
                 "api_key": "test-key",
                 "base_url": "https://example.invalid/v1",
                 "provider": "custom",
                 "api_mode": "chat_completions",
             },
         ), \
         patch("run_agent.AIAgent", return_value=agent):
        return run_job(job)


def test_run_job_stops_and_sends_on_a_looping_run_when_scheduled_is_on(tmp_path):
    agent = _LoopingAgent(plan=(True, False))
    success, _output, final_response, error = _run_job_with(
        tmp_path,
        "model:\n  default: test-model\nui:\n  loop_detect:\n    scheduled: true\n",
        agent,
    )
    assert success is True, error
    assert final_response == ANSWER
    # The job's prompt (as cron frames it), then the resume.
    assert len(agent.calls) == 2
    assert "write the report" in agent.calls[0]["message"]
    assert agent.calls[1]["message"] == DEFAULT_MESSAGE
    assert agent.interrupts == 1


def test_run_job_calls_the_agent_exactly_as_before_when_scheduled_is_off(tmp_path):
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": "ok", "messages": []}
    success, _output, final_response, error = _run_job_with(
        tmp_path, "model:\n  default: test-model\n", agent
    )
    assert success is True, error
    assert final_response == "ok"
    call = agent.run_conversation.call_args
    # One positional prompt and the task id: no stream hook, no history.
    assert len(call.args) == 1 and set(call.kwargs) == {"task_id"}
