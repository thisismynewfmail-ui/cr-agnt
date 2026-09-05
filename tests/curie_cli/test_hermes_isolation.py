"""Curie must not touch a Hermes install.

They are separate products. On a machine that still runs Hermes, any sharing
is not convenience — it is two agents taking turns corrupting each other's
sessions, config and credentials.

An earlier version of the rename mirrored every ``HERMES_*`` variable onto its
``CURIE_*`` name and adopted ``~/.hermes`` as the home when ``~/.curie`` did
not exist. A live ``HERMES_HOME`` in a shell profile would then have sent
Curie's writes straight into the Hermes home. These tests exist so that
cannot come back.
"""

from __future__ import annotations

import os
from pathlib import Path

import curie_constants as cc


# ── The home directory ───────────────────────────────────────────────────

def test_default_home_is_curie_even_when_a_hermes_home_exists(tmp_path, monkeypatch):
    """An existing ~/.hermes must not be adopted, however complete it looks."""
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    (hermes / "config.yaml").write_text("agent: {}\n", encoding="utf-8")
    (hermes / "sessions").mkdir()

    monkeypatch.delenv("CURIE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cc.sys, "platform", "linux")

    assert cc._get_platform_default_curie_home() == tmp_path / ".curie"


def test_windows_default_home_is_curie_even_when_hermes_exists(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData" / "Local"
    (appdata / "hermes").mkdir(parents=True)

    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setattr(cc.sys, "platform", "win32")

    assert cc._get_platform_default_curie_home() == appdata / "curie"


def test_no_adoption_helper_survives():
    """The helper that resolved ~/.hermes as a home is gone for good."""
    assert not hasattr(cc, "_adopt_legacy_home")


def test_explicit_curie_home_is_honoured(tmp_path, monkeypatch):
    chosen = tmp_path / "somewhere-else"
    monkeypatch.setenv("CURIE_HOME", str(chosen))
    assert cc.get_curie_home() == chosen


# ── The environment ──────────────────────────────────────────────────────

def test_hermes_env_vars_are_never_mirrored(monkeypatch):
    """A live Hermes environment must leave Curie's settings untouched.

    ``HERMES_HOME`` is the dangerous one — mirrored, it would redirect every
    write into the Hermes home — but the rule is all of them, because any
    mirrored value silently changes Curie's behaviour based on a different
    product's configuration.
    """
    monkeypatch.delenv("CURIE_HOME", raising=False)
    monkeypatch.delenv("CURIE_STREAM_STALE_TIMEOUT", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/home/drew/.hermes")
    monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "420")

    import importlib

    importlib.reload(cc)
    try:
        assert "CURIE_HOME" not in os.environ
        assert "CURIE_STREAM_STALE_TIMEOUT" not in os.environ
        assert cc.get_curie_home() == cc._get_platform_default_curie_home()
        assert ".hermes" not in str(cc.get_curie_home())
    finally:
        importlib.reload(cc)


def test_no_env_mirroring_helper_survives():
    assert not hasattr(cc, "adopt_legacy_env")


def test_a_hermes_home_in_the_environment_cannot_redirect_writes(monkeypatch, tmp_path):
    """The specific failure this file exists for."""
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    monkeypatch.delenv("CURIE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cc.sys, "platform", "linux")

    resolved = cc._get_platform_default_curie_home()
    assert resolved == tmp_path / ".curie"
    assert resolved != hermes


# ── Repo-wide: nothing installs into, or removes from, a Hermes install ──

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_installers_never_name_a_hermes_path():
    """Neither installer may write to, read from, or migrate ~/.hermes."""
    root = _repo_root()
    for name in ("scripts/install.sh", "scripts/install.ps1", "scripts/install.cmd"):
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "hermes" not in text.lower(), (
            f"{name} references Hermes; the two installs must not share a path"
        )


def test_uninstaller_never_names_a_hermes_path():
    """`curie uninstall` must not delete another product's files."""
    text = (_repo_root() / "curie_cli" / "uninstall.py").read_text(encoding="utf-8")
    # One comment mentions a *deliberately non-matching* prefix as a negative
    # test case for the PATH-entry scan; that is the only allowed occurrence.
    hits = [
        line for line in text.splitlines()
        if "hermes" in line.lower() and "chermes-foo" not in line
    ]
    assert not hits, f"uninstaller references Hermes: {hits}"


def test_service_identifiers_are_curie_scoped():
    """Systemd units and launchd labels must not collide with Hermes's.

    A shared unit name means installing one agent stops the other.
    """
    import subprocess

    root = _repo_root()
    out = subprocess.run(
        ["git", "grep", "-ohE",
         r"hermes[a-z-]*\.service|com\.[a-z.]*hermes[a-z.]*",
         "--", "*.py", "*.sh", "*.ps1", "*.service", "*.plist"],
        cwd=root, capture_output=True, text=True,
    ).stdout.strip()
    assert not out, f"Hermes-named service identifiers still present:\n{out}"
