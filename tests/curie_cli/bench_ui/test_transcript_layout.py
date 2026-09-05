"""The name goes in a gutter and the text runs beside it.

Written as a prefix on the first line, a speaker's name only *looks* parallel
to what it introduces. It stops as soon as the text wraps — every line after
the first starts back at the margin, under the name — and it fails outright
when a model opens its answer with a blank line, which puts the first words a
row below their own label. A gutter is the fix: the name occupies a column,
the text occupies the rest, and every row of the answer is beside the name
rather than under it.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import BenchPane, Entry  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_lines,
)


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 3) -> None:
    for _ in range(times):
        await pilot.pause()


def _rows(app) -> list[str]:
    return [row for row in _transcript_lines(app) if row.strip()]


def test_the_reply_starts_on_the_same_row_as_its_name():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write("reply", "the assay came back clean")
            await _settle(pilot)
            named = [row for row in _rows(app) if "CURIE" in row]
            assert len(named) == 1, f"the name is on more than one row: {named}"
            assert "the assay came back clean" in named[0], (
                f"the reply is not beside its name: {named[0]!r}"
            )
    _run(scenario())


def test_a_reply_that_opens_with_a_blank_line_still_runs_beside_its_name():
    """The common real case: models open answers with a newline.

    Inline, this put the first words of every such answer a row below the
    label — the exact fault the gutter exists to remove, and the one that
    showed up as "the response is under the model's name".
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write("reply", "\n\nTwo of the three commits are docs-only.")
            await _settle(pilot)
            named = [row for row in _rows(app) if "CURIE" in row]
            assert len(named) == 1
            assert "Two of the three" in named[0], (
                f"a leading blank line orphaned the name: {named[0]!r}"
            )
    _run(scenario())


def test_every_wrapped_row_of_a_reply_is_beside_the_name_not_under_it():
    """The whole point of a gutter: the second row lines up with the first."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write(
                "reply",
                "A reply with enough words in it to occupy at least three rows "
                "of the transcript, so that the alignment of the rows after "
                "the first is something a test can actually measure.",
            )
            await _settle(pilot)
            rows = _rows(app)
            first = next(i for i, row in enumerate(rows) if "CURIE" in row)
            body_column = rows[first].index("A reply with")
            for row in rows[first + 1 : first + 3]:
                text_at = len(row) - len(row.lstrip())
                assert text_at == body_column, (
                    f"a wrapped row is not aligned with the first: "
                    f"{text_at} vs {body_column} in {row!r}"
                )
    _run(scenario())


def test_the_user_message_uses_the_same_column_as_the_reply():
    """Both turns on the same visual footing, or the layout takes a side."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "the question")
            pane.write("reply", "the answer")
            await _settle(pilot)
            rows = _rows(app)
            asked = next(row for row in rows if "the question" in row)
            answered = next(row for row in rows if "the answer" in row)
            assert asked.index("the question") == answered.index("the answer"), (
                f"the two turns start in different columns:\n{asked!r}\n{answered!r}"
            )
            assert asked.strip().startswith("▶")
            assert answered.strip().startswith("▮")
    _run(scenario())


def test_a_streamed_reply_keeps_the_layout_as_it_grows():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.bridge._events.put(TurnEvent("delta", "\n"))
            app.bridge._events.put(TurnEvent("delta", "streamed "))
            app.bridge._events.put(TurnEvent("delta", "in pieces."))
            app._pump_agent()
            await _settle(pilot)
            named = [row for row in _rows(app) if "CURIE" in row]
            assert len(named) == 1
            assert "streamed in pieces." in named[0], named
    _run(scenario())


def test_the_name_shrinks_to_its_glyph_on_a_narrow_window():
    """Six columns of name out of forty is a sixth of the reading width."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app._bench().write("reply", "an answer that has to stay readable")
            await _settle(pilot)
            assert any("CURIE" in row for row in _rows(app))

            await pilot.resize_terminal(60, 30)
            await _settle(pilot, 4)
            rows = _rows(app)
            assert not any("CURIE" in row for row in rows), (
                "the full name survived into a narrow window"
            )
            marked = next(row for row in rows if "an answer" in row)
            assert marked.strip().startswith("▮"), marked

            await pilot.resize_terminal(120, 30)
            await _settle(pilot, 4)
            assert any("CURIE" in row for row in _rows(app)), (
                "the name did not come back when there was room for it"
            )
    _run(scenario())


def test_an_entry_survives_a_skin_change_in_both_halves():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            pane = app.query_one("#pane-bench", BenchPane)
            pane.write("reply", "still readable afterwards")
            await _settle(pilot)

            from curie_cli.bench_ui.theme import resolve_palette
            from curie_cli.skin_engine import load_skin

            pane.restyle(resolve_palette(load_skin("curie-vga"), dark=True))
            await _settle(pilot)
            entry = next(w for _k, _t, w in pane._entries if isinstance(w, Entry))
            assert "CURIE" in str(entry._gutter.render())
            assert "still readable afterwards" in str(entry._body.render())
    _run(scenario())


def test_the_command_index_uses_the_same_two_columns():
    """A key and what it does is the same shape as a name and what it said."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await _settle(pilot)
            app.run_keyline_action("help")
            await _settle(pilot)
            rows = _rows(app)
            # The index is longer than the pane and the transcript follows
            # its tail, so the pair to look at is one near the end of it.
            paired = [row for row in rows if "start a new conversation" in row]
            assert paired and "F12" in paired[0], rows
            key_at = paired[0].index("F12")
            text_at = paired[0].index("start a new conversation")
            assert text_at > key_at + len("F12"), (
                f"the key and what it does are not in two columns: {paired[0]!r}"
            )
    _run(scenario())
