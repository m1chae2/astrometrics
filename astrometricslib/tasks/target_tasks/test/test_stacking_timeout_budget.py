"""Tests for how long a stack is allowed to run before being abandoned.

A flat 600s budget discarded finished work on the 2026-08-24 DSLR pass:
four targets were declared timed out and three then stacked
*successfully* minutes later. NGC 7000 was abandoned at 06:27:21 and
finished at 06:35:11, leaving a complete 288MB stack on disk with
nothing in the catalog pointing at it.

Two separate causes, one per half of this module: colour stacks simply
need longer than 600s, and time queued behind another target's Siril run
was being charged to the waiting target's own budget.
"""

import threading
import time

import pytest

from astrometricslib.drivers import siril_interface
from astrometricslib.tasks.target_tasks import pipeline_tasks
from astrometricslib.tasks.target_tasks.pipeline_tasks import (
    STACKING_TIMEOUT_SECONDS,
    compute_stacking_timeout_seconds,
)


def test_a_tiny_stack_still_gets_the_historical_budget():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Scaling must never hand a small stack less time than it used to."""
    assert compute_stacking_timeout_seconds(2) == STACKING_TIMEOUT_SECONDS


def test_budget_grows_with_frame_count():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Cost is dominated by per-frame work, so the budget must track it."""
    assert compute_stacking_timeout_seconds(535) > compute_stacking_timeout_seconds(84)


def test_the_largest_real_target_gets_more_than_its_measured_cost():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """NGC 7023's 535 frames must not be killed mid-stack.

    NGC 7000 needed roughly 800s of Siril for 84 colour frames. Scaling
    that rate to 535 frames gives about 5,100s, and the budget has to
    clear that with room to spare or the biggest targets can never
    finish.
    """
    measured_seconds_per_frame = 800 / 84

    assert compute_stacking_timeout_seconds(535) > 535 * measured_seconds_per_frame


def test_a_negative_frame_count_is_not_a_negative_budget():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A nonsense count must degrade to the floor, not to zero."""
    assert compute_stacking_timeout_seconds(-5) == STACKING_TIMEOUT_SECONDS


def test_lock_wait_starts_at_zero_after_reset():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Each stacking attempt measures only its own queueing."""
    siril_interface.reset_siril_lock_wait_seconds()

    assert siril_interface.get_siril_lock_wait_seconds() == pytest.approx(0.0)


def test_an_uncontended_lock_records_no_wait(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A free lock must not inflate anyone's budget."""
    monkeypatch.setattr(
        siril_interface, "SIRIL_PROCESS_LOCK_PATH", str(tmp_path / "siril.lock"), raising=False
    )
    siril_interface.reset_siril_lock_wait_seconds()

    with siril_interface.siril_process_lock():
        pass

    assert siril_interface.get_siril_lock_wait_seconds() == pytest.approx(0.0)


def test_time_blocked_on_the_lock_is_recorded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The wait has to be measured before it can be given back.

    Uses a real second process, because `fcntl.flock` scopes to
    processes and a thread in this one would not block at all.
    """
    import subprocess  # ruff: ignore[suspicious-subprocess-import] -- a real second process is the point
    import sys

    holder_source = (
        "import time, sys;"
        f"sys.path.insert(0, {__import__('os').getcwd()!r});"
        "from astrometricslib.drivers.siril_interface import siril_process_lock;"
        "ctx = siril_process_lock();"
        "ctx.__enter__();"
        "print('holding', flush=True);"
        "time.sleep(2);"
        "ctx.__exit__(None, None, None)"
    )
    holder = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv, no shell
        [sys.executable, "-c", holder_source], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "holding"
        siril_interface.reset_siril_lock_wait_seconds()

        with siril_interface.siril_process_lock():
            pass

        assert siril_interface.get_siril_lock_wait_seconds() > 0.5
    finally:
        holder.wait(timeout=10)


def test_queue_time_does_not_consume_the_stacking_budget(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A target queued behind another Siril run keeps its full budget.

    The regression this pins: NGC 1499 was given 600s at 06:27:21 but
    did not reach Siril until 06:31:51, so it was abandoned having done
    only 5.5 minutes of work.
    """
    stack_duration_seconds = 0.6
    reported_lock_wait = 5.0

    def _slow_stack(target, frames_to_stack=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        time.sleep(stack_duration_seconds)
        return "/stacked/output.fits"

    monkeypatch.setattr(pipeline_tasks, "stack_and_solve", _slow_stack)
    # The stack outlives its nominal budget, but every second of the
    # overrun is attributable to queueing, so it must still be allowed
    # to finish.
    monkeypatch.setattr(siril_interface, "get_siril_lock_wait_seconds", lambda: reported_lock_wait)
    monkeypatch.setattr(siril_interface, "reset_siril_lock_wait_seconds", lambda: None)

    class _Target:
        id = "NGC 1499"

    result = pipeline_tasks._stack_frames_with_timeout(_Target(), [], timeout_seconds=0)

    assert result == "/stacked/output.fits"


def test_a_genuinely_hung_stack_is_still_abandoned(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Excluding queue time must not disarm hang detection."""
    release_hung_stack = threading.Event()

    def _hung_stack(target, frames_to_stack=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        release_hung_stack.wait(30)
        return "/never/reached.fits"

    monkeypatch.setattr(pipeline_tasks, "stack_and_solve", _hung_stack)
    monkeypatch.setattr(siril_interface, "get_siril_lock_wait_seconds", lambda: 0.0)
    monkeypatch.setattr(siril_interface, "reset_siril_lock_wait_seconds", lambda: None)

    class _Target:
        id = "HungTarget"

    try:
        result = pipeline_tasks._stack_frames_with_timeout(_Target(), [], timeout_seconds=1)

        assert result is None
    finally:
        release_hung_stack.set()
