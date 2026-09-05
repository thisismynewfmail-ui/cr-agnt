"""The bridge must build a real agent with the real constructor.

The first version of this bridge invented two keyword arguments and two
callback names, none of which exist. The result was a console that looked
finished and failed on the first message with
``TypeError: AIAgent.__init__() got an unexpected keyword argument
'max_turns'`` — a whole feature broken by four unchecked guesses.

These tests check the bridge against the constructor's actual signature, so a
rename on either side fails here instead of in front of a user.
"""

from __future__ import annotations

import inspect

import pytest

from curie_cli.bench_ui.agent_bridge import AgentBridge, TurnEvent


@pytest.fixture(scope="module")
def agent_params() -> set[str]:
    from run_agent import AIAgent

    return set(inspect.signature(AIAgent.__init__).parameters)


def test_every_constructor_keyword_the_bridge_uses_exists(agent_params, monkeypatch):
    """Catch invented keywords before a user does."""
    import curie_cli.bench_ui.agent_bridge as bridge_mod

    captured: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("run_agent.AIAgent", _Recorder)
    try:
        bridge_mod._build_agent()
    except Exception as exc:  # provider resolution may fail in a bare env
        pytest.skip(f"runtime provider unavailable here: {exc}")

    unknown = set(captured) - agent_params
    assert not unknown, (
        f"_build_agent passes keywords AIAgent does not accept: {sorted(unknown)}"
    )


def test_turn_limit_uses_the_constructor_spelling(agent_params):
    """Config says ``max_turns``; the constructor says ``max_iterations``."""
    assert "max_iterations" in agent_params
    assert "max_turns" not in agent_params, (
        "if the constructor gained max_turns, simplify _build_agent"
    )


@pytest.mark.parametrize(
    "callback", ["reasoning_callback", "tool_gen_callback", "status_callback"]
)
def test_every_callback_the_bridge_wires_exists(callback, agent_params):
    """A hook that does not exist is a hook that silently never fires."""
    assert callback in agent_params, (
        f"{callback} is not an AIAgent constructor parameter; the bridge "
        f"would set an attribute nothing ever reads"
    )


def test_stream_callback_is_the_documented_chat_argument():
    from run_agent import AIAgent

    params = inspect.signature(AIAgent.chat).parameters
    assert "stream_callback" in params, (
        "the bridge streams deltas through agent.chat(stream_callback=...)"
    )


def test_retired_callback_names_are_never_assigned():
    """The two invented names must not creep back into executable code.

    Checked against the parsed AST rather than the file text, so the comment
    explaining why they were wrong does not trip the test that enforces it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(
        Path(inspect.getsourcefile(AgentBridge)).read_text(encoding="utf-8")
    )
    retired = {"reasoning_delta_callback", "tool_gen_started_callback"}
    used = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not (retired & used), (
        f"{sorted(retired & used)} do not exist on AIAgent"
    )


def test_describe_is_empty_before_the_agent_exists():
    """The panel renders dashes rather than inventing plausible numbers."""
    assert AgentBridge().describe() == {}


def test_a_construction_failure_is_reported_not_raised(monkeypatch):
    """A broken provider config must surface as a notice, not a crash."""
    import curie_cli.bench_ui.agent_bridge as bridge_mod

    def _boom(**_kwargs):
        raise RuntimeError("no provider configured")

    monkeypatch.setattr(bridge_mod, "_build_agent", _boom)
    bridge = AgentBridge()

    assert bridge.ensure_agent() is False
    assert "no provider configured" in (bridge.load_error or "")


def test_a_failed_turn_still_emits_done(monkeypatch):
    """Without a terminating event the console would spin forever."""
    import curie_cli.bench_ui.agent_bridge as bridge_mod

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        def chat(self, message, stream_callback=None):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(bridge_mod, "_build_agent", _Agent)
    bridge = AgentBridge()
    bridge._run_turn("hello")

    kinds = [e.kind for e in bridge.drain()]
    assert "error" in kinds
    assert kinds[-1] == "done", f"turn did not terminate: {kinds}"
