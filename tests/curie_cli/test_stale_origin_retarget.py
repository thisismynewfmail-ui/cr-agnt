"""An install made before the rename must not stop at a password prompt.

Fixing the clone URLs fixed nothing for anyone who already had Curie: the
installer and ``curie update`` both reach the network through the checkout's
own ``origin``, and an install predating the rename still has the old address
there. GitHub answers 404 for it, git cannot tell a 404 from a private
repository, so it asks for credentials — and the reported failure was:

    → Existing installation found, updating...
    Username for 'https://github.com':
    Password for 'https://github.com':
    remote: Repository not found.
    fatal: Authentication failed for '.../Cur-Agnt.git/'

Two things had to be true to end that. The remote gets repointed before
anything reaches the network, and git is never allowed to block on an
interactive prompt — under ``curl | bash`` it opens /dev/tty directly, so the
pipe is no protection at all.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CURRENT_SLUG = "thisismynewfmail-ui/cru"
FORMER_SLUG = "thisismynewfmail-ui/Cur-Agnt"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


@pytest.fixture
def checkout(tmp_path):
    """A factory for a real git checkout with a given origin.

    Returns ``(path, stored)`` where ``stored`` is the URL git actually kept,
    which is not always the one asked for: a machine can carry a
    ``url.<base>.insteadOf`` rewrite (proxies and sandboxes commonly map
    ``git@github.com:`` to HTTPS), and every assertion below should be about
    what the code did to the remote rather than about what the environment
    chose to store.
    """

    counter = {"n": 0}

    def make(origin: str):
        counter["n"] += 1
        path = tmp_path / f"repo{counter['n']}"
        path.mkdir()
        _git("init", "-q", ".", cwd=path)
        _git("remote", "add", "origin", origin, cwd=path)
        return path, _origin(path)

    return make


def _origin(path: Path) -> str:
    return _git("remote", "get-url", "origin", cwd=path).stdout.strip()



# ── The slug reader ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://github.com/{FORMER_SLUG}.git", FORMER_SLUG.lower()),
        (f"https://github.com/{FORMER_SLUG}", FORMER_SLUG.lower()),
        (f"https://github.com/{FORMER_SLUG}/", FORMER_SLUG.lower()),
        (f"git@github.com:{FORMER_SLUG}.git", FORMER_SLUG.lower()),
        (f"ssh://git@github.com/{FORMER_SLUG}.git", FORMER_SLUG.lower()),
        (f"https://github.com/{CURRENT_SLUG}.git", CURRENT_SLUG),
        # Not GitHub, or not a URL at all: no opinion, and nothing rewritten.
        ("https://gitlab.com/someone/mirror.git", ""),
        ("/srv/local/curie.git", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_the_slug_reader_understands_every_remote_form(url, expected):
    """Every URL form git accepts, including the scp-like one.

    Missing a form would not be a crash; it would silently classify a stale
    remote as somebody's fork and leave the install unable to update.
    """
    from curie_cli.update_cmd import _remote_slug

    assert _remote_slug(url) == expected


# ── Retargeting ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "origin",
    [
        f"https://github.com/{FORMER_SLUG}.git",
        f"git@github.com:{FORMER_SLUG}.git",
        f"ssh://git@github.com/{FORMER_SLUG}.git",
        f"https://github.com/{FORMER_SLUG.lower()}.git",
    ],
)
def test_a_former_name_is_repointed_at_the_repository_that_exists(checkout, origin):
    from curie_cli.update_cmd import OFFICIAL_REPO_URL, _retarget_stale_origin

    path, _stored = checkout(origin)
    assert _retarget_stale_origin(["git", "-C", str(path)], path) is True
    assert _origin(path) == OFFICIAL_REPO_URL


def test_the_ssh_form_is_repointed_to_https(checkout):
    """The old SSH address is just as dead, and HTTPS needs no key.

    Leaving it on SSH would trade a credential prompt for a key prompt.
    """
    from curie_cli.update_cmd import _retarget_stale_origin

    path, stored = checkout(f"git@github.com:{FORMER_SLUG}.git")
    if not stored.startswith("git@"):
        pytest.skip("this machine rewrites SSH GitHub remotes to HTTPS itself")
    _retarget_stale_origin(["git", "-C", str(path)], path)
    assert _origin(path).startswith("https://")


@pytest.mark.parametrize(
    "origin",
    [
        f"https://github.com/{CURRENT_SLUG}.git",
        "https://github.com/someone/my-fork.git",
        "git@github.com:someone/my-fork.git",
        "https://gitlab.com/someone/mirror.git",
    ],
)
def test_a_fork_a_mirror_or_an_already_correct_remote_is_left_alone(checkout, origin):
    """Rewriting someone's deliberate remote would be the worse bug.

    Only a name this project has actually published under is repointed; every
    other remote is a choice somebody made and is none of the installer's
    business.
    """
    from curie_cli.update_cmd import _retarget_stale_origin

    path, stored = checkout(origin)
    assert _retarget_stale_origin(["git", "-C", str(path)], path) is False
    # Against what git kept, not what was asked for: a machine-level
    # insteadOf rewrite is not this function leaving a fingerprint.
    assert _origin(path) == stored


def test_a_checkout_with_no_origin_at_all_is_not_a_crash(tmp_path):
    from curie_cli.update_cmd import _retarget_stale_origin

    path = tmp_path / "no-remote"
    path.mkdir()
    _git("init", "-q", ".", cwd=path)
    assert _retarget_stale_origin(["git", "-C", str(path)], path) is False


# ── The shell installer's copy of the same logic ─────────────────────────

def _bash_func(name: str) -> str:
    """One function, lifted out of the shipped installer."""
    src = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    start = src.index(f"{name}() {{")
    return src[start : src.index("\n}\n", start) + 3]


def _installer_repo_vars() -> str:
    """The installer's own REPO_* assignments, lifted verbatim.

    Read from the file rather than restated here. Restating them is what
    makes a harness pass while the shipped script is broken: emptying
    REPO_SLUG_FORMER in install.sh stops it repointing anything, and a test
    carrying its own copy of that list would not notice.
    """
    src = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    # Assignments only. The surrounding prose is not needed to run the
    # function, and dragging it in trips the repo's live-system guard, which
    # reads the whole command text looking for anything that resembles an
    # update against the real checkout.
    return "\n".join(
        line
        for line in src.splitlines()
        if re.match(r"^REPO_[A-Z_]+=", line)
    )


def _run_installer_retarget(path: Path) -> str:
    """Run install.sh's own retarget_stale_origin against ``path``."""
    script = "\n".join(
        [
            "set -e",
            # Only the presentation helpers are stubbed. Everything the
            # function actually decides with comes out of the installer.
            'RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; NC=""; BOLD=""',
            'log_info() { :; }',
            'log_warn() { :; }',
            'log_error() { :; }',
            'log_success() { :; }',
            _installer_repo_vars(),
            _bash_func("remote_slug"),
            _bash_func("retarget_stale_origin"),
            f'retarget_stale_origin "{path}"',
        ]
    )
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    return _origin(path)


@pytest.mark.live_system_guard_bypass
def test_the_shell_installer_repoints_a_stale_remote(checkout):
    """The same behaviour, in the script the reported failure came from.

    Bypasses the live-system guard, which blocks any subprocess whose text
    contains both "curie" and a bare "update" — this one does, from
    ``$CURIE_REPO`` and from an error message about updating a git remote.
    It is safe for the reason the guard documents: the only commands run are
    ``git remote get-url`` and ``git remote set-url`` inside a throwaway
    ``tmp_path`` checkout. Nothing fetches, and the real repository is never
    named on a command line.
    """
    path, _stored = checkout(f"https://github.com/{FORMER_SLUG}.git")
    assert _run_installer_retarget(path) == f"https://github.com/{CURRENT_SLUG}.git"


@pytest.mark.live_system_guard_bypass
def test_the_shell_installer_leaves_a_fork_alone(checkout):
    """See the note on the test above for why the guard is bypassed."""
    path, stored = checkout("https://github.com/someone/my-fork.git")
    assert _run_installer_retarget(path) == stored


def test_the_shell_installer_retargets_before_it_fetches():
    """Order is the whole point — a fetch first is the hang being fixed."""
    src = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    update_block = src[src.index("Existing installation found, updating...") :]
    retarget_at = update_block.index("retarget_stale_origin ")
    fetch_at = update_block.index("git fetch origin")
    assert retarget_at < fetch_at, (
        "install.sh fetches before repointing a stale remote — which is the "
        "credential prompt this exists to remove"
    )


# ── Nothing may block on a credential prompt ─────────────────────────────

@pytest.mark.parametrize(
    "relative,pattern",
    [
        ("scripts/install.sh", r"^export GIT_TERMINAL_PROMPT=0$"),
        ("scripts/install.ps1", r'^\$env:GIT_TERMINAL_PROMPT = "0"$'),
    ],
)
def test_the_installers_refuse_interactive_git_prompts(relative, pattern):
    """Git opens /dev/tty for prompts, so a pipe does not stop one.

    Without this, any unreachable remote hangs the install on a username
    prompt instead of failing with something a user can act on.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert re.search(pattern, text, re.MULTILINE), (
        f"{relative} does not disable git's interactive credential prompt"
    )


def test_the_powershell_prompt_guard_comes_after_param():
    """PowerShell requires param() to be the script's first statement.

    Putting the assignment above it does not fail to parse — it fails at run
    time with "The term 'param' is not recognized", after which the installer
    ignores every switch it was given.
    """
    text = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert text.index("param(") < text.index("$env:GIT_TERMINAL_PROMPT")
