"""Ctrl+Shift+C copies the chat window.

The partner of the terminal's own Ctrl+Shift+V, which pastes into the
composer. With text selected it copies the selection; without, the whole
conversation on the bench. Ctrl+C copies a selection too — a terminal with no
modern keyboard protocol sends Ctrl+Shift+C *as* Ctrl+C, and before this the
console answered it by stopping the turn — and stops the turn otherwise.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import BenchPane, Entry  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    for name in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path


@pytest.fixture
def system_clipboard(monkeypatch):
    """Stand in for pbcopy/xclip/wl-copy: record what reached it."""
    landed: list[str] = []

    def write(text: str) -> bool:
        landed.append(text)
        return True

    monkeypatch.setattr("curie_cli.clipboard.write_clipboard_text", write)
    return landed


class _InterruptibleBridge(_StubBridge):
    """A bridge that reports a turn running and records being stopped."""

    def __init__(self):
        super().__init__()
        self.stopped = 0

    @property
    def busy(self) -> bool:
        return True

    def interrupt(self) -> bool:
        self.stopped += 1
        return True


def _bench(app) -> BenchPane:
    return app.query_one("#pane-bench", BenchPane)


def _conversation(app) -> None:
    pane = _bench(app)
    pane.write("user", "how do I list files?")
    pane.write("reply", "Use `ls -la`.\nIt shows hidden files too.")
    pane.write("error", "TimeoutError: provider slow")


# ── What is copied ───────────────────────────────────────────────────────

def test_the_chat_window_reads_out_as_speakers_and_their_words():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _conversation(app)
            text = _bench(app).transcript_text()
            assert text.index("YOU: how do I list files?") < text.index("CURIE: Use `ls -la`.")
            assert "It shows hidden files too." in text, "a reply lost its second line"
            assert "FAULT: TimeoutError: provider slow" in text
            # No gutter glyphs: the clipboard goes somewhere else.
            assert "▶" not in text and "▮" not in text
    _run(scenario())


def test_a_shut_fold_is_not_copied_and_an_open_one_is():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = _bench(app)
            pane.write("user", "check the disk")
            fold = pane.fold()
            fold.add_reasoning("the user wants df output")
            fold.add_tool("terminal")
            pane.end_turn()
            await _settle(pilot)
            assert fold.collapsed
            assert "df output" not in pane.transcript_text()
            fold.collapsed = False
            await _settle(pilot)
            copied = pane.transcript_text()
            assert "the user wants df output" in copied
            assert "terminal" in copied
    _run(scenario())


# ── Ctrl+Shift+C ─────────────────────────────────────────────────────────

def test_ctrl_shift_c_copies_the_whole_chat_window(system_clipboard):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _conversation(app)
            await pilot.press("ctrl+shift+c")
            await _settle(pilot, 6)
            expected = _bench(app).transcript_text()
            assert app.clipboard == expected, "the in-app clipboard was not filled"
            assert system_clipboard == [expected], "the system clipboard was not reached"
            notice = str(app.query_one("#notice").render())
            assert "Copied the chat window" in notice, notice
            assert "2 messages" in notice, notice
    _run(scenario())


def test_ctrl_shift_c_with_a_selection_copies_only_the_selection(system_clipboard):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _conversation(app)
            await _settle(pilot)
            reply = [e for e in app.query(Entry) if e.kind == "reply"][0]
            app.screen._select_all_in_widget(reply)
            selected = app.screen.get_selected_text()
            assert selected and "ls -la" in selected
            await pilot.press("ctrl+shift+c")
            await _settle(pilot, 6)
            assert app.clipboard == selected
            assert "how do I list files" not in app.clipboard
            assert not app.screen.selections, "the selection outlived its copy"
    _run(scenario())


def test_an_empty_chat_window_says_so_rather_than_copying_nothing(system_clipboard):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("ctrl+shift+c")
            await _settle(pilot)
            assert system_clipboard == []
            assert "Nothing in the chat window" in str(app.query_one("#notice").render())
    _run(scenario())


def test_over_ssh_it_copies_through_the_terminal_not_the_remote_clipboard(
    system_clipboard, monkeypatch
):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51000 10.0.0.1 22")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _conversation(app)
            await pilot.press("ctrl+shift+c")
            await _settle(pilot, 6)
            assert system_clipboard == [], "wrote the remote machine's clipboard"
            assert app.clipboard, "nothing was handed to the terminal"
            assert "OSC 52" in str(app.query_one("#notice").render())
    _run(scenario())


# ── Ctrl+C ───────────────────────────────────────────────────────────────

def test_ctrl_c_with_a_selection_copies_instead_of_stopping(system_clipboard):
    async def scenario():
        bridge = _InterruptibleBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _conversation(app)
            await _settle(pilot)
            reply = [e for e in app.query(Entry) if e.kind == "reply"][0]
            app.screen._select_all_in_widget(reply)
            await pilot.press("ctrl+c")
            await _settle(pilot, 6)
            assert bridge.stopped == 0, "a copy stopped the turn"
            assert "ls -la" in app.clipboard
            # The selection went with the copy, so the next press stops.
            await pilot.press("ctrl+c")
            await _settle(pilot)
            assert bridge.stopped == 1
    _run(scenario())


def test_ctrl_c_with_nothing_selected_still_stops_the_turn(system_clipboard):
    async def scenario():
        bridge = _InterruptibleBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            await pilot.press("ctrl+c")
            await _settle(pilot)
            assert bridge.stopped == 1
            assert system_clipboard == []
    _run(scenario())


def test_the_stop_keycap_stops_even_with_text_selected(system_clipboard):
    """A click on STOP is unambiguous; only the key is shared with copying."""
    async def scenario():
        bridge = _InterruptibleBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            _conversation(app)
            await _settle(pilot)
            reply = [e for e in app.query(Entry) if e.kind == "reply"][0]
            app.screen._select_all_in_widget(reply)
            app.run_keyline_action("stop")
            assert bridge.stopped == 1
    _run(scenario())
