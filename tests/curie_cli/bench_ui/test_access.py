"""ACCESS on the bench console: the approval prompt, UNLOCK, and sudo.

The console used to have no approval gate at all — ``curie ui`` never marked
its turns interactive, so ``tools.approval`` approved every dangerous command
without asking. These tests hold the three pieces to what reaches the real
machinery: the prompt answers ``tools.approval``'s own call, UNLOCK writes
the same per-conversation flag every other surface's ``/unlock`` does, and
the stored sudo password reaches the terminal tool's sudo rewrite — and
nothing else, and only while SUDO UNLOCK is on.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

import tools.approval as approval  # noqa: E402
import tools.terminal_tool as terminal_tool  # noqa: E402
from curie_cli.bench_ui import access  # noqa: E402
from curie_cli.bench_ui.access import (  # noqa: E402
    APPROVAL_ARMING_SECONDS,
    SUDO_ENV_KEY,
    SUDO_SECRET_KEY,
    SudoSupply,
    approval_choices,
    forget_sudo_password,
    store_sudo_password,
    stored_sudo_password,
)
from curie_cli.bench_ui.agent_bridge import AgentBridge  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import ApprovalBar, PanelPane, ToggleSwitch  # noqa: E402
from curie_cli.bench_ui.settings import read_settings  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


async def _until(pilot, condition, attempts: int = 80) -> bool:
    for _ in range(attempts):
        if condition():
            return True
        await pilot.pause(0.05)
    return condition()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    home = tmp_path / "curie"
    home.mkdir()
    monkeypatch.setenv("CURIE_HOME", str(home))
    monkeypatch.delenv(SUDO_ENV_KEY, raising=False)
    monkeypatch.delenv(SUDO_SECRET_KEY, raising=False)
    yield home


@pytest.fixture
def no_other_sudo(monkeypatch):
    """Take the host's own sudo out of the picture: no NOPASSWD probe, no
    password cached by an earlier prompt."""
    monkeypatch.setattr(terminal_tool, "_sudo_nopasswd_works", lambda: False)
    monkeypatch.setattr(terminal_tool, "_get_cached_sudo_password", lambda: "")


class _SessionBridge(_StubBridge):
    """A stub bridge that is writing to a named conversation."""

    def __init__(self, session_id: str = "bench-sess-1"):
        super().__init__()
        self._agent = SimpleNamespace(session_id=session_id)

    def ensure_agent(self) -> bool:
        return True


def _panel_switch(app, key: str) -> ToggleSwitch:
    return app.query_one(PanelPane).query_one(f"#switch-{key}", ToggleSwitch)


# ── The stored password ──────────────────────────────────────────────────


def test_a_stored_password_lives_in_env_not_config(_own_home):
    assert store_sudo_password("correct horse") == ""
    assert stored_sudo_password() == "correct horse"
    assert SUDO_SECRET_KEY in (_own_home / ".env").read_text()
    config = _own_home / "config.yaml"
    assert not config.exists() or "correct horse" not in config.read_text()


def test_forgetting_takes_it_out_of_env_and_the_process(_own_home):
    store_sudo_password("pw-1")
    assert forget_sudo_password() == ""
    assert stored_sudo_password() == ""
    assert SUDO_SECRET_KEY not in os.environ


@pytest.mark.parametrize("password", ["", "two\nlines", "carriage\rreturn", "pässwörd"])
def test_a_password_the_store_would_change_is_refused(password):
    assert store_sudo_password(password)
    assert stored_sudo_password() == ""


@pytest.mark.parametrize("password", ["pa$$word", "a${HOME}b", "q'uote\"s", "#hash = x", " spaced "])
def test_whatever_is_stored_reads_back_exactly(password):
    """Never a mangled password: sudo would refuse it, repeatedly."""
    problem = store_sudo_password(password)
    if problem:
        assert stored_sudo_password() == ""
    else:
        assert stored_sudo_password() == password


def test_the_password_is_a_known_secret_setting():
    from curie_cli.config import OPTIONAL_ENV_VARS, _is_env_config_key

    meta = OPTIONAL_ENV_VARS[SUDO_SECRET_KEY]
    assert meta.get("password") is True
    assert _is_env_config_key(SUDO_SECRET_KEY)


def test_neither_password_reaches_a_child_process(monkeypatch):
    from tools.environments.local import build_subprocess_env

    monkeypatch.setenv(SUDO_SECRET_KEY, "console-secret")
    monkeypatch.setenv(SUDO_ENV_KEY, "global-secret")
    env = build_subprocess_env()
    assert SUDO_SECRET_KEY not in env
    assert SUDO_ENV_KEY not in env
    assert "console-secret" not in env.values()


# ── Supplying it ─────────────────────────────────────────────────────────


def test_supply_puts_the_password_where_the_terminal_tool_reads_it():
    supply = SudoSupply()
    supply.apply(True, "pw")
    assert os.environ[SUDO_ENV_KEY] == "pw"
    assert supply.supplying
    supply.withdraw()
    assert SUDO_ENV_KEY not in os.environ


def test_supply_never_takes_away_a_password_configured_for_every_surface(monkeypatch):
    monkeypatch.setenv(SUDO_ENV_KEY, "global")
    supply = SudoSupply()
    assert supply.global_password
    supply.apply(False, "pw")
    assert os.environ[SUDO_ENV_KEY] == "global"
    supply.apply(True, "pw")
    assert os.environ[SUDO_ENV_KEY] == "pw"
    supply.apply(False, "pw")
    assert os.environ[SUDO_ENV_KEY] == "global"


def test_supply_without_a_password_supplies_nothing():
    supply = SudoSupply()
    supply.apply(True, "")
    assert not supply.supplying
    assert SUDO_ENV_KEY not in os.environ


def test_sudo_unlock_hands_the_stored_password_to_sudo(no_other_sudo):
    """End to end: PANEL → SUDO UNLOCK → the terminal tool's sudo rewrite."""
    store_sudo_password("hunter2")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            command, stdin = terminal_tool._transform_sudo_command("sudo apt-get update")
            assert stdin is None, "locked: sudo gets no password"

            app.run_keyline_action("access-sudo")
            await _settle(pilot)
            assert read_settings().sudo_unlock is True
            assert _panel_switch(app, "access-sudo").is_on
            command, stdin = terminal_tool._transform_sudo_command("sudo apt-get update")
            assert stdin == "hunter2\n"
            assert "sudo -S" in command

            app.run_keyline_action("access-sudo")
            await _settle(pilot)
            command, stdin = terminal_tool._transform_sudo_command("sudo apt-get update")
            assert stdin is None
            assert command == "sudo apt-get update"

    _run(scenario())


def test_sudo_unlock_is_withdrawn_when_the_console_closes(no_other_sudo):
    store_sudo_password("hunter2")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("access-sudo")
            await _settle(pilot)
            assert os.environ.get(SUDO_ENV_KEY) == "hunter2"

    _run(scenario())
    assert SUDO_ENV_KEY not in os.environ


def test_typing_a_password_into_the_panel_stores_it_and_clears_the_field():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 50)) as pilot:
            await _settle(pilot)
            app.show_pane("panel")
            await _settle(pilot)
            field = app.query_one("#sudo-password")
            field.focus()
            await _settle(pilot)
            for key in "s3cret":
                await pilot.press(key)
            await pilot.press("enter")
            await _settle(pilot)
            assert field.value == ""
            assert field.password is True
            assert stored_sudo_password() == "s3cret"

    _run(scenario())


def test_a_stored_password_is_handed_over_at_start_when_sudo_unlock_is_on(no_other_sudo):
    from curie_cli.bench_ui.settings import KEY_SUDO_UNLOCK, write_setting

    store_sudo_password("from-last-time")
    write_setting(KEY_SUDO_UNLOCK, True)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            _cmd, stdin = terminal_tool._transform_sudo_command("sudo true")
            assert stdin == "from-last-time\n"

    _run(scenario())


# ── UNLOCK ───────────────────────────────────────────────────────────────


def test_unlock_writes_the_conversation_flag_every_surface_reads():
    bridge = _SessionBridge("bench-sess-unlock")

    async def scenario():
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            assert not approval.is_session_unlock_enabled("bench-sess-unlock")
            app.run_keyline_action("access-unlock")
            await _settle(pilot)
            assert approval.is_session_unlock_enabled("bench-sess-unlock")
            assert read_settings().unlock is True
            assert _panel_switch(app, "access-unlock").is_on
            app.run_keyline_action("access-unlock")
            await _settle(pilot)
            assert not approval.is_session_unlock_enabled("bench-sess-unlock")
            assert read_settings().unlock is False

    try:
        _run(scenario())
    finally:
        approval.disable_session_unlock("bench-sess-unlock")


def test_a_turn_binds_the_conversation_the_prompt_and_interactivity():
    """What the CLI binds around its turns, bound around the bench's."""
    prompt = lambda *a, **k: "deny"  # noqa: E731
    bridge = AgentBridge(session_id="bench-sess-turn")
    bridge.approval_callback = prompt
    bridge.unlocked = True
    seen = {}

    def turn():
        release = bridge._turn_access()
        try:
            seen["key"] = approval.get_current_session_key()
            seen["interactive"] = approval._is_interactive_cli()
            seen["callback"] = terminal_tool._get_approval_callback()
            seen["unlocked"] = approval.is_current_session_unlock_enabled()
        finally:
            release()
        seen["after"] = terminal_tool._get_approval_callback()

    worker = threading.Thread(target=turn)
    worker.start()
    worker.join(10)
    try:
        assert seen["key"] == "bench-sess-turn"
        assert seen["interactive"] is True
        assert seen["callback"] is prompt
        assert seen["unlocked"] is True
        assert seen["after"] is None
    finally:
        approval.disable_session_unlock("bench-sess-turn")


def test_a_bridge_with_no_prompt_gains_no_gate():
    bridge = AgentBridge(session_id="bench-sess-bare")
    seen = {}

    def turn():
        release = bridge._turn_access()
        try:
            seen["callback"] = terminal_tool._get_approval_callback()
        finally:
            release()

    worker = threading.Thread(target=turn)
    worker.start()
    worker.join(10)
    assert seen["callback"] is None


# ── The approval prompt ──────────────────────────────────────────────────


def _ask_in_background(callback, **kwargs):
    """Ask the way ``tools.approval`` does, from a tool's own thread."""
    result: dict = {}

    def ask():
        result["choice"] = approval.prompt_dangerous_approval(
            "rm -rf build/", "recursive delete", approval_callback=callback, **kwargs
        )

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    return worker, result


def test_a_dangerous_command_is_put_to_the_reader():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            worker, result = _ask_in_background(app.bridge.approval_callback)
            bar = app.query_one("#approval-bar", ApprovalBar)
            assert await _until(pilot, lambda: bar.display)
            assert "rm -rf build/" in str(app.query_one("#approval-command").render())
            # Keys in flight when it opens do not answer it.
            await pilot.press("y")
            await _settle(pilot)
            assert "choice" not in result
            await pilot.pause(APPROVAL_ARMING_SECONDS + 0.1)
            await pilot.press("s")
            assert await _until(pilot, lambda: not worker.is_alive())
            assert result["choice"] == "session"
            await _settle(pilot)
            assert not bar.display

    _run(scenario())


def test_a_one_operation_gate_offers_only_once_or_no():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            worker, result = _ask_in_background(
                app.bridge.approval_callback, allow_permanent=False, allow_session=False
            )
            bar = app.query_one("#approval-bar", ApprovalBar)
            assert await _until(pilot, lambda: bar.display)
            shown = {
                name for name in ("once", "session", "always", "deny")
                if app.query_one(f"#approval-{name}").display
            }
            assert shown == {"once", "deny"}
            app.run_keyline_action("approval-always")
            assert await _until(pilot, lambda: not worker.is_alive())
            assert result["choice"] == "deny", "an answer not offered is a no"

    _run(scenario())


def test_unlock_does_not_answer_a_gate_that_asks_every_time():
    """Protected instruction files and memory writes come through the same
    prompt and are, by design, never bypassed by UNLOCK."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("access-unlock")
            await _settle(pilot)
            worker, result = _ask_in_background(
                app.bridge.approval_callback, allow_permanent=False, allow_session=False
            )
            bar = app.query_one("#approval-bar", ApprovalBar)
            assert await _until(pilot, lambda: bar.display)
            # Throwing UNLOCK again (off, then on) does not answer it either.
            app.run_keyline_action("access-unlock")
            app.run_keyline_action("access-unlock")
            await _settle(pilot)
            assert worker.is_alive() and "choice" not in result
            app.run_keyline_action("approval-deny")
            assert await _until(pilot, lambda: not worker.is_alive())
            assert result["choice"] == "deny"

    _run(scenario())


def test_stop_refuses_a_waiting_command():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            worker, result = _ask_in_background(app.bridge.approval_callback)
            bar = app.query_one("#approval-bar", ApprovalBar)
            assert await _until(pilot, lambda: bar.display)
            app.run_keyline_action("stop")
            assert await _until(pilot, lambda: not worker.is_alive())
            assert result["choice"] == "deny"

    _run(scenario())


def test_closing_the_console_refuses_a_waiting_command():
    result: dict = {}

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            worker, answer = _ask_in_background(app.bridge.approval_callback)
            result["worker"], result["answer"] = worker, answer
            bar = app.query_one("#approval-bar", ApprovalBar)
            assert await _until(pilot, lambda: bar.display)

    _run(scenario())
    result["worker"].join(5)
    assert result["answer"]["choice"] == "deny"


@pytest.mark.parametrize(
    "kwargs, offered",
    [
        ({}, ("once", "session", "always", "deny")),
        ({"allow_permanent": False}, ("once", "session", "deny")),
        ({"allow_session": False}, ("once", "deny")),
        ({"smart_denied": True}, ("once", "deny")),
    ],
)
def test_the_answers_offered_follow_the_cli(kwargs, offered):
    assert approval_choices(**kwargs) == offered


def test_status_reports_access():
    store_sudo_password("pw")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            rows = dict(app._access_status_rows())
            assert rows["unlock"] == "OFF" and rows["sudo"] == "locked"
            app.run_keyline_action("access-sudo")
            await _settle(pilot)
            assert dict(app._access_status_rows())["sudo"].startswith("unlocked")

    _run(scenario())


def test_check_sudo_password_never_checks_another_machines_sudo(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    ok, said = access.check_sudo_password("pw")
    assert ok is None and "docker" in said


def test_check_sudo_password_with_nothing_stored():
    ok, _said = access.check_sudo_password("")
    assert ok is None


def test_an_approval_request_answer_is_final():
    request = access.ApprovalRequest(
        command="x", description="y", choices=("once", "deny"), deadline=time.monotonic() + 5
    )
    request.answer("once")
    request.answer("deny")
    assert request.choice == "once"
