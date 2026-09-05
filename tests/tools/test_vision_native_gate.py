"""``vision_analyze`` must not disappear for the users the native route serves.

Two independent routes serve the tool. The native route hands the image
straight to the main model and needs no auxiliary credentials at all; the
legacy route asks a separate auxiliary vision model to describe it. The tool
was gated on the auxiliary chain alone, so a vision-capable main model
(Claude, GPT, Gemini 3) with no OpenRouter or Nous credit resolved no
auxiliary client, the gate said no, and the tool was stripped from the model's
tool list — leaving it unable to look at an image it could read perfectly
well.

``video_analyze`` has no native route, so it keeps the narrower gate: there,
an auxiliary client really is required.
"""

from __future__ import annotations

import pytest

import tools.vision_tools as vt
from tools.registry import registry


@pytest.fixture
def no_auxiliary(monkeypatch):
    """No auxiliary vision client resolves, by any route."""
    monkeypatch.setattr(vt, "check_vision_requirements", lambda: False)


@pytest.fixture
def native_available(monkeypatch):
    monkeypatch.setattr(vt, "_should_use_native_vision_fast_path", lambda: True)


@pytest.fixture
def native_unavailable(monkeypatch):
    monkeypatch.setattr(vt, "_should_use_native_vision_fast_path", lambda: False)


def test_native_route_alone_is_enough_to_offer_the_tool(
    no_auxiliary, native_available
):
    assert vt.check_image_analysis_requirements() is True


def test_auxiliary_route_alone_is_still_enough(monkeypatch, native_unavailable):
    monkeypatch.setattr(vt, "check_vision_requirements", lambda: True)
    assert vt.check_image_analysis_requirements() is True


def test_neither_route_means_the_tool_is_correctly_withheld(
    no_auxiliary, native_unavailable
):
    """The gate must stay honest — offering a tool that cannot run is worse."""
    assert vt.check_image_analysis_requirements() is False


def test_the_native_route_is_checked_before_the_auxiliary_probe(
    monkeypatch, native_available
):
    """Probing the auxiliary chain costs network and credential lookups.

    When the native route already answers yes, none of that is needed.
    """
    probed = []
    monkeypatch.setattr(
        vt, "check_vision_requirements", lambda: probed.append(1) or True
    )
    assert vt.check_image_analysis_requirements() is True
    assert probed == [], "the auxiliary chain was probed unnecessarily"


def test_vision_analyze_is_registered_with_the_widened_gate():
    entry = registry._tools["vision_analyze"]
    assert entry.check_fn is vt.check_image_analysis_requirements, (
        "vision_analyze is back on the auxiliary-only gate, which hides it "
        "from every user whose main model is itself vision-capable"
    )


def test_video_analyze_keeps_the_auxiliary_only_gate():
    """It has no native route, so an auxiliary client is genuinely required."""
    entry = registry._tools["video_analyze"]
    assert entry.check_fn is vt.check_vision_requirements


def test_the_handler_takes_the_native_route_when_it_is_available(
    monkeypatch, native_available
):
    """The gate and the handler must agree on which route is in play."""
    import asyncio

    taken: dict = {}

    async def _native(*_args, **_kwargs):
        taken["native"] = True
        return {"_multimodal": True, "content": []}

    monkeypatch.setattr(vt, "_vision_analyze_native", _native)
    # The auxiliary route would need a provider; taking it here is the failure.
    async def _legacy(*_args, **_kwargs):
        taken["legacy"] = True
        return "auxiliary route"

    monkeypatch.setattr(vt, "vision_analyze_tool", _legacy)

    asyncio.run(vt._handle_vision_analyze({"image_url": "x.png", "question": "?"}))
    assert taken.get("native") is True
    assert "legacy" not in taken


def test_a_real_image_makes_it_through_the_native_route(monkeypatch, tmp_path):
    """End to end on the route the gate fix unblocks."""
    pytest.importorskip("PIL")
    from PIL import Image

    import asyncio

    monkeypatch.setattr(vt, "_should_use_native_vision_fast_path", lambda: True)
    path = tmp_path / "swatch.png"
    Image.new("RGB", (48, 32), (180, 84, 26)).save(path)

    result = asyncio.run(
        vt._handle_vision_analyze(
            {"image_url": str(path), "question": "what colour is this"}
        )
    )
    assert isinstance(result, dict), f"native route returned {result!r}"
    assert result.get("_multimodal") is True
    kinds = [part.get("type") for part in result.get("content", [])]
    assert kinds == ["text", "image_url"]
    url = next(
        part["image_url"]["url"]
        for part in result["content"]
        if part.get("type") == "image_url"
    )
    assert url.startswith("data:image/png;base64,")


def test_a_corrupt_image_is_still_rejected(monkeypatch, tmp_path):
    """Widening the gate must not widen what counts as an image.

    A vision tool-result is baked into immutable conversation history and
    re-sent every turn, so embedding undecodable bytes wedges the session for
    good — the magic-byte header alone is not enough to accept them.
    """
    pytest.importorskip("PIL")

    import asyncio

    monkeypatch.setattr(vt, "_should_use_native_vision_fast_path", lambda: True)
    path = tmp_path / "broken.png"
    # A correct PNG signature followed by rubbish.
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    result = asyncio.run(
        vt._handle_vision_analyze({"image_url": str(path), "question": "?"})
    )
    assert isinstance(result, str), "corrupt bytes were embedded as an image"
    assert "not a recognized image" in result
