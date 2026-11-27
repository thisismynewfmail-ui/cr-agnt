"""A local server is never sent more than the window it is serving.

Reported from the bench console: straight after a tool call that returned
something large, the model's thinking says "the user's message is empty —
there's no actual query content, just the conversation start marker", or
"the user hasn't actually asked a question yet — the last message is just
system instructions". It was answering Curie's system prompt and nothing
else. ``model.context_length`` said 180,000 tokens; the server had been
loaded with less, and a request past its window is not refused — LM Studio's
overflow policy keeps the system prompt and as many of the newest messages
as fit, older llama.cpp builds discard the middle — so a big enough tool
result left nothing after the system prompt at all.

The configured window was trusted without asking the server. Now, on a local
endpoint whose server reports the window it was loaded with, the configured
value is capped to it, and the conversation is compacted to fit before it is
sent rather than cut by the server after.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import agent.model_metadata as mm

TASK = "TASK: count the lines in every part file, then report the total."
SERVED_WINDOW = 48_000


def _tokens(payload) -> int:
    return len(json.dumps(payload)) // 4


class _LoadedServer(BaseHTTPRequestHandler):
    """A llama.cpp-shaped server loaded with a :data:`SERVED_WINDOW` window.

    Reports the window on ``/props``, as llama.cpp does. A request past it is
    cut the way LM Studio cuts one — the system prompt, then the newest
    messages that still fit — and its "model" answers whatever survived: with
    no user message left, it says so and does nothing.
    """

    log: list = []
    work = ""
    window = SERVED_WINDOW

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith(("/props", "/v1/props")):
            self._json({"default_generation_settings": {"n_ctx": type(self).window}})
        elif self.path.startswith("/v1/models"):
            self._json({"data": [{"id": "local-model", "owned_by": "llamacpp"}]})
        else:
            self._json({"error": "not found"}, status=404)

    def _served(self, messages, tools):
        budget = type(self).window - _tokens(tools) - 2_000
        system, rest = messages[:1], messages[1:]
        budget -= _tokens(system)
        kept: list = []
        for message in reversed(rest):
            cost = _tokens(message)
            if cost > budget:
                break
            budget -= cost
            kept.insert(0, message)
        return system + kept

    def do_POST(self):  # noqa: N802
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        messages = request.get("messages") or []
        tools = request.get("tools") or []
        sent = _tokens(messages) + _tokens(tools)
        served = self._served(messages, tools) if tools else messages
        type(self).log.append({"tools": bool(tools), "sent": sent,
                               "cut": len(served) < len(messages)})
        usage = {"prompt_tokens": _tokens(served) + _tokens(tools),
                 "completion_tokens": 10, "total_tokens": 0}
        if not tools:  # the compaction summarizer
            reply, finish = {"role": "assistant", "content": "## Active Task\nCount lines."}, "stop"
        elif not any(m.get("role") == "user" and TASK in str(m.get("content")) for m in served):
            reply = {"role": "assistant",
                     "content": "The user's message is empty — there is nothing to do."}
            finish = "stop"
        else:
            done = sum(1 for m in served if m.get("role") == "tool")
            calls = sum(1 for r in type(self).log if r["tools"])
            if calls <= 6:
                path = os.path.join(type(self).work, f"part{calls % 6}.txt")
                call = {"id": f"call_{calls}", "type": "function",
                        "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}
                reply, finish = {"role": "assistant", "content": "", "tool_calls": [call]}, "tool_calls"
            else:
                reply, finish = {"role": "assistant", "content": f"Counted {done} parts."}, "stop"
        if not request.get("stream"):
            self._json({"id": "x", "object": "chat.completion",
                        "choices": [{"index": 0, "message": reply, "finish_reason": finish}],
                        "usage": usage})
            return
        delta = {"role": "assistant"}
        if reply.get("content"):
            delta["content"] = reply["content"]
        if reply.get("tool_calls"):
            delta["tool_calls"] = [dict(c, index=i) for i, c in enumerate(reply["tool_calls"])]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in (
            {"id": "x", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
        ):
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_a):
        pass


@pytest.fixture(params=[SERVED_WINDOW, 98_304], ids=["under-minimum", "over-minimum"])
def loaded_server(request, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    for index in range(6):
        (work / f"part{index}.txt").write_text(
            "\n".join(f"part {index} line {n}: " + "lorem ipsum dolor " * 6 for n in range(900))
        )
    _LoadedServer.log = []
    _LoadedServer.work = str(work)
    _LoadedServer.window = request.param
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LoadedServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    monkeypatch.chdir(work)
    with open(os.path.join(os.environ["CURIE_HOME"], "config.yaml"), "w") as fh:
        fh.write(
            "model:\n  default: local-model\n  provider: custom\n"
            f"  base_url: http://127.0.0.1:{port}/v1\n  api_key: local\n"
            "  context_length: 180000\n"
        )
    try:
        yield port
    finally:
        server.shutdown()


def test_a_big_tool_result_never_leaves_the_model_with_only_the_system_prompt(loaded_server):
    from run_agent import AIAgent

    port = loaded_server
    agent = AIAgent(
        api_key="local", base_url=f"http://127.0.0.1:{port}/v1", provider="custom",
        model="local-model", platform="cli", enabled_toolsets=["file"], quiet_mode=True,
        skip_context_files=True, skip_memory=True, save_trajectories=False,
    )
    agent.suppress_status_output = True
    # Under the minimum the agent applies it for the session; at or over it,
    # the resolver does. Either way it is the window requests are fitted to.
    assert agent.context_compressor.context_length == _LoadedServer.window
    result = agent.run_conversation(TASK, conversation_history=[])
    assert "empty" not in str(result.get("final_response")), result.get("final_response")
    assert str(result.get("final_response")).startswith("Counted")
    main = [r for r in _LoadedServer.log if r["tools"]]
    assert main and not any(r["cut"] for r in main)


# ── The cap itself ───────────────────────────────────────────────────────


@pytest.fixture()
def probe(monkeypatch):
    state = {"type": "llamacpp", "served": 98_304, "calls": 0}

    def detect(base_url, api_key=""):
        return state["type"]

    def query(model, base_url, api_key=""):
        state["calls"] += 1
        if isinstance(state["served"], Exception):
            raise state["served"]
        return state["served"]

    def read(server_type, model, base_url, api_key):
        return query(model, base_url, api_key)

    monkeypatch.setattr(mm, "detect_local_server_type", detect)
    monkeypatch.setattr(mm, "_read_serving_window", read)
    monkeypatch.setattr(mm, "_SERVING_WINDOW_WARNED", set())
    monkeypatch.setattr(mm, "_SERVING_WINDOW_CACHE", {})
    return state


LOCAL = "http://127.0.0.1:8080/v1"


def test_a_configured_window_larger_than_served_is_capped_with_a_warning(probe, caplog):
    with caplog.at_level(logging.WARNING, logger=mm.logger.name):
        assert mm.get_model_context_length("m", LOCAL, config_context_length=180_000) == 98_304
        assert mm.get_model_context_length("m", LOCAL, config_context_length=180_000) == 98_304
    warnings = [r for r in caplog.records if "is serving" in r.getMessage()]
    assert len(warnings) == 1
    assert "180,000" in warnings[0].getMessage() and "98,304" in warnings[0].getMessage()


def test_a_served_window_under_the_minimum_is_left_to_the_agent(probe):
    """The configured value stays the start-up check's escape hatch."""
    probe["served"] = 48_000
    assert mm.get_model_context_length("m", LOCAL, config_context_length=180_000) == 180_000


@pytest.mark.parametrize("served", [180_000, 262_144, None, 0])
def test_the_configured_window_stands_when_the_server_serves_as_much_or_says_nothing(probe, served):
    probe["served"] = served
    assert mm.get_model_context_length("m", LOCAL, config_context_length=180_000) == 180_000


def test_a_failed_probe_keeps_the_configured_window(probe):
    probe["served"] = OSError("connection refused")
    assert mm.get_model_context_length("m", LOCAL, config_context_length=180_000) == 180_000


@pytest.mark.parametrize("server_type", ["ollama", None])
def test_servers_that_do_not_fix_their_window_are_not_asked(probe, server_type):
    """Ollama serves the num_ctx Curie sends with each request."""
    probe["type"] = server_type
    assert mm.get_model_context_length("m", LOCAL, config_context_length=180_000) == 180_000
    assert probe["calls"] == 0


def test_the_lmstudio_provider_keeps_its_own_load_and_reconcile(probe):
    """It loads at the configured window and reconciles after the load."""
    probe["type"] = "lm-studio"
    assert mm.get_model_context_length(
        "m", LOCAL, config_context_length=180_000, provider="lmstudio"
    ) == 180_000
    assert probe["calls"] == 0


def test_a_hosted_endpoint_is_never_asked(probe):
    assert mm.get_model_context_length(
        "m", "https://api.example.com/v1", config_context_length=180_000
    ) == 180_000
    assert probe["calls"] == 0
