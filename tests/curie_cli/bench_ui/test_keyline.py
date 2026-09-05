"""The function-key line has to work while the user is typing.

That is not a corner case: the composer holds focus for essentially the whole
life of the console, so a key that only reaches the app when focus happens to
be elsewhere is a key that does not work. Textual offers a keypress to the
focused widget's bindings before the app's, and the composer is a ``TextArea``,
which binds F6 to select-line and F7 to select-all — so those two keys were
swallowed and the switch they were painted on did nothing.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui.app import BenchConsole, KeyCap  # noqa: E402
from curie_cli.bench_ui.panes import Composer  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


#: Every key the console binds, and the action it must reach.
KEYLINE_KEYS = [
    ("f1", "help"),
    ("f2", "bench"),
    ("f3", "logbook"),
    ("f4", "instruments"),
    ("f5", "supply"),
    ("f6", "panel"),
    ("f7", "rail_toggle"),
    ("f8", "instruments_toggle"),
    ("f9", "diagnostics"),
    ("ctrl+l", "clear"),
    ("ctrl+r", "reload_logbook"),
]


@pytest.mark.parametrize("key,action", KEYLINE_KEYS)
def test_every_key_reaches_the_app_while_the_composer_has_focus(key, action):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            seen: list[str] = []
            original = app.run_keyline_action
            app.run_keyline_action = lambda a: (seen.append(a), original(a))[1]

            app.query_one("#composer", Composer).focus()
            await pilot.pause()
            assert isinstance(app.focused, Composer), "focus precondition"

            await pilot.press(key)
            await pilot.pause()
            assert seen == [action], (
                f"{key} did not reach the console while typing (got {seen!r}) "
                "— a focused widget claimed it first"
            )
    _run(scenario())


def test_the_composer_declines_the_actions_whose_keys_the_console_owns():
    """The composer must not merely lose the race — it must not enter it.

    Leaving ``TextArea``'s own F6/F7 bindings enabled would keep offering two
    keys in the composer's command list that it can never be given.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            for action in ("select_line", "select_all"):
                assert composer.check_action(action, ()) is None, (
                    f"{action} is still live on the composer; its key belongs "
                    "to the console's key line"
                )
            # Every other editing action is untouched.
            assert composer.check_action("cursor_left", ()) is True
    _run(scenario())


def test_f7_hides_and_restores_the_rail():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            rail = app.query_one("#rail")
            assert rail.display, "the rail should start visible at 120 columns"

            await pilot.press("f7")
            await pilot.pause()
            assert not rail.display, "F7 did not hide the rail"

            await pilot.press("f7")
            await pilot.pause()
            assert rail.display, "F7 did not bring the rail back"
    _run(scenario())


def test_a_hidden_rail_survives_a_resize():
    """The width-driven collapse must not undo an explicit hide.

    Recomputing rail visibility from the window width alone would put the rail
    straight back on the next resize event, which is emitted constantly while
    a window is being dragged.
    """
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f7")
            await pilot.pause()
            assert not app.query_one("#rail").display

            for width in (100, 140, 90):
                await pilot.resize_terminal(width, 40)
                for _ in range(2):
                    await pilot.pause()
                assert not app.query_one("#rail").display, (
                    f"the rail came back on resize to {width} columns"
                )
    _run(scenario())


def test_hiding_the_rail_gives_the_transcript_the_room():
    """The point of hiding it is the reading width, so prove it moved."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            before = app.query_one("#transcript").content_region.width
            await pilot.press("f7")
            for _ in range(2):
                await pilot.pause()
            after = app.query_one("#transcript").content_region.width
            assert after > before, (
                f"hiding the rail did not widen the transcript "
                f"({before} -> {after})"
            )
    _run(scenario())


def test_the_rail_toggle_is_on_the_key_line():
    """It was asked for as a visible control, not just a keybinding."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            caps = {
                cap.cap: cap.key_action
                for cap in app.query_one("#keyline").query(KeyCap)
            }
            assert caps.get("F7") == "rail_toggle", (
                f"F7 is not on the key line beside the others: {caps}"
            )
            # And it sits with the other pane keys rather than off with quit.
            order = [cap.cap for cap in app.query_one("#keyline").query(KeyCap)]
            assert order.index("F7") < order.index("^Q")
    _run(scenario())


def test_clicking_the_rail_keycap_works_too():
    """Every keycap is a mouse target; this one is no exception."""
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            cap = next(
                c for c in app.query_one("#keyline").query(KeyCap)
                if c.cap == "F7"
            )
            await pilot.click(cap)
            await pilot.pause()
            assert not app.query_one("#rail").display
    _run(scenario())
