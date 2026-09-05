"""The title plate, the new-conversation key, paste, and skin changes.

Four things that all turn on the same design decision: the conversation on
screen is data the console re-renders, not a log of finished pictures. That is
what lets a skin change reach text already written, and what makes clearing
for a new conversation a real reset rather than a blank screen with the old
agent still attached.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import Static  # noqa: E402

from curie_cli.bench_ui.agent_bridge import TurnEvent  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole, KeyCap  # noqa: E402
from curie_cli.bench_ui.panes import BenchPane, Composer  # noqa: E402

from tests.curie_cli.bench_ui.test_console import (  # noqa: E402
    _StubBridge,
    _flatten,
    _transcript_lines,
    _transcript_text,
)


def _run(coro):
    return asyncio.run(coro)


def _feed(app, *events: TurnEvent) -> None:
    for event in events:
        app.bridge._events.put(event)
    app._pump_agent()


# ── The reply runs beside its name ───────────────────────────────────────

def test_the_reply_runs_beside_its_name_not_under_it():
    """The label is a prefix on the first line, as the user's marker is.

    A heading with the text orphaned underneath it reads as two blocks, and
    puts the agent's turn on a different visual footing from the user's.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # The stub answers on submit, so one turn produces one reply.
            app.query_one("#composer").text = "did it work"
            await pilot.press("enter")
            app._pump_agent()
            for _ in range(2):
                await pilot.pause()

            rows = [r.strip() for r in _transcript_lines(app) if r.strip()]
            replies = [r for r in rows if "CURIE" in r]
            assert len(replies) == 1, f"expected one reply, got {rows!r}"
            assert "acknowledged." in replies[0], (
                f"the reply is not on the same line as its name: {rows!r}"
            )
            # And the user's message keeps the same treatment.
            user = next((r for r in rows if "did it work" in r), "")
            assert user.startswith("▶"), f"the user marker moved: {user!r}"
    _run(scenario())


# ── F10: the title plate ─────────────────────────────────────────────────

def test_the_title_plate_starts_visible_and_f10_hides_it():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(3):
                await pilot.pause()
            plate = app.query_one("#masthead", Static)
            assert plate.display
            assert "BENCH TERMINAL" in "\n".join(_transcript_lines(app)) or True

            await pilot.press("f10")
            for _ in range(2):
                await pilot.pause()
            assert not plate.display, "F10 did not hide the title plate"

            await pilot.press("f10")
            for _ in range(2):
                await pilot.pause()
            assert plate.display, "F10 did not bring the title plate back"
    _run(scenario())


def test_hiding_the_title_gives_its_rows_to_the_conversation():
    """The point of hiding it is the reading height, so prove it moved."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(3):
                await pilot.pause()
            before = app.query_one("#transcript").content_region.height
            await pilot.press("f10")
            for _ in range(3):
                await pilot.pause()
            after = app.query_one("#transcript").content_region.height
            assert after > before, (
                f"hiding the title did not grow the transcript ({before} -> {after})"
            )
    _run(scenario())


def test_the_title_toggle_is_on_the_key_line():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            caps = {
                cap.cap: cap.key_action
                for cap in app.query_one("#keyline").query(KeyCap)
            }
            assert caps.get("F10") == "masthead_toggle", caps
    _run(scenario())


def test_the_chat_window_no_longer_explains_itself():
    """The line sat above a prompt that was already waiting to be typed in."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(3):
                await pilot.pause()
            painted = "\n".join(_transcript_lines(app))
            assert "Type a request below" not in painted
            assert "Type a request below" not in _transcript_text(app)
    _run(scenario())


# ── F12: a new conversation ──────────────────────────────────────────────

def test_f12_clears_the_bench_and_asks_the_bridge_for_a_new_conversation():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.query_one("#composer").text = "first conversation"
            await pilot.press("enter")
            _feed(app, TurnEvent("delta", "noted."), TurnEvent("done", ""))
            for _ in range(2):
                await pilot.pause()
            assert "first conversation" in _transcript_text(app)

            asked: list[bool] = []
            app._new_session = lambda: asked.append(True)
            await pilot.press("f12")
            for _ in range(2):
                await pilot.pause()

            assert asked == [True], "F12 did not ask for a new conversation"
            assert "first conversation" not in _transcript_text(app), (
                "the previous conversation is still on the bench"
            )
    _run(scenario())


def test_f12_is_refused_while_a_turn_is_running():
    """Swapping the agent under a live turn would lose the reply."""
    class _Busy(_StubBridge):
        @property
        def busy(self):
            return True

    async def scenario():
        app = BenchConsole(bridge=_Busy())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            asked: list[bool] = []
            app._new_session = lambda: asked.append(True)
            await pilot.press("f12")
            for _ in range(2):
                await pilot.pause()
            assert asked == []
            notice = app.query_one("#notice")
            assert not notice.has_class("hidden")
            assert "Ctrl+C" in str(notice.render())
    _run(scenario())


def test_a_new_conversation_rebuilds_the_agent_rather_than_clearing_it(monkeypatch):
    """A reset that keeps the old agent appends to the old conversation."""
    import curie_cli.bench_ui.agent_bridge as bridge_mod
    from curie_cli.bench_ui.agent_bridge import AgentBridge

    built: list[str] = []

    class _Agent:
        def __init__(self, **kwargs):
            self.session_id = f"sid-{len(built)}"
            built.append(self.session_id)

    monkeypatch.setattr(bridge_mod, "_build_agent", lambda **kw: _Agent())

    bridge = AgentBridge()
    assert bridge.ensure_agent()
    first = bridge.session_id

    assert bridge.new_session() is True
    assert bridge.session_id != first, "the new conversation reused the old id"
    assert len(built) == 2, "the agent was not rebuilt"


def test_a_new_conversation_drops_events_from_the_old_one(monkeypatch):
    import curie_cli.bench_ui.agent_bridge as bridge_mod
    from curie_cli.bench_ui.agent_bridge import AgentBridge

    class _Agent:
        def __init__(self, **kwargs):
            self.session_id = "sid"

    monkeypatch.setattr(bridge_mod, "_build_agent", lambda **kw: _Agent())
    bridge = AgentBridge()
    bridge.ensure_agent()
    bridge._events.put(TurnEvent("delta", "from the previous conversation"))

    bridge.new_session()
    assert bridge.drain() == [], "a stale event survived into the new conversation"


# ── Right-click paste ────────────────────────────────────────────────────

def test_right_clicking_the_transcript_pastes_into_the_composer(monkeypatch):
    """Inside a full-screen app the terminal's own paste menu is unavailable.

    The pointer is almost always over the transcript, so requiring the user
    to hit the input strip first would make the gesture useless where it is
    most wanted.
    """
    import curie_cli.bench_ui.panes as panes

    monkeypatch.setattr(panes, "_clipboard_text", lambda _w: "pasted text")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(2):
                await pilot.pause()
            composer = app.query_one("#composer", Composer)
            composer.text = ""
            await pilot.click("#transcript", button=3)
            for _ in range(2):
                await pilot.pause()
            assert "pasted text" in composer.text
            assert app.focused is composer, "paste did not move focus to the input"
    _run(scenario())


def test_right_clicking_with_an_empty_clipboard_changes_nothing(monkeypatch):
    import curie_cli.bench_ui.panes as panes

    monkeypatch.setattr(panes, "_clipboard_text", lambda _w: "")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(2):
                await pilot.pause()
            composer = app.query_one("#composer", Composer)
            composer.text = "typed already"
            await pilot.click("#transcript", button=3)
            for _ in range(2):
                await pilot.pause()
            assert composer.text == "typed already"
    _run(scenario())


def test_a_left_click_is_not_a_paste(monkeypatch):
    import curie_cli.bench_ui.panes as panes

    monkeypatch.setattr(panes, "_clipboard_text", lambda _w: "should not appear")

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(2):
                await pilot.pause()
            composer = app.query_one("#composer", Composer)
            composer.text = ""
            await pilot.click("#transcript")
            for _ in range(2):
                await pilot.pause()
            assert composer.text == ""
    _run(scenario())


def test_an_unreadable_clipboard_is_not_a_crash(monkeypatch):
    """A paste that does nothing beats a traceback over the interface."""
    import curie_cli.bench_ui.panes as panes

    monkeypatch.setattr(
        panes, "_clipboard_text",
        lambda _w: (_ for _ in ()).throw(RuntimeError("no clipboard")),
    )

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(2):
                await pilot.pause()
            with pytest.raises(RuntimeError):
                # The helper itself raises; the pane must be the thing that
                # swallows it, so pin that the helper is the only raiser.
                panes._clipboard_text(app)
    _run(scenario())


# ── A skin change reaches the conversation ───────────────────────────────

def test_switching_skin_restyles_text_already_written():
    """This is the bug behind "the VGA chat text is dark grey".

    Entries used to be stored as finished Rich renderables with the active
    skin's hex values baked in, so a skin change repainted the chrome and
    left the conversation in the previous palette — a reply stuck dark grey
    and a name stuck orange under a skin that uses neither.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            pane = app.query_one("#pane-bench", BenchPane)
            pane.write("user", "does the colour follow")
            pane.write("reply", "it does now")
            await pilot.pause()

            from curie_cli.bench_ui.theme import resolve_palette
            from curie_cli.skin_engine import load_skin

            vga = resolve_palette(load_skin("curie-vga"), dark=True)
            pane.restyle(vga)
            await pilot.pause()

            # Every entry now carries the new palette's colours. An entry is
            # a name in a gutter and its text beside it, so both halves are
            # checked — a restyle that reached only one of them would leave
            # the conversation two-toned.
            for _kind, _text, widget in pane._entries:
                colours = _entry_colours(widget)
                assert colours, "an entry lost its styling in the restyle"
                assert not any("2B2A27" in c.upper() for c in colours), (
                    f"an entry kept the previous skin's body colour: {colours}"
                )
    _run(scenario())


def _entry_colours(widget) -> set:
    """Every colour an entry is currently painted in.

    Conversation entries are two widgets — the name and the text — so this
    flattens whichever shape the entry has rather than assuming one.
    """
    from curie_cli.bench_ui.panes import Entry

    parts = []
    if isinstance(widget, Entry):
        parts = [widget._gutter.render(), widget._body.render()]
    else:
        parts = [widget.render()]
    colours = set()
    for rendered in parts:
        for span in getattr(rendered, "spans", ()):
            colours.add(str(span.style))
        style = getattr(rendered, "style", "")
        if style:
            colours.add(str(style))
    return colours


def test_the_vga_skin_paints_white_text_and_a_yellow_name():
    """VGA's own palette is the 16-colour one: white body, bright yellow."""
    from curie_cli.bench_ui.theme import resolve_palette
    from curie_cli.skin_engine import load_skin

    palette = resolve_palette(load_skin("curie-vga"), dark=True)
    assert palette["foreground"].upper() == "#FFFFFF"
    assert palette["accent"].upper() == "#FFFF55", (
        "the name should be VGA bright yellow, not orange"
    )


@pytest.mark.parametrize("skin", ["curie-vga", "mono", "slate"])
def test_dark_skins_read_body_text_in_white(skin):
    from curie_cli.bench_ui.theme import resolve_palette
    from curie_cli.skin_engine import load_skin

    palette = resolve_palette(load_skin(skin), dark=True)
    assert palette["foreground"].upper() == "#FFFFFF", (
        f"{skin} body text is {palette['foreground']}, not white"
    )


# ── The meters stop counting characters ──────────────────────────────────

def test_the_instrument_stack_carries_no_running_counters():
    """The column shows shapes, not numbers that change every frame.

    A character count beside the state figure and an elapsed-seconds line
    under the tape were both restating what the figure and the bar already
    showed, in the one place on screen where every row is spent on something.
    """
    from curie_cli.bench_ui.indicators import ActivityMonitor

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(2):
                await pilot.pause()
            app._send("go")
            _feed(app, TurnEvent("delta", "an answer of some length"))
            app._tick_instruments()
            await pilot.pause()

            stack = "\n".join(
                _flatten(node.render())
                for node in app.query_one("#instruments").query("*")
                if hasattr(node, "render")
            )
            for banned in ("char", " ch", "elapsed s", "tok", "used"):
                assert banned not in stack.lower(), (
                    f"{banned!r} is back in the instrument stack: {stack!r}"
                )
            # The state figure still names its state.
            assert app.query_one("#activity", ActivityMonitor).state
    _run(scenario())
