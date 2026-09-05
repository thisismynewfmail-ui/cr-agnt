"""The session overlay comes from ``agent.system_prompt`` and nowhere else.

The agent's voice lives in ``SOUL.md``.  Config used to carry a competing
description of it — ``display.personality`` selecting from a built-in table,
extensible via ``agent.personalities`` — and whichever source won, the other
was silently ignored.  Both keys are gone; ``agent.system_prompt`` remains as
the user-authored manual overlay, which is not a personality: it ships with no
presets and nothing in the codebase writes it.
"""

from curie_cli.soul_overlay import prompt_text, resolve_ephemeral_system_prompt


def test_resolve_returns_the_user_owned_manual_prompt():
    cfg = {"agent": {"system_prompt": "Answer in metric units."}}
    assert resolve_ephemeral_system_prompt(cfg) == "Answer in metric units."


def test_resolve_returns_empty_when_unset():
    """No overlay means SOUL.md alone describes the agent."""
    assert resolve_ephemeral_system_prompt({}) == ""
    assert resolve_ephemeral_system_prompt({"agent": {}}) == ""
    assert resolve_ephemeral_system_prompt({"agent": {"system_prompt": ""}}) == ""
    assert resolve_ephemeral_system_prompt(None) == ""


def test_retired_personality_keys_are_inert():
    """A hand-edited config carrying the old keys must not resurrect them.

    The v40 migration strips them, but a user can always paste them back, and
    a config that predates the migration can be read by a tool that skips it.
    Either way they have to do nothing.
    """
    cfg = {
        "display": {"personality": "kawaii"},
        "agent": {
            "system_prompt": "",
            "personalities": {"kawaii": "be extremely cute"},
        },
        "personalities": {"pirate": "arrr"},
    }
    assert resolve_ephemeral_system_prompt(cfg) == ""


def test_retired_keys_never_shadow_the_manual_prompt():
    cfg = {
        "display": {"personality": "kawaii"},
        "agent": {
            "system_prompt": "Answer in metric units.",
            "personalities": {"kawaii": "be extremely cute"},
        },
    }
    assert resolve_ephemeral_system_prompt(cfg) == "Answer in metric units."


def test_resolve_tolerates_non_dict_intermediate_nodes():
    assert resolve_ephemeral_system_prompt({"agent": "nonsense"}) == ""
    assert resolve_ephemeral_system_prompt({"agent": None}) == ""


def test_prompt_text_normalizes_yaml_shapes():
    assert prompt_text("  hi  ") == "hi"
    assert prompt_text(None) == ""
    assert prompt_text(["a", "", "  b  "]) == "a\nb"
    assert prompt_text(7) == "7"


def test_resolve_joins_a_yaml_block_list():
    """``system_prompt:`` written as a YAML list is a documented shape."""
    cfg = {"agent": {"system_prompt": ["First rule.", "Second rule."]}}
    assert resolve_ephemeral_system_prompt(cfg) == "First rule.\nSecond rule."
