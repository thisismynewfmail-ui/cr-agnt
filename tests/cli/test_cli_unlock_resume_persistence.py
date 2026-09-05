"""Regression tests: UNLOCK mode persists across ``curie --resume``.

Pre-fix bug: the ``/unlock`` toggle (and the process-start ``--unlock`` flag)
lived only in the in-memory ``tools.approval._session_unlock`` set / the
frozen env var. Resuming a session in a fresh process silently reverted the
bypass — dangerous commands started prompting again even though the user had
UNLOCK on for that session.

The fix persists a ``unlock_mode`` flag inside the session row's
``model_config`` JSON:

- ``SessionDB.set_session_unlock`` merges the flag (preserving lineage markers
  like ``_branched_from``), written by the CLI ``/unlock`` toggle.
- ``AIAgent._ensure_db_session`` carries a live session bypass (or a frozen
  ``--unlock`` launch, via agent_init) into the creation-time model_config.
- ``CurieCLI._restore_session_unlock`` reads the flag on every resume path
  and re-enables the in-memory bypass.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import tools.approval as approval_module
from cli import CurieCLI
from curie_state import SessionDB


SESSION_ID = "unlock_persist_session"


@pytest.fixture(autouse=True)
def _hermetic_unlock(monkeypatch):
    monkeypatch.delenv("CURIE_UNLOCK_MODE", raising=False)
    monkeypatch.setattr(approval_module, "_UNLOCK_MODE_FROZEN", False)
    approval_module.clear_session(SESSION_ID)
    yield
    approval_module.clear_session(SESSION_ID)


@pytest.fixture
def db(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    try:
        d.close()
    except Exception:
        pass


class TestSessionDbUnlockFlag:
    def test_set_and_read_round_trip(self, db):
        db.create_session(session_id=SESSION_ID, source="cli", model="m")
        db.set_session_unlock(SESSION_ID, True)
        meta = db.get_session(SESSION_ID)
        assert SessionDB.session_unlock_enabled(meta) is True

        db.set_session_unlock(SESSION_ID, False)
        meta = db.get_session(SESSION_ID)
        assert SessionDB.session_unlock_enabled(meta) is False

    def test_merge_preserves_existing_model_config_keys(self, db):
        db.create_session(
            session_id=SESSION_ID,
            source="cli",
            model="m",
            model_config={"max_iterations": 42, "_branched_from": "parent_x"},
        )
        db.set_session_unlock(SESSION_ID, True)
        meta = db.get_session(SESSION_ID)
        config = json.loads(meta["model_config"])
        assert config["unlock_mode"] is True
        assert config["max_iterations"] == 42
        assert config["_branched_from"] == "parent_x"

    def test_missing_row_is_noop(self, db):
        # Row doesn't exist yet (lazy creation) — must not raise or create.
        db.set_session_unlock("does_not_exist", True)
        assert db.get_session("does_not_exist") is None

    def test_creation_time_model_config_flag_reads_back(self, db):
        db.create_session(
            session_id=SESSION_ID,
            source="cli",
            model="m",
            model_config={"unlock_mode": True},
        )
        meta = db.get_session(SESSION_ID)
        assert SessionDB.session_unlock_enabled(meta) is True

    def test_reader_is_false_on_garbage(self):
        assert SessionDB.session_unlock_enabled(None) is False
        assert SessionDB.session_unlock_enabled({}) is False
        assert SessionDB.session_unlock_enabled({"model_config": None}) is False
        assert SessionDB.session_unlock_enabled({"model_config": "not json {"}) is False
        assert SessionDB.session_unlock_enabled({"model_config": "[1,2]"}) is False
        assert (
            SessionDB.session_unlock_enabled({"model_config": '{"unlock_mode": false}'})
            is False
        )


def _stand_in(session_id=SESSION_ID, session_db=None):
    return SimpleNamespace(
        session_id=session_id,
        _session_db=session_db,
        _console_print=lambda *a, **k: None,
    )


class TestRestoreSessionUnlock:
    def test_restore_enables_bypass_when_flag_set(self):
        stand_in = _stand_in()
        meta = {"id": SESSION_ID, "model_config": '{"unlock_mode": true}'}

        assert approval_module.is_session_unlock_enabled(SESSION_ID) is False
        CurieCLI._restore_session_unlock(stand_in, meta)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is True

    def test_restore_noop_when_flag_absent(self):
        stand_in = _stand_in()
        meta = {"id": SESSION_ID, "model_config": '{"max_iterations": 10}'}

        CurieCLI._restore_session_unlock(stand_in, meta)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is False

    def test_restore_noop_when_meta_empty(self):
        stand_in = _stand_in()
        CurieCLI._restore_session_unlock(stand_in, {})
        CurieCLI._restore_session_unlock(stand_in, None)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is False

    def test_restore_idempotent_when_already_enabled(self):
        stand_in = _stand_in()
        approval_module.enable_session_unlock(SESSION_ID)
        meta = {"id": SESSION_ID, "model_config": '{"unlock_mode": true}'}
        # Should not raise or print duplicate banners; state stays enabled.
        CurieCLI._restore_session_unlock(stand_in, meta)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is True

    def test_restore_skipped_under_frozen_process_unlock(self):
        stand_in = _stand_in()
        meta = {"id": SESSION_ID, "model_config": '{"unlock_mode": true}'}
        with patch.object(approval_module, "_UNLOCK_MODE_FROZEN", True):
            CurieCLI._restore_session_unlock(stand_in, meta)
        # Frozen bypass already covers everything — the session set is
        # untouched (avoids persisting a session-scoped bypass the user
        # only asked for at process scope).
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is False


class TestToggleUnlockPersists:
    def test_toggle_writes_flag_through_session_db(self):
        db = MagicMock()
        stand_in = SimpleNamespace(session_id=SESSION_ID, _session_db=db)
        # Bind the real persist helper so the toggle's getattr finds it.
        stand_in._persist_session_unlock = (
            lambda key, enabled: CurieCLI._persist_session_unlock(
                stand_in, key, enabled
            )
        )

        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)  # ON
        db.set_session_unlock.assert_called_once_with(SESSION_ID, True)

        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)  # OFF
        db.set_session_unlock.assert_called_with(SESSION_ID, False)

    def test_toggle_survives_missing_session_db(self):
        stand_in = SimpleNamespace(session_id=SESSION_ID, _session_db=None)
        stand_in._persist_session_unlock = (
            lambda key, enabled: CurieCLI._persist_session_unlock(
                stand_in, key, enabled
            )
        )
        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)  # must not raise
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is True

    def test_toggle_still_works_without_persist_helper(self):
        # Back-compat with the minimal stand-in used by older tests.
        stand_in = SimpleNamespace(session_id=SESSION_ID)
        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(stand_in)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is True


class TestEndToEndPersistAndRestore:
    def test_full_round_trip_through_real_db(self, db):
        """Toggle ON in 'process 1', restore in 'process 2' (fresh in-memory
        approval state), and verify a dangerous command auto-approves."""
        db.create_session(session_id=SESSION_ID, source="cli", model="m")

        # Process 1: user toggles /unlock ON — persisted to the row.
        cli_one = SimpleNamespace(session_id=SESSION_ID, _session_db=db)
        cli_one._persist_session_unlock = (
            lambda key, enabled: CurieCLI._persist_session_unlock(
                cli_one, key, enabled
            )
        )
        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(cli_one)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is True

        # Simulate process exit: in-memory approval state is gone.
        approval_module.clear_session(SESSION_ID)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is False

        # Process 2: --resume reads the row and restores the bypass.
        meta = db.get_session(SESSION_ID)
        cli_two = _stand_in(session_db=db)
        CurieCLI._restore_session_unlock(cli_two, meta)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is True

        token = approval_module.set_current_session_key(SESSION_ID)
        try:
            result = approval_module.check_all_command_guards(
                "rm -rf /tmp/scratch-xyzzy", "local",
            )
            assert result["approved"] is True
        finally:
            approval_module.reset_current_session_key(token)

    def test_toggle_off_round_trip(self, db):
        """OFF must persist too — a resumed session must not resurrect a
        bypass the user explicitly turned off."""
        db.create_session(
            session_id=SESSION_ID,
            source="cli",
            model="m",
            model_config={"unlock_mode": True},
        )
        cli_one = SimpleNamespace(session_id=SESSION_ID, _session_db=db)
        cli_one._persist_session_unlock = (
            lambda key, enabled: CurieCLI._persist_session_unlock(
                cli_one, key, enabled
            )
        )
        approval_module.enable_session_unlock(SESSION_ID)
        with patch("cli._cprint"):
            CurieCLI._toggle_unlock(cli_one)  # OFF
        approval_module.clear_session(SESSION_ID)

        meta = db.get_session(SESSION_ID)
        cli_two = _stand_in(session_db=db)
        CurieCLI._restore_session_unlock(cli_two, meta)
        assert approval_module.is_session_unlock_enabled(SESSION_ID) is False
