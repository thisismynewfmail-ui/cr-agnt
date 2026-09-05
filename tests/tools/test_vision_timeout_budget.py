"""The vision budget has to be the thing that actually bounds a vision call.

The reported failure: image analysis worked when a photo arrived over Telegram
and timed out in the terminal and the bench console. Both run the same tool
against the same model, so "vision is broken in the terminal" was never the
shape of it — the two paths differ in *who calls the tool*.

  * The gateway calls ``vision_analyze_tool`` directly
    (``GatewayRunner._enrich_message_with_vision``), so the only bound on it
    is ``auxiliary.vision.timeout``.
  * The terminal and the console let the model call it as a tool, which goes
    through ``agent/tool_executor.py`` — and that applied a generic 420s
    deadline to every tool alike.

So a vision call was cancelled at seven minutes however high
``auxiliary.vision.timeout`` was set, and the setting looked inert. These
tests pin the fix from both ends: the executor's deadline for a vision call
now follows the vision budget, and the vision budget is one wall-clock number
for the whole call rather than a fresh one for each attempt inside it.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import tool_executor
from tools import vision_tools


def _config(**vision) -> dict:
    return {"auxiliary": {"vision": vision}}


# ── The bug: the executor out-ranked the budget ──────────────────────────

def test_the_generic_deadline_no_longer_cancels_a_vision_call_early():
    """The regression. 420s was cutting a call the config allowed hours for."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=7200)):
        generic = 420.0
        vision = tool_executor._resolve_tool_call_timeout("vision_analyze", generic)
        assert vision is not None
        assert vision > generic, (
            "the vision tool is still bounded by the generic tool deadline, "
            "so auxiliary.vision.timeout does nothing in the terminal"
        )
        assert vision >= 7200.0, (
            f"the vision call gets {vision}s of a 7200s budget"
        )


def test_the_two_numbers_cannot_drift():
    """Raising the budget raises the deadline. One number, read in one place."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=600)):
        short = tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0)
    with patch("curie_cli.config.load_config", return_value=_config(timeout=9000)):
        long = tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0)
    assert short is not None and long is not None
    assert long > short
    assert short >= 600.0 and long >= 9000.0


def test_an_ordinary_tool_keeps_the_generic_deadline():
    """The fix is a claim a named tool makes, not a blanket raise."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=7200)):
        for name in ("read_file", "terminal", "web_search", ""):
            assert (
                tool_executor._resolve_tool_call_timeout(name, 420.0) == 420.0
            ), name


def test_the_generic_deadline_still_wins_when_it_is_the_larger_one():
    """A user who raised the generic bound asked for more time, not less."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=60)):
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 9000.0)
            == 9000.0
        )


@pytest.mark.parametrize("tool", ["vision_analyze", "video_analyze"])
def test_both_vision_tools_claim_the_budget(tool):
    with patch("curie_cli.config.load_config", return_value=_config(timeout=7200)):
        assert tool_executor._resolve_tool_call_timeout(tool, 420.0) >= 7200.0


# ── The batch has to hold its slowest member ─────────────────────────────

def test_a_batch_holding_a_vision_call_waits_for_it():
    """One deadline covers the batch, so it must be the longest one claimed.

    Cutting the batch at the generic deadline would cancel the vision call
    *and* every tool running beside it.
    """
    with patch("curie_cli.config.load_config", return_value=_config(timeout=7200)):
        with patch.object(
            tool_executor, "_resolve_concurrent_tool_timeout", return_value=420.0
        ):
            mixed = tool_executor._resolve_batch_tool_timeout(
                ["read_file", "vision_analyze", "terminal"]
            )
            plain = tool_executor._resolve_batch_tool_timeout(
                ["read_file", "terminal"]
            )
    assert mixed >= 7200.0
    assert plain == 420.0, "a batch with no vision call must be unchanged"


def test_a_batch_is_unbounded_when_a_member_is():
    with patch("curie_cli.config.load_config", return_value=_config(timeout=0)):
        with patch.object(
            tool_executor, "_resolve_concurrent_tool_timeout", return_value=None
        ):
            assert (
                tool_executor._resolve_batch_tool_timeout(["vision_analyze"]) is None
            )


# ── What the executor is told, end to end ────────────────────────────────

def test_the_sequential_path_asks_for_the_tool_it_is_running():
    """The deadline is resolved per call, not once for every tool alike."""
    import inspect

    source = inspect.getsource(
        tool_executor._run_sequential_tool_execution_middleware
    )
    assert "_resolve_tool_call_timeout(" in source, (
        "the sequential path resolves one deadline for every tool again"
    )
    assert "function_name" in source.split("_resolve_tool_call_timeout(")[1][:80]


def test_the_concurrent_path_asks_for_the_batch_it_is_running():
    import inspect

    source = inspect.getsource(tool_executor.execute_tool_calls_concurrent)
    assert "_resolve_batch_tool_timeout(" in source


# ── Configuration a person can actually get wrong ────────────────────────

def _with_timeouts(section: dict, **vision):
    """Patch both config readers: the dotted resolver uses the read-only one."""
    config = _config(**vision)
    config["timeouts"] = section
    return (
        patch("curie_cli.config.load_config", return_value=config),
        patch("curie_cli.config.load_config_readonly", return_value=config),
    )


def test_an_explicit_per_tool_override_wins():
    """``timeouts.tools.per_tool.<name>`` beats the tool's own budget."""
    read, readonly = _with_timeouts(
        {"tools": {"per_tool": {"vision_analyze": 30}}}, timeout=7200
    )
    with read, readonly:
        assert tool_executor._resolve_owned_tool_timeout("vision_analyze") == 30.0
        # ...and the generic floor still applies, because a user who raised
        # the generic bound asked for more time.
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0) == 420.0
        )
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 10.0) == 30.0
        )


def test_an_explicit_per_tool_zero_is_unbounded_not_absent():
    """The distinction ``resolve_timeout`` erases and this layer has to keep."""
    read, readonly = _with_timeouts(
        {"tools": {"per_tool": {"vision_analyze": 0}}}, timeout=7200
    )
    with read, readonly:
        assert (
            tool_executor._resolve_owned_tool_timeout("vision_analyze")
            == tool_executor.UNBOUNDED_TOOL_TIMEOUT
        )
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0) is None
        ), "an explicit 'no ceiling' fell back under the generic deadline"


def test_an_unbounded_budget_makes_the_deadline_unbounded():
    """``0`` means no ceiling — the generic one must not become the real limit.

    Getting this wrong is the reported bug one layer down: a budget saying
    "do not cut this off" would have been cut off at 420s anyway.
    """
    with patch("curie_cli.config.load_config", return_value=_config(timeout=0)):
        assert (
            tool_executor._resolve_owned_tool_timeout("vision_analyze")
            == tool_executor.UNBOUNDED_TOOL_TIMEOUT
        )
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0) is None
        )
        # An ordinary tool is untouched by the vision budget being unbounded.
        assert (
            tool_executor._resolve_tool_call_timeout("read_file", 420.0) == 420.0
        )


@pytest.mark.parametrize("bad", ["soon", None, True, [7200], {"seconds": 7200}])
def test_a_malformed_budget_falls_back_instead_of_raising(bad):
    """These come out of a hand-edited YAML file."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=bad)):
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0) == 420.0
        )


def test_an_unreadable_config_does_not_shorten_the_leash():
    def boom(*_a, **_k):
        raise RuntimeError("config.yaml is a directory")

    with patch("curie_cli.config.load_config", side_effect=boom):
        assert (
            tool_executor._resolve_tool_call_timeout("vision_analyze", 420.0) == 420.0
        )


# ── The budget itself ────────────────────────────────────────────────────

def test_the_shipped_default_is_two_hours():
    assert vision_tools.DEFAULT_VISION_TIMEOUT_SECONDS == 7200.0

    from curie_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["auxiliary"]["vision"]["timeout"] == 7200


def test_both_paths_read_the_same_key():
    """They used to carry different hardcoded fallbacks (120s vs 180s)."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=900)):
        assert vision_tools.resolve_vision_timeout() == 900.0
        assert (
            vision_tools.resolve_vision_timeout(
                minimum=vision_tools.MIN_VIDEO_TIMEOUT_SECONDS
            )
            == 900.0
        )
    # ...and the video floor applies to a value below it, both being reads of
    # one key rather than two defaults that can disagree.
    with patch("curie_cli.config.load_config", return_value=_config(timeout=30)):
        assert vision_tools.resolve_vision_timeout() == 30.0
        assert (
            vision_tools.resolve_vision_timeout(
                minimum=vision_tools.MIN_VIDEO_TIMEOUT_SECONDS
            )
            == vision_tools.MIN_VIDEO_TIMEOUT_SECONDS
        )


@pytest.mark.parametrize("bad", ["soon", True, False, [900], {"seconds": 900}])
def test_a_malformed_budget_resolves_to_the_default(bad):
    """A YAML ``true`` becoming a one-second budget is the strangest of these."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=bad)):
        assert (
            vision_tools.resolve_vision_timeout()
            == vision_tools.DEFAULT_VISION_TIMEOUT_SECONDS
        )


def test_the_resolver_survives_a_config_it_cannot_read():
    def boom(*_a, **_k):
        raise RuntimeError("no config")

    with patch("curie_cli.config.load_config", side_effect=boom):
        assert (
            vision_tools.resolve_vision_timeout()
            == vision_tools.DEFAULT_VISION_TIMEOUT_SECONDS
        )
        assert vision_tools.resolve_vision_temperature() == 0.1


# ── One budget for the whole call, not one per attempt ───────────────────

def test_the_budget_counts_down_across_attempts():
    budget = vision_tools._CallBudget(100.0)
    first = budget.remaining()
    assert first is not None and first == pytest.approx(100.0, abs=1.0)
    budget._started -= 60.0  # 60 seconds have passed
    second = budget.remaining()
    assert second == pytest.approx(40.0, abs=1.0)
    assert second < first, (
        "each attempt gets a fresh full timeout, so the real worst case is a "
        "multiple of what the setting says"
    )


def test_a_spent_budget_does_not_start_another_attempt():
    budget = vision_tools._CallBudget(100.0)
    assert not budget.spent()
    budget._started -= 95.0
    assert budget.spent(), (
        "a retry starting with five seconds produces a timeout, not an answer"
    )


def test_an_unbounded_budget_is_never_spent():
    budget = vision_tools._CallBudget(0.0)
    assert budget.unbounded
    assert budget.remaining() is None
    assert not budget.spent()


VALID_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_the_retry_gets_what_is_left_and_not_a_fresh_budget(tmp_path):
    """The whole point of the shared budget, exercised through the tool."""
    image = tmp_path / "scan.png"
    image.write_bytes(VALID_PNG)

    empty = MagicMock()
    empty.choices = [MagicMock()]
    empty.choices[0].message.content = ""
    answered = MagicMock()
    answered.choices = [MagicMock()]
    answered.choices[0].message.content = "A page of small print."

    seen: list[float] = []

    async def _call(**kwargs):
        seen.append(kwargs["timeout"])
        # Spend a measurable slice of the budget, so "what is left" and "the
        # whole thing again" are different numbers rather than the same one
        # to six decimal places — without this the assertion below passes
        # against a retry handed a fresh full budget.
        await asyncio.sleep(0.05)
        # First attempt returns nothing, which is what triggers the retry.
        return empty if len(seen) == 1 else answered

    with (
        patch("curie_cli.config.load_config", return_value=_config(timeout=600)),
        patch(
            "tools.vision_tools._image_to_base64_data_url",
            return_value="data:image/png;base64,abc",
        ),
        patch("tools.vision_tools.async_call_llm", new=AsyncMock(side_effect=_call)),
    ):
        result = json.loads(
            await vision_tools.vision_analyze_tool(
                str(image), "what does it say?", "test/model"
            )
        )

    assert result["success"] is True
    assert len(seen) == 2, f"expected one retry, got {len(seen)} attempts"
    assert seen[0] == pytest.approx(600.0, abs=2.0)
    assert seen[1] < seen[0], (
        "the retry was handed a fresh full budget instead of the remainder — "
        f"attempt timeouts were {seen}"
    )
    # And what it lost is roughly what the first attempt spent.
    assert (seen[0] - seen[1]) == pytest.approx(0.05, abs=0.5)


# ── The download bound the video path could not configure ────────────────

def test_the_video_download_bound_is_configurable_and_floored():
    with patch("curie_cli.config.load_config", return_value=_config(download_timeout=45)):
        assert vision_tools.download_timeout() == 45.0
        assert (
            vision_tools.download_timeout(
                vision_tools.MIN_VIDEO_DOWNLOAD_TIMEOUT_SECONDS
            )
            == vision_tools.MIN_VIDEO_DOWNLOAD_TIMEOUT_SECONDS
        )
    with patch("curie_cli.config.load_config", return_value=_config(download_timeout=900)):
        assert vision_tools.download_timeout() == 900.0
        assert (
            vision_tools.download_timeout(
                vision_tools.MIN_VIDEO_DOWNLOAD_TIMEOUT_SECONDS
            )
            == 900.0
        ), "a configured value above the floor must still win"


def test_the_video_download_no_longer_hardcodes_a_minute():
    import inspect

    source = inspect.getsource(vision_tools._download_video)
    assert "timeout=60.0" not in source, (
        "a video that takes over a minute to fetch fails with no setting that "
        "can change it"
    )
    assert "download_timeout(" in source


# ── End to end: the deadline reaching the wait loop ──────────────────────
#
# The tests above assert the arithmetic. This one runs the sequential
# executor for real — the code path a terminal turn takes — with a generic
# deadline shorter than the tool takes, and shows that the ordinary tool is
# cancelled where the vision tool is not. Small numbers, same mechanism.


class _FakeAgent:
    def __init__(self):
        import threading

        self._tool_worker_threads = set()
        self._tool_worker_threads_lock = threading.Lock()
        self._interrupt_requested = False
        self.activity = []

    def _touch_activity(self, msg):
        self.activity.append(msg)


def _run_one(monkeypatch, tool_name: str, work_seconds: float):
    """Run one tool through the sequential executor; return its result."""
    import time as _time

    from agent.tool_executor import (
        _ManagedToolResult,
        _run_sequential_tool_execution_middleware,
    )

    def _slow_middleware(agent_arg, **kwargs):
        _time.sleep(work_seconds)
        return _ManagedToolResult(
            result="finished", args={}, middleware_trace=[],
            blocked=False, dispatched=True,
        )

    monkeypatch.setattr(
        tool_executor, "_run_agent_tool_execution_middleware", _slow_middleware
    )
    monkeypatch.setattr(tool_executor, "_SEQUENTIAL_INTERRUPT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(
        tool_executor, "_emit_terminal_post_tool_call", lambda agent, **kw: None
    )
    # A generic deadline far too short for the work, and a vision budget that
    # is comfortably long enough. This is the shape of the reported bug with
    # the units changed: 420s against a model that needed longer.
    monkeypatch.setattr(
        tool_executor, "_resolve_sequential_tool_timeout", lambda: 0.15
    )
    return _run_sequential_tool_execution_middleware(
        _FakeAgent(),
        function_name=tool_name,
        function_args={},
        effective_task_id="t",
        tool_call_id="call-1",
        execute=lambda *a, **k: None,
    )


def test_an_ordinary_tool_is_still_cut_at_the_generic_deadline(monkeypatch):
    """The control. Without this the next test proves nothing."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=30)):
        result = _run_one(monkeypatch, "read_file", work_seconds=1.0)
    assert "timed out" in str(result.result).lower(), (
        f"the generic deadline stopped bounding ordinary tools: {result.result!r}"
    )


def test_a_vision_call_runs_past_the_generic_deadline(monkeypatch):
    """The fix, through the real executor path a terminal turn takes."""
    with patch("curie_cli.config.load_config", return_value=_config(timeout=30)):
        result = _run_one(monkeypatch, "vision_analyze", work_seconds=1.0)
    assert result.result == "finished", (
        "the vision call was cancelled by the generic tool deadline again — "
        f"got {result.result!r}"
    )
