"""Slash commands on the bench — ``/unlock``, ``/skill-name`` and the rest.

A line typed into the composer that starts with ``/`` is a command rather
than a message, resolved the way the CLI resolves it, in the CLI's order:

1. **Built-ins** from the central registry (:mod:`curie_cli.commands`) —
   names and aliases, so ``/fork`` is ``/branch`` here as everywhere. The
   console carries out the ones that mean something on a bench
   (:data:`BENCH_BUILTINS`); the rest are named as belonging to another
   surface rather than sent to the model as if they were a question.
2. **Quick commands** from ``quick_commands`` in ``config.yaml``.
3. **Plugin commands** registered through the plugin API.
4. **Skill bundles**, then **skills** — ``/gif-search cats`` loads the skill
   and sends it as a turn, exactly as the CLI does, stacked up to five deep
   (``/skill-a /skill-b do this``).
5. A **unique prefix** of any of those — ``/unl`` is ``/unlock``.

A line whose first word has a second ``/`` in it is a path, not a command —
``/etc/hosts is empty, why?`` is a question — which is the CLI's rule too.
``//`` sends a line that starts with a slash as a message.

The completion list above the composer (:class:`SlashPopup` in
:mod:`curie_cli.bench_ui.panes`) is drawn from the same catalogue the
resolver reads, so what is offered is exactly what will run.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: The registry commands the console carries out itself, by canonical name,
#: and whether each may run while a turn is in flight. The ones that may not
#: are the ones that change *which* conversation is on the bench or what is
#: in it — doing that under a live turn would lose whichever came second.
BENCH_BUILTINS: Dict[str, bool] = {
    "help": True,
    "commands": True,
    "new": False,
    "clear": False,
    "retry": False,
    "undo": False,
    "title": True,
    "branch": False,
    "resume": False,
    "sessions": True,
    "status": True,
    "unlock": True,
    "approvals": True,
    "stop": True,
    "copy": True,
    "skin": True,
    "voice": True,
    "config": True,
    "tools": True,
    "toolsets": True,
    "plugins": True,
    "skills": True,
    "cron": True,
    "reload-skills": True,
    "version": True,
    "profile": True,
    "bundles": True,
    "quit": True,
}

#: Built-ins answered by a pane rather than by text: the pane already shows
#: everything the command would print, kept current.
PANE_COMMANDS: Dict[str, str] = {
    "sessions": "logbook",
    "config": "instruments",
    "tools": "supply",
    "toolsets": "supply",
    "plugins": "supply",
    "cron": "schedule",
}

#: Built-ins whose text comes from the registry's shared executors
#: (:mod:`curie_cli.slash_exec`), so the console prints the same words every
#: other surface does.
EXECUTOR_COMMANDS = frozenset({"version", "profile", "bundles"})

#: How many suggestions the list above the composer shows at once.
SUGGESTION_ROWS = 6


def looks_like_command(text: str) -> bool:
    """Whether a composed line is a slash command rather than a message.

    The CLI's rule (``cli._looks_like_slash_command``): after the leading
    slash a command's first word has no further slashes, and a path's always
    does — so ``/Users/me/notes.md: summarise this`` goes to the model.
    ``//`` is the way to send a line that starts with a slash anyway.
    """
    if not text or not text.startswith("/") or text.startswith("//"):
        return False
    first = text.split()[0]
    return len(first) > 1 and "/" not in first[1:]


def split_line(line: str) -> Tuple[str, str]:
    """``"/title  My chat "`` → ``("title", "My chat")``."""
    stripped = (line or "").strip()
    if not stripped.startswith("/"):
        return "", stripped
    parts = stripped[1:].split(None, 1)
    token = parts[0] if parts else ""
    return token, (parts[1].strip() if len(parts) > 1 else "")


# ── The catalogue ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Suggestion:
    """One entry in the completion list."""

    name: str
    description: str = ""
    hint: str = ""
    #: ``command``, ``alias``, ``skill``, ``bundle``, ``quick`` or ``plugin``.
    kind: str = "command"
    #: For an alias, the command it stands for.
    target: str = ""


@dataclass
class Extensions:
    """Everything beyond the registry that a slash can name, gathered once.

    Gathered on a worker (see ``SlashCommandsMixin._gather_extensions``): a
    skill scan reads every ``SKILL.md`` on disk and plugin discovery imports
    plugins, neither of which belongs on the keystroke that opens the list.
    """

    skills: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    bundles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    quick: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plugins: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def gather_extensions() -> Extensions:
    """Read the skills, bundles, quick commands and plugin commands in force.

    Every source is optional and every failure is an empty source: a broken
    skill folder must cost the console its skill commands, not its commands.
    """
    found = Extensions()
    try:
        from agent.skill_commands import get_skill_commands

        found.skills = dict(get_skill_commands() or {})
    except Exception:
        pass
    try:
        from agent.skill_bundles import get_skill_bundles

        found.bundles = dict(get_skill_bundles() or {})
    except Exception:
        pass
    try:
        from curie_cli.config import load_config_readonly

        quick = (load_config_readonly() or {}).get("quick_commands") or {}
        if isinstance(quick, Mapping):
            found.quick = {
                str(name).lower(): dict(value)
                for name, value in quick.items()
                if isinstance(value, Mapping)
            }
    except Exception:
        pass
    try:
        from curie_cli.plugins import get_plugin_commands

        found.plugins = {
            str(name).lower(): dict(value) if isinstance(value, Mapping) else {}
            for name, value in (get_plugin_commands() or {}).items()
        }
    except Exception:
        pass
    return found


def builtin_suggestions() -> List[Suggestion]:
    """The console's own commands, with their aliases, from the registry."""
    try:
        from curie_cli.commands import COMMAND_REGISTRY
    except Exception:
        return []
    out: List[Suggestion] = []
    for cmd in COMMAND_REGISTRY:
        if cmd.name not in BENCH_BUILTINS:
            continue
        out.append(Suggestion(cmd.name, cmd.description, cmd.args_hint, "command"))
        for alias in cmd.aliases:
            if alias.replace("_", "-") == cmd.name:
                continue
            out.append(Suggestion(alias, cmd.description, cmd.args_hint, "alias", cmd.name))
    return out


def extension_suggestions(found: Extensions) -> List[Suggestion]:
    out: List[Suggestion] = []
    for key, info in sorted(found.bundles.items()):
        out.append(Suggestion(key.lstrip("/"), _describe(info, "skill bundle"), "", "bundle"))
    for key, info in sorted(found.skills.items()):
        out.append(Suggestion(key.lstrip("/"), _describe(info, "skill"), "[instruction]", "skill"))
    for name, info in sorted(found.quick.items()):
        what = info.get("description") or info.get("command") or info.get("target") or ""
        out.append(Suggestion(name, f"quick command — {what}".rstrip(" —"), "", "quick"))
    for name, info in sorted(found.plugins.items()):
        out.append(Suggestion(name, _describe(info, "plugin command"), "", "plugin"))
    return out


def _describe(info: Mapping[str, Any], fallback: str) -> str:
    text = " ".join(str(info.get("description") or "").split())
    return text or fallback


#: The order kinds are listed in when names tie: the console's own commands
#: first, because they are what a reader typing ``/`` is most often after.
_KIND_RANK = {"command": 0, "alias": 1, "bundle": 2, "skill": 3, "quick": 4, "plugin": 5}


def match_suggestions(
    prefix: str, catalogue: Iterable[Suggestion], limit: int = 50
) -> List[Suggestion]:
    """The suggestions for what has been typed so far, best first.

    Names that *start* with the prefix come before names that merely contain
    it; within those an exact match leads, then the console's own commands,
    then the alphabet. An alias is only offered when its command is not
    already on the list — ``/fork`` for a reader who typed ``/fo``, never
    ``/fork`` beside ``/branch`` for a reader who typed ``/``.
    """
    wanted = (prefix or "").lstrip("/").lower()
    starts: List[Suggestion] = []
    contains: List[Suggestion] = []
    for entry in catalogue:
        name = entry.name.lower()
        if name.startswith(wanted):
            starts.append(entry)
        elif wanted and wanted in name:
            contains.append(entry)

    def order(entry: Suggestion) -> tuple:
        return (entry.name.lower() != wanted, _KIND_RANK.get(entry.kind, 9), entry.name)

    ranked = sorted(starts, key=order) + sorted(contains, key=order)
    shown = {e.name for e in ranked if e.kind != "alias"}
    out: List[Suggestion] = []
    for entry in ranked:
        if entry.kind == "alias" and (not wanted or entry.target in shown):
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


# ── Resolution ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlashCall:
    """What a typed slash line turned out to name."""

    #: ``builtin``, ``unsupported``, ``quick``, ``plugin``, ``bundle``,
    #: ``skill``, ``ambiguous`` or ``unknown``.
    kind: str
    #: The canonical name: a registry name, a quick/plugin name, or a
    #: ``/key`` for skills and bundles.
    name: str
    args: str = ""
    typed: str = ""
    detail: Any = None


def resolve(line: str, found: Optional[Extensions] = None) -> SlashCall:
    """Work out what a slash line names. Pure: nothing is run here."""
    token, args = split_line(line)
    lowered = token.lower()
    if not lowered:
        return SlashCall("unknown", "", args, token)
    found = found if found is not None else Extensions()

    call = _resolve_exact(lowered, args, token, found)
    if call is not None:
        return call

    # A unique prefix of anything that would have resolved exactly.
    names = {s.name.lower() for s in builtin_suggestions()}
    names |= {k.lstrip("/").lower() for k in found.skills}
    names |= {k.lstrip("/").lower() for k in found.bundles}
    names |= set(found.quick) | set(found.plugins)
    matches = sorted(n for n in names if n.startswith(lowered))
    if len(matches) > 1:
        # Two spellings of one command (a name and its alias) are one match.
        canonical = {_canonical_builtin(m) or m for m in matches}
        if len(canonical) == 1:
            matches = [next(iter(canonical))]
        else:
            shortest = min(len(m) for m in matches)
            tied = [m for m in matches if len(m) == shortest]
            if len(tied) == 1 and all(m.startswith(tied[0]) for m in matches):
                matches = tied
    if len(matches) == 1:
        call = _resolve_exact(matches[0], args, token, found)
        if call is not None:
            return call
    if len(matches) > 1:
        return SlashCall("ambiguous", lowered, args, token, tuple(matches[:8]))
    return SlashCall("unknown", lowered, args, token)


def _canonical_builtin(name: str) -> Optional[str]:
    try:
        from curie_cli.commands import resolve_command
    except Exception:
        return None
    cmd = resolve_command(name)
    return cmd.name if cmd is not None else None


def _resolve_exact(
    lowered: str, args: str, token: str, found: Extensions
) -> Optional[SlashCall]:
    try:
        from curie_cli.commands import resolve_command

        cmd = resolve_command(lowered)
    except Exception:
        cmd = None
    if cmd is not None:
        kind = "builtin" if cmd.name in BENCH_BUILTINS else "unsupported"
        return SlashCall(kind, cmd.name, args, token, cmd)
    if lowered in found.quick:
        return SlashCall("quick", lowered, args, token, found.quick[lowered])
    if lowered in found.plugins:
        return SlashCall("plugin", lowered, args, token, found.plugins[lowered])
    key = f"/{lowered}"
    if key in found.bundles:
        return SlashCall("bundle", key, args, token, found.bundles[key])
    skill_key = f"/{lowered.replace('_', '-')}"
    if skill_key in found.skills:
        return SlashCall("skill", skill_key, args, token, found.skills[skill_key])
    return None


def unsupported_reason(cmd: Any) -> str:
    """Why a registry command does not run on the bench, in one clause."""
    name = getattr(cmd, "name", "")
    if getattr(cmd, "gateway_only", False):
        return f"/{name} is for the messaging platforms, not the console"
    return (
        f"/{name} is not one the console runs — use it in `curie chat`, "
        "the classic prompt"
    )


def run_quick_command(spec: Mapping[str, Any], timeout: float = 30.0) -> str:
    """Run an ``exec`` quick command and return what it printed.

    The CLI's own rules: ``shell=True`` because a quick command is a shell
    snippet the user wrote into their own config (never anything the model
    produced), a sanitised environment so the snippet cannot read the API
    keys this process holds, a thirty-second limit, and secrets redacted
    from whatever comes back.
    """
    command = str(spec.get("command") or "").strip()
    if not command:
        return "(this quick command has no command defined)"
    try:
        from tools.environments.local import build_subprocess_env

        env = build_subprocess_env()
    except Exception:
        env = None
    try:
        from curie_cli._subprocess_compat import windows_hide_flags

        flags = windows_hide_flags()
    except Exception:
        flags = 0
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return f"(timed out after {int(timeout)}s)"
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"(could not run: {exc})"
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if not output:
        return "(no output)"
    try:
        from agent.redact import redact_sensitive_text

        output = redact_sensitive_text(output)
    except Exception:
        pass
    return output


# ── The console's handlers ───────────────────────────────────────────────


#: The handler for each built-in, by canonical name.
_HANDLERS: Dict[str, str] = {
    "help": "_slash_help",
    "commands": "_slash_help",
    "new": "_slash_new",
    "clear": "_slash_new",
    "retry": "_slash_retry",
    "undo": "_slash_undo",
    "title": "_slash_title",
    "branch": "_slash_branch",
    "resume": "_slash_resume",
    "status": "_slash_status",
    "unlock": "_slash_unlock",
    "approvals": "_slash_approvals",
    "stop": "_slash_stop",
    "copy": "_slash_copy",
    "skin": "_slash_skin",
    "voice": "_slash_voice",
    "skills": "_slash_skills",
    "reload-skills": "_slash_reload_skills",
    "quit": "_slash_quit",
}

#: How deep a quick-command alias may point at another alias before the
#: chain is called a loop.
_ALIAS_DEPTH = 5


class SlashCommandsMixin:
    """Slash-command handling for :class:`~curie_cli.bench_ui.app.BenchConsole`.

    A mixin rather than more methods on the console: the console is already
    the largest file in the package, and this is one feature with one entry
    point (:meth:`_run_slash`) and its own state. It leans on the console for
    everything it shows — ``_write``, ``_notify_panel``, ``_send`` — and for
    the things the commands *do*, which are the same methods the keys and
    buttons call, so ``/retry`` and Ctrl+G cannot drift apart.
    """

    def _slash_init(self) -> None:
        self._slash_found = Extensions()
        self._slash_catalogue: List[Suggestion] = builtin_suggestions()
        #: A title asked for with ``/new <name>``, given to the new
        #: conversation once its first turn has written it to the store.
        self._pending_title = ""

    # ── The catalogue ────────────────────────────────────────────────────

    def slash_catalogue(self) -> List[Suggestion]:
        """Everything a slash can name right now, for the completion list."""
        return list(self._slash_catalogue)

    def _refresh_slash_catalogue(self) -> None:
        """Re-read skills, bundles, quick and plugin commands, off the UI thread."""
        self.run_worker(self._gather_extensions, thread=True, exclusive=False)

    def _gather_extensions(self) -> None:
        found = gather_extensions()
        try:
            self.call_from_thread(self._extensions_gathered, found)
        except Exception:
            pass

    def _extensions_gathered(self, found: Extensions) -> None:
        self._slash_found = found
        self._slash_catalogue = builtin_suggestions() + extension_suggestions(found)

    # ── Dispatch ─────────────────────────────────────────────────────────

    def _dispatch_line(self, line: str) -> bool:
        """Send a composed line: a command if it is one, a message if not.

        Returns whether the line was taken — False leaves it for the caller
        to put back in the composer (an unknown command, a refusal).
        """
        if line.startswith("//"):
            self._send(line[1:])
            return True
        if looks_like_command(line):
            return self._run_slash(line)
        self._send(line)
        return True

    def _run_slash(self, line: str, depth: int = 0) -> bool:
        """Carry out one slash line. Returns whether it was taken."""
        call = resolve(line, self._slash_found)
        typed = f"/{call.typed}" if call.typed else line.split()[0]
        if call.kind == "builtin":
            if self.bridge.busy and not BENCH_BUILTINS.get(call.name, False):
                self._notify_panel(
                    f"A turn is running — /{call.name} waits until it is done "
                    "(Ctrl+C stops it)."
                )
                return False
            return self._run_builtin(call, line)
        if call.kind == "unsupported":
            self._notify_panel(unsupported_reason(call.detail) + ".", seconds=8.0)
            return False
        if call.kind in ("skill", "bundle"):
            return self._invoke_skill(call, line)
        if call.kind == "quick":
            return self._run_quick(call, line, depth)
        if call.kind == "plugin":
            return self._run_plugin(call, line)
        if call.kind == "ambiguous":
            options = ", ".join(f"/{name}" for name in call.detail)
            self._notify_panel(f"{typed} could be {options} — type a little more.")
            return False
        self._notify_panel(
            f"Unknown command {typed} — /help lists what the console runs. "
            "To send it as a message instead, start it with //.",
            seconds=8.0,
        )
        return False

    def _run_builtin(self, call: SlashCall, line: str) -> bool:
        pane = PANE_COMMANDS.get(call.name)
        if pane is not None:
            self.show_pane(pane)
            return True
        if call.name in EXECUTOR_COMMANDS:
            return self._slash_executor(call)
        handler = getattr(self, _HANDLERS.get(call.name, ""), None)
        if handler is None:
            self._notify_panel(unsupported_reason(call.detail) + ".")
            return False
        result = handler(call)
        return True if result is None else bool(result)

    # ── Writing a command's answer ───────────────────────────────────────

    def _write_block(self, title: str, lines: Iterable[str] = ()) -> None:
        """A command's answer, written into the transcript as a titled note."""
        self.show_pane("bench")
        self._write("note", "")
        self._write("head", f"▮ {title}")
        for line in lines:
            self._write("note", f"  {line}" if line else "")

    def _write_pairs(self, title: str, pairs: Iterable[Tuple[str, str]]) -> None:
        self.show_pane("bench")
        self._write("note", "")
        self._write("head", f"▮ {title}")
        for key, value in pairs:
            self._write("kv", f"{key}\t{value}")

    # ── The built-ins ────────────────────────────────────────────────────

    def _slash_executor(self, call: SlashCall) -> bool:
        try:
            from curie_cli.slash_exec import CommandContext, run_execute

            reply = run_execute(call.detail, CommandContext(surface="cli", args=call.args))
        except Exception as exc:  # noqa: BLE001 - surfaced to the reader
            self._notify_panel(f"/{call.name} failed — {exc}")
            return False
        if reply is None or not str(reply.text or "").strip():
            self._notify_panel(f"/{call.name} had nothing to say.")
            return True
        self._write_block(call.name.upper(), str(reply.text).splitlines())
        return True

    def _slash_help(self, call: SlashCall) -> None:
        wanted = call.args.strip().lower().lstrip("/")
        if wanted == "skills":
            self._slash_skills(SlashCall("builtin", "skills"))
            return
        if not wanted:
            self._write_help()
            return
        matches = match_suggestions(wanted, self.slash_catalogue(), limit=40)
        if not matches:
            self._notify_panel(f"No command or skill matches “{wanted}”.")
            return
        self._write_pairs(
            f"COMMANDS MATCHING “{wanted}”",
            [(f"/{s.name}", s.description) for s in matches],
        )

    def _slash_new(self, call: SlashCall) -> None:
        self._pending_title = " ".join(call.args.split())
        self._start_new_session()

    def _slash_retry(self, call: SlashCall) -> None:
        self._regenerate()

    def _slash_undo(self, call: SlashCall) -> bool:
        try:
            count = max(1, int(call.args)) if call.args.strip() else 1
        except ValueError:
            self._notify_panel("Usage: /undo [N] — how many of your messages to take back.")
            return False
        prompt = None
        taken = 0
        for _ in range(count):
            back = self._take_back_last_exchange()
            if back is None:
                break
            prompt, taken = back, taken + 1
        if not taken:
            self._notify_panel("Nothing to take back — the bench is clear.")
            return True
        composer = self._maybe("#composer")
        if composer is not None and prompt is not None:
            composer.text = prompt
            composer.focus()
        self._set_subject(self.bridge.describe())
        self._notify_panel(
            f"Took back {taken} message{'s' if taken != 1 else ''} — the "
            "earliest is in the composer, ready to edit.",
            seconds=6.0,
        )
        return True

    def _slash_title(self, call: SlashCall) -> bool:
        from curie_cli.bench_ui.sessions import rename_session

        sid = self.bridge.session_id
        if not sid:
            self._notify_panel("There is no conversation on the bench yet.")
            return False
        if not call.args.strip():
            try:
                from curie_state import SessionDB

                current = SessionDB().get_session_title(sid) or ""
            except Exception:
                current = ""
            self._notify_panel(
                f"This conversation is “{current}”." if current
                else "This conversation has no title yet — /title <name> gives it one."
            )
            return True
        problem = rename_session(sid, call.args)
        if problem:
            self._notify_panel(f"Could not rename it — {problem}.")
            return False
        self._notify_panel(f"Titled “{' '.join(call.args.split())}”.")
        self._reload_logbook()
        return True

    def _slash_branch(self, call: SlashCall) -> bool:
        sid = self.bridge.session_id
        pane = self._bench()
        if not sid or pane is None or not pane.message_count():
            self._notify_panel("No conversation to branch — send a message first.")
            return False
        self._duplicate(sid, title=call.args)
        return True

    def _slash_resume(self, call: SlashCall) -> bool:
        if not call.args.strip():
            self.show_pane("logbook")
            return True
        from curie_cli.bench_ui.sessions import find_session

        try:
            sid = find_session(call.args)
        except Exception:
            sid = None
        if not sid:
            self._notify_panel(
                f"No conversation is called “{call.args.strip()}” — the "
                "logbook (F3) lists them all."
            )
            return False
        self._open_session(sid)
        return True

    def _slash_status(self, call: SlashCall) -> None:
        facts = self.bridge.describe()
        rows: List[Tuple[str, str]] = [
            ("model", str(facts.get("model") or "not loaded yet")),
            ("provider", str(facts.get("provider") or "—")),
        ]
        if facts.get("base_url"):
            rows.append(("endpoint", str(facts["base_url"])))
        sid = self.bridge.session_id or ""
        rows.append(("conversation", sid or "not started"))
        try:
            reading = self.bridge.context_usage()
        except Exception:
            reading = None
        if reading:
            used, ceiling = reading
            share = (used / ceiling * 100) if ceiling else 0
            rows.append(("context", f"{used:,} of {ceiling:,} tokens ({share:.0f}%)"))
        rows.append(("turns", f"{facts.get('turns', 0):,}"))
        for key, value in self._access_status_rows():
            rows.append((key, value))
        self._write_pairs("STATUS", rows)

    def _slash_unlock(self, call: SlashCall) -> bool:
        wanted = call.args.strip().lower()
        if wanted in ("", "toggle"):
            self._toggle_unlock()
        elif wanted in ("on", "off"):
            if (wanted == "on") != self._unlock:
                self._toggle_unlock()
            else:
                self._notify_panel(f"UNLOCK is already {wanted.upper()}.")
        elif wanted == "status":
            self._notify_panel(self._unlock_sentence())
        else:
            self._notify_panel("Usage: /unlock [on|off|status]")
            return False
        return True

    def _slash_approvals(self, call: SlashCall) -> bool:
        from curie_cli.bench_ui.settings import write_setting

        wanted = call.args.strip().lower()
        if not wanted:
            self._notify_panel(
                f"Approval mode: {self._approval_mode()} — manual asks every "
                "time, smart lets a reviewer model pass the safe ones, off "
                "never asks. /approvals <mode> changes it everywhere."
            )
            return True
        if wanted not in ("manual", "smart", "off"):
            self._notify_panel("Usage: /approvals [manual|smart|off]")
            return False
        problem = write_setting("approvals.mode", wanted)
        self._notify_panel(
            f"Approval mode set to {wanted} — for every Curie surface, not only "
            "this console."
            + (f"  (not saved: {problem})" if problem else "")
        )
        return True

    def _slash_stop(self, call: SlashCall) -> None:
        self._notify_panel("Stopping background processes …", seconds=10.0)
        self.run_worker(self._stop_background_work, thread=True, exclusive=False)

    def _stop_background_work(self) -> None:
        said: List[str] = []
        try:
            from tools.process_registry import process_registry

            running = [
                p for p in process_registry.list_sessions()
                if p.get("status") == "running"
            ]
            if running:
                said.append(f"stopped {process_registry.kill_all()} background process(es)")
        except Exception as exc:  # noqa: BLE001 - surfaced to the reader
            said.append(f"could not stop processes ({exc})")
        try:
            from tools.async_delegation import active_count, interrupt_all

            if active_count():
                said.append(f"interrupted {interrupt_all(reason='/stop')} background delegation(s)")
        except Exception:
            pass
        message = "; ".join(said) if said else "no background processes were running"
        try:
            self.call_from_thread(self._notify_panel, message[0].upper() + message[1:] + ".")
        except Exception:
            pass

    def _slash_copy(self, call: SlashCall) -> bool:
        pane = self._bench()
        replies = pane.replies() if pane is not None else []
        if not replies:
            self._notify_panel("Nothing to copy yet — no replies on the bench.")
            return False
        if call.args.strip():
            try:
                index = int(call.args) - 1
            except ValueError:
                self._notify_panel("Usage: /copy [number] — which reply, counting from the first.")
                return False
            if not 0 <= index < len(replies):
                self._notify_panel(f"There are {len(replies)} replies — /copy 1 to /copy {len(replies)}.")
                return False
        else:
            index = len(replies) - 1
        self._copy_out(replies[index], f"reply #{index + 1} — {len(replies[index]):,} characters")
        return True

    def _slash_skin(self, call: SlashCall) -> bool:
        name = call.args.strip()
        if not name:
            from curie_cli.bench_ui.panes import _read_skins

            self._write_block(
                "SKINS",
                [f"{row[0].strip():<16}{row[2]}" for row in _read_skins()]
                + ["", "/skin <name> applies one — here, and in the CLI and the TUI."],
            )
            return True
        return self._apply_skin(name)

    def _slash_voice(self, call: SlashCall) -> bool:
        wanted = call.args.strip().lower()
        if wanted in ("", "toggle"):
            self._toggle_listening()
        elif wanted == "on":
            if not self.voice.listening:
                self._toggle_listening()
            else:
                self._notify_panel("Dictation is already on.")
        elif wanted == "off":
            if self.voice.listening:
                self._toggle_listening()
            else:
                self._notify_panel("Dictation is already off.")
        elif wanted == "tts":
            self._toggle_speaking()
        elif wanted == "status":
            self._notify_panel(
                f"Dictation {'on' if self.voice.listening else 'off'} · spoken "
                f"replies {'on' if self.voice.speaking_enabled else 'off'}"
                + (f" · {self.voice.reason}" if self.voice.reason else "")
            )
        else:
            self._notify_panel("Usage: /voice [on|off|tts|status]")
            return False
        return True

    def _slash_skills(self, call: SlashCall) -> bool:
        if call.args.strip():
            self._notify_panel(
                f"Managing skills is a terminal job: curie skills {call.args.strip()}"
            )
            return False
        skills = sorted(self._slash_found.skills.items())
        if not skills:
            self._notify_panel(
                "No skill commands are installed — curie skills browse finds some."
            )
            return True
        self._write_pairs(
            f"SKILL COMMANDS — {len(skills)} installed",
            [(key, _describe(info, "skill")) for key, info in skills],
        )
        return True

    def _slash_reload_skills(self, call: SlashCall) -> None:
        self._notify_panel("Re-reading the skill folders …", seconds=10.0)
        self.run_worker(self._reload_skills_worker, thread=True, exclusive=False)

    def _reload_skills_worker(self) -> None:
        try:
            from agent.skill_commands import reload_skills

            result = reload_skills() or {}
        except Exception as exc:  # noqa: BLE001 - surfaced to the reader
            result = {"error": str(exc)}
        found = gather_extensions()

        def landed() -> None:
            self._extensions_gathered(found)
            added = result.get("added") or []
            removed = result.get("removed") or []
            if result.get("error"):
                self._notify_panel(f"Could not re-read the skills — {result['error']}")
                return
            self._notify_panel(
                f"Skills re-read — {len(found.skills)} installed"
                + (f", {len(added)} new" if added else "")
                + (f", {len(removed)} gone" if removed else "")
                + "."
            )

        try:
            self.call_from_thread(landed)
        except Exception:
            pass

    def _slash_quit(self, call: SlashCall) -> None:
        self.exit()

    # ── Skills, bundles, quick commands, plugins ─────────────────────────

    def _invoke_skill(self, call: SlashCall, line: str) -> bool:
        """Load a skill (or bundle, or a stack of skills) and send it as a turn.

        The message the model receives is the skill's whole body, as the CLI
        builds it; the transcript shows the line as it was typed, which is
        what the reader said. Built on a worker: loading a skill reads its
        files and may inject its configuration.
        """
        if self.bridge.busy:
            self._notify_panel("A turn is already running — Ctrl+C stops it.")
            return False
        self._notify_panel(f"Loading {line.split()[0]} …", seconds=10.0)
        task_id = self.bridge.session_id
        self.run_worker(
            lambda: self._build_skill_turn(call, line, task_id),
            thread=True,
            exclusive=False,
        )
        return True

    def _build_skill_turn(self, call: SlashCall, line: str, task_id) -> None:
        message: Optional[str] = None
        label = ""
        missing: List[str] = []
        error = ""
        try:
            if call.kind == "bundle":
                from agent.skill_bundles import build_bundle_invocation_message

                built = build_bundle_invocation_message(call.name, call.args, task_id=task_id)
                if built:
                    message, loaded, missing = built
                    name = (call.detail or {}).get("name") or call.name
                    label = f"bundle {name} — {len(loaded)} skills"
            else:
                from agent.skill_commands import (
                    build_skill_invocation_message,
                    build_stacked_skill_invocation_message,
                    split_stacked_skill_commands,
                )

                extra, instruction = split_stacked_skill_commands(call.args)
                if extra:
                    built = build_stacked_skill_invocation_message(
                        [call.name, *extra], instruction, task_id=task_id
                    )
                    if built:
                        message, loaded, missing = built
                        label = f"{len(loaded)} skills — {', '.join(loaded)}"
                else:
                    message = build_skill_invocation_message(
                        call.name, call.args, task_id=task_id
                    )
                    name = (call.detail or {}).get("name") or call.name.lstrip("/")
                    label = f"skill {name}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the reader
            error = f"{type(exc).__name__}: {exc}"
        try:
            self.call_from_thread(
                self._skill_turn_ready, line, message, label, list(missing or []), error
            )
        except Exception:
            pass

    def _skill_turn_ready(
        self, line: str, message: Optional[str], label: str, missing: List[str], error: str
    ) -> None:
        if not message:
            self._notify_panel(
                f"Could not load {line.split()[0]} — "
                + (error or "its SKILL.md could not be read")
                + "."
            )
            self._restore_composer(line)
            return
        if self.bridge.busy:
            # A turn started while the skill was loading — dictation, or a
            # second press. The line goes back rather than being dropped.
            self._notify_panel("A turn started meanwhile — the command is back in the composer.")
            self._restore_composer(line)
            return
        self._send(message, shown=line)
        self._write(
            "note",
            f"  ⚡ {label} loaded"
            + (f" — skipped, not installed: {', '.join(missing)}" if missing else ""),
        )

    def _restore_composer(self, line: str) -> None:
        composer = self._maybe("#composer")
        if composer is not None and not composer.text.strip():
            composer.text = line

    def _run_quick(self, call: SlashCall, line: str, depth: int) -> bool:
        spec = call.detail or {}
        kind = str(spec.get("type") or "").lower()
        if kind == "alias":
            target = str(spec.get("target") or "").strip()
            if not target:
                self._notify_panel(f"Quick command /{call.name} has no target defined.")
                return False
            if depth >= _ALIAS_DEPTH:
                self._notify_panel(f"Quick command /{call.name} points round in a loop.")
                return False
            target = target if target.startswith("/") else f"/{target}"
            return self._run_slash(f"{target} {call.args}".strip(), depth + 1)
        if kind == "exec":
            self._notify_panel(f"Running /{call.name} …", seconds=30.0)
            self.run_worker(
                lambda: self._quick_worker(call.name, spec), thread=True, exclusive=False
            )
            return True
        self._notify_panel(
            f"Quick command /{call.name} has a type the console does not run "
            "(exec and alias are supported)."
        )
        return False

    def _quick_worker(self, name: str, spec: Mapping[str, Any]) -> None:
        output = run_quick_command(spec)
        try:
            self.call_from_thread(
                self._write_block, f"/{name}", output.splitlines() or ["(no output)"]
            )
        except Exception:
            pass

    def _run_plugin(self, call: SlashCall, line: str) -> bool:
        self.run_worker(
            lambda: self._plugin_worker(call.name, call.args), thread=True, exclusive=False
        )
        return True

    def _plugin_worker(self, name: str, args: str) -> None:
        try:
            from curie_cli.plugins import (
                get_plugin_command_handler,
                resolve_plugin_command_result,
            )

            handler = get_plugin_command_handler(name)
            if handler is None:
                text = "(the plugin that registered this command is not loaded)"
            else:
                result = resolve_plugin_command_result(handler(args))
                text = str(result) if result else "(done)"
        except Exception as exc:  # noqa: BLE001 - surfaced to the reader
            text = f"(plugin command failed: {exc})"
        try:
            self.call_from_thread(self._write_block, f"/{name}", text.splitlines())
        except Exception:
            pass


__all__ = [
    "BENCH_BUILTINS",
    "EXECUTOR_COMMANDS",
    "SlashCommandsMixin",
    "Extensions",
    "PANE_COMMANDS",
    "SUGGESTION_ROWS",
    "SlashCall",
    "Suggestion",
    "builtin_suggestions",
    "extension_suggestions",
    "gather_extensions",
    "looks_like_command",
    "match_suggestions",
    "resolve",
    "run_quick_command",
    "split_line",
    "unsupported_reason",
]
