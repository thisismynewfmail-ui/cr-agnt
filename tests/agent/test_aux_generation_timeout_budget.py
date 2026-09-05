"""The auxiliary generation budget is 5 hours, and its backstop stays sane.

A 30-second default was sized for a fast completion model answering a
one-line prompt.  A reasoning model on the same task blows through it while
still visibly working, so the call was cancelled and the caller silently
dropped to a degraded deterministic path.

The safety net for a *dead* stream is the progress watchdog, not this
number: the no-progress window fails fast and re-arms on every substantive
event, so a long budget costs nothing when the model is quick.
"""

import pytest

from agent.auxiliary_client import (
    _AUX_STREAM_CEILING_FLOOR_SECONDS,
    _AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS,
    _DEFAULT_AUX_TIMEOUT,
    _aux_stream_total_ceiling,
    _effective_aux_timeout,
)

FIVE_HOURS_SECONDS = 5 * 60 * 60


def test_default_auxiliary_generation_budget_is_five_hours():
    assert _DEFAULT_AUX_TIMEOUT == pytest.approx(FIVE_HOURS_SECONDS)


def test_unconfigured_task_inherits_the_five_hour_budget():
    """A task with no ``auxiliary.<task>.timeout`` gets the new default."""
    effective = _effective_aux_timeout("no_such_task_in_config", None)
    assert effective == pytest.approx(FIVE_HOURS_SECONDS)


def test_explicit_per_call_timeout_still_wins():
    """An explicit ``timeout=`` is a deliberate deadline — never widened."""
    assert _effective_aux_timeout("no_such_task_in_config", 12.0) == 12.0
    # Including for compression, whose config-derived floor is skipped when
    # the caller passes an explicit value.
    assert _effective_aux_timeout("compression", 5.0) == 5.0


@pytest.mark.parametrize("timeout", [None, 0, 30.0, 120.0, 300.0])
def test_short_budgets_keep_the_generous_multiplier(timeout):
    """Short timeouts keep their 4x/600s headroom, unchanged."""
    ceiling = _aux_stream_total_ceiling(timeout)
    assert ceiling >= _AUX_STREAM_CEILING_FLOOR_SECONDS
    assert ceiling <= 4.0 * max(float(timeout or 0.0), 150.0)


def test_long_budget_backstop_does_not_compound():
    """The 4x multiplier must not turn a 5-hour budget into a 20-hour one.

    The backstop exists to terminate a stream that trickles one token per
    idle window forever.  It has to sit *above* the budget to be a backstop
    at all, but multiplying a long budget makes it meaningless.
    """
    ceiling = _aux_stream_total_ceiling(_DEFAULT_AUX_TIMEOUT)
    assert ceiling > _DEFAULT_AUX_TIMEOUT, "backstop must exceed the budget"
    assert ceiling == pytest.approx(
        _DEFAULT_AUX_TIMEOUT + _AUX_STREAM_CEILING_FLOOR_SECONDS
    )
    assert ceiling < 2 * _DEFAULT_AUX_TIMEOUT


def test_dead_stream_still_fails_fast():
    """The long budget must not become the dead-stream detection latency."""
    assert _AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS <= 300.0, (
        "a dead auxiliary stream must be caught in minutes, not hours — "
        "the 5-hour budget is a ceiling for a stream that is still working"
    )
