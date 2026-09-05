"""The small auxiliary calls get an hour, and existing configs get it too.

``auxiliary.title_generation.timeout`` was 30s and ``auxiliary.vision.timeout``
was 120s. Both were sized for a fast completion model, and both are now
routinely served by a reasoning model that spends its whole thinking budget
before emitting a first token — so the call was cancelled while it was still
working and the user saw "⚠ Auxiliary title generation failed: Request timed
out." on a job that would have finished. A dead stream is still caught in
about a minute by the no-progress watchdog, so a generous ceiling costs
nothing.

A default alone would not have fixed anyone already running Curie: their
config.yaml carries the old number. Hence the migration.
"""

from __future__ import annotations

import importlib

import pytest
import yaml

ONE_HOUR = 3600


# ── The defaults ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("task", ["title_generation", "vision"])
def test_the_default_is_one_hour_in_seconds(task):
    from curie_cli.config_defaults import DEFAULT_CONFIG

    section = DEFAULT_CONFIG["auxiliary"][task]
    assert section["timeout"] == ONE_HOUR, (
        f"auxiliary.{task}.timeout is {section['timeout']}, not one hour"
    )


def test_the_image_download_timeout_is_left_alone():
    """A separate, documented knob for a separate failure mode.

    The hour is for the model's thinking. How long to wait on an HTTP fetch
    is the user's call about their connection, and raising it would turn an
    unreachable host into an hour of silence.
    """
    from curie_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["auxiliary"]["vision"]["download_timeout"] == 30


def test_the_schema_version_advanced_with_the_change():
    """A default change nobody migrates to is a default change nobody gets."""
    from curie_cli.config_defaults import DEFAULT_CONFIG
    from curie_cli.config_migrations import MIGRATIONS

    version = DEFAULT_CONFIG["_config_version"]
    assert version == MIGRATIONS[-1][0], (
        f"_config_version is {version} but the last migration targets "
        f"{MIGRATIONS[-1][0]}"
    )


# ── The migration ────────────────────────────────────────────────────────

def _run_migration(tmp_path, monkeypatch, auxiliary: dict) -> dict:
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    import curie_constants

    importlib.reload(curie_constants)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({"_config_version": 40, "auxiliary": auxiliary}),
        encoding="utf-8",
    )
    import curie_cli.config_migrations as migrations

    results = {"config_added": [], "config_removed": []}
    migrations.run_migrations(40, results, quiet=True)
    return yaml.safe_load(cfg.read_text(encoding="utf-8"))


def test_an_existing_config_on_the_old_defaults_is_raised(tmp_path, monkeypatch):
    after = _run_migration(
        tmp_path,
        monkeypatch,
        {"title_generation": {"timeout": 30}, "vision": {"timeout": 120}},
    )
    assert after["auxiliary"]["title_generation"]["timeout"] == ONE_HOUR
    assert after["auxiliary"]["vision"]["timeout"] == ONE_HOUR


@pytest.mark.parametrize(
    "auxiliary",
    [
        {"title_generation": {"timeout": 45}, "vision": {"timeout": 60}},
        {"title_generation": {"timeout": 5}, "vision": {"timeout": 900}},
    ],
)
def test_a_value_the_user_chose_is_never_overwritten(
    tmp_path, monkeypatch, auxiliary
):
    """Anything other than the old default is a deliberate setting."""
    chosen = {task: cfg["timeout"] for task, cfg in auxiliary.items()}
    after = _run_migration(tmp_path, monkeypatch, auxiliary)
    for task, value in chosen.items():
        assert after["auxiliary"][task]["timeout"] == value, (
            f"the migration overwrote a deliberate auxiliary.{task}.timeout"
        )


def test_unrelated_auxiliary_tasks_are_untouched(tmp_path, monkeypatch):
    after = _run_migration(
        tmp_path,
        monkeypatch,
        {
            "title_generation": {"timeout": 30},
            "compression": {"timeout": 900, "provider": "auto"},
        },
    )
    assert after["auxiliary"]["compression"] == {
        "timeout": 900,
        "provider": "auto",
    }


def test_a_config_with_no_auxiliary_section_is_left_alone(tmp_path, monkeypatch):
    """Nothing to raise, and nothing should be invented."""
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    import curie_constants

    importlib.reload(curie_constants)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"_config_version": 40}), encoding="utf-8")
    import curie_cli.config_migrations as migrations

    results = {"config_added": [], "config_removed": []}
    migrations.run_migrations(40, results, quiet=True)
    after = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "auxiliary" not in after


def test_the_migration_reports_what_it_changed(tmp_path, monkeypatch):
    """A silent config rewrite is indistinguishable from a bug."""
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    import curie_constants

    importlib.reload(curie_constants)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {"_config_version": 40, "auxiliary": {"vision": {"timeout": 120}}}
        ),
        encoding="utf-8",
    )
    import curie_cli.config_migrations as migrations

    results = {"config_added": [], "config_removed": []}
    migrations.run_migrations(40, results, quiet=True)
    reported = " ".join(results["config_added"])
    assert "vision" in reported and "timeout" in reported, reported
