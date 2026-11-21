"""PANEL → VISION → DIRECT IMAGES: images to the model itself, or described.

The switch is Curie's own ``agent.image_input_mode`` — ``native`` on,
``text`` off — so the agent follows it on the very next image, and the CLI,
the TUI and the gateway agree with the bench. The agent side of both modes is
driven end to end in ``tests/agent/test_direct_images.py``; here, the switch
and the bench's own part: an image file's path typed into the composer is
attached as the picture when images go direct.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import agent_bridge as bridge_mod  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import ToggleSwitch, vision_note  # noqa: E402
from curie_cli.bench_ui.settings import image_mode, read_settings  # noqa: E402

from tests.agent.test_direct_images import _VisionServer, vision_server  # noqa: E402,F401
from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


def _write_mode(mode: str) -> None:
    with open(os.path.join(os.environ["CURIE_HOME"], "config.yaml"), "w") as fh:
        fh.write(f"agent:\n  image_input_mode: {mode}\n")


@pytest.mark.parametrize(
    "stored,expected",
    [("native", "native"), ("text", "text"), ("auto", "auto"),
     ("NATIVE", "native"), ("bogus", "auto"), (None, "auto")],
)
def test_the_mode_is_read_the_way_the_agent_reads_it(stored, expected):
    assert image_mode(stored) == expected


def test_each_mode_says_what_it_does():
    assert "native" in vision_note("native")
    assert "description" in vision_note("text")
    assert "auto" in vision_note("auto").lower()


@pytest.mark.parametrize("mode", ["bench", "dos"])
def test_the_switch_flips_the_agents_own_setting_both_ways(mode):
    _write_mode("auto")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(130, 60)) as pilot:
            await _settle(pilot)
            if mode == "dos":
                app.run_keyline_action("dos-mode")
            app.show_pane("panel")
            await _settle(pilot)
            switch = app.query_one("#switch-vision-direct", ToggleSwitch)
            assert switch.is_on is False, "auto is not DIRECT IMAGES"

            switch.scroll_visible(animate=False)
            await _settle(pilot)
            await pilot.click("#switch-vision-direct")
            await _settle(pilot)
            assert switch.is_on
            assert read_settings().image_input_mode == "native"
            assert "Saved as agent.image_input_mode: native" in str(
                app.query_one("#vision-note").render()
            )

            await pilot.click("#switch-vision-direct")
            await _settle(pilot)
            assert not switch.is_on
            assert read_settings().image_input_mode == "text"
    _run(scenario())


def test_a_change_made_elsewhere_reaches_the_switch():
    """``curie config set agent.image_input_mode native`` in another terminal."""
    _write_mode("text")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(130, 60)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            switch = app.query_one("#switch-vision-direct", ToggleSwitch)
            assert not switch.is_on
            time.sleep(0.05)  # a distinct mtime for the settings file
            _write_mode("native")
            app._poll_shared_stores()
            await _settle(pilot)
            assert switch.is_on
    _run(scenario())


# ── A path typed into the composer ───────────────────────────────────────


class _Agent:
    provider = "custom"
    model = "local-model"
    requested_provider = ""


def test_a_typed_image_path_is_attached_when_images_go_direct(tmp_path):
    from PIL import Image

    picture = tmp_path / "plan.png"
    Image.new("RGB", (8, 8), (0, 0, 255)).save(picture)
    message = f"What does {picture} show?"

    _write_mode("native")
    sent = bridge_mod._with_attached_images(_Agent(), message)
    assert isinstance(sent, list)
    assert any(p.get("type") == "image_url"
               and p["image_url"]["url"].startswith("data:image/png;base64,")
               for p in sent)
    assert str(picture) in sent[0]["text"]

    _write_mode("text")
    assert bridge_mod._with_attached_images(_Agent(), message) == message


def test_a_message_without_an_image_path_goes_as_typed():
    _write_mode("native")
    for message in ("just words", "see https://example.com/a.png", "/no/such/file.png"):
        assert bridge_mod._with_attached_images(_Agent(), message) == message


def _bridge_turn(port, message):
    """One turn through the bench's own bridge and agent construction."""
    bridge = bridge_mod.AgentBridge()
    assert bridge.ensure_agent(), bridge.load_error
    assert bridge.submit(message)
    deadline = time.time() + 60
    while time.time() < deadline:
        if any(ev.kind == "done" for ev in bridge.drain()):
            return
        time.sleep(0.02)
    raise AssertionError("the turn never finished")


def _first_user(requests):
    main = [r for r in requests if r.get("tools")]
    return next(m for m in main[0]["messages"] if m.get("role") == "user")


def test_through_the_bridge_a_typed_path_reaches_the_model_as_the_picture(
    vision_server, tmp_path  # noqa: F811
):
    port = vision_server("native")
    _bridge_turn(port, f"What is in {_VisionServer.image_path}?")
    first = _first_user(_VisionServer.log)
    assert isinstance(first["content"], list)
    assert any(p.get("type") == "image_url" for p in first["content"])


def test_through_the_bridge_tool_mode_sends_the_words_as_typed(
    vision_server, tmp_path  # noqa: F811
):
    port = vision_server("text")
    _bridge_turn(port, f"What is in {_VisionServer.image_path}?")
    first = _first_user(_VisionServer.log)
    content = first["content"]
    text = content if isinstance(content, str) else " ".join(
        str(p.get("text", "")) for p in content if isinstance(p, dict)
    )
    assert _VisionServer.image_path in text
    assert not (isinstance(content, list)
                and any(p.get("type") == "image_url" for p in content))
