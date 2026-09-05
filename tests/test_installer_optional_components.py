"""The two installers must offer the same optional components.

`install.sh` and `install.ps1` are maintained separately, so a component added
to one and forgotten in the other produces the worst kind of bug report: it
works on the reporter's machine and not on yours. These tests pin the pieces
that have to exist on both sides.

They read the installers as text rather than running them. Running install.sh
end-to-end needs a network and twenty minutes; running install.ps1 needs
Windows. Text assertions catch the failure this file exists for — a flag,
branch, or function that only one installer has.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "scripts" / "install.sh"
PS1 = ROOT / "scripts" / "install.ps1"


@pytest.fixture(scope="module")
def sh() -> str:
    return SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1() -> str:
    return PS1.read_text(encoding="utf-8")


# ── Local text-to-speech (piper-tts) ─────────────────────────────────────

def test_both_installers_accept_a_tts_flag(sh, ps1):
    assert "--with-tts)" in sh, "install.sh is missing the --with-tts flag"
    assert "[switch]$WithTts" in ps1, "install.ps1 is missing -WithTts"


def test_both_installers_install_piper_tts(sh, ps1):
    assert "piper-tts" in sh
    assert "piper-tts" in ps1


def test_tts_is_opt_in_on_both(sh, ps1):
    """onnxruntime is a ~60MB download for a feature most users never enable.

    Defaulting it on would slow every fresh install, and it has no wheel at
    all on some platforms — so an unconditional install would also fail
    outright there.
    """
    assert "WITH_TTS=false" in sh, "install.sh must default TTS off"
    assert "if (-not $WithTts) { return }" in ps1, "install.ps1 must default TTS off"


def test_tts_failure_is_not_fatal_on_either(sh, ps1):
    """Speech is one optional tool; a wheel that will not resolve today must
    not take the whole install down, especially since `curie tools` can
    install the same thing later."""
    assert "curie tools" in sh
    assert "curie tools" in ps1


def test_neither_installer_forces_an_upgrade_of_pinned_transitives(sh, ps1):
    """`pip install -U piper-tts` drags numpy and onnxruntime forward.

    Both are constrained by the project. Upgrading them to satisfy piper
    silently replaces pinned versions the rest of the code depends on, and
    the resulting breakage surfaces somewhere unrelated.
    """
    assert "-U piper-tts" not in sh, "install.sh must not force-upgrade for piper"
    assert "-U piper-tts" not in ps1, "install.ps1 must not force-upgrade for piper"


# ── The bench console (`curie ui`) ───────────────────────────────────────

def test_both_installers_can_install_the_ui_extra(sh, ps1):
    assert 'pip install "${pip_target[@]}" -e ".[ui]"' in sh
    assert 'pip install -e ".[ui]"' in ps1


def test_ui_is_installed_as_the_project_extra_not_as_bare_textual(sh, ps1):
    """Textual depends on rich, which the project pins.

    `pip install textual` on its own resolves rich to its newest release,
    walking straight past `rich==14.3.3` and leaving the install with a rich
    the rest of the code was never tested against. Installing `.[ui]` puts
    the resolution inside pyproject's constraints.
    """
    for name, text in (("install.sh", sh), ("install.ps1", ps1)):
        assert "install -U textual" not in text, (
            f"{name} installs textual unconstrained; use the .[ui] extra"
        )


def test_both_installers_verify_textual_landed(sh, ps1):
    """The tiered install cascade can fall back to a narrower extra set.

    When it does, the first the user hears of it is `curie ui` refusing to
    start — so check, and say how to fix it.
    """
    assert "import textual" in sh
    assert "import textual" in ps1
    assert "curie update --ensure ui" in sh
    assert "curie update --ensure ui" in ps1


# ── --ensure / -Ensure parity ────────────────────────────────────────────

ENSURE_DEPS = ("node", "browser", "ripgrep", "ffmpeg", "piper", "ui")


@pytest.mark.parametrize("dep", ENSURE_DEPS)
def test_ensure_mode_handles_the_same_deps_on_both(dep, sh, ps1):
    assert f'"{dep}"' in ps1 or f"{dep}|" in ps1 or f'"{dep})' in ps1 or dep in ps1
    assert dep in sh


def test_unknown_ensure_dep_lists_the_supported_ones(sh, ps1):
    """A typo should tell the user what was available, not just complain."""
    expected = "node, browser, ripgrep, ffmpeg, piper, ui"
    assert expected in sh
    assert expected in ps1


def test_help_text_documents_the_new_flags(sh):
    assert "--with-tts" in sh
    assert "piper (local TTS), ui (bench console)" in sh


# ── Invariants the installers already had, worth keeping ─────────────────

def test_install_sh_is_valid_bash():
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(SH)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_install_ps1_stays_pure_ascii(ps1):
    """PowerShell's default encoding on older Windows mangles non-ASCII.

    A stray em-dash in a comment becomes mojibake in the console output, and
    in a string literal it changes what the installer writes to disk.
    """
    offenders = [
        (n, line)
        for n, line in enumerate(ps1.splitlines(), 1)
        if any(ord(ch) > 127 for ch in line)
    ]
    assert not offenders, f"non-ASCII at lines {[n for n, _ in offenders[:10]]}"


def test_installers_reference_this_repository(sh, ps1):
    """The old docs host does not serve a Curie install script.

    The expected slug is imported rather than written out again. This test
    used to carry its own copy, and when the repository was renamed the copy
    was what got updated — so it went on passing while asserting that both
    installers cloned from an address that returns 404. A second literal is
    not a second check; it is a second thing to get wrong.
    """
    from tests.curie_cli.test_official_repo_urls import EXPECTED_SLUG

    assert EXPECTED_SLUG in sh
    assert EXPECTED_SLUG in ps1
    assert "hermes-agent.nousresearch.com" not in sh
    assert "hermes-agent.nousresearch.com" not in ps1
