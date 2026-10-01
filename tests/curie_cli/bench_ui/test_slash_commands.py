"""Slash commands in the bench console's composer.

A line starting with ``/`` is resolved the way the CLI resolves it —
registry built-ins and their aliases, quick commands, plugin commands, skill
bundles and skills, and a unique prefix of any of them — and carried out by
the console instead of being sent to the model as a question. The list above
the composer is drawn from the same catalogue, so what it offers is what runs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import slash  # noqa: E402
from curie_cli.bench_ui.app import BenchConsole  # noqa: E402
from curie_cli.bench_ui.panes import BenchPane, Composer, SlashPopup  # noqa: E402
from curie_cli.bench_ui.settings import read_settings  # noqa: E402
from curie_cli.bench_ui.slash import (  # noqa: E402
    BENCH_BUILTINS,
    Extensions,
    Suggestion,
    builtin_suggestions,
    extension_suggestions,
    looks_like_command,
    match_suggestions,
    resolve,
    run_quick_command,
    split_line,
)

from tests.curie_cli.bench_ui.test_console import _StubBridge, _transcript_text  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 4) -> None:
    for _ in range(times):
        await pilot.pause()


async def _until(pilot, condition, attempts: int = 60) -> bool:
    for _ in range(attempts):
        if condition():
            return True
        await pilot.pause(0.05)
    return condition()


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


SKILLS = {
    "/gif-search": {"name": "gif-search", "description": "Find a GIF for the moment."},
    "/arxiv": {"name": "arxiv", "description": "Search arXiv papers."},
}


@pytest.fixture
def extensions(monkeypatch):
    """Skills, a quick command and a plugin command, as the console gathers them."""
    found = Extensions(
        skills=dict(SKILLS),
        quick={
            "hi": {"type": "exec", "command": "echo hello from quick"},
            "lock": {"type": "alias", "target": "/unlock"},
        },
        plugins={"pluggy": {"description": "A plugin's command."}},
    )
    monkeypatch.setattr(slash, "gather_extensions", lambda: found)
    return found


@pytest.fixture
def skill_loader(monkeypatch):
    """What the CLI's skill loader would build, recorded."""
    calls = []

    def build(key, instruction, task_id=None, **_kw):
        calls.append((key, instruction))
        return f"[SKILL {key}]\n{instruction}"

    import agent.skill_commands as skill_commands

    monkeypatch.setattr(skill_commands, "build_skill_invocation_message", build)
    return calls


def _composer(app) -> Composer:
    return app.query_one("#composer", Composer)


def _popup(app) -> SlashPopup:
    return app.query_one("#slash-popup", SlashPopup)


async def _submit(pilot, app, line: str) -> None:
    composer = _composer(app)
    composer.focus()
    composer.text = line
    await pilot.pause()
    app._submit_turn()
    await _settle(pilot)


async def _catalogue_ready(pilot, app) -> None:
    await _until(pilot, lambda: any(s.kind == "skill" for s in app.slash_catalogue()))


# ── What counts as a command ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "line, command",
    [
        ("/unlock", True),
        ("/title My chat", True),
        ("/gif-search cats", True),
        ("/etc/hosts is empty, why?", False),
        ("/Users/me/notes.md: summarise this", False),
        ("//help is a word I want to send", False),
        ("/", False),
        ("hello /unlock", False),
        ("", False),
    ],
)
def test_a_command_is_a_slash_and_a_word_a_path_is_not(line, command):
    assert looks_like_command(line) is command


def test_a_line_splits_into_its_command_and_arguments():
    assert split_line("/title   My   chat ") == ("title", "My   chat")
    assert split_line("/unlock") == ("unlock", "")


# ── Resolution ───────────────────────────────────────────────────────────


def test_every_bench_builtin_is_a_registry_command():
    from curie_cli.commands import resolve_command

    for name in BENCH_BUILTINS:
        cmd = resolve_command(name)
        assert cmd is not None and cmd.name == name, name


def test_aliases_resolve_to_their_command():
    call = resolve("/fork a different path")
    assert (call.kind, call.name, call.args) == ("builtin", "branch", "a different path")


def test_a_unique_prefix_is_the_command_it_starts():
    call = resolve("/unl on")
    assert (call.kind, call.name, call.args) == ("builtin", "unlock", "on")


def test_an_ambiguous_prefix_names_the_candidates():
    call = resolve("/re")
    assert call.kind == "ambiguous"
    assert {"retry", "resume"} <= set(call.detail)


def test_a_command_from_another_surface_is_named_as_such():
    """Sent to the model, ``/model gpt-5`` would be answered as a question."""
    from curie_cli.commands import COMMAND_REGISTRY

    elsewhere = next(c for c in COMMAND_REGISTRY if c.name not in BENCH_BUILTINS)
    call = resolve(f"/{elsewhere.name}")
    assert call.kind == "unsupported"
    assert f"/{elsewhere.name}" in slash.unsupported_reason(call.detail)


def test_skills_quick_and_plugin_commands_resolve(extensions):
    assert resolve("/gif-search cats", extensions).kind == "skill"
    # The CLI accepts the underscore spelling of a hyphenated skill.
    assert resolve("/gif_search cats", extensions).name == "/gif-search"
    assert resolve("/hi", extensions).kind == "quick"
    assert resolve("/pluggy x", extensions).kind == "plugin"
    assert resolve("/gif", extensions).name == "/gif-search"
    assert resolve("/nonesuch", extensions).kind == "unknown"


def test_a_builtin_wins_over_a_skill_of_the_same_name():
    found = Extensions(skills={"/status": {"name": "status"}})
    assert resolve("/status", found).kind == "builtin"


# ── The completion list's order ──────────────────────────────────────────


def test_suggestions_start_with_names_that_start_with_what_was_typed():
    catalogue = [
        Suggestion("unlock", kind="command"),
        Suggestion("skill-unlocker", kind="skill"),
        Suggestion("unload", kind="skill"),
    ]
    names = [s.name for s in match_suggestions("unl", catalogue)]
    assert names == ["unlock", "unload", "skill-unlocker"]


def test_an_exact_match_leads():
    catalogue = [Suggestion("stopwatch", kind="command"), Suggestion("stop", kind="skill")]
    assert match_suggestions("stop", catalogue)[0].name == "stop"


def test_an_alias_is_offered_only_when_its_command_is_not():
    catalogue = builtin_suggestions()
    every = [s.name for s in match_suggestions("", catalogue)]
    assert "branch" in every and "fork" not in every
    assert [s.name for s in match_suggestions("fo", catalogue)] == ["fork"]


def test_extension_suggestions_carry_their_kind(extensions):
    kinds = {s.name: s.kind for s in extension_suggestions(extensions)}
    assert kinds["gif-search"] == "skill"
    assert kinds["hi"] == "quick"
    assert kinds["pluggy"] == "plugin"


# ── The completion list on screen ────────────────────────────────────────


def test_typing_a_slash_opens_the_list_and_letters_narrow_it(extensions):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _catalogue_ready(pilot, app)
            composer = _composer(app)
            composer.focus()
            await pilot.press("slash")
            await _settle(pilot)
            popup = _popup(app)
            assert popup.active
            everything = len(popup.matches)
            assert everything > 1
            for key in "gif":
                await pilot.press(key)
            await _settle(pilot)
            assert [s.name for s in popup.matches] == ["gif-search"]
            await pilot.press("escape")
            await _settle(pilot)
            assert not popup.active
            assert composer.text == "/gif"

    _run(scenario())


def test_tab_completes_and_the_list_becomes_a_usage_line(extensions):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _catalogue_ready(pilot, app)
            composer = _composer(app)
            composer.focus()
            for key in ("slash", "t", "i", "t"):
                await pilot.press(key)
            await _settle(pilot)
            await pilot.press("tab")
            await _settle(pilot)
            assert composer.text == "/title "
            popup = _popup(app)
            assert popup.display and not popup.active
            assert "/title" in str(popup.render())

    _run(scenario())


def test_a_path_never_opens_the_list():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            composer = _composer(app)
            composer.text = "/etc/hosts"
            await _settle(pilot)
            assert not _popup(app).display

    _run(scenario())


def test_enter_on_a_partial_name_runs_the_highlighted_command():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            composer = _composer(app)
            composer.focus()
            for key in ("slash", "u", "n", "l"):
                await pilot.press(key)
            await _settle(pilot)
            await pilot.press("enter")
            await _settle(pilot)
            assert app._unlock is True
            assert composer.text == ""
            assert bridge.submitted == []

    _run(scenario())


# ── Running them ─────────────────────────────────────────────────────────


def test_unlock_throws_the_switch_and_is_not_sent_to_the_model():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            await _submit(pilot, app, "/unlock")
            assert app._unlock is True
            assert bridge.unlocked is True
            assert read_settings().unlock is True
            await _submit(pilot, app, "/unlock off")
            assert app._unlock is False
            assert read_settings().unlock is False
            assert bridge.submitted == []
            assert _composer(app).text == ""

    _run(scenario())


def test_a_path_and_the_double_slash_escape_go_to_the_model():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            await _submit(pilot, app, "/etc/hosts is empty, why?")
            await _until(pilot, lambda: not app.bridge.busy and len(bridge.submitted) == 1)
            await _submit(pilot, app, "//unlock is a word")
            await _until(pilot, lambda: len(bridge.submitted) == 2)
            assert bridge.submitted == ["/etc/hosts is empty, why?", "/unlock is a word"]
            assert app._unlock is False

    _run(scenario())


def test_an_unknown_command_stays_in_the_composer():
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            await _submit(pilot, app, "/definitely-not-a-command now")
            assert _composer(app).text == "/definitely-not-a-command now"
            assert bridge.submitted == []

    _run(scenario())


def test_a_skill_command_sends_the_skill_and_shows_what_was_typed(extensions, skill_loader):
    async def scenario():
        bridge = _StubBridge()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _catalogue_ready(pilot, app)
            await _submit(pilot, app, "/gif-search cats in hats")
            assert await _until(pilot, lambda: bridge.submitted)
            assert skill_loader == [("/gif-search", "cats in hats")]
            assert bridge.submitted == ["[SKILL /gif-search]\ncats in hats"]
            await _settle(pilot)
            text = _transcript_text(app)
            assert "/gif-search cats in hats" in text
            assert "[SKILL" not in text

    _run(scenario())


def test_a_quick_command_alias_runs_its_target(extensions):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _catalogue_ready(pilot, app)
            await _submit(pilot, app, "/lock")
            assert app._unlock is True

    _run(scenario())


def test_an_exec_quick_command_prints_what_it_printed(extensions):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _catalogue_ready(pilot, app)
            await _submit(pilot, app, "/hi")
            assert await _until(pilot, lambda: "hello from quick" in _transcript_text(app), 100)

    _run(scenario())


def test_run_quick_command_reports_rather_than_raises():
    assert run_quick_command({}).startswith("(")
    assert "hello" in run_quick_command({"command": "echo hello"})


def test_title_names_the_conversation_or_says_there_is_none(monkeypatch):
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            # No conversation yet: refused, and the line kept for later.
            await _submit(pilot, app, "/title Project notes")
            assert _composer(app).text == "/title Project notes"

            renamed = []
            monkeypatch.setattr(
                "curie_cli.bench_ui.sessions.rename_session",
                lambda sid, title: renamed.append((sid, title)) or "",
            )
            app.bridge._agent = SimpleNamespace(session_id="sess-1")
            await _submit(pilot, app, "/title Project notes")
            assert renamed == [("sess-1", "Project notes")]
            assert _composer(app).text == ""

    _run(scenario())


def test_copy_puts_the_last_reply_on_the_clipboard(monkeypatch):
    landed: list[str] = []
    monkeypatch.setattr(
        "curie_cli.clipboard.write_clipboard_text", lambda text: landed.append(text) or True
    )
    for name in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"):
        monkeypatch.delenv(name, raising=False)

    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            pane = app.query_one("#pane-bench", BenchPane)
            pane.write("user", "first?")
            pane.write("reply", "the first answer")
            pane.write("user", "second?")
            pane.write("reply", "the second answer")
            await _settle(pilot)
            await _submit(pilot, app, "/copy")
            await _submit(pilot, app, "/copy 1")
            assert landed[-2:] == ["the second answer", "the first answer"]

    _run(scenario())


def test_a_command_that_changes_the_conversation_waits_for_the_turn():
    class _Busy(_StubBridge):
        @property
        def busy(self) -> bool:
            return True

    async def scenario():
        bridge = _Busy()
        app = BenchConsole(bridge=bridge)
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            await _submit(pilot, app, "/new")
            assert _composer(app).text == "/new"
            # The ones a reader reaches for mid-turn still run.
            await _submit(pilot, app, "/unlock")
            assert app._unlock is True

    _run(scenario())


def test_help_lists_the_slash_commands():
    async def scenario():
        app = BenchConsole(bridge=_StubBridge())
        async with app.run_test(size=(140, 44)) as pilot:
            await _settle(pilot)
            await _submit(pilot, app, "/help")
            text = _transcript_text(app)
            assert "SLASH COMMANDS" in text
            assert "/unlock" in text

    _run(scenario())
