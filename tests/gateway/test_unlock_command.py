"""Tests for gateway /unlock session scoping."""

import os

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from tools.approval import disable_session_unlock, is_session_unlock_enabled


@pytest.fixture(autouse=True)
def _clean_unlock_state(monkeypatch):
    monkeypatch.delenv("CURIE_UNLOCK_MODE", raising=False)
    disable_session_unlock("agent:main:telegram:dm:chat-a")
    disable_session_unlock("agent:main:telegram:dm:chat-b")
    yield
    monkeypatch.delenv("CURIE_UNLOCK_MODE", raising=False)
    disable_session_unlock("agent:main:telegram:dm:chat-a")
    disable_session_unlock("agent:main:telegram:dm:chat-b")


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.session_store = None
    runner.config = None
    return runner


def _make_event(chat_id: str) -> MessageEvent:
    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id=f"user-{chat_id}",
        chat_id=chat_id,
        user_name="tester",
        chat_type="dm",
    )
    return MessageEvent(text="/unlock", source=source)


@pytest.mark.asyncio
async def test_unlock_command_toggles_only_current_session(monkeypatch):
    runner = _make_runner()

    event_a = _make_event("chat-a")
    session_a = runner._session_key_for_source(event_a.source)
    session_b = runner._session_key_for_source(_make_event("chat-b").source)

    result_on = await runner._handle_unlock_command(event_a)

    assert "ON" in result_on
    assert is_session_unlock_enabled(session_a) is True
    assert is_session_unlock_enabled(session_b) is False
    assert os.environ.get("CURIE_UNLOCK_MODE") is None

    result_off = await runner._handle_unlock_command(event_a)

    assert "OFF" in result_off
    assert is_session_unlock_enabled(session_a) is False
    assert os.environ.get("CURIE_UNLOCK_MODE") is None
