"""What the agent may do from this console: approvals, UNLOCK, and sudo.

Three controls, all on the PANEL pane under ACCESS.

**The approval prompt.** A dangerous command — ``rm -rf``, a write to
``~/.ssh``, a pipe to a shell — is put to the reader before it runs, with the
same four answers every Curie surface offers: once, for this conversation,
always, or no. The console had no prompt at all before this: ``curie ui``
never marked its turns interactive, so the approval gate took its
non-interactive path, which approves without asking anyone.

**UNLOCK** (``ui.unlock``, and ``/unlock``) skips that prompt for the
console's conversations. It is the console's own spelling of ``/unlock`` and
writes the same per-conversation flag in ``tools.approval`` that the CLI's,
the TUI's and the gateway's do; it is remembered, because it is a switch on a
settings page. Hardline blocks (``rm -rf /``, ``mkfs``…), ``approvals.deny``
rules and the sudo-guessing guard still apply — nothing on this console can
switch those off.

**SUDO UNLOCK** (``ui.sudo_unlock``) hands the console's stored sudo password
to ``sudo`` when the agent runs a command that needs it. The password is a
secret, so it lives in ``~/.curie/.env`` as :data:`SUDO_SECRET_KEY`, never in
``config.yaml``; and it is the console's own, not ``SUDO_PASSWORD``, because
``SUDO_PASSWORD`` is read by every surface — storing it from a settings pane
would quietly give the messaging gateway sudo too, and the switch beside it
could not take that back.

The password reaches ``sudo`` the way a configured ``SUDO_PASSWORD`` does:
in this process's environment, where the terminal tool looks, and nowhere
else. The terminal tool rewrites ``sudo`` to ``sudo -S`` and pipes it the
password on stdin; neither name is ever passed to a child process — both are
``password`` settings, which the terminal backend strips from every
environment it builds.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

#: Where the console keeps its sudo password, in ``~/.curie/.env``.
SUDO_SECRET_KEY = "CURIE_UI_SUDO_PASSWORD"

#: The name the terminal tool reads a sudo password from.
SUDO_ENV_KEY = "SUDO_PASSWORD"

#: The four answers to an approval, in the order the prompt offers them.
APPROVAL_CHOICES: Tuple[Tuple[str, str], ...] = (
    ("once", "ONCE"),
    ("session", "SESSION"),
    ("always", "ALWAYS"),
    ("deny", "DENY"),
)

#: The key that answers each choice while the prompt has focus.
APPROVAL_KEYS = {"y": "once", "s": "session", "a": "always", "n": "deny"}

#: How long the prompt ignores keys after it appears. A reader typing the
#: next message when it opens has keystrokes in flight, and a ``y`` or an
#: Enter meant for the composer must not answer a question they have not yet
#: read. Clicks are never debounced: a click is aimed.
APPROVAL_ARMING_SECONDS = 0.6


# ── The stored password ──────────────────────────────────────────────────


def stored_sudo_password() -> str:
    """The console's stored sudo password, as written in ``.env``.

    Read from the file rather than from the environment: the environment
    copy went through the dotenv loader, which expands ``${…}`` and would
    hand ``sudo`` a different password than the one that was typed.
    """
    try:
        from curie_cli.config import load_env

        return str(load_env().get(SUDO_SECRET_KEY) or "")
    except Exception:
        return ""


def store_sudo_password(password: str) -> str:
    """Keep ``password`` as the console's sudo password. Returns "" or why not.

    Refused rather than mangled: ``.env`` is an ASCII, one-line-per-value
    store, and a password it would change on the way in is a password ``sudo``
    would then refuse — repeatedly, which on many systems locks the account.
    So a value that does not read back exactly as written is taken back out
    and the reader told why.
    """
    if not password:
        return "type the password first"
    if "\n" in password or "\r" in password:
        return "a password cannot contain a line break"
    try:
        password.encode("ascii")
    except UnicodeEncodeError:
        return (
            "it has characters outside ASCII, which the .env store cannot "
            "keep exactly"
        )
    try:
        from curie_cli.config import load_env, remove_env_value, save_env_value
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the settings store is unavailable ({exc})"
    said = io.StringIO()
    try:
        with redirect_stdout(said), redirect_stderr(said):
            save_env_value(SUDO_SECRET_KEY, password)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"it could not be saved ({type(exc).__name__}: {exc})"
    if load_env().get(SUDO_SECRET_KEY) != password:
        try:
            with redirect_stdout(said), redirect_stderr(said):
                remove_env_value(SUDO_SECRET_KEY)
        except Exception:
            pass
        spoke = " ".join(said.getvalue().split())
        return spoke[:160] or "the .env store could not hold it exactly"
    return ""


def forget_sudo_password() -> str:
    """Take the stored password out of ``.env``. Returns "" or why not."""
    try:
        from curie_cli.config import remove_env_value
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"the settings store is unavailable ({exc})"
    said = io.StringIO()
    try:
        with redirect_stdout(said), redirect_stderr(said):
            remove_env_value(SUDO_SECRET_KEY)
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return f"it could not be removed ({type(exc).__name__}: {exc})"
    os.environ.pop(SUDO_SECRET_KEY, None)
    return ""


def check_sudo_password(password: str, timeout: float = 10.0) -> Tuple[Optional[bool], str]:
    """Ask this machine's ``sudo`` whether it takes ``password``.

    ``(True, …)`` accepted, ``(False, …)`` refused, ``(None, …)`` not
    checkable here — with the sentence to show either way.

    ``sudo -k`` with a command checks the password *without* leaving a
    cached ticket behind; a plain ``sudo -v`` would, and the agent's own sudo
    calls from this process would then run on that ticket for the next
    fifteen minutes whatever the SUDO UNLOCK switch said.

    Only the local terminal backend is checked: on Docker, SSH or Modal the
    agent's ``sudo`` is another machine's, with another password.
    """
    if not password:
        return None, "no password is stored"
    backend = (os.environ.get("TERMINAL_ENV") or "local").strip().lower() or "local"
    if backend != "local":
        return None, (
            f"the agent's terminal runs on {backend}, so its sudo is not this "
            "machine's — not checked"
        )
    if os.name == "nt" or shutil.which("sudo") is None:
        return None, "there is no sudo on this machine to check against"
    # No terminal of its own (a new session), so sudo cannot fall back to
    # asking on the console's tty — the one it would ask on is the screen the
    # console is drawing. A bare environment, because this process's holds
    # API keys and sudo needs none of them; English messages, so a refusal
    # reads the same here whatever the locale.
    quiet = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "start_new_session": True,
        "env": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    }
    try:
        free = subprocess.run(
            ["sudo", "-n", "-k", "true"], stdin=subprocess.DEVNULL, **quiet
        )
        if free.returncode == 0:
            return True, "sudo here needs no password at all, so any password works"
        result = subprocess.run(
            ["sudo", "-S", "-k", "-p", "", "true"], input=password + "\n", **quiet
        )
    except subprocess.TimeoutExpired:
        return None, "sudo did not answer in time"
    except Exception as exc:  # noqa: BLE001 - surfaced to the reader
        return None, f"sudo could not be run ({exc})"
    if result.returncode == 0:
        return True, "sudo accepted it"
    said = " ".join((result.stderr or "").split())
    return False, "sudo refused it" + (f" ({said[:120]})" if said else "")


# ── Supplying it ─────────────────────────────────────────────────────────


class SudoSupply:
    """Puts the console's password where the terminal tool reads one.

    In this process only, and only while SUDO UNLOCK is on. A
    ``SUDO_PASSWORD`` the user configured for every surface is left exactly
    as it was found: it is put back when the console's own is withdrawn, and
    it is never overwritten by switching SUDO UNLOCK *off* — locking sudo here
    must not unlock it less than it was before the console opened.
    """

    def __init__(self) -> None:
        #: ``SUDO_PASSWORD`` as the process had it before the console touched
        #: it — the user's own, from ``.env`` or the shell, or None.
        self._original: Optional[str] = os.environ.get(SUDO_ENV_KEY)
        self._supplying = False

    @property
    def global_password(self) -> bool:
        """Whether a ``SUDO_PASSWORD`` for every surface is configured."""
        return self._original is not None

    @property
    def supplying(self) -> bool:
        """Whether the console's own password is the one in force."""
        return self._supplying

    def apply(self, enabled: bool, password: str) -> None:
        if enabled and password:
            os.environ[SUDO_ENV_KEY] = password
            self._supplying = True
        else:
            self.withdraw()

    def withdraw(self) -> None:
        if not self._supplying:
            return
        if self._original is None:
            os.environ.pop(SUDO_ENV_KEY, None)
        else:
            os.environ[SUDO_ENV_KEY] = self._original
        self._supplying = False


# ── The approval prompt's request ────────────────────────────────────────


@dataclass
class ApprovalRequest:
    """One dangerous command waiting for an answer.

    Made on a tool's worker thread, answered on the UI thread; the worker
    waits on :attr:`answered`.
    """

    command: str
    description: str
    choices: Tuple[str, ...]
    deadline: float
    source: str = ""
    answered: threading.Event = field(default_factory=threading.Event)
    choice: str = ""

    def answer(self, choice: str) -> None:
        if self.answered.is_set():
            return
        self.choice = choice if choice in self.choices or choice == "timeout" else "deny"
        self.answered.set()

    @property
    def seconds_left(self) -> int:
        return max(0, int(self.deadline - time.monotonic()))


def approval_choices(
    *, allow_permanent: bool = True, allow_session: bool = True, smart_denied: bool = False
) -> Tuple[str, ...]:
    """The answers this request may be given — the CLI's rules.

    An owner overriding a smart-approval DENY, and a gate that asks every
    time, get once-or-no only; a request with content-level findings cannot
    be allowed *always*.
    """
    if smart_denied or not allow_session:
        return ("once", "deny")
    if not allow_permanent:
        return ("once", "session", "deny")
    return tuple(name for name, _label in APPROVAL_CHOICES)


def approval_timeout() -> int:
    """``approvals.timeout`` — how long a prompt waits before it says no."""
    try:
        from curie_cli.config import load_config_readonly

        value = int(((load_config_readonly() or {}).get("approvals") or {}).get("timeout", 300))
    except Exception:
        value = 300
    return max(10, value)


def approval_mode() -> str:
    """``approvals.mode``: manual, smart or off."""
    try:
        from tools.approval import _get_approval_mode

        return str(_get_approval_mode())
    except Exception:
        return "manual"


# ── The console's half ───────────────────────────────────────────────────


class AccessMixin:
    """UNLOCK, SUDO UNLOCK and the approval prompt, for the bench console.

    Mixed into :class:`~curie_cli.bench_ui.app.BenchConsole` beside the slash
    commands, for the same reason: one feature, its own state, and the
    console's notices and panes to report through.
    """

    def _access_init(self, settings) -> None:
        self._unlock = bool(settings.unlock)
        self._sudo_unlock = bool(settings.sudo_unlock)
        self._sudo = SudoSupply()
        self._sudo.apply(self._sudo_unlock, stored_sudo_password())
        #: The request on screen, if any, and the ones waiting behind it.
        self._approval: Optional[ApprovalRequest] = None
        self._approval_lock = threading.Lock()

    # ── Wiring bridges ───────────────────────────────────────────────────

    def _arm_bridge(self, bridge, source: str = "") -> None:
        """Give a bridge this console's prompt and its UNLOCK."""
        if bridge is None:
            return
        bridge.approval_callback = self._make_approval_callback(source)
        bridge.unlocked = self._unlock

    def _bridges(self) -> list:
        return [b for b in (getattr(self, "bridge", None), getattr(self, "_task_bridge", None)) if b]

    # ── UNLOCK ───────────────────────────────────────────────────────────

    def _unlock_sentence(self) -> str:
        if self._unlock:
            return (
                "UNLOCK is ON — dangerous commands run without asking. Hardline "
                "blocks and approvals.deny rules still apply."
            )
        return "UNLOCK is OFF — dangerous commands are put to you before they run."

    def _toggle_unlock(self) -> None:
        """PANEL → UNLOCK and ``/unlock``: skip the approval prompt, or not."""
        from curie_cli.bench_ui.settings import KEY_UNLOCK

        self._unlock = not self._unlock
        for bridge in self._bridges():
            try:
                bridge.apply_unlock(self._unlock)
            except Exception:
                pass
        problem = self._save_setting(KEY_UNLOCK, self._unlock)
        frozen = self._unlock_frozen()
        # A prompt already open is left for the reader to answer. It may not
        # be a dangerous command at all: writes to AGENTS.md and the like,
        # and memory writes, come through the same prompt and are asked
        # every time *by design* — UNLOCK does not cover them, so throwing
        # it must not answer them either.
        self._sync_access_switches()
        self._notify_panel(
            self._unlock_sentence()
            + (
                "  (The process was started with --unlock, so nothing here can "
                "turn approvals back on until it restarts.)"
                if frozen and not self._unlock
                else ""
            )
            + (f"  (not saved: {problem})" if problem else ""),
            seconds=8.0,
        )

    @staticmethod
    def _unlock_frozen() -> bool:
        try:
            from tools.approval import _UNLOCK_MODE_FROZEN

            return bool(_UNLOCK_MODE_FROZEN)
        except Exception:
            return False

    def _approval_mode(self) -> str:
        return approval_mode()

    # ── SUDO UNLOCK ──────────────────────────────────────────────────────

    def _sudo_sentence(self) -> str:
        stored = bool(stored_sudo_password())
        if self._sudo.supplying:
            return "SUDO UNLOCK is ON — sudo is given the stored password."
        if self._sudo_unlock and not stored:
            return "SUDO UNLOCK is ON, but no password is stored yet — type one below."
        if self._sudo.global_password:
            return (
                "SUDO UNLOCK is OFF here, but SUDO_PASSWORD is set in .env, "
                "which unlocks sudo on every surface — remove it there to lock it."
            )
        return "SUDO UNLOCK is OFF — sudo commands get no password and fail."

    def _toggle_sudo_unlock(self) -> None:
        from curie_cli.bench_ui.settings import KEY_SUDO_UNLOCK

        self._sudo_unlock = not self._sudo_unlock
        self._sudo.apply(self._sudo_unlock, stored_sudo_password())
        problem = self._save_setting(KEY_SUDO_UNLOCK, self._sudo_unlock)
        self._sync_access_switches()
        self._notify_panel(
            self._sudo_sentence() + (f"  (not saved: {problem})" if problem else ""),
            seconds=8.0,
        )

    def _store_sudo_password(self, password: str) -> bool:
        problem = store_sudo_password(password)
        if problem:
            self._notify_panel(f"Password not stored — {problem}.", seconds=8.0)
            return False
        self._sudo.apply(self._sudo_unlock, password)
        self._sync_access_switches()
        self._notify_panel(
            f"Sudo password stored ({len(password)} characters) in .env as "
            f"{SUDO_SECRET_KEY}. "
            + (
                "SUDO UNLOCK is on, so sudo gets it from now on."
                if self._sudo_unlock
                else "Turn SUDO UNLOCK on to hand it to sudo."
            ),
            seconds=8.0,
        )
        return True

    def _forget_sudo_password(self) -> None:
        problem = forget_sudo_password()
        self._sudo.apply(self._sudo_unlock, "")
        self._sync_access_switches()
        self._notify_panel(
            "Sudo password forgotten — taken out of .env."
            if not problem
            else f"Could not forget it — {problem}.",
            seconds=6.0,
        )

    def _test_sudo_password(self) -> None:
        password = stored_sudo_password()
        if not password:
            self._notify_panel("No password is stored yet — type one and press STORE.")
            return
        self._notify_panel("Asking sudo …", seconds=15.0)

        def check() -> None:
            ok, said = check_sudo_password(password)
            mark = {True: "✓", False: "✗", None: "·"}[ok]
            try:
                self.call_from_thread(
                    self._notify_panel, f"{mark} The stored password: {said}.", 8.0
                )
            except Exception:
                pass

        self.run_worker(check, thread=True, exclusive=False)

    # ── The prompt ───────────────────────────────────────────────────────

    def _make_approval_callback(self, source: str = "") -> Callable[..., str]:
        """The approval callback a bridge hands to its turns."""

        def approve(command: str, description: str, *, allow_permanent: bool = True,
                    allow_session: bool = True, smart_denied: bool = False) -> str:
            return self._await_approval(
                command,
                description,
                approval_choices(
                    allow_permanent=allow_permanent,
                    allow_session=allow_session,
                    smart_denied=smart_denied,
                ),
                source,
            )

        return approve

    def _await_approval(
        self, command: str, description: str, choices: Tuple[str, ...], source: str
    ) -> str:
        """Put one request to the reader and wait. Runs on a tool's thread.

        One at a time: parallel tool calls can each want an answer, and the
        prompt is one widget — so a second request waits for the first,
        exactly as the CLI's does.
        """
        with self._approval_lock:
            # No UNLOCK shortcut here. ``tools.approval`` consults UNLOCK
            # before it ever calls this, for the commands UNLOCK covers; what
            # still arrives while it is on is a gate that is meant to ask
            # every time regardless — a protected instruction file, a memory
            # write — and answering it here would be the bypass it forbids.
            timeout = approval_timeout()
            request = ApprovalRequest(
                command=command,
                description=description,
                choices=choices,
                deadline=time.monotonic() + timeout,
                source=source,
            )
            try:
                self.call_from_thread(self._show_approval, request)
            except Exception:
                return "deny"
            if not request.answered.wait(timeout):
                request.answer("timeout")
            try:
                self.call_from_thread(self._approval_done, request)
            except Exception:
                pass
            return request.choice or "deny"

    def _show_approval(self, request: ApprovalRequest) -> None:
        from curie_cli.bench_ui.panes import ApprovalBar

        self._approval = request
        self.show_pane("bench")
        bar = self._maybe("#approval-bar", ApprovalBar)
        if bar is None:
            request.answer("deny")
            return
        bar.ask(request)
        lamps = self._maybe("#titlebar-lamps")
        if lamps is not None:
            try:
                lamps.set_lamp("LOG", "blink")
            except Exception:
                pass

    def _answer_approval(self, choice: str) -> None:
        request = self._approval
        if request is not None:
            request.answer(choice)

    def _approval_done(self, request: ApprovalRequest) -> None:
        from curie_cli.bench_ui.panes import ApprovalBar

        if self._approval is request:
            self._approval = None
        bar = self._maybe("#approval-bar", ApprovalBar)
        if bar is not None:
            bar.close(request)
        lamps = self._maybe("#titlebar-lamps")
        if lamps is not None:
            try:
                lamps.set_lamp("LOG", "warn" if self.bridge.busy else "off")
            except Exception:
                pass
        said = {
            "once": "allowed once",
            "session": "allowed for this conversation",
            "always": "allowed from now on (added to the allowlist)",
            "deny": "refused",
            "timeout": "refused — nobody answered in time",
        }.get(request.choice, request.choice)
        self._notify_panel(f"Dangerous command {said}.", seconds=5.0)

    def _deny_pending_approval(self) -> bool:
        """Refuse whatever is waiting — STOP, and the way out of the console."""
        if self._approval is None:
            return False
        self._answer_approval("deny")
        return True

    # ── Readouts ─────────────────────────────────────────────────────────

    def _access_status_rows(self) -> List[Tuple[str, str]]:
        return [
            ("unlock", "ON" if self._unlock or self._unlock_frozen() else "OFF"),
            ("approvals", self._approval_mode()),
            (
                "sudo",
                "unlocked (stored password)" if self._sudo.supplying
                else "unlocked (SUDO_PASSWORD in .env)" if self._sudo.global_password
                else "locked",
            ),
        ]

    def _sync_access_switches(self) -> None:
        from curie_cli.bench_ui.panes import PanelPane

        pane = self._maybe("#pane-panel", PanelPane)
        if pane is None:
            return
        try:
            pane.refresh_access(
                unlock=self._unlock,
                sudo_unlock=self._sudo_unlock,
                stored=bool(stored_sudo_password()),
                note=self._unlock_sentence() + "\n" + self._sudo_sentence(),
            )
        except Exception:
            pass


__all__ = [
    "APPROVAL_ARMING_SECONDS",
    "APPROVAL_CHOICES",
    "APPROVAL_KEYS",
    "AccessMixin",
    "ApprovalRequest",
    "SUDO_ENV_KEY",
    "SUDO_SECRET_KEY",
    "SudoSupply",
    "approval_choices",
    "approval_mode",
    "approval_timeout",
    "check_sudo_password",
    "forget_sudo_password",
    "store_sudo_password",
    "stored_sudo_password",
]
