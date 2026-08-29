"""Tests for how long a stack is allowed to run before being abandoned.

We use a dynamic timeout budget instead of a flat one. This is because
large stacks take longer, and targets waiting in the queue shouldn't
be penalized for their wait time.
"""

import os
import threading
import time

import pytest

from astrometricslib.drivers import siril_interface
from astrometricslib.pipelines import dispatch
from astrometricslib.pipelines.dispatch import (
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


def test_an_uncontended_slot_records_a_negligible_wait():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A free slot must not inflate anyone's stacking budget.

    The wait is measured unconditionally now, so an uncontended
    acquisition records the cost of taking the lock itself rather than
    exactly zero. What matters is that it is far below the budget it
    would be added to.
    """
    siril_interface.reset_siril_lock_wait_seconds()

    with siril_interface.siril_process_lock(max_concurrent_runs=1):
        pass

    assert siril_interface.get_siril_lock_wait_seconds() < 0.5


def test_time_blocked_on_a_slot_is_recorded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The wait has to be measured before it can be given back.

    Both sides run as subprocesses sharing this working directory, so
    they resolve the same configuration and therefore the same slot
    files. Slots live under the configured library path, so two
    processes pointed at different libraries deliberately do not
    contend -- which is why the waiter cannot simply be this test.
    """
    import subprocess  # ruff: ignore[suspicious-subprocess-import] -- real processes are the point
    import sys

    repository_root = os.getcwd()
    holder_source = (
        "import time, sys;"
        f"sys.path.insert(0, {repository_root!r});"
        "from astrometricslib.drivers.siril_interface import siril_process_lock;"
        "ctx = siril_process_lock(max_concurrent_runs=1);"
        "ctx.__enter__();"
        "print('holding', flush=True);"
        "time.sleep(3);"
        "ctx.__exit__(None, None, None)"
    )
    waiter_source = (
        "import sys;"
        f"sys.path.insert(0, {repository_root!r});"
        "from astrometricslib.drivers import siril_interface;"
        "siril_interface.reset_siril_lock_wait_seconds();"
        "ctx = siril_interface.siril_process_lock(max_concurrent_runs=1);"
        "ctx.__enter__();"
        "ctx.__exit__(None, None, None);"
        "print(siril_interface.get_siril_lock_wait_seconds(), flush=True)"
    )

    holder = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", holder_source], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "holding"
        waiter = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
            [sys.executable, "-c", waiter_source], capture_output=True, text=True, timeout=60
        )
        recorded_wait = float(waiter.stdout.strip().splitlines()[-1])

        assert recorded_wait > 0.5
    finally:
        holder.wait(timeout=15)


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

    monkeypatch.setattr(dispatch, "stack_and_solve", _slow_stack)
    # The stack outlives its nominal budget, but every second of the
    # overrun is attributable to queueing, so it must still be allowed
    # to finish.
    monkeypatch.setattr(siril_interface, "get_siril_lock_wait_seconds", lambda: reported_lock_wait)
    monkeypatch.setattr(siril_interface, "reset_siril_lock_wait_seconds", lambda: None)

    class _Target:
        id = "NGC 1499"

    result = dispatch._stack_frames_with_timeout(_Target(), [], timeout_seconds=0)

    assert result == "/stacked/output.fits"


def test_a_genuinely_hung_stack_is_still_abandoned(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Excluding queue time must not disarm hang detection."""
    release_hung_stack = threading.Event()

    def _hung_stack(target, frames_to_stack=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        release_hung_stack.wait(30)
        return "/never/reached.fits"

    monkeypatch.setattr(dispatch, "stack_and_solve", _hung_stack)
    monkeypatch.setattr(siril_interface, "get_siril_lock_wait_seconds", lambda: 0.0)
    monkeypatch.setattr(siril_interface, "reset_siril_lock_wait_seconds", lambda: None)

    class _Target:
        id = "HungTarget"

    try:
        result = dispatch._stack_frames_with_timeout(_Target(), [], timeout_seconds=1)

        assert result is None
    finally:
        release_hung_stack.set()
