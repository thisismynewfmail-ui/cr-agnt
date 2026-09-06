"""The SCHEDULE pane: real tasks, in the real store, visible from every window.

Three things have to be true together, and each of them is a different kind of
claim:

* **it is a scheduling surface**, so a countdown, a wall clock and a cron
  expression all become the one schedule string the store parses, and the
  numbers the table prints are the store's own;
* **it schedules for real**, so what the form writes is a job the gateway
  fires — the same file ``curie cron create`` writes, not a list this console
  keeps to itself;
* **it is not the only window**, so a task created in one console shows up in
  another, and a run happening in the gateway is visible here while it happens.

The task window is tested as what it is: a chat onto one run, which is a real
conversation in the session store because every fire opens one.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from curie_cli.bench_ui import dos, schedule  # noqa: E402
from curie_cli.bench_ui.app import (  # noqa: E402
    PANE_KEYS,
    SWITCHES,
    BenchConsole,
    KeyCap,
    Switch,
    pane_key_label,
)
from curie_cli.bench_ui.schedule_pane import NAME_MIN  # noqa: E402

from tests.curie_cli.bench_ui.test_console import _StubBridge  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _settle(pilot, times: int = 5) -> None:
    for _ in range(times):
        await pilot.pause()


def _console(mode: str = dos.MODE_BENCH) -> BenchConsole:
    app = BenchConsole(bridge=_StubBridge())
    app._display_mode = mode
    app._optics = dos.Optics(mode=mode)
    app.bench_palette = app._resolve_palette()
    return app


@pytest.fixture(autouse=True)
def _own_store(tmp_path, monkeypatch):
    """A cron store and a session store nobody else is writing to.

    Both resolve their paths from ``CURIE_HOME`` on every call, so one
    redirect isolates the tasks *and* the conversations their runs produce —
    which matters here more than usual, because these tests deliberately
    exercise two consoles reading one store.
    """
    monkeypatch.setenv("CURIE_HOME", str(tmp_path))
    yield tmp_path


def _make_task(name: str = "Build watch", **kwargs):
    spec = kwargs.pop("spec", None) or schedule.ScheduleSpec(
        mode=schedule.MODE_EVERY, every_value=15, every_unit="m"
    )
    task, problem = schedule.create_task(
        name=name,
        prompt=kwargs.pop("prompt", "Check the build queue."),
        spec=spec,
        **kwargs,
    )
    assert problem == "", problem
    return task


def _seed_run(task, stamp: str = "20260906_011045", messages=None):
    """Write one run of a task into the session store, as the scheduler does.

    ``cron_<job id>_<stamp>`` is the shape ``cron/scheduler.run_job`` uses, and
    it is what makes a run findable from its task — so the tests build one the
    same way rather than through a helper that could drift from it.
    """
    from curie_state import SessionDB

    db = SessionDB()
    session_id = f"cron_{task.id}_{stamp}"
    db.create_session(session_id, source="cron")
    db.append_messages_batch(
        session_id,
        messages
        or [
            {"role": "user", "content": "Check the build queue."},
            {"role": "assistant", "content": "Two jobs are red."},
        ],
    )
    return session_id


# ── The schedule itself ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec,expected",
    [
        (
            schedule.ScheduleSpec(
                mode=schedule.MODE_EVERY, every_value=45, every_unit="s"
            ),
            "every 45s",
        ),
        (
            schedule.ScheduleSpec(
                mode=schedule.MODE_EVERY, every_value=2, every_unit="h"
            ),
            "every 2h",
        ),
        (
            schedule.ScheduleSpec(
                mode=schedule.MODE_AT, day="monday", time="19:32"
            ),
            "every monday at 19:32",
        ),
        (
            schedule.ScheduleSpec(
                mode=schedule.MODE_AT, day="weekdays", time="07:00"
            ),
            "every weekdays at 07:00",
        ),
        (
            schedule.ScheduleSpec(mode=schedule.MODE_CRON, cron_expr="*/15 * * * *"),
            "*/15 * * * *",
        ),
    ],
)
def test_the_controls_produce_the_schedule_the_store_parses(spec, expected):
    from cron.jobs import parse_schedule

    assert spec.to_schedule() == expected
    # Parsed as well as produced: a string this module invents that the store
    # rejects is a form that accepts a schedule and then loses it.
    assert parse_schedule(expected)


def test_a_countdown_can_be_shorter_than_a_minute():
    """"every 45 seconds" is a schedule a person asks for, so it has to work.

    Stored as a fraction of a minute — the unit every reader of a schedule
    already understands — so the due check, the cadence estimate and the grace
    window need to know nothing about seconds.
    """
    from cron.jobs import parse_schedule

    parsed = parse_schedule("every 45s")
    assert parsed["kind"] == "interval"
    assert parsed["minutes"] == pytest.approx(0.75)
    assert parsed["display"] == "every 45s"


def test_a_one_time_countdown_is_a_different_word():
    """REPEATING off turns "every 2h" into "in 2h" — one fire, not a cadence."""
    spec = schedule.ScheduleSpec(
        mode=schedule.MODE_EVERY, every_value=2, every_unit="h", repeating=False
    )
    assert spec.to_schedule() == "in 2h"
    assert spec.repeat_argument() == 1


def test_a_date_is_a_one_shot_however_the_switch_is_set():
    spec = schedule.ScheduleSpec(
        mode=schedule.MODE_AT, day="once", date="2027-01-02", time="07:15"
    )
    assert spec.to_schedule() == "2027-01-02T07:15:00"


@pytest.mark.parametrize(
    "spec,fragment",
    [
        (schedule.ScheduleSpec(mode=schedule.MODE_AT, time="99:99"), "no 99:99"),
        (schedule.ScheduleSpec(mode=schedule.MODE_AT, time="nonsense"), "HH:MM"),
        (
            schedule.ScheduleSpec(mode=schedule.MODE_AT, day="once", date="soon"),
            "YYYY-MM-DD",
        ),
        (schedule.ScheduleSpec(mode=schedule.MODE_CRON, cron_expr="  "), "cron"),
        (
            schedule.ScheduleSpec(mode=schedule.MODE_EVERY, every_value=0),
            "greater than zero",
        ),
    ],
)
def test_a_bad_schedule_says_which_control_is_wrong(spec, fragment):
    """The message goes on the notice line, so it names the field, not the parser."""
    assert fragment in spec.describe()


def test_the_form_says_when_the_task_would_actually_fire():
    spec = schedule.ScheduleSpec(
        mode=schedule.MODE_EVERY, every_value=45, every_unit="s"
    )
    described = spec.describe()
    assert "every 45s" in described
    assert "until stopped" in described
    assert "first run in" in described


# ── The store ────────────────────────────────────────────────────────────


def test_a_task_made_here_is_a_real_scheduled_job():
    """Written to the store the gateway ticks, not to a list this console keeps."""
    from cron.jobs import list_jobs

    task = _make_task("Nightly digest")
    stored = [job for job in list_jobs(include_disabled=True) if job["id"] == task.id]
    assert stored, "the task is not in the cron store"
    assert stored[0]["prompt"] == "Check the build queue."
    assert stored[0]["origin"] == {"surface": "bench-console"}


def test_a_task_round_trips_through_the_form_controls():
    """Editing a task and changing nothing must leave its schedule alone."""
    for spec in (
        schedule.ScheduleSpec(mode=schedule.MODE_EVERY, every_value=45, every_unit="s"),
        schedule.ScheduleSpec(mode=schedule.MODE_EVERY, every_value=3, every_unit="d"),
        schedule.ScheduleSpec(mode=schedule.MODE_AT, day="weekdays", time="07:30"),
        schedule.ScheduleSpec(mode=schedule.MODE_AT, day="monday", time="19:32"),
        schedule.ScheduleSpec(mode=schedule.MODE_CRON, cron_expr="*/15 * * * *"),
    ):
        task = _make_task(f"round trip {spec.mode} {spec.to_schedule()}", spec=spec)
        recovered = schedule.spec_from_job(task.raw)
        assert recovered.to_schedule() == spec.to_schedule(), (
            f"{spec.to_schedule()} came back as {recovered.to_schedule()}"
        )


def test_an_empty_prompt_is_refused_with_a_sentence():
    _task, problem = schedule.create_task(
        name="", prompt="   ", spec=schedule.ScheduleSpec()
    )
    assert _task is None
    assert "prompt" in problem


def test_pausing_and_arming_move_the_state_the_table_prints():
    task = _make_task()
    assert schedule.set_paused(task.id, True) == ""
    tasks, _ = schedule.list_tasks()
    assert schedule.task_state_key(tasks[0]) == "paused"
    assert schedule.set_paused(task.id, False) == ""
    tasks, _ = schedule.list_tasks()
    assert schedule.task_state_key(tasks[0]) == "scheduled"


def test_run_now_brings_the_next_occurrence_forward():
    """It arms the task for the ticker rather than running it here.

    Running it in this process would give it the console's working directory,
    the console's environment and no fire claim — so two copies of one task
    could run at once, which is the thing the claim exists to prevent.
    """
    from cron.jobs import get_job

    task = _make_task()
    before = get_job(task.id)["next_run_at"]
    assert schedule.run_now(task.id) == ""
    after = get_job(task.id)
    assert after["next_run_at"] != before
    assert after["manual_run_at"]


def test_stop_leaves_a_request_the_running_process_will_find():
    """STOP crosses a process boundary, because the run is in the gateway."""
    from cron.jobs import clear_job_stop, job_stop_requested

    task = _make_task()
    assert schedule.stop_run(task.id) == ""
    assert job_stop_requested(task.id) is True
    clear_job_stop(task.id)
    assert job_stop_requested(task.id) is False


def test_removing_a_task_removes_it():
    task = _make_task()
    assert schedule.remove_task(task.id) == ""
    tasks, _ = schedule.list_tasks()
    assert tasks == []


def test_a_run_is_a_conversation_the_window_can_open():
    task = _make_task()
    session_id = _seed_run(task)
    assert schedule.latest_session_id(task.id) == session_id
    messages, problem = schedule.read_conversation(session_id)
    assert problem == ""
    assert messages[0][0] == "user"
    assert "Two jobs are red." in messages[-1][1]


# ── The pane ─────────────────────────────────────────────────────────────


def test_the_pane_is_on_the_rail_and_answers_its_own_key():
    assert "schedule" in {key for key, *_rest in SWITCHES}
    assert PANE_KEYS["schedule"] == "ctrl+t"
    assert pane_key_label("schedule") == "^T"
    bindings = {binding.action: binding.key for binding in BenchConsole.BINDINGS}
    assert bindings["keyline('schedule')"] == "ctrl+t"


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_table_lists_what_is_scheduled(mode):
    _make_task("Nightly digest")
    _make_task(
        "Queue poll",
        spec=schedule.ScheduleSpec(
            mode=schedule.MODE_EVERY, every_value=45, every_unit="s"
        ),
    )

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            pane = app._schedule()
            names = {task.name for task in pane._tasks}
            assert names == {"Nightly digest", "Queue poll"}
            summary = app.query_one("#schedule-summary").render().plain
            assert "2 tasks" in summary
            assert "2 armed" in summary

    _run(scenario())


def test_the_name_column_takes_whatever_the_others_leave():
    """A task table whose *name* is the truncated column is unreadable.

    Measured against the table rather than thresholded against the window,
    because the pane's width is the window's minus a rail and an instrument
    stack that both come and go.
    """
    _make_task("A task with a deliberately long descriptive name")

    async def scenario():
        for width, expect_when in ((190, True), (120, False)):
            app = _console()
            async with app.run_test(size=(width, 40)) as pilot:
                await _settle(pilot)
                app.show_pane("schedule")
                await _settle(pilot)
                pane = app._schedule()
                columns = pane._columns()
                headings = [heading for heading, *_rest in columns]
                assert ("WHEN" in headings) is expect_when, (width, headings)
                name = next(width for h, width, _k in columns if h == "TASK")
                assert name >= NAME_MIN, (width, name)

    _run(scenario())


def test_the_form_writes_a_task_and_the_table_shows_it():
    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-new")
            await _settle(pilot)
            pane = app._schedule()
            assert pane.form_open

            pane._set_value("#schedule-name", "Morning digest")
            pane._maybe("#schedule-prompt").text = "Summarise overnight activity."
            pane.set_mode(schedule.MODE_AT)
            pane._set_cycler("#schedule-day", "weekdays")
            pane._set_value("#schedule-time", "07:30")
            await _settle(pilot)

            app.run_keyline_action("schedule-save")
            await _settle(pilot)

            assert not pane.form_open, "the form stayed open after a good save"
            assert [t.name for t in pane._tasks] == ["Morning digest"]
            assert pane._tasks[0].schedule == "every weekdays at 07:30"

    _run(scenario())


def test_a_bad_schedule_keeps_the_form_open_and_says_why():
    """A form that closes on a rejected save has thrown the work away."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-new")
            await _settle(pilot)
            pane = app._schedule()
            pane._maybe("#schedule-prompt").text = "Do the thing."
            pane.set_mode(schedule.MODE_AT)
            pane._set_value("#schedule-time", "25:00")
            app.run_keyline_action("schedule-save")
            await _settle(pilot)

            assert pane.form_open
            said = app.query_one("#schedule-form-problem").render().plain
            assert "25:00" in said
            assert pane._tasks == []

    _run(scenario())


def test_editing_a_task_keeps_its_id_and_its_history():
    """In place, so a corrected prompt does not cost the task its run log."""
    task = _make_task("Build watch")
    _seed_run(task)

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-edit")
            await _settle(pilot)
            pane = app._schedule()
            assert pane.form_open
            assert pane._value("#schedule-name") == "Build watch"
            pane._maybe("#schedule-prompt").text = "Check the queue, twice."
            app.run_keyline_action("schedule-save")
            await _settle(pilot)

            assert [t.id for t in pane._tasks] == [task.id]
            assert pane._tasks[0].prompt == "Check the queue, twice."
            assert schedule.latest_session_id(task.id), "the run history was lost"

    _run(scenario())


def test_delete_takes_two_presses():
    """One press cannot be taken back, so one press does not do it."""
    _make_task()

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-delete")
            await _settle(pilot)
            pane = app._schedule()
            assert len(pane._tasks) == 1, "one press deleted it"
            app.run_keyline_action("schedule-delete")
            await _settle(pilot)
            assert pane._tasks == []

    _run(scenario())


# ── The running indicator ────────────────────────────────────────────────


def test_the_running_count_is_right_from_another_pane(monkeypatch):
    """The indicator's whole job: a task starting while you are elsewhere.

    Answered from the execution ledger rather than from the task table, which
    is only re-read while its own pane is on screen — so counting the table's
    rows would answer for whenever the reader last looked at it.
    """
    task = _make_task()

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            assert app.active_pane == "bench"
            pane = app._schedule()
            assert pane.running_count == 0

            # It starts, in the gateway, while the reader is on the bench.
            monkeypatch.setattr(schedule, "_running_ids", lambda: {task.id})
            app._tick_schedule()
            await _settle(pilot)
            assert app.query_one("#switch-schedule", Switch).badge == "●1"

    _run(scenario())


def test_a_running_task_shows_on_the_rail_and_the_key_bar(monkeypatch):
    """The one thing this pane has to say from outside itself.

    A task runs in the gateway while the reader is on another pane. Without a
    count on the switch the only way to find out is to go and look, which is
    the state of affairs the indicator exists to end.
    """
    task = _make_task()
    monkeypatch.setattr(schedule, "_running_ids", lambda: {task.id})

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            pane = app._schedule()
            assert pane.running_count == 1
            assert schedule.task_state_key(pane._tasks[0]) == "running"
            # And it is answered without the table, because the pane the
            # reader is on may not be this one.
            app.show_pane("bench")
            await _settle(pilot)
            assert pane.running_count == 1

            app._refresh_task_badge(pane)
            await _settle(pilot)
            switch = app.query_one("#switch-schedule", Switch)
            assert switch.badge == "●1"
            assert "●1" in switch.render().plain
            caps = [c for c in app.query(KeyCap) if c.key_action == "schedule"]
            assert caps and caps[0].badge == "●1"

            # Painted, not merely set. The cap is ``width: auto``, so a plain
            # repaint draws the badge into the old width and then clips it
            # off the end — present in the widget and invisible on the bar.
            strips = app.screen._compositor.render_strips()
            bar = app.query_one("#keyline").region
            row = "".join(seg.text for seg in strips[bar.y])
            assert "●1" in row, row

    _run(scenario())


def test_the_badge_goes_away_when_nothing_is_running():
    """A permanent zero is a number the eye learns to stop reading."""
    _make_task()

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            switch = app.query_one("#switch-schedule", Switch)
            assert switch.badge == ""

    _run(scenario())


# ── The task window ──────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [dos.MODE_BENCH, dos.MODE_DOS])
def test_the_window_opens_on_the_newest_run(mode):
    task = _make_task()
    _seed_run(task, "20260901_090000")
    newest = _seed_run(task, "20260906_011045")

    async def scenario():
        app = _console(mode)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-preview")
            await _settle(pilot)
            window = app._task_window()
            assert window.is_open
            assert window.session_id == newest
            shown = _window_text(app)
            assert "Two jobs are red." in shown

    _run(scenario())


def _window_text(app) -> str:
    from textual.widgets import Static

    parts = []
    for node in app.query_one("#preview-log").query(Static):
        parts.append(node.render().plain if hasattr(node.render(), "plain") else "")
    return "\n".join(parts)


def test_the_window_steps_back_through_older_runs():
    task = _make_task()
    older = _seed_run(task, "20260901_090000", messages=[
        {"role": "user", "content": "Check the build queue."},
        {"role": "assistant", "content": "Everything green."},
    ])
    newest = _seed_run(task, "20260906_011045")

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-preview")
            await _settle(pilot)
            window = app._task_window()
            assert window.session_id == newest

            app.run_keyline_action("schedule-preview-older")
            await _settle(pilot)
            assert window.session_id == older
            assert "Everything green." in _window_text(app)

            app.run_keyline_action("schedule-preview-newer")
            await _settle(pilot)
            assert window.session_id == newest

            # And it says so rather than wrapping round to the other end.
            app.run_keyline_action("schedule-preview-newer")
            await _settle(pilot)
            assert "newest run" in window._status

    _run(scenario())


def test_the_window_is_not_the_main_chat():
    """Its own composer, its own log, and no path from one to the other."""
    task = _make_task()
    _seed_run(task)

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-preview")
            await _settle(pilot)
            assert app.query_one("#preview-composer") is not app.query_one("#composer")
            # The bench transcript is untouched by anything the window shows.
            from tests.curie_cli.bench_ui.test_console import _transcript_text

            assert "Two jobs are red." not in _transcript_text(app)

    _run(scenario())


def test_the_window_refuses_to_send_while_the_scheduler_owns_the_run(monkeypatch):
    """A run holds a durable turn lease, so a second turn would wait 30 minutes.

    Saying so and offering STOP is the honest answer; a composer that
    swallows the message and goes quiet is not.
    """
    task = _make_task()
    _seed_run(task)
    monkeypatch.setattr(schedule, "_running_ids", lambda: {task.id})

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-preview")
            await _settle(pilot)
            app._send_into_task("what is taking so long?")
            await _settle(pilot)
            window = app._task_window()
            assert "STOP" in window._status
            assert not window.busy
            assert app._task_bridge is None, "a turn was started anyway"

    _run(scenario())


def test_stop_typed_into_the_window_stops_the_run():
    """In a window onto one run, "stop" can only mean one thing."""
    from cron.jobs import job_stop_requested

    task = _make_task()
    _seed_run(task)

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-preview")
            await _settle(pilot)
            # A run has to be in flight for a stop to reach anything.
            import curie_cli.bench_ui.schedule as store

            app._schedule()._tasks = [
                type(task)(**{**task.__dict__, "running": True})
            ]
            composer = app.query_one("#preview-composer")
            composer.text = "/stop"
            composer.post_message(type(composer).PreviewSubmitted())
            await _settle(pilot)
            assert job_stop_requested(task.id) is True
            assert store is schedule

    _run(scenario())


def test_the_window_gives_its_rows_back_when_it_closes():
    """Docked, not floating: opening it shrinks the list rather than covering it."""
    task = _make_task()
    _seed_run(task)

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            table = app.query_one("#schedule-table")
            tall = table.region.height

            app.run_keyline_action("schedule-preview")
            await _settle(pilot)
            short = table.region.height
            assert short < tall, "the window covered the list instead of sharing it"
            # The controls survive whatever the window does to the pane.
            assert app.query_one("#schedule-actions").region.height == 1

            app.run_keyline_action("schedule-preview-size")
            await _settle(pilot)
            assert app.query_one("#schedule-actions").region.height == 1, (
                "the enlarged window took the buttons that close it off the pane"
            )

            app.run_keyline_action("schedule-preview-close")
            await _settle(pilot)
            assert table.region.height == tall

    _run(scenario())


# ── Two consoles at once ─────────────────────────────────────────────────


def test_a_task_made_in_one_console_reaches_the_other():
    """No handshake and no daemon: both windows are reading one file."""

    async def scenario():
        first = _console()
        async with first.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            first.show_pane("schedule")
            await _settle(pilot)
            assert first._schedule()._tasks == []

            # The second console — or `curie cron create`, or the dashboard.
            _make_task("Made somewhere else")

            first._poll_shared_stores()
            await _settle(pilot)
            assert [t.name for t in first._schedule()._tasks] == [
                "Made somewhere else"
            ]

    _run(scenario())


def test_this_console_s_own_write_is_not_read_back_as_somebody_else_s():
    """Otherwise every button press is announced to the person who pressed it."""

    async def scenario():
        app = _console()
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app.run_keyline_action("schedule-new")
            await _settle(pilot)
            pane = app._schedule()
            pane._maybe("#schedule-prompt").text = "Do the thing."
            app.run_keyline_action("schedule-save")
            await _settle(pilot)
            assert "schedule" not in app._sync.changes()

    _run(scenario())


# ── Both skins ───────────────────────────────────────────────────────────


def test_the_pane_repaints_when_the_skin_changes():
    """The table's cells hold styled text, so a re-skin has to rebuild them."""
    _make_task()

    async def scenario():
        app = _console(dos.MODE_BENCH)
        async with app.run_test(size=(132, 40)) as pilot:
            await _settle(pilot)
            app.show_pane("schedule")
            await _settle(pilot)
            app._toggle_display_mode()
            await _settle(pilot)
            assert app.bench_palette.dos
            # The menu line for this pane carries its key, and the key is a
            # control key — so the DOS rail has to print it as one.
            switch = app.query_one("#switch-schedule", Switch)
            assert switch.render().plain.strip().startswith("^T)")

    _run(scenario())


def test_every_pane_key_is_the_key_that_throws_it():
    """The rail prints a key; a key that does nothing is worse than none."""
    bindings = {binding.action: binding.key for binding in BenchConsole.BINDINGS}
    for pane, key in PANE_KEYS.items():
        assert bindings.get(f"keyline('{pane}')") == key, pane
    assert set(PANE_KEYS) == {key for key, *_rest in SWITCHES}
