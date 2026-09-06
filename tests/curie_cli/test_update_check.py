"""Tests for the update check mechanism in curie_cli.banner."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest




def test_check_for_updates_uses_cache(tmp_path, monkeypatch):
    """When cache is fresh, check_for_updates should return cached value without calling git."""
    from curie_cli.banner import check_for_updates
    from curie_cli import __version__

    # Create a fake git repo and fresh cache
    repo_dir = tmp_path / "curie-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    cache_file = tmp_path / ".update_check"
    cache_file.write_text(json.dumps({"ts": time.time(), "behind": 3, "ver": __version__}))

    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    with patch("curie_cli.banner.subprocess.run") as mock_run:
        result = check_for_updates()

    assert result == 3
    mock_run.assert_not_called()






def test_prefetch_non_blocking():
    """prefetch_update_check() should return immediately without blocking."""
    import curie_cli.banner as banner

    # Reset module state
    banner._update_result = None
    banner._update_check_done = threading.Event()

    with patch.object(banner, "check_for_updates", return_value=5):
        start = time.monotonic()
        banner.prefetch_update_check()
        elapsed = time.monotonic() - start

        # Should return almost immediately (well under 1 second)
        assert elapsed < 1.0

        # Wait for the background thread to finish
        banner._update_check_done.wait(timeout=5)
        assert banner._update_result == 5


def test_check_via_local_git_fetch_failure_returns_none(tmp_path, monkeypatch):
    """When git fetch fails and the stale origin/main ref is not ahead,
    _check_via_local_git must return None (#82166).

    A stale tracking ref cannot prove *currentness* (rev-list 0 just means
    the ref hasn't caught up), so returning None is the honest inconclusive
    result — and the caller must not cache it as "up to date".
    """
    from curie_cli import banner

    repo_dir = tmp_path / "curie-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    # Simulate a non-shallow, non-SSH-remote checkout
    def mock_git_stdout(args, *, cwd, timeout=5):
        if args[:2] == ["remote", "get-url"]:
            return "https://github.com/thisismynewfmail-ui/Cur-Agnt.git"
        if args[:2] == ["rev-parse", "--is-shallow-repository"]:
            return "false"
        # The stale tracking ref reports nothing to pull.
        if args[:2] == ["rev-list", "--count"]:
            return "0"
        return None

    # Fetch fails (returncode != 0); stale rev-list reports 0 behind
    failed_proc = MagicMock()
    failed_proc.returncode = 1
    failed_proc.stdout = ""
    failed_proc.stderr = "fatal: could not reach remote"

    def mock_run(args, **kwargs):
        if args[:2] == ["fetch", "origin"]:
            return failed_proc
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(banner, "_git_stdout", mock_git_stdout)
    monkeypatch.setattr(banner, "_run_git", mock_run)

    result = banner._check_via_local_git(repo_dir)
    assert result is None, (
        "Fetch failure with stale 0-behind must return None, not 'up to date'"
    )


def test_check_via_local_git_fetch_failure_keeps_positive_stale_count(tmp_path, monkeypatch):
    """A failed fetch must preserve sound evidence: if the stale origin/main
    ref already shows HEAD behind, that positive count is still an update
    signal and must be returned (review #92578)."""
    from curie_cli import banner

    repo_dir = tmp_path / "curie-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    def mock_git_stdout(args, *, cwd, timeout=5):
        if args[:2] == ["remote", "get-url"]:
            return "https://github.com/thisismynewfmail-ui/Cur-Agnt.git"
        if args[:2] == ["rev-parse", "--is-shallow-repository"]:
            return "false"
        # The stale tracking ref still says HEAD is five behind.
        if args[:2] == ["rev-list", "--count"]:
            return "5"
        return None

    failed_proc = MagicMock()
    failed_proc.returncode = 1
    failed_proc.stdout = ""
    failed_proc.stderr = "fatal: could not reach remote"

    def mock_run(args, **kwargs):
        if args[:2] == ["fetch", "origin"]:
            return failed_proc
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(banner, "_git_stdout", mock_git_stdout)
    monkeypatch.setattr(banner, "_run_git", mock_run)

    result = banner._check_via_local_git(repo_dir)
    assert result == 5, "Stale positive behind-count must be preserved on fetch failure"


def test_check_via_local_git_fetch_failure_rev_list_error_returns_none(tmp_path, monkeypatch):
    """If the stale rev-list itself fails, the check stays inconclusive (None)."""
    from curie_cli import banner

    repo_dir = tmp_path / "curie-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    def mock_git_stdout(args, *, cwd, timeout=5):
        if args[:2] == ["remote", "get-url"]:
            return "https://github.com/thisismynewfmail-ui/Cur-Agnt.git"
        if args[:2] == ["rev-parse", "--is-shallow-repository"]:
            return "false"
        return None

    failed_proc = MagicMock()
    failed_proc.returncode = 1
    failed_proc.stdout = ""
    failed_proc.stderr = "fatal: could not reach remote"

    def mock_run(args, **kwargs):
        if args[:2] == ["fetch", "origin"]:
            return failed_proc
        raise AssertionError(f"unexpected git call: {args}")

    # The stale rev-list itself fails, so ``_git_stdout`` answers None for it
    # (the mock above returns None for anything it does not recognise).
    monkeypatch.setattr(banner, "_git_stdout", mock_git_stdout)
    monkeypatch.setattr(banner, "_run_git", mock_run)

    result = banner._check_via_local_git(repo_dir)
    assert result is None


def test_check_for_updates_does_not_cache_none(tmp_path, monkeypatch):
    """check_for_updates must not cache None results so a transient fetch
    failure doesn't suppress retries for the full 6-hour cache window (#82166).

    Instead of mocking the full Path resolution chain, we verify the cache-write
    guard directly: call check_for_updates with a mocked _check_via_local_git
    that returns None, and confirm no cache file is created.
    """
    import curie_cli.banner as banner

    cache_file = tmp_path / ".update_check"
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    monkeypatch.delenv("CURIE_REVISION", raising=False)

    # Create a fake repo dir so the .git check passes
    repo_dir = tmp_path / "curie-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    # Mock the internal functions to force the local-git path returning None
    monkeypatch.setattr(banner, "_check_via_local_git", lambda rd: None)
    monkeypatch.setattr(
        "curie_cli.config.detect_install_method", lambda root: "git"
    )
    monkeypatch.setattr(
        "curie_cli.config.get_project_root", lambda: repo_dir
    )

    # Patch __file__ resolution by monkeypatching the module's Path calls.
    # check_for_updates does: Path(__file__).parent.parent.resolve()
    # We intercept by making the resolve() return our fake repo_dir.
    original_init = Path.__init__

    def patched_path_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

    # Simpler: just patch the get_curie_home and the repo_dir resolution
    # by making check_for_updates find our fake repo via curie_home fallback.
    # The code checks Path(__file__).parent.parent/.git first, then falls
    # back to curie_home / "curie-agent". We ensure the fallback hits.
    # To do this, we make Path(__file__).parent.parent.resolve() return
    # a path without .git, so it falls through to curie_home / "curie-agent".
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        s = str(self)
        if "banner.py" in s or s.endswith("curie_cli"):
            # Return a path that has no .git, forcing the fallback
            return tmp_path / "no-git-here"
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    result = banner.check_for_updates()
    assert result is None

    # The cache file must NOT have been written with a None result
    assert not cache_file.exists(), "None result must not be cached"




