"""The addresses the installer and the update path actually reach for.

Every one of these was wrong at once: the README's install one-liner, the
installer's clone URLs, the update path's official-repo constants and the
desktop updater's canonical string all named a repository that returns 404.
They were wrong *separately* — each is a hand-written copy of the same
"owner/name" pair, so a rename reaches whichever ones someone remembers.

These pin the two properties that keep that from recurring: the slug is
stated once per surface and everything else is derived from it, and the
names the repository has previously been published under stay recognised so
existing checkouts are not reclassified as forks.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The repository this install ships from.
EXPECTED_SLUG = "thisismynewfmail-ui/cr-agnt"

#: Names it has been published under before. GitHub redirects a renamed
#: repository, so a checkout carrying one of these is still the official one.
FORMER_SLUGS = ("thisismynewfmail-ui/cru", "thisismynewfmail-ui/Cur-Agnt")


# ── The constants ────────────────────────────────────────────────────────

def test_the_update_path_derives_every_url_from_one_slug():
    from curie_cli.update_cmd import (
        OFFICIAL_REPO_RAW_BASE,
        OFFICIAL_REPO_SLUG,
        OFFICIAL_REPO_URL,
        OFFICIAL_REPO_WEB_URL,
    )

    assert OFFICIAL_REPO_SLUG == EXPECTED_SLUG
    assert OFFICIAL_REPO_WEB_URL == f"https://github.com/{EXPECTED_SLUG}"
    assert OFFICIAL_REPO_URL == f"https://github.com/{EXPECTED_SLUG}.git"
    assert OFFICIAL_REPO_RAW_BASE == f"https://raw.githubusercontent.com/{EXPECTED_SLUG}"


@pytest.mark.parametrize("slug", (EXPECTED_SLUG, *FORMER_SLUGS))
@pytest.mark.parametrize(
    "template",
    (
        "https://github.com/{slug}.git",
        "git@github.com:{slug}.git",
        "https://github.com/{slug}",
        "git@github.com:{slug}",
    ),
)
def test_an_official_checkout_is_not_mistaken_for_a_fork(slug, template):
    """Including under a former name.

    A rename does not break `git pull` — GitHub redirects it — so an install
    made under the old name keeps working. Dropping the old name from the
    recognised set would not break their update; it would quietly reclassify
    them as a fork and start prompting them to add an upstream remote they
    already effectively have.
    """
    from curie_cli.update_cmd import _is_fork

    assert _is_fork(template.format(slug=slug)) is False


@pytest.mark.parametrize(
    "origin",
    (
        "https://github.com/someone/curie-agent.git",
        "git@github.com:someone/cru.git",
        "https://gitlab.com/thisismynewfmail-ui/cru.git",
    ),
)
def test_a_real_fork_is_still_detected(origin):
    from curie_cli.update_cmd import _is_fork

    assert _is_fork(origin) is True


# ── The shipped scripts ──────────────────────────────────────────────────

def _text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


#: Anything that addresses GitHub by "owner/name", in any of the forms the
#: installers use. The slug is group 1.
_GITHUB_SLUG = re.compile(
    r"(?:raw\.githubusercontent\.com/|github\.com[:/])"
    r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
)

#: The files a user's install actually flows through, in order: the one-liner
#: they paste, the script it fetches, and the script that clones.
INSTALL_SURFACES = (
    "README.md",
    "scripts/install.sh",
    "scripts/install.ps1",
    "scripts/install.cmd",
)

#: How each installer *defines* the slug it will clone from. Checking the
#: definition rather than the URLs around it is the point: the URLs are
#: derived at run time, so a URL-shaped search reads the derivation and never
#: sees the value — a wrong slug would sail straight past it.
SLUG_DEFINITIONS = {
    "scripts/install.sh": re.compile(
        r'^REPO_SLUG="\$\{CURIE_REPO:-([^}"]+)\}"', re.MULTILINE
    ),
    "scripts/install.ps1": re.compile(
        r'^\$RepoSlug = .*?else \{ "([^"]+)" \}', re.MULTILINE
    ),
    "scripts/install.cmd": re.compile(
        r'^if "%CURIE_REPO%"=="" set "CURIE_REPO=([^"]+)"', re.MULTILINE
    ),
}


@pytest.mark.parametrize("relative", sorted(SLUG_DEFINITIONS))
def test_each_installer_clones_from_this_repository(relative):
    """The value the installer will actually clone from.

    This is the one that was wrong in every installer at once, and the one a
    URL-shaped check cannot see now that the slug is defined once and the
    URLs are built from it.
    """
    match = SLUG_DEFINITIONS[relative].search(_text(relative))
    assert match, (
        f"{relative} no longer defines its repository slug where this test "
        "looks for it — if the definition moved, move this pattern with it "
        "rather than deleting the check"
    )
    assert match.group(1) == EXPECTED_SLUG


@pytest.mark.parametrize("relative", INSTALL_SURFACES)
def test_no_install_surface_names_a_repository_that_is_not_ours(relative):
    """A stale slug here is a 404 in the user's terminal on their first move.

    Former names are allowed to appear (the update path recognises them on
    purpose) but nothing may address a *third* repository — which is exactly
    what had happened: a half-finished rename left the desktop updater
    pointing at the project this was forked from.
    """
    allowed = {EXPECTED_SLUG.lower(), *(s.lower() for s in FORMER_SLUGS)}
    text = _text(relative)
    found = {m.group(1).lower() for m in _GITHUB_SLUG.finditer(text)}
    # The slug definitions are not URL-shaped, so collect those too.
    pattern = SLUG_DEFINITIONS.get(relative)
    if pattern is not None:
        found.update(m.group(1).lower() for m in pattern.finditer(text))
    # Slugs that belong to other projects entirely (astral-sh/uv and the like)
    # are fetched deliberately; only this owner's repositories are ours to pin.
    owner = EXPECTED_SLUG.split("/", 1)[0].lower()
    ours = {slug for slug in found if slug.startswith(f"{owner}/")}
    assert ours <= allowed, f"{relative} addresses {sorted(ours - allowed)}"


@pytest.mark.parametrize("relative", ("scripts/install.sh", "scripts/install.ps1"))
def test_each_installer_states_the_slug_once(relative):
    """Repeating it is how it drifts: one copy gets updated and the rest lie.

    The installer keeps printing the right address while cloning the wrong
    one, which is the failure that is hardest to diagnose from a bug report.
    """
    literals = _text(relative).count(EXPECTED_SLUG)
    assert literals <= 2, (
        f"{relative} writes the slug out {literals} times; derive the URLs "
        "from the single definition instead"
    )


def test_the_readme_one_liner_is_the_script_that_exists():
    """The command a first-time user pastes, checked against the repo tree."""
    readme = _text("README.md")
    pasted = re.findall(
        r"https://raw\.githubusercontent\.com/([^/\s]+/[^/\s]+)/main/(scripts/\S+?)[\s)]",
        readme + "\n",
    )
    assert pasted, "the README no longer carries an install one-liner"
    for slug, script_path in pasted:
        assert slug == EXPECTED_SLUG, f"README installs from {slug}"
        assert (REPO_ROOT / script_path).is_file(), (
            f"README fetches {script_path}, which is not in the repository"
        )


def test_the_one_liner_points_at_this_repository():
    """The URL has to name the remote this checkout actually came from.

    Skipped rather than guessed at when the checkout has no GitHub origin
    (a mirror, a tarball, a CI checkout with the remote stripped) — the
    invariant only means something where there is a remote to compare to.
    """
    try:
        origin = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a missing git is not a failure here
        origin = ""
    if "github.com" not in origin:
        pytest.skip("no GitHub origin to compare the install URL against")

    match = _GITHUB_SLUG.search(origin)
    assert match, f"could not read a slug out of origin {origin!r}"
    actual = match.group(1).removesuffix(".git").lower()
    allowed = {EXPECTED_SLUG.lower(), *(s.lower() for s in FORMER_SLUGS)}
    assert actual in allowed, (
        f"this checkout came from {actual}, but the installers address "
        f"{EXPECTED_SLUG}"
    )
