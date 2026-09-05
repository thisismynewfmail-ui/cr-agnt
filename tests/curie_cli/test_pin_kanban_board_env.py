"""Tests for `_pin_kanban_board_env` helper invoked by `cmd_chat`.

Regression coverage for #20074: a chat session must export the active kanban
board into `CURIE_KANBAN_BOARD` at boot so subprocess shell-outs (e.g.
`curie kanban …`) inherit the same board the in-process kanban tools resolve.
Without this, a concurrent `curie kanban boards switch` from another session
can flip the global current-board file mid-turn and silently divert the
shell calls to a different DB.
"""
import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_kanban_board_env():
    """Snapshot `CURIE_KANBAN_BOARD` and restore it after the test.

    `_pin_kanban_board_env()` writes to ``os.environ`` directly, bypassing
    any ``monkeypatch.setenv`` tracking. Without this fixture the mutation
    leaks into subsequent tests and breaks anything that resolves a kanban
    path from the env (e.g. ``TestSharedBoardPaths`` in test_kanban_db.py).
    """
    prev = os.environ.get("CURIE_KANBAN_BOARD")
    os.environ.pop("CURIE_KANBAN_BOARD", None)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("CURIE_KANBAN_BOARD", None)
        else:
            os.environ["CURIE_KANBAN_BOARD"] = prev


def test_pin_writes_resolved_board_when_env_unset(monkeypatch):
    main_mod = importlib.import_module("curie_cli.main")

    import curie_cli.kanban_db as kdb
    monkeypatch.setattr(kdb, "get_current_board", lambda: "space")

    main_mod._pin_kanban_board_env()

    assert main_mod.os.environ.get("CURIE_KANBAN_BOARD") == "space"


def test_pin_does_not_overwrite_existing_env(monkeypatch):
    monkeypatch.setenv("CURIE_KANBAN_BOARD", "preset")
    main_mod = importlib.import_module("curie_cli.main")

    import curie_cli.kanban_db as kdb

    def _explode():
        raise AssertionError("get_current_board must not be called when env is set")

    monkeypatch.setattr(kdb, "get_current_board", _explode)

    main_mod._pin_kanban_board_env()

    assert main_mod.os.environ.get("CURIE_KANBAN_BOARD") == "preset"


