"""A long conversation must not make the console slow.

Reported: "as the chat log gets longer — more tool calls, more thinking
traces — the software starts to LAG a LOT", streaming included. Measured on a
hundred-turn log, a streaming tick cost about half a second, and the causes
were all things whose cost grew with the conversation:

* the console's own widget lookups walked and CSS-matched the whole DOM, and
  timers make several a second;
* every block of workings ran a 12 Hz animation clock forever, parked or not;
* a shut fold rebuilt its whole run of reasoning on every streamed piece and
  asked the screen for a full layout pass to show nothing;
* the open reply was found by scanning the conversation on every piece;
* and every layout pass walks every mounted widget, so each piece of an
  answer paid for the whole transcript — 13 ms at ten turns, 150 ms at two
  hundred — while building a long transcript was quadratic.

The last is answered by keeping only the newest blocks on the screen and the
rest as records the reader can bring back. These tests hold each fix as a
behaviour, not as a timing.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.indicators import ActivityMonitor, Pen  # noqa: E402
from curie_cli.bench_ui.panes import (  # noqa: E402
    BenchPane,
    Entry,
    Fold,
    HistoryMarker,
    _Retired,
)

from tests.curie_cli.bench_ui.test_compaction import _OpenTurn  # noqa: E402
from tests.curie_cli.bench_ui.test_console import _StubBridge, _transcript_text  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


@pytest.fixture()
def small_window(monkeypatch):
    """The window at a size a test can fill: twelve blocks, four at a time."""
    monkeypatch.setattr(BenchPane, "MOUNTED_BLOCKS", 12)
    monkeypatch.setattr(BenchPane, "BLOCK_BATCH", 4)


def _turn(pane: BenchPane, number: int) -> None:
    """One turn as the console writes it: ask, workings, answer."""
    pane.write("user", f"question number {number}")
    fold = pane.fold()
    fold.add_reasoning(f"thinking about question {number}")
    fold.add_tool("read_file")
    pane.start_thinking()
    pane.begin_reply()
    pane.append_reply(f"answer number {number}\n\nwith a second paragraph")
    pane.end_turn()


def _blocks_on_screen(pane: BenchPane) -> list:
    log = pane._log()
    return [child for child in log.children if not isinstance(child, HistoryMarker)]


# ── Keeping only the newest blocks on screen ─────────────────────────────


def test_a_long_conversation_keeps_a_bounded_number_of_blocks_mounted(small_window):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(20):  # sixty blocks
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)

            mounted = _blocks_on_screen(pane)
            assert len(mounted) <= BenchPane.MOUNTED_BLOCKS + BenchPane.BLOCK_BATCH
            assert pane.retired_count == 60 - len(mounted)
            # The oldest go first, and the screen shows them in order.
            assert all(
                isinstance(widget, _Retired)
                for _k, _t, widget in pane._entries[: pane.retired_count]
            )
            marker = pane._log().query_one(HistoryMarker)
            assert marker.count == pane.retired_count
            assert f"{pane.retired_count} earlier blocks" in _transcript_text(app)
            # Nothing is lost from the record.
            assert pane.message_count() == 40
            assert len(pane.replies()) == 20
            copied = pane.transcript_text()
            assert "question number 0" in copied and "question number 19" in copied
    _run(scenario())


def test_scrolling_to_the_top_brings_back_the_next_batch_in_order(small_window):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(20):
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)
            before = pane.retired_count
            log = pane._log()
            first = _blocks_on_screen(pane)[0]

            log.scroll_home(animate=False)
            await _settle(pilot, 8)

            assert pane.retired_count == before - BenchPane.BLOCK_BATCH
            # On screen, the blocks are in the conversation's own order.
            tops = [pane._top_of(w) for _k, _t, w in pane._entries
                    if not isinstance(w, _Retired)]
            assert _blocks_on_screen(pane) == tops
            # The reader's place is held: what was first is still in view.
            assert first.region.y < log.region.bottom
            assert first.region.bottom > log.region.y
            assert not log.following
    _run(scenario())


def test_the_marker_brings_them_back_when_clicked(small_window):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(20):
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)
            before = pane.retired_count
            pane._log().query_one(HistoryMarker).on_click(_Click())
            await _settle(pilot, 6)
            assert pane.retired_count == before - BenchPane.BLOCK_BATCH
    _run(scenario())


class _Click:
    def stop(self) -> None:
        pass


def test_nothing_is_retired_while_the_reader_is_scrolled_up(small_window):
    """Taking blocks out from under someone reading them would move the text."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(20):
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)
            log = pane._log()
            log.scroll_to(y=max(1, log.max_scroll_y // 2), animate=False)
            await _settle(pilot)
            assert not log.following
            retired = pane.retired_count
            for number in range(20, 26):
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)
            assert pane.retired_count == retired
    _run(scenario())


def test_a_restored_fold_keeps_its_run_and_the_workings_switch(small_window):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(20):
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)
            # Thrown while the oldest folds are off the screen.
            pane.set_workings_open(True)
            retired_folds = [
                index for index, (kind, _t, widget) in enumerate(pane._entries)
                if kind == "fold" and isinstance(widget, _Retired)
            ]
            assert retired_folds, "no fold was off the screen to begin with"
            pane._restore_earlier()
            await _settle(pilot, 6)
            restored = [
                pane._entries[index][2] for index in retired_folds
                if isinstance(pane._entries[index][2], Fold)
            ]
            assert restored, "no retired fold came back"
            for fold in restored:
                assert fold.is_attached
                assert not fold.collapsed, "a restored fold ignored the switch"
                assert "read_file" in fold.plain_text()
                assert "thinking about question" in fold.plain_text()
            # The copy of the window includes an open retired fold's run too.
            assert "thinking about question 0" in pane.transcript_text()
    _run(scenario())


def test_going_back_and_a_skin_change_work_with_blocks_off_screen(small_window):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(20):
                _turn(pane, number)
                await pilot.pause()
            await _settle(pilot)
            assert pane.drop_last_exchange() == "question number 19"
            assert pane.message_count() == 38
            app.run_keyline_action("dos-mode")
            await _settle(pilot)
            pane._restore_earlier()
            await _settle(pilot, 6)
            shown = _transcript_text(app)
            assert "► YOU" in shown and "▶ YOU" not in shown
    _run(scenario())


# ── Costs that grew with the conversation ────────────────────────────────


def test_a_parked_pen_stops_its_clock():
    """One pen per block of workings; a parked one must not keep ticking."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "go")
            # A block with something in it, so it stays on the transcript
            # once the turn ends — an empty one is taken away whole.
            pane.fold().add_tool("read_file")
            pane.start_thinking()
            await _settle(pilot)
            pen = pane.query_one(Pen)
            assert pen._timer is not None and pen._timer._active.is_set()
            pane.end_turn()
            await _settle(pilot)
            assert pen.is_attached, "the block was taken away"
            assert pen._timer._task is not None, "the clock was stopped, not parked"
            assert not pen._timer._active.is_set(), "a parked pen kept its clock"
            # A new turn's pen runs again.
            pane.write("user", "again")
            pane.start_thinking()
            await _settle(pilot)
            assert pane._pen._timer._active.is_set()
            # The state figure in the stack animates on READY, and keeps its clock.
            monitor = app.query_one("#activity", ActivityMonitor)
            assert monitor._timer._active.is_set()
    _run(scenario())


def test_a_shut_fold_does_not_draw_what_nobody_can_see():
    """And opening it shows every word that arrived while it was shut."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.write("user", "go")
            fold = pane.fold()
            await _settle(pilot)
            assert fold.collapsed
            drawn: list = []
            original = fold._body.show
            fold._body.show = lambda pieces: (drawn.append(1), original(pieces))[1]
            for index in range(30):
                fold.add_reasoning(f"piece {index} ")
            await _settle(pilot)
            assert drawn == [], "a shut fold redrew its body for every piece"
            fold.collapsed = False
            await _settle(pilot)
            assert drawn, "opening the fold did not draw it"
            assert "piece 0" in fold.plain_text() and "piece 29" in fold.plain_text()
            painted = "\n".join(str(c.render()) for c in fold._body._chunks)
            assert "piece 29" in painted
    _run(scenario())


def test_finished_blocks_are_drawn_as_one_piece_each():
    """A widget per paragraph is for text still streaming in, not after."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            pane.set_workings_open(True)
            pane.write("user", "first paragraph\n\nsecond paragraph\n\nthird")
            fold = pane.fold()
            fold.add_reasoning("one thought\n\nanother thought\n\na third")
            await _settle(pilot)
            assert len(fold._body._chunks) == 3, "a live fold is drawn per paragraph"
            pane.begin_reply()
            for index in range(4):
                pane.append_reply(f"paragraph {index}\n\n")
            await _settle(pilot)
            reply = pane._reply
            assert len(reply._body._chunks) >= 4, "a live reply is drawn per paragraph"
            pane.end_turn()
            await _settle(pilot)
            assert len(reply._body._chunks) == 1
            assert len(fold._body._chunks) == 1
            user = next(w for k, _t, w in pane._entries if k == "user")
            assert len(user._body._chunks) == 1
            # The same words either way.
            assert "paragraph 3" in str(reply._body.rendered())
            assert "a third" in str(fold._body._chunks[0].render())
    _run(scenario())


def test_widget_lookups_do_not_walk_the_whole_conversation():
    """The timers' lookups stop at the first match instead of collecting all."""

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)

            def no_full_query(*_a, **_k):
                raise AssertionError("a lookup walked the whole DOM")

            app.query = no_full_query
            assert isinstance(app._maybe("#activity", ActivityMonitor), ActivityMonitor)
            assert app._maybe("#no-such-widget") is None
    _run(scenario())


def test_streaming_into_a_long_reply_finds_it_without_a_scan():
    async def scenario():
        app = BenchConsole(bridge=_OpenTurn())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot)
            pane = app._bench()
            for number in range(5):
                _turn(pane, number)
            app._send("go on")
            await _settle(pilot)

            class _NoScan(list):
                def __iter__(self):
                    raise AssertionError("the open reply was found by a scan")

            app.bridge._events.put(TurnEvent("delta", "first "))
            app._pump_agent()
            pane._entries = _NoScan(pane._entries)
            for piece in ("second ", "third"):
                app.bridge._events.put(TurnEvent("delta", piece))
                app._pump_agent()
            pane._entries = list(list.copy(pane._entries))
            assert pane.reply_text.endswith("first second third")
    _run(scenario())
