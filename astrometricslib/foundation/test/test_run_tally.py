"""Tests for `RunTally`: a run's own total, handed to deep code by binding."""

import threading
import time
from contextvars import ContextVar

import pytest

from astrometricslib.foundation.run_tally import RunTally, add_to_bound_tally, bind_tally

_VARIABLE: ContextVar[RunTally | None] = ContextVar("test_tally", default=None)


def test_a_tally_adds_up_and_starts_where_it_is_told() -> None:
    """The total is the start plus everything added."""
    tally = RunTally(1.5)
    tally.add(2.0)
    tally.add(0.5)
    assert tally.total == pytest.approx(4.0)


def test_adding_with_nothing_bound_changes_nothing() -> None:
    """Code run outside any tallied run is not affected."""
    add_to_bound_tally(_VARIABLE, 5.0)
    assert _VARIABLE.get() is None


def test_the_innermost_binding_gets_the_amount_and_the_outer_one_returns() -> None:
    """A nested binding takes over; leaving it brings the outer one back."""
    outer, inner = RunTally(), RunTally()
    with bind_tally(_VARIABLE, outer):
        add_to_bound_tally(_VARIABLE, 1.0)
        with bind_tally(_VARIABLE, inner):
            add_to_bound_tally(_VARIABLE, 10.0)
        add_to_bound_tally(_VARIABLE, 1.0)
    assert outer.total == pytest.approx(2.0)
    assert inner.total == pytest.approx(10.0)
    assert _VARIABLE.get() is None


def test_a_new_thread_does_not_inherit_the_binding_and_binds_its_own() -> None:
    """A thread the run starts counts only when it binds the tally itself."""
    tally = RunTally()

    def unbound_worker() -> None:
        """Add without binding: nothing is counted."""
        add_to_bound_tally(_VARIABLE, 100.0)

    def bound_worker() -> None:
        """Bind the run's tally, then add."""
        with bind_tally(_VARIABLE, tally):
            add_to_bound_tally(_VARIABLE, 3.0)

    with bind_tally(_VARIABLE, tally):
        for worker in (unbound_worker, bound_worker):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
    assert tally.total == pytest.approx(3.0)


def test_an_open_wait_counts_until_it_stops_and_stopping_twice_is_harmless() -> None:
    """An open wait shows in the total; only added seconds stay after."""
    tally = RunTally(total=1.0)

    stop = tally.start_open_wait()
    time.sleep(0.05)
    assert tally.total > 1.0

    stop()
    stop()
    assert tally.total == pytest.approx(1.0)
