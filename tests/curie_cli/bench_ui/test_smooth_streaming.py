"""A long reply streams at an even pace, with or without the chatter.

The fault this guards: every streamed piece re-rendered the *whole* reply, so
a batch of pieces cost a batch of whole-reply renders. The longer the reply,
the longer each batch took, the more pieces were waiting for the next one —
until the window froze mid-reply and then dropped a paragraph in at once.
The chatter made it worse (its audio thread competes for the interpreter),
which is where it was noticed.

Two halves of the fix, held here as behaviour: a tick's pieces of one stream
are joined into one event, and an entry is a widget per paragraph, so only
the paragraph being written is touched.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent, coalesce_events  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import Entry, split_paragraphs  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))


def test_neighbouring_pieces_of_a_stream_are_joined_and_order_is_kept():
    events = [
        TurnEvent("reasoning", "th"), TurnEvent("reasoning", "ink"),
        TurnEvent("delta", "Hel"), TurnEvent("delta", "lo"),
        TurnEvent("tool", "read_file"),
        TurnEvent("delta", " again"), TurnEvent("done", "x"),
    ]
    out = coalesce_events(events)
    assert [(e.kind, e.text) for e in out] == [
        ("reasoning", "think"), ("delta", "Hello"), ("tool", "read_file"),
        ("delta", " again"), ("done", "x"),
    ]


def test_events_that_are_not_streams_are_never_joined():
    events = [TurnEvent("tool", "a"), TurnEvent("tool", "b"), TurnEvent("wait", "w"), TurnEvent("wait", "w")]
    assert coalesce_events(events) == events


@pytest.mark.parametrize("text", [
    "", "one", "one\n\ntwo", "one\n\n\n\ntwo\nmore\n\nthree\n", "\n\nlead", "a\n\n",
    "```\ncode\n\nstill code\n```\n\nafter",
])
def test_paragraphs_put_back_together_are_the_text(text):
    pieces = split_paragraphs(text)
    assert "".join(pieces) == text
    assert all(pieces) or pieces == [""]


def _run(coro):
    return asyncio.run(coro)


def test_a_long_reply_streams_into_its_last_paragraph_only():
    """Finished paragraphs are left alone while the next one is written."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(4):
                await pilot.pause()
            pane = app._bench()
            pane.begin_reply()
            for paragraph in range(6):
                pane.append_reply(f"Paragraph {paragraph} says something.\n\n")
            await pilot.pause()
            entry = next(w for _k, _t, w in pane._entries if isinstance(w, Entry))
            body = entry._body
            finished = list(body._chunks[:-1])
            assert len(finished) >= 5, "the reply was not split into its paragraphs"
            calls = []
            for chunk in finished:
                original = chunk.update
                chunk.update = lambda *a, _o=original, **k: (calls.append(1), _o(*a, **k))[1]
            for word in "the seventh paragraph arrives a word at a time".split():
                pane.append_reply(word + " ")
            await pilot.pause()
            assert not calls, "a finished paragraph was re-rendered"
            assert body._chunks[: len(finished)] == finished
            shown = body.rendered().plain
            assert shown == pane.reply_text.lstrip("\r\n")
            assert shown.endswith("the seventh paragraph arrives a word at a time ")

    _run(scenario())


def test_a_tick_of_pieces_renders_the_reply_once():
    class Burst(_StubBridge):
        def submit(self, message):
            self.submitted.append(message)
            for piece in ["Hel", "lo ", "the", "re, ", "rea", "der."]:
                self._events.put(TurnEvent("delta", piece))
            return True

    async def scenario():
        app = BenchConsole(bridge=Burst())
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(4):
                await pilot.pause()
            pane = app._bench()
            appended = []
            original = pane.append_reply
            pane.append_reply = lambda text: (appended.append(text), original(text))[1]
            app.bridge.submit("go")
            app._pump_agent()
            assert appended == ["Hello there, reader."]

    _run(scenario())
