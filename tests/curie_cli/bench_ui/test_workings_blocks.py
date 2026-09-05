"""How a turn's tool calls are ordered, counted, and got back out of.

An agent turn is not thinking-then-answering. It is however many rounds of
the two the model needs: it reads a file, says what it found, reads three
more on the strength of that, then answers. Collecting all of it into one
fold put every tool call in a drawer above the first answer, which reads as
the agent having decided everything up front and says nothing about what
followed from what. So each round of workings gets its own WORKINGS block,
mounted where it happened — under the answer that prompted it.

The rest of this file is the same subject from the other end: a call is
written into the transcript once however many hooks report it, the console
gets back out of the tool state even when a call never reports finishing,
and a request with nothing in it never reaches the model at all. All three
were ways the console could show a turn as still running tools when it was
not — or as answering when it was not — which is what a reader watching a
turn that will not end is actually looking at.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.indicators import (  # noqa: E402
    STREAMING,
    THINKING,
    TOOL,
    WAITING,
    Pen,
)
from curie_cli.bench_ui.panes import Composer, Entry, Fold  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _transcript_lines,
)


class _QuietBridge(_StubBridge):
    """A bridge that accepts a prompt and answers nothing.

    The stub answers the moment it is asked, which would race the events a
    test wants to feed by hand. Here the test is the only thing talking.
    """

    def submit(self, message: str) -> bool:
        self.submitted.append(message)
        return True


def _run(coro):
    return asyncio.run(coro)


def _feed(app, *events: TurnEvent) -> None:
    for event in events:
        app.bridge._events.put(event)
    app._pump_agent()


def _order(app) -> list[str]:
    """The transcript's blocks, top to bottom, as ``kind:text`` labels.

    Folds report their heading; replies and user messages report their text.
    Anything else is chrome and is left out, so a test can state the shape it
    wants without restating the whole widget tree.
    """
    marks: list[str] = []
    for node in app.query_one("#transcript").children:
        for fold in node.query(Fold) if not isinstance(node, Fold) else [node]:
            marks.append(f"fold:{fold.title}")
            break
        else:
            if isinstance(node, Entry):
                marks.append(f"{node.kind}:{node.entry_text}")
    return marks


#: The shape the user described: some tools, an answer, then more tools
#: decided on after that answer, then the rest of the answer.
TWO_ROUNDS = (
    TurnEvent("reasoning", "Checking what the config says."),
    TurnEvent("tool", "read_file"),
    TurnEvent("tool", "list_dir"),
    TurnEvent("tool", "grep"),
    TurnEvent("tool", "read_file"),
    TurnEvent("delta", "The config sets three backends."),
    TurnEvent("tool", "write_file"),
    TurnEvent("tool", "run_tests"),
    TurnEvent("delta", "Patched, and the suite is green."),
    TurnEvent("done", ""),
)


def test_tools_run_after_an_answer_start_a_new_workings_block():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, *TWO_ROUNDS)
            for _ in range(2):
                await pilot.pause()

            folds = list(app.query(Fold))
            assert len(folds) == 2, (
                f"expected a block per round of workings, got {len(folds)}"
            )
            assert str(folds[0].title) == "WORKINGS · 4 tool calls"
            assert str(folds[1].title) == "WORKINGS · 2 tool calls", (
                "the tools run after the answer were counted into the "
                "block above it"
            )
    _run(scenario())


def test_the_second_block_sits_under_the_answer_that_prompted_it():
    """Order is the whole point: above the answer says the wrong thing."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, *TWO_ROUNDS)
            for _ in range(2):
                await pilot.pause()

            assert _order(app) == [
                "fold:WORKINGS · 4 tool calls",
                "reply:The config sets three backends.",
                "fold:WORKINGS · 2 tool calls",
                "reply:Patched, and the suite is green.",
            ]
    _run(scenario())


def test_each_block_keeps_only_its_own_workings():
    """Opening the second block must not replay the first one's calls."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, *TWO_ROUNDS)
            for _ in range(2):
                await pilot.pause()

            first, second = list(app.query(Fold))
            assert [name for _kind, name in second._lines] == [
                "write_file", "run_tests",
            ]
            assert "read_file" not in [name for _kind, name in second._lines]
            assert "write_file" not in [name for _kind, name in first._lines]
    _run(scenario())


def test_a_turn_that_never_answers_mid_way_still_gets_one_block():
    """Splitting is driven by an answer arriving, not by a tool count."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", "thinking it through"),
                *[TurnEvent("tool", f"tool_{i}") for i in range(6)],
                TurnEvent("delta", "done deliberating."),
                TurnEvent("done", ""),
            )
            for _ in range(2):
                await pilot.pause()
            assert len(list(app.query(Fold))) == 1
    _run(scenario())


def test_the_answer_is_not_split_by_a_block_that_never_opened():
    """Reasoning between two deltas is one block, and one answer either side.

    Reasoning is workings like any other, so it opens a block and the answer
    resumes below it — but two deltas with nothing between them stay one
    paragraph rather than becoming two entries with a gutter name each.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("delta", "One sentence, "),
                TurnEvent("delta", "then the next."),
                TurnEvent("done", ""),
            )
            for _ in range(2):
                await pilot.pause()
            assert _order(app) == ["reply:One sentence, then the next."]
    _run(scenario())


def test_reasoning_after_an_answer_also_opens_a_new_block():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("delta", "First thought."),
                TurnEvent("reasoning", "on reflection, check the log"),
                TurnEvent("tool", "read_file"),
                TurnEvent("delta", "Second thought."),
                TurnEvent("done", ""),
            )
            for _ in range(2):
                await pilot.pause()
            assert _order(app) == [
                "reply:First thought.",
                "fold:WORKINGS · 1 tool call",
                "reply:Second thought.",
            ]
    _run(scenario())


def test_the_whole_turn_is_what_gets_spoken_not_its_last_paragraph():
    """Speech reads the answer, which is now more than one entry."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("delta", "First half. "),
                TurnEvent("tool", "read_file"),
                TurnEvent("delta", "Second half."),
            )
            await pilot.pause()
            assert app._bench().reply_text == "First half. Second half."

            _feed(app, TurnEvent("done", ""))
            await pilot.pause()
            assert app._bench().reply_text == "", (
                "the turn's answer outlived the turn"
            )
    _run(scenario())


def test_a_skin_change_repaints_every_block_not_just_the_open_one():
    """A sealed block held its old colours because it was off the record.

    Blocks used to be tracked in a single slot holding whichever one was
    open, so a skin change reached that one and left every earlier block in
    the palette it was drawn in.
    """
    from curie_cli.bench_ui.theme import resolve_palette
    from curie_cli.skin_engine import load_skin

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, *TWO_ROUNDS)
            for _ in range(2):
                await pilot.pause()

            vga = resolve_palette(load_skin("curie-vga"), dark=True)
            app._bench().restyle(vga)
            for _ in range(2):
                await pilot.pause()

            for index, fold in enumerate(app.query(Fold)):
                assert fold._palette is vga, (
                    f"block {index} kept the old skin's colours"
                )
    _run(scenario())


def test_rewinding_the_turn_takes_every_block_with_it():
    """A block left behind claims the agent ran tools unprompted."""
    async def scenario():
        app = BenchConsole(bridge=_QuietBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer", Composer).text = "do the thing"
            await pilot.press("enter")
            for _ in range(2):
                await pilot.pause()
            _feed(app, *TWO_ROUNDS)
            for _ in range(2):
                await pilot.pause()
            assert len(list(app.query(Fold))) == 2

            app.run_keyline_action("back")
            for _ in range(3):
                await pilot.pause()

            assert list(app.query(Fold)) == [], (
                "a block of workings survived the rewind"
            )
            assert list(app.query(Pen)) == [], (
                "a block's indicator was left behind in an empty row"
            )
            painted = "\n".join(_transcript_lines(app))
            for gone in (
                "do the thing",
                "The config sets three backends.",
                "WORKINGS",
            ):
                assert gone not in painted, f"{gone!r} survived the rewind"
            assert not [
                row for row in painted.splitlines() if row.strip()
            ], f"the rewind left rows behind: {painted!r}"
    _run(scenario())


# ── What the state instrument says while all this happens ────────────────

def test_a_tool_finishing_after_an_answer_reports_thinking_not_streaming():
    """The meters follow the block that is open, not the turn's history.

    The console used to decide this on whether any answer text had arrived
    this turn, which is true for the rest of the turn — so the state figure
    claimed to be streaming an answer while a tool the model reached for
    afterwards was still running.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("delta", "Reading the config now."),
                TurnEvent("tool", "read_file"),
            )
            await pilot.pause()
            assert app._activity == TOOL

            _feed(app, TurnEvent("tool_done", "read_file"))
            await pilot.pause()
            assert app._activity == THINKING, (
                "the console called it streaming while it was mid-workings"
            )
    _run(scenario())


def test_a_tool_finishing_mid_answer_hands_the_meters_back_to_the_answer():
    """The other side of it: no block open means the answer is what runs."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("tool", "read_file"),
                TurnEvent("delta", "Read it."),
                TurnEvent("tool_done", "read_file"),
            )
            await pilot.pause()
            assert app._activity == STREAMING
    _run(scenario())


# ── Blocks that open onto nothing ────────────────────────────────────────

def test_a_block_opened_and_never_used_is_dropped_when_it_is_sealed():
    """``start_thinking`` opens a block before anything is known to fill it.

    A turn that starts thinking and then answers without a single tool call
    or a token of reasoning would otherwise leave a WORKINGS control the
    reader can open onto an empty drawer.
    """
    from curie_cli.bench_ui.panes import BenchPane

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#pane-bench", BenchPane)

            pane.start_thinking()
            await pilot.pause()
            assert list(app.query(Fold)), "start_thinking opened no block"

            pane.begin_reply()
            pane.append_reply("nothing to look up.")
            for _ in range(2):
                await pilot.pause()
            assert list(app.query(Fold)) == [], (
                "an empty block was left above the answer"
            )

            pane.start_thinking()
            await pilot.pause()
            pane.end_turn()
            for _ in range(2):
                await pilot.pause()
            assert list(app.query(Fold)) == [], (
                "an empty block outlived the turn"
            )
    _run(scenario())


def test_clearing_mid_tool_does_not_grow_a_stray_block():
    """Ctrl+L while a tool runs, then its completion arrives.

    The tool is still the running one, so the completion is acted on — but
    the transcript it belonged to is gone, and putting the pen back to work
    would open a fresh WORKINGS block in a bench the reader just cleared.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("tool", "read_file"))
            await pilot.pause()
            app.run_keyline_action("clear")
            for _ in range(2):
                await pilot.pause()

            _feed(app, TurnEvent("tool_done", "read_file"))
            for _ in range(2):
                await pilot.pause()
            assert list(app.query(Fold)) == [], (
                "a completion re-opened a block in a cleared bench"
            )
            assert app._activity == WAITING, (
                "the meters claimed an answer was streaming into a bench "
                "that had never had one"
            )
    _run(scenario())


def test_a_reply_that_arrives_all_at_once_still_parks_the_pen():
    """Not every provider streams: some turns are reasoning, then the answer.

    The pen is parked when a block is sealed rather than only when deltas
    stop, so a turn whose whole reply arrives in the completion event does
    not leave an indicator sweeping over a finished conversation.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("reasoning", "worked it out"))
            for _ in range(2):
                await pilot.pause()
            assert app.query_one(Pen).running

            _feed(app, TurnEvent("done", "here is the whole answer."))
            for _ in range(2):
                await pilot.pause()
            assert not app.query_one(Pen).running, (
                "the pen kept sweeping after the turn ended"
            )
    _run(scenario())


def test_a_dropped_block_leaves_the_record_as_well_as_the_screen():
    """The record is what a restyle and a rewind walk.

    Blocks are entries now, so one that is dropped has to come off the list
    too. A widget that is gone from the screen but still on the record is
    the same shape of bug as the one that had sealed blocks missing a skin
    change: the list and the transcript disagree, and whichever of the two a
    later feature reads decides whether it is correct.
    """
    from curie_cli.bench_ui.panes import BenchPane

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#pane-bench", BenchPane)
            pane.start_thinking()
            await pilot.pause()
            pane.begin_reply()
            pane.append_reply("nothing to look up.")
            for _ in range(2):
                await pilot.pause()

            recorded = [widget for _kind, _text, widget in pane._entries]
            assert not any(isinstance(w, Fold) for w in recorded), (
                "a dropped block is still on the transcript's record"
            )
            for widget in recorded:
                assert widget.is_mounted, f"{widget!r} is recorded but gone"
    _run(scenario())


# ── One call, one line, however many hooks saw it ────────────────────────

def test_a_call_is_written_into_the_workings_once():
    """The console draws a call when it runs, not when it is being written.

    A model composing a large payload is worth an indicator, which is what
    the earlier ``tool_gen`` moment is for. It is not worth a second row in
    the fold and a second notch on the count — which is what the console
    used to give it, so a turn that made four calls read as eight.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("tool_gen", "terminal"),
                TurnEvent("tool", "terminal"),
                TurnEvent("tool_done", "terminal"),
                TurnEvent("tool_gen", "terminal"),
                TurnEvent("tool", "terminal"),
                TurnEvent("tool_done", "terminal"),
            )
            for _ in range(2):
                await pilot.pause()
            fold = app.query_one(Fold)
            assert str(fold.title) == "WORKINGS · 2 tool calls"
            assert [name for _kind, name in fold._lines] == [
                "terminal", "terminal",
            ]
    _run(scenario())


def test_a_call_still_being_written_moves_the_indicator():
    """It is the whole reason the earlier moment is reported at all."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("tool_gen", "write_file"))
            for _ in range(2):
                await pilot.pause()
            assert app._activity == TOOL
            assert "write_file" in app._activity_detail
    _run(scenario())


def test_a_call_written_and_then_abandoned_leaves_no_block_behind():
    """Models announce calls they never make; the agent nudges them for it.

    Opening a block on the announcement would split the answer in two
    around a drawer that turned out to have nothing in it.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("delta", "Here is the first half. "),
                TurnEvent("tool_gen", "write_file"),   # never runs
                TurnEvent("delta", "And the second."),
                TurnEvent("done", ""),
            )
            for _ in range(2):
                await pilot.pause()
            assert list(app.query(Fold)) == [], "an abandoned call left a block"
            assert _order(app) == [
                "reply:Here is the first half. And the second."
            ], "the answer was split around a call that never happened"
    _run(scenario())


def test_a_call_being_written_inside_a_block_re_aims_its_pen():
    """When a block *is* open, the announcement is what the pen follows."""
    from curie_cli.bench_ui.indicators import TOOL as TOOL_STATE

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("reasoning", "I will need to write that out"),
                TurnEvent("tool_gen", "write_file"),
            )
            for _ in range(2):
                await pilot.pause()
            pen = app.query_one(Pen)
            assert pen.running and pen._state == TOOL_STATE
    _run(scenario())


# ── Getting back out of a tool ───────────────────────────────────────────

def test_two_calls_at_once_both_have_to_finish():
    """A completion clears its own call, not every call of that name."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("tool", "terminal"),
                TurnEvent("tool", "terminal"),
                TurnEvent("tool_done", "terminal"),
            )
            await pilot.pause()
            assert app._activity == TOOL, (
                "the console called the turn done with tools while the "
                "second one was still running"
            )

            _feed(app, TurnEvent("tool_done", "terminal"))
            await pilot.pause()
            assert app._activity == THINKING
    _run(scenario())


def test_a_call_that_never_reports_finishing_does_not_strand_the_console():
    """A blocked call gets no completion event. The turn goes on regardless.

    This is the "stuck in the tool" fault: the executor skips the completion
    hook for a call it refused, so a console that waited for one sat on TOOL
    — naming a tool that was never going to finish — for the rest of the
    turn, however much the model went on to think or say.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(app, TurnEvent("tool", "terminal"))
            await pilot.pause()
            assert app._activity == TOOL

            # No tool_done — the call was blocked. The model thinks anyway,
            # and reaches for a tool that does report finishing. That second
            # call's completion has to hand the meters back: while the
            # refused one was still counted as running, it never could, and
            # the console sat on TOOL for the rest of the turn.
            _feed(
                app,
                TurnEvent("reasoning", "that was refused; try another"),
                TurnEvent("tool", "read_file"),
                TurnEvent("tool_done", "read_file"),
            )
            for _ in range(2):
                await pilot.pause()
            assert app._activity == THINKING, (
                "a call that never reported finishing kept the console in "
                "the tool state after a later call completed"
            )

            _feed(app, TurnEvent("delta", "I could not run that."))
            await pilot.pause()
            assert app._activity == STREAMING
    _run(scenario())


def test_an_answer_also_releases_a_call_that_never_finished():
    """The same recovery on the other side of the turn.

    A blocked call before the answer, then a tool the model reaches for
    after it: that second call's completion has to return the console to
    its workings, not leave it in a tool state owned by a refusal three
    events ago.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _feed(
                app,
                TurnEvent("tool", "terminal"),      # blocked; never finishes
                TurnEvent("delta", "That one is not allowed. Reading instead."),
                TurnEvent("tool", "read_file"),
                TurnEvent("tool_done", "read_file"),
            )
            for _ in range(2):
                await pilot.pause()
            assert app._activity == THINKING, (
                "the refused call was still counted as running after the "
                "answer that gave up on it"
            )
    _run(scenario())


# ── A request with nothing in it ─────────────────────────────────────────

def test_a_blank_request_is_never_sent():
    """An empty user turn is a question the model cannot answer.

    Dictation is where one comes from — a breath, a cough, a door — and the
    model, addressed with nothing, casts about for what it is meant to be
    doing. The reader cannot see the cause, because the turn that caused it
    shows as an empty line.
    """
    async def scenario():
        bridge = _QuietBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for blank in ("", "   ", "\n", "\t \n"):
                app._send(blank)
                # ``_dictated`` is the handler ``_voice_transcript`` marshals
                # onto the app's thread; called direct, it is what the
                # recogniser's sentence actually reaches.
                app._dictated(blank)
            for _ in range(2):
                await pilot.pause()
            assert bridge.submitted == [], (
                f"a blank request reached the agent: {bridge.submitted!r}"
            )
            assert _order(app) == [], "a blank request was written down"
    _run(scenario())


def test_a_dictated_sentence_is_still_sent():
    """The guard must not eat real speech."""
    async def scenario():
        bridge = _QuietBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._dictated("  what changed in the config  ")
            for _ in range(2):
                await pilot.pause()
            assert bridge.submitted == ["  what changed in the config  "]
    _run(scenario())


def test_silence_heard_during_a_turn_is_not_announced_as_speech():
    """Dictation that arrives mid-turn is held in the composer and said so.

    Worth having a reader see for a sentence. For a cough it is a notice
    about nothing, and a space quietly added to whatever they were typing.
    """
    async def scenario():
        bridge = _QuietBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#composer", Composer).text = "half a thought"
            app._send("keep the bench busy")
            for _ in range(2):
                await pilot.pause()
            assert app.bridge.busy or bridge.submitted

            notice = app.query_one("#notice")
            before = str(notice.render())
            app._dictated("   ")
            for _ in range(2):
                await pilot.pause()

            assert app.query_one("#composer", Composer).text == "half a thought", (
                "silence was appended to what the reader was typing"
            )
            assert str(notice.render()) == before, (
                "a cough was announced as something the console heard"
            )
    _run(scenario())
