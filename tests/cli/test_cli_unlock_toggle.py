"""Regression tests for the CLI ``/unlock`` in-chat toggle.

Pre-fix bug (issue #33925): ``cli.CurieCLI._toggle_unlock`` mutated only
``os.environ["CURIE_UNLOCK_MODE"]``. That env var is captured once at
module-import time into ``tools.approval._UNLOCK_MODE_FROZEN`` (security
hardening: stops prompt-injected skills from flipping the bypass mid-run),
so the post-startup toggle was a silent no-op. ``/unlock`` advertised "UNLOCK ON"
in the status bar while every dangerous command still hit the approval
prompt. Only ``curie --unlock`` (process-start env), ``CURIE_UNLOCK_MODE=1``,
and ``curie config set approvals.mode off`` actually bypassed.

The fix routes the CLI toggle through ``enable_session_unlock`` /
``disable_session_unlock`` (matching the gateway and TUI ``/unlock`` paths) and
binds ``self.session_id`` as the active approval session key around each
``run_conversation`` call so ``is_current_session_unlock_enabled()`` resolves
against the same key the toggle writes under.

We test ``_toggle_unlock`` and ``_is_session_unlock_active`` as unbound methods
against a minimal stand-in object that exposes only the attribute they
read (``session_id``). This avoids the heavy ``CurieCLI`` construction
path used in ``test_cli_init.py``, which is incompatible with this test
file's path layout — ``CurieCLI.__init__`` imports a lot of optional
state we don't need here.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import tools.approval as approval_module
from cli import CurieCLI


SESSION_KEY = "test-cli-unlock-session"


@pytest.fixture(autouse=True)
def _clear_approval_state(monkeypatch):
    """Clear the UNLOCK bypass + env var around every test so cases are independent."""
    monkeypatch.delenv("CURIE_UNLOCK_MODE", raising=False)
    # The value is intentionally frozen at tools.approval import time. Local
    # Curie-driven test runs may inherit CURIE_UNLOCK_MODE=1 from the parent
    # agent process, so make the default test state hermetic; the one test that
    # covers startup-frozen UNLOCK explicitly patches it back to True.
    monkeypatch.setattr(approval_module, "_UNLOCK_MODE_FROZEN", False)
    approval_module.clear_session(SESSION_KEY)
    approval_module.clear_session("default")
    yield
    approval_module.clear_session(SESSION_KEY)
    approval_module.clear_session("default")


def _make_stand_in(session_id: str = SESSION_KEY) -> SimpleNamespace:
    """Minimal stand-in exposing only ``session_id``.

    ``_toggle_unlock`` and ``_is_session_unlock_active`` are both pure methods
    that only read ``self.session_id`` — no other CLI state is touched.
    Calling them as unbound functions against this stand-in is equivalent
    to invoking them on a fully-constructed ``CurieCLI`` for the
    behaviour under test, and avoids the brittle prompt_toolkit / config
    stubbing required to instantiate ``CurieCLI`` from this test file.
    """
    return SimpleNamespace(session_id=session_id)


class TestToggleUnlockIsSessionScoped:
    """The CLI /unlock handler must mutate the session-unlock set, not the env var.

    The env var path is dead-on-arrival because ``_UNLOCK_MODE_FROZEN`` is
    captured once at module import, long before the CLI's ``/unlock`` command
    can run.
    """

    def test_toggle_unlock_enables_session_bypass(self):
        stand_in = _make_stand_in()

        assert approval_module.is_session_unlock_enabled(SESSION_KEY) is False

        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)

        assert approval_module.is_session_unlock_enabled(SESSION_KEY) is True

    def test_toggle_unlock_disables_session_bypass_on_second_call(self):
        stand_in = _make_stand_in()
        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)  # ON
            assert approval_module.is_session_unlock_enabled(SESSION_KEY) is True
            CurieCLI._toggle_unlock(stand_in)  # OFF
            assert approval_module.is_session_unlock_enabled(SESSION_KEY) is False



    def test_two_independent_sessions_are_isolated(self):
        """``/unlock`` toggled in one session must not bypass approvals in
        another session — mirrors the gateway-side invariant."""
        cli_a = _make_stand_in(session_id="session-unlock-a")
        cli_b = _make_stand_in(session_id="session-unlock-b")

        try:
            with patch("cli._cprint"):
                CurieCLI._toggle_unlock(cli_a)

            assert approval_module.is_session_unlock_enabled("session-unlock-a") is True
            assert approval_module.is_session_unlock_enabled("session-unlock-b") is False
        finally:
            approval_module.clear_session("session-unlock-a")
            approval_module.clear_session("session-unlock-b")


class TestIsSessionUnlockActiveHelper:
    """The status-bar helper must read the live session-unlock state, not the
    env var (which is the bug class this PR fixes)."""

    def test_helper_reflects_toggle(self):
        stand_in = _make_stand_in()

        assert CurieCLI._is_session_unlock_active(stand_in) is False

        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)

        assert CurieCLI._is_session_unlock_active(stand_in) is True

        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)

        assert CurieCLI._is_session_unlock_active(stand_in) is False

    def test_helper_honors_frozen_unlock_mode(self):
        """``curie --unlock`` sets ``CURIE_UNLOCK_MODE`` before tool imports, so
        ``_UNLOCK_MODE_FROZEN`` ends up True. The status bar should still
        reflect UNLOCK on in that case even when the session toggle is off."""
        stand_in = _make_stand_in()

        with patch.object(approval_module, "_UNLOCK_MODE_FROZEN", True):
            assert CurieCLI._is_session_unlock_active(stand_in) is True

    def test_toggle_under_frozen_unlock_reports_locked_and_stays_on(self):
        """With process-level UNLOCK frozen ON, /unlock must NOT claim approvals
        are back. Pre-fix, the second toggle printed "UNLOCK mode OFF —
        dangerous commands will require approval" while the frozen flag kept
        auto-approving everything — a false safety claim."""
        stand_in = _make_stand_in()

        printed = []
        with patch.object(approval_module, "_UNLOCK_MODE_FROZEN", True):
            with patch("cli._cprint", side_effect=lambda msg: printed.append(msg)):
                CurieCLI._toggle_unlock(stand_in)
                CurieCLI._toggle_unlock(stand_in)

            # Still effectively ON, and no session-level state was flipped.
            assert CurieCLI._is_session_unlock_active(stand_in) is True
            assert not approval_module.is_session_unlock_enabled(SESSION_KEY)

        joined = "\n".join(printed)
        assert "locked ON" in joined
        assert "will require approval" not in joined


class TestToggleUnlockEndToEnd:
    """End-to-end: a dangerous command must auto-approve through the same
    ``check_all_command_guards`` path the terminal tool uses."""

    def test_toggle_unlock_bypasses_dangerous_command_check(self):
        stand_in = _make_stand_in()

        token = approval_module.set_current_session_key(SESSION_KEY)
        try:
            with patch("cli._cprint"):
                CurieCLI._toggle_unlock(stand_in)  # UNLOCK ON

            result = approval_module.check_all_command_guards(
                "rm -rf /tmp/scratch-xyzzy", "local",
            )
            assert result["approved"] is True, (
                f"UNLOCK toggle should auto-approve dangerous commands, got: {result}"
            )
        finally:
            approval_module.reset_current_session_key(token)




class TestSessionRotationTransfersUnlock:
    """When the CLI's ``session_id`` rotates mid-run (``/branch``, auto
    compression continuation), UNLOCK state keyed under the old id must move
    to the new id. Otherwise the user's ``/unlock ON`` silently reverts on
    the next turn — the same UX failure mode this PR set out to fix.
    Mirrors ``tui_gateway/server.py`` ~line 1297-1305."""

    def test_transfer_moves_unlock_to_new_session(self):
        stand_in = _make_stand_in(session_id="old-id")
        try:
            approval_module.enable_session_unlock("old-id")
            assert approval_module.is_session_unlock_enabled("old-id") is True

            CurieCLI._transfer_session_unlock(stand_in, "old-id", "new-id")

            assert approval_module.is_session_unlock_enabled("new-id") is True
            assert approval_module.is_session_unlock_enabled("old-id") is False
        finally:
            approval_module.clear_session("old-id")
            approval_module.clear_session("new-id")



    def test_transfer_handles_empty_inputs_safely(self):
        stand_in = _make_stand_in(session_id="x")
        # Both directions of empty input should be safe no-ops; nothing
        # to transfer from "" / to "".
        CurieCLI._transfer_session_unlock(stand_in, "", "new")
        CurieCLI._transfer_session_unlock(stand_in, "old", "")
        # Neither key should have been touched.
        assert approval_module.is_session_unlock_enabled("new") is False
        assert approval_module.is_session_unlock_enabled("old") is False
