"""Two places must not carry a message of the day.

A rotating suggestion is fine on a splash screen. It is not fine in the box
the user is about to type into, and it is not fine appended to a status
readout — in both cases it sits between the user and the thing they came for,
it differs every time, and after the first session it is only noise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── The composer (terminal input) ────────────────────────────────────────

def test_the_empty_composer_offers_no_suggestion():
    """An idle, empty input box shows nothing.

    It used to carry a rotating suggested task ("Summarize what's in this
    folder"), which reads as pre-filled text and vanishes the moment the user
    types.
    """
    src = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "_composer_placeholder" not in src, (
        "the rotating composer placeholder is back"
    )
    assert "get_random_composer_placeholder" not in src


def test_the_tip_corpus_is_gone_entirely():
    """Nothing renders a tip on any surface any more, so the corpus went too.

    A module of two hundred suggestion strings that no code path reads is
    dead weight that reads as a feature to the next person editing here.
    """
    assert not (ROOT / "curie_cli" / "tips.py").exists(), (
        "curie_cli/tips.py is back; nothing should render tips"
    )


def test_the_terminal_start_up_carries_no_tip_and_no_welcome_line():
    """Starting the CLI shows the banner and the prompt. Nothing else.

    The welcome line ("Welcome to Curie Agent! Type your message…") sat
    directly above the prompt that was already waiting to be typed into, and
    the startup tip rotated a different suggestion into the same spot every
    session.
    """
    src = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "get_random_tip" not in src, "the terminal shows tips again"
    assert "✦ Tip:" not in src, "a tip line is back in the terminal"
    assert "Welcome to Curie Agent" not in src, (
        "the welcome line is back above the prompt"
    )


def test_new_session_in_the_terminal_carries_no_tip():
    """`/new` clears the screen and says so; it does not editorialise."""
    src = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "Show a random tip on new session" not in src


def test_state_hints_in_the_composer_are_kept():
    """Removing the MOTD must not take the real hints with it.

    A parked draft, the voice-record key, and the sudo/secret prompts all
    describe the *current state* and belong in the composer.
    """
    src = (ROOT / "cli.py").read_text(encoding="utf-8")
    assert "placeholder_hint()" in src, "the parked-draft hint was removed too"
    assert "_voice_record_key_label()" in src, "the voice hint was removed too"


# ── The gateway session card ─────────────────────────────────────────────

def test_the_session_card_carries_no_tip():
    """`/new` reports the session; it does not also editorialise."""
    src = (ROOT / "gateway" / "slash_commands.py").read_text(encoding="utf-8")
    assert "gateway.reset.tip" not in src
    assert "get_random_tip" not in src, (
        "the gateway no longer shows tips on any command"
    )


def test_the_session_card_still_reports_the_session():
    """The readout itself — model, provider, context — must survive."""
    src = (ROOT / "gateway" / "slash_commands.py").read_text(encoding="utf-8")
    assert "session_info" in src
    assert 'EphemeralReply(f"{header}\\n\\n{session_info}")' in src


@pytest.mark.parametrize(
    "locale", sorted(p.name for p in (ROOT / "locales").glob("*.yaml"))
)
def test_no_locale_still_defines_the_reset_tip(locale):
    """A leftover translation is a string nothing renders — dead weight."""
    text = (ROOT / "locales" / locale).read_text(encoding="utf-8")
    assert not re.search(r"^    tip:\s", text, re.M), (
        f"{locale} still defines gateway.reset.tip"
    )


def test_every_locale_still_parses():
    import yaml

    for path in sorted((ROOT / "locales").glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            assert yaml.safe_load(handle) is not None, f"{path.name} is empty"
