"""A local server that silently truncates a request must not end the turn.

The reported fault: in the middle of a task, usually right after a tool call,
the model "acts as if the entire pretext is blank" — its reasoning says the
user's message is empty and the last thing it can see is the system prompt —
and either answers with an offer to help or nothing at all. The turn ends
there. The next request may fit and the model is back, as if nothing happened.

That is what a local server does when a request is larger than the window it
has loaded and it truncates instead of refusing: Ollama keeps the system
message and the newest messages and drops the oldest — which is the request
that says what the task is. The server's usage reports what it read, and
within one turn each request extends the previous one, so a count that does
not grow with what was appended is the tell. These tests drive the real agent
loop against a fake local server with exactly that rule.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.prompt_truncation import (
    MIN_APPENDED_TOKENS,
    MIN_SENT_TOKENS,
    ServerTruncatedPromptError,
    truncation_window,
)

LOCAL = "http://127.0.0.1:11434/v1"
TASK = "Please do the multi-step job, every step of it."
BLANK_REPLY = "Ready - what would you like me to do?"
FINISHED = "JOB FINISHED."


# ── The two readings ─────────────────────────────────────────────────────


def test_a_count_that_stalls_while_the_request_grows_is_a_truncation():
    """Ollama's shape: most of the request read, the oldest part dropped."""
    window = truncation_window(
        reported_prompt_tokens=20_802,
        sent_tokens=29_910,
        base_url=LOCAL,
        previous_prompt_tokens=20_822,
        appended_tokens=9_088,
    )
    assert window is not None
    assert 2_048 <= window <= 20_802


def test_an_honest_count_that_grows_is_not_judged_by_its_ratio():
    """Compressible output reads as fewer tokens than estimated — honestly."""
    assert truncation_window(
        reported_prompt_tokens=14_000,
        sent_tokens=21_000,
        base_url=LOCAL,
        previous_prompt_tokens=12_000,
        appended_tokens=9_000,
    ) is None


def test_far_short_of_what_was_sent_is_a_truncation_without_history():
    """A resumed conversation's first call has nothing earlier to compare."""
    assert truncation_window(
        reported_prompt_tokens=9_000, sent_tokens=60_000, base_url=LOCAL
    ) is not None


@pytest.mark.parametrize(
    "reported,sent",
    [
        (0, 60_000),  # no usage reported: nothing to judge
        (5_000, MIN_SENT_TOKENS - 1),  # too small a request to judge
        (40_000, 60_000),  # within estimation error
        (55_000, 60_000),
    ],
)
def test_ordinary_readings_are_not_truncations(reported, sent):
    assert truncation_window(
        reported_prompt_tokens=reported, sent_tokens=sent, base_url=LOCAL
    ) is None


def test_a_small_append_is_not_enough_to_judge_growth():
    assert truncation_window(
        reported_prompt_tokens=20_000,
        sent_tokens=24_000,
        base_url=LOCAL,
        previous_prompt_tokens=20_000,
        appended_tokens=MIN_APPENDED_TOKENS - 1,
    ) is None


@pytest.mark.parametrize(
    "base_url",
    ["https://api.openai.com/v1", "https://openrouter.ai/api/v1", ""],
)
def test_hosted_endpoints_are_never_judged(base_url):
    """Hosted APIs refuse an oversized request; their usage is not second-guessed."""
    assert truncation_window(
        reported_prompt_tokens=9_000, sent_tokens=60_000, base_url=base_url,
        previous_prompt_tokens=20_000, appended_tokens=9_000,
    ) is None


def test_a_moa_turn_is_never_judged():
    assert truncation_window(
        reported_prompt_tokens=9_000, sent_tokens=60_000, base_url=LOCAL,
        provider="moa",
    ) is None


def test_the_window_leaves_room_for_a_reply_but_errs_small():
    window = truncation_window(
        reported_prompt_tokens=10_000, sent_tokens=60_000, base_url=LOCAL,
        max_tokens=50_000,
    )
    # At most half of what was read is added for the reply.
    assert 10_000 <= window <= 15_000


def test_the_error_is_a_context_overflow_naming_the_window():
    """The loop's overflow recovery reads both of these off the error."""
    from agent.error_classifier import FailoverReason, classify_api_error
    from agent.model_metadata import (
        get_context_length_from_provider_error,
        parse_available_output_tokens_from_error,
    )

    error = ServerTruncatedPromptError(
        served_tokens=20_802, sent_tokens=29_910, window=20_736,
        configured_window=131_072,
    )
    classified = classify_api_error(
        error, provider="custom", model="m", approx_tokens=29_910,
        context_length=131_072, num_messages=12,
    )
    assert classified.reason == FailoverReason.context_overflow
    assert get_context_length_from_provider_error(str(error), 131_072) == 20_736
    # Not mistaken for an output-cap error, which skips compaction.
    assert parse_available_output_tokens_from_error(str(error)) is None
    assert error.session_only_window is True


# ── End to end, through the real agent loop ──────────────────────────────


def _tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False)) // 4


class _TruncatingServer(BaseHTTPRequestHandler):
    """An OpenAI-compatible local server with a smaller window than Curie thinks.

    Truncates like Ollama — system messages and the newest messages, oldest
    dropped first — and reports in its usage what it actually read. Its
    "model" works through a five-step job while it can see the job, and
    offers to help with nothing when it cannot.
    """

    window = 24_000
    log: list = []
    #: Whether over-window requests are truncated (True) or read in full.
    truncates = True

    def do_GET(self):  # noqa: N802 - http.server API
        body = json.dumps({"data": [{"id": "local-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _kept(self, messages, tools):
        if not type(self).truncates:
            return messages
        budget = type(self).window - _tokens(tools or [])
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        kept, used = [], _tokens(system)
        for message in reversed(rest):
            cost = _tokens(message)
            if used + cost > budget:
                break
            kept.insert(0, message)
            used += cost
        return system + kept

    def _send(self, chunks, stream):
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        message = {"role": "assistant", "content": ""}
        finish, usage = "stop", None
        for chunk in chunks:
            choice = chunk["choices"][0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                message["content"] += delta["content"]
            if delta.get("tool_calls"):
                message["tool_calls"] = delta["tool_calls"]
            finish = choice.get("finish_reason") or finish
            usage = chunk.get("usage") or usage
        body = json.dumps({
            "id": "x", "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": usage,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - http.server API
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        messages = request.get("messages") or []
        tools = request.get("tools")
        kept = self._kept(messages, tools)
        served = _tokens(kept) + _tokens(tools or [])
        type(self).log.append({"request": request, "kept": kept, "served": served})
        usage = {"prompt_tokens": served, "completion_tokens": 8, "total_tokens": served + 8}
        stream = bool(request.get("stream"))

        def chunk(delta, finish=None, with_usage=False):
            out = {"id": "c", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if with_usage:
                out["usage"] = usage
            return out

        if not tools:  # the compaction summary
            text = "## Summary\nThe user asked for the multi-step job; continue it."
            self._send([chunk({"role": "assistant", "content": text}),
                        chunk({}, "stop", True)], stream)
            return
        sees_task = any(
            TASK in json.dumps(m.get("content")) or "multi-step job" in json.dumps(m.get("content"))
            for m in kept if m.get("role") in ("user", "assistant")
        )
        if not sees_task:
            self._send([
                chunk({"role": "assistant", "reasoning_content": "The user's message is empty."}),
                chunk({"content": BLANK_REPLY}),
                chunk({}, "stop", True),
            ], stream)
            return
        step = sum(1 for m in messages if m.get("role") == "tool")
        if step < 5:
            call = {"index": 0, "id": f"call_{len(type(self).log)}", "type": "function",
                    "function": {"name": "read_file",
                                 "arguments": json.dumps({"path": str(self.server.files[step])})}}
            self._send([chunk({"role": "assistant", "tool_calls": [call]}),
                        chunk({}, "tool_calls", True)], stream)
            return
        self._send([chunk({"role": "assistant", "content": FINISHED}),
                    chunk({}, "stop", True)], stream)

    def log_message(self, *_args):
        pass


@pytest.fixture()
def truncating_server(tmp_path, monkeypatch):
    import os

    _TruncatingServer.log = []
    _TruncatingServer.truncates = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TruncatingServer)
    # Five files of ~4K tokens each: by the fourth or fifth the request no
    # longer fits the server's window, and it starts dropping the oldest
    # messages — the task.
    server.files = []
    for index in range(5):
        path = tmp_path / f"part{index}.txt"
        path.write_text(
            "\n".join(f"line {index}.{n}: " + "lorem ipsum dolor sit amet " * 6 for n in range(100))
        )
        server.files.append(path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    home = os.environ["CURIE_HOME"]
    # Curie is told a far larger window than the server has loaded — the
    # setting that has to be raised to get past the 64K start-up minimum.
    with open(os.path.join(home, "config.yaml"), "w") as fh:
        fh.write(
            "model:\n"
            "  default: local-model\n"
            "  provider: custom\n"
            f"  base_url: http://127.0.0.1:{port}/v1\n"
            "  api_key: local\n"
            "  context_length: 131072\n"
        )
    monkeypatch.chdir(tmp_path)
    try:
        yield server, port
    finally:
        server.shutdown()


def _make_agent(port, warnings):
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="local", base_url=f"http://127.0.0.1:{port}/v1",
        provider="custom", model="local-model", platform="cli",
        enabled_toolsets=["file"], quiet_mode=True,
        skip_context_files=True, skip_memory=True, save_trajectories=False,
    )
    agent.suppress_status_output = True
    agent.status_callback = lambda kind, text="": warnings.append((kind, text))
    return agent


def test_a_truncating_server_no_longer_ends_the_task(truncating_server):
    """The reply made from a truncated request is set aside, not answered with.

    Before: the fifth request reached the server larger than its window, the
    server dropped the task, the model offered to help with nothing — and
    that was the turn's answer.
    """
    _server, port = truncating_server
    warnings: list = []
    agent = _make_agent(port, warnings)

    result = agent.run_conversation(TASK, conversation_history=[])

    assert result["final_response"] == FINISHED, result["final_response"]
    # The blank-context reply never entered the conversation.
    said = [m.get("content") for m in result["messages"] if m.get("role") == "assistant"]
    assert BLANK_REPLY not in said
    # The server did truncate, it was noticed, and the reader was told.
    log = _TruncatingServer.log
    assert any(e["served"] < _tokens(e["request"].get("messages")) for e in log)
    assert any("cut that request short" in text for _kind, text in warnings)
    # After recovery, every request the model answered could see the task.
    tool_requests = [e for e in log if e["request"].get("tools")]
    last = tool_requests[-1]
    assert any("multi-step job" in json.dumps(m.get("content")) for m in last["kept"])


def test_the_learned_window_is_not_persisted(truncating_server, monkeypatch):
    """An inference from one truncation must not outlive the session."""
    import agent.conversation_loop as loop

    saved: list = []
    monkeypatch.setattr(loop, "save_context_length", lambda *a, **k: saved.append(a))
    _server, port = truncating_server
    agent = _make_agent(port, [])
    agent.run_conversation(TASK, conversation_history=[])
    assert saved == []
    # It does hold for the rest of this session.
    assert agent.context_compressor.context_length < 131_072


def test_an_honest_server_is_left_alone(truncating_server):
    """The same job on a server that reads everything is never second-guessed."""
    _TruncatingServer.truncates = False
    _server, port = truncating_server
    warnings: list = []
    agent = _make_agent(port, warnings)
    result = agent.run_conversation(TASK, conversation_history=[])
    assert result["final_response"] == FINISHED
    assert not any("cut that request short" in text for _kind, text in warnings)
    assert agent.context_compressor.context_length == 131_072
