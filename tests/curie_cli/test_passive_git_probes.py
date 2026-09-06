"""``curie`` must never stop and ask GitHub for a password.

The reported fault: the repository the update check points at was deleted, and
``curie`` — any subcommand, on the way to doing something else entirely —
opened a GitHub credential prompt for it. The user is asked for a username and
password for a repository they have never heard of, by a command that was
supposed to start a chat.

Why it happens is worth stating, because it is not obvious and it is not
git being unreasonable: a repository that does not exist and one the caller
may not read both answer **404**, because telling them apart would leak the
existence of private repositories. So git assumes the second and asks for
credentials. It opens ``/dev/tty`` directly to do it, which means neither
``capture_output`` nor a redirected stdin hides the prompt, and the subprocess
timeout does not cancel a read that is blocked on a human.

The guard is therefore at the source: every git invocation on the passive path
refuses the prompt, and the failure comes back as a non-zero exit the callers
already treat as "check inconclusive". Everything below holds that guard in
place — the environment, the fact that *every* call gets it, and that a
repository nobody can read costs the update badge and nothing else.
"""

from __future__ import annotations

import subprocess

import pytest

from curie_cli import banner


# ── The environment every probe runs under ───────────────────────────────


def test_the_probe_environment_refuses_every_way_of_asking_a_human():
    env = banner._git_env()
    # git's own prompt, on the terminal it opened directly.
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # The graphical ones — a password box behind the terminal is a hang the
    # user cannot even see.
    assert env["GIT_ASKPASS"] == ""
    assert env["SSH_ASKPASS"] == ""
    # Git Credential Manager, the default helper on Windows installs.
    assert env["GCM_INTERACTIVE"] == "Never"


def test_the_probe_environment_keeps_the_rest_of_the_environment(monkeypatch):
    """A working credential helper must still work; only the blocking paths go."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/somewhere/gitconfig")
    assert banner._git_env()["GIT_CONFIG_GLOBAL"] == "/somewhere/gitconfig"


def test_every_probe_closes_stdin_too(monkeypatch):
    """A credential helper can read the parent's inherited stdin."""
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(banner.subprocess, "run", fake_run)
    banner._run_git(["status"])
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["args"][0] == "git"


def test_a_git_that_cannot_be_run_at_all_is_not_an_exception(monkeypatch):
    """No git on PATH is a check that cannot run, not a console that dies."""

    def explode(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(banner.subprocess, "run", explode)
    assert banner._run_git(["status"]) is None


# ── Every call goes through it ───────────────────────────────────────────


def test_no_probe_shells_out_to_git_behind_the_guard():
    """One helper, so a call added later cannot quietly skip the guard.

    Read off the source rather than by exercising each path: the paths that
    matter are the ones that reach the *network*, and a test that ran them
    would either need a network or a mock per call — and a mock per call is
    exactly the thing that lets a new, unmocked call slip through.
    """
    import inspect

    source = inspect.getsource(banner)
    # The one permitted ``subprocess.run`` is the one inside ``_run_git``.
    assert source.count("subprocess.run(") == 1, (
        "a git call was added outside _run_git, so it can prompt for a password"
    )
    assert "def _run_git(" in source


def test_the_upstream_url_is_derived_from_the_one_slug():
    """A hard-coded second copy of the address is how this broke in the first place."""
    from curie_constants import OFFICIAL_REPO_GIT_URL, OFFICIAL_REPO_SLUG

    assert banner._UPSTREAM_REPO_URL == OFFICIAL_REPO_GIT_URL
    assert OFFICIAL_REPO_SLUG in banner._OFFICIAL_REPO_CANONICAL


# ── What a missing repository actually costs ─────────────────────────────


def test_a_remote_that_answers_404_fails_fast_instead_of_prompting():
    """The end-to-end shape of the fault, against a repository that is not there.

    Marked as needing the network only in the sense that it runs git against
    github.com; with no network it fails on the lookup instead, which is the
    same class of answer — a non-zero exit and no prompt.
    """
    result = banner._run_git(
        [
            "ls-remote",
            "https://github.com/thisismynewfmail-ui/a-repository-that-is-not-there.git",
            "refs/heads/main",
        ],
        timeout=25,
    )
    if result is None:
        pytest.skip("git could not be run here")
    assert result.returncode != 0
    combined = (result.stderr or "") + (result.stdout or "")
    # Either it refused the prompt, or it never got far enough to be asked.
    assert combined.strip(), "git failed silently, which is not a diagnosis"
    assert "Username for" not in combined or "prompts disabled" in combined


def test_a_failed_probe_is_an_inconclusive_check_not_a_crash(monkeypatch, tmp_path):
    """The badge goes away. Nothing else about the console changes."""
    repo = tmp_path / "curie-agent"
    (repo / ".git").mkdir(parents=True)

    monkeypatch.setattr(banner, "_git_stdout", lambda *a, **k: None)
    monkeypatch.setattr(
        banner,
        "_run_git",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, 128, "", "fatal: could not read Username for 'https://github.com'"
        ),
    )
    assert banner._check_via_local_git(repo) is None


def test_a_refused_prompt_is_explained_as_a_moved_or_private_remote():
    """"Authentication failed" is true and useless. Which of the two is it?"""
    from curie_cli.update_cmd import _classify_fetch_failure

    said = _classify_fetch_failure(
        "fatal: could not read Username for 'https://github.com': "
        "terminal prompts disabled"
    )
    assert "moved or been removed" in said
    assert "private" in said
    assert "remote set-url" in said
    # And it says the console is still fine, because it is.
    assert "keeps working" in said


def test_the_update_path_never_prompts_either():
    """``curie update`` is not watched by anyone either — it is run and left."""
    import inspect

    from curie_cli import update_cmd

    source = inspect.getsource(update_cmd)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith('git_cmd + ["fetch"') or stripped.startswith(
            'git_cmd + ["pull"'
        ):
            break
    else:  # pragma: no cover - the module always has these
        pytest.fail("no fetch/pull call found to check")

    # Every network call in the module carries the guard.
    network = [
        chunk
        for chunk in source.split("subprocess.run(")[1:]
        if '"fetch"' in chunk.split(")")[0] or '"pull"' in chunk.split(")")[0]
    ]
    assert network, "no fetch/pull subprocess call found"
    for chunk in network:
        head = chunk[: chunk.index("\n        )") + 12] if "\n        )" in chunk else chunk[:600]
        assert "noninteractive_git_env" in head, head[:300]
