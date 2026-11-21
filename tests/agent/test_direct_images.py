"""Images straight to the model, or described by the vision tool — both work.

The bench's DIRECT IMAGES switch is ``agent.image_input_mode``: ``native``
sends the picture itself into the conversation, ``text`` has the vision tool
describe it in words. Reported: on a custom model only the description ever
happened. ``native`` was honoured when the image was attached and undone when
the request was sent — a model unknown to the capability catalogues had every
image swapped for a description — and the vision tool's direct route was
gated on a provider allowlist a custom endpoint is never on. And a plain
chat-completions server cannot read an image inside a ``tool`` message at
all, so on such a route the request carries it in a user turn.

Driven end to end: the real agent loop and the real vision tool, against a
fake OpenAI-compatible server whose "model" says whether it can see an image.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.tool_images import CARRIER_TEXT, carry_tool_images_in_user_turn

SAW_IT = "I can see the image directly."
DESCRIBED = "A small red square on a white ground."
DATA = "data:image/png;base64,AAAA"


# ── The wire transform ───────────────────────────────────────────────────


def _image(url=DATA):
    return {"type": "image_url", "image_url": {"url": url}}


def test_tool_images_move_into_one_user_turn_after_the_run():
    messages = [
        {"role": "user", "content": "look at both"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": [{"type": "text", "text": "first"}, _image("1")]},
        {"role": "tool", "tool_call_id": "b", "content": [{"type": "text", "text": "second"}, _image("2")]},
    ]
    sent = carry_tool_images_in_user_turn(messages)
    assert [m["role"] for m in sent] == ["user", "assistant", "tool", "tool", "user"]
    assert all(isinstance(m["content"], str) for m in sent if m["role"] == "tool")
    assert "first" in sent[2]["content"] and "follows in the next message" in sent[2]["content"]
    carrier = sent[-1]["content"]
    assert carrier[0] == {"type": "text", "text": CARRIER_TEXT}
    assert [p["image_url"]["url"] for p in carrier[1:]] == ["1", "2"]
    # The stored history is untouched.
    assert isinstance(messages[2]["content"], list)


def test_a_following_user_message_takes_the_images_instead_of_a_second_turn():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": [_image()]},
        {"role": "user", "content": "and now?"},
    ]
    sent = carry_tool_images_in_user_turn(messages)
    assert [m["role"] for m in sent] == ["assistant", "tool", "user"]
    assert sent[-1]["content"][1] == _image()
    assert sent[-1]["content"][-1] == {"type": "text", "text": "and now?"}


def test_a_conversation_without_tool_images_is_left_alone():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}, _image()]},
        {"role": "tool", "tool_call_id": "a", "content": "plain"},
    ]
    assert carry_tool_images_in_user_turn(messages) is messages


# ── End to end ───────────────────────────────────────────────────────────


class _VisionServer(BaseHTTPRequestHandler):
    """An OpenAI-compatible server. Its model looks at one image, then says
    whether a real image reached it; its vision side describes images."""

    log: list = []
    image_path = ""

    def do_GET(self):  # noqa: N802
        body = json.dumps({"data": [{"id": "local-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply(self, message, finish, stream):
        usage = {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55}
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant"}
            if message.get("content"):
                delta["content"] = message["content"]
            if message.get("tool_calls"):
                delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(message["tool_calls"])]
            for chunk in (
                {"id": "x", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
            ):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
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

    def do_POST(self):  # noqa: N802
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).log.append(request)
        messages = request.get("messages") or []
        stream = bool(request.get("stream"))
        if not request.get("tools"):
            # The auxiliary vision call.
            self._reply({"role": "assistant", "content": DESCRIBED}, "stop", stream)
            return
        if not any(m.get("role") == "tool" for m in messages):
            call = {"id": "call_1", "type": "function", "function": {
                "name": "vision_analyze",
                "arguments": json.dumps({"image_url": type(self).image_path,
                                         "question": "what is it?"})}}
            self._reply({"role": "assistant", "content": "", "tool_calls": [call]},
                        "tool_calls", stream)
            return
        sees = any(
            isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url"
                    and str(p.get("image_url", {}).get("url", "")).startswith("data:image/")
                    for p in m["content"])
            for m in messages if m.get("role") == "user"
        )
        tool_text = " ".join(
            str(m.get("content")) for m in messages if m.get("role") == "tool"
        )
        answer = SAW_IT if sees else f"From the description: {tool_text[:200]}"
        self._reply({"role": "assistant", "content": answer}, "stop", stream)

    def log_message(self, *_a):
        pass


@pytest.fixture()
def vision_server(tmp_path, monkeypatch):
    import os

    from PIL import Image

    picture = tmp_path / "square.png"
    Image.new("RGB", (32, 32), (220, 20, 20)).save(picture)
    _VisionServer.log = []
    _VisionServer.image_path = str(picture)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VisionServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    monkeypatch.chdir(tmp_path)

    def configure(mode):
        with open(os.path.join(os.environ["CURIE_HOME"], "config.yaml"), "w") as fh:
            fh.write(
                "model:\n"
                "  default: local-model\n"
                "  provider: custom\n"
                f"  base_url: http://127.0.0.1:{port}/v1\n"
                "  api_key: local\n"
                "  context_length: 131072\n"
                "agent:\n"
                f"  image_input_mode: {mode}\n"
            )
        return port

    try:
        yield configure
    finally:
        server.shutdown()


def _run_turn(port):
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="local", base_url=f"http://127.0.0.1:{port}/v1",
        provider="custom", model="local-model", platform="cli",
        enabled_toolsets=["vision"], quiet_mode=True,
        skip_context_files=True, skip_memory=True, save_trajectories=False,
    )
    agent.suppress_status_output = True
    return agent.run_conversation("What is in square.png?", conversation_history=[])


def _main_requests():
    return [r for r in _VisionServer.log if r.get("tools")]


def _has_image(message) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in content
    )


def test_direct_mode_puts_the_picture_itself_in_front_of_the_model(vision_server):
    port = vision_server("native")
    result = _run_turn(port)
    assert result["final_response"] == SAW_IT, result["final_response"]
    last = _main_requests()[-1]["messages"]
    # The tool result is text on the wire (a chat server reads nothing else
    # there), and the image follows it in a user turn — the real pixels.
    tool = next(m for m in last if m["role"] == "tool")
    assert isinstance(tool["content"], str)
    carrier = last[last.index(tool) + 1]
    assert carrier["role"] == "user" and _has_image(carrier)
    assert CARRIER_TEXT in json.dumps(carrier["content"])
    # And no description was asked for: there was no auxiliary vision call.
    assert not [r for r in _VisionServer.log if not r.get("tools")]


def test_tool_mode_has_the_vision_tool_describe_it_in_words(vision_server):
    port = vision_server("text")
    result = _run_turn(port)
    assert DESCRIBED in result["final_response"], result["final_response"]
    # The model never received image data — only the description.
    for request in _main_requests():
        assert not any(_has_image(m) for m in request["messages"])
    # The image went to the vision side instead.
    aux = [r for r in _VisionServer.log if not r.get("tools")]
    assert aux and any(_has_image(m) for m in aux[-1]["messages"])
