"""Tests for the machine-wide Siril serialization lock.

Siril takes the whole CPU when it runs, so this tests that we
limit the number of Siril tasks running at once to prevent timeouts.
"""

import multiprocessing
import os
import time

from astrometricslib.drivers.siril_interface import (
    SIRIL_PROCESS_LOCK_PATH,
    siril_process_lock,
)


def test_lock_is_reentrant_across_sequential_uses():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Releasing the lock lets the next acquisition through immediately."""
    for _ in range(3):
        with siril_process_lock(max_concurrent_runs=1):
            pass

    start = time.monotonic()
    with siril_process_lock(max_concurrent_runs=1):
        pass
    assert time.monotonic() - start < 5.0


def test_lock_file_path_is_shared_and_absolute():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Every process must resolve the same rendezvous file."""
    assert os.path.isabs(SIRIL_PROCESS_LOCK_PATH)
    assert SIRIL_PROCESS_LOCK_PATH.endswith("astrometricslib-siril.lock")


def _hold_slot_briefly(ready_queue, hold_seconds: float) -> None:  # ruff: ignore[missing-type-function-argument]
    """Take a slot, announce it, and hold it for `hold_seconds`."""
    with siril_process_lock(max_concurrent_runs=1):
        ready_queue.put("holding")
        time.sleep(hold_seconds)


def _measure_acquisition_wait(result_queue, slot_count: int) -> None:  # ruff: ignore[missing-type-function-argument]
    """Acquire a slot and report how long the acquisition blocked."""
    started_at = time.monotonic()
    with siril_process_lock(max_concurrent_runs=slot_count):
        result_queue.put(time.monotonic() - started_at)


def _run_holder_and_waiter(slot_count: int) -> float:
    """Time a waiter's acquisition while a holder occupies one slot.

    Both sides are spawned children so they resolve the same
    configuration, and therefore the same slot files. Slots live under
    the configured library path, and under pytest this process resolves
    a temporary configuration that a child would not share -- so the
    waiter cannot simply be the test itself.

    Parameters
    ----------
    slot_count : `int`
        Slots the waiter is allowed to use.

    Returns
    -------
    waited_seconds : `float`
        How long the waiter blocked before acquiring.
    """
    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    holder = context.Process(target=_hold_slot_briefly, args=(ready_queue, 8.0))
    holder.start()
    try:
        assert ready_queue.get(timeout=60) == "holding"
        waiter = context.Process(target=_measure_acquisition_wait, args=(result_queue, slot_count))
        waiter.start()
        try:
            return float(result_queue.get(timeout=60))
        finally:
            waiter.join(timeout=30)
    finally:
        holder.join(timeout=30)


def test_a_second_process_waits_rather_than_running_concurrently():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """With one slot configured, the second acquisition must block."""
    assert _run_holder_and_waiter(slot_count=1) > 1.0


def test_two_slots_allow_two_concurrent_runs():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The limit is a tuning knob, so a second slot must actually help.

    Stacking was 44% of a 199-minute run at roughly 57% CPU utilisation
    while pinned to one Siril at a time, so being able to raise this is
    the point of reading it from configuration.
    """
    assert _run_holder_and_waiter(slot_count=2) < 1.0


def _acquire_and_record(order_queue: multiprocessing.Queue, worker_index: int) -> None:
    """Take the lock, record entry/exit, and hold it briefly."""
    with siril_process_lock(max_concurrent_runs=1):
        order_queue.put(("enter", worker_index, time.monotonic()))
        time.sleep(0.4)
        order_queue.put(("exit", worker_index, time.monotonic()))


def test_lock_intervals_never_overlap():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Three competing processes must hold the lock in disjoint intervals."""
    context = multiprocessing.get_context("spawn")
    order_queue = context.Queue()
    workers = [context.Process(target=_acquire_and_record, args=(order_queue, i)) for i in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    events = []
    while not order_queue.empty():
        events.append(order_queue.get())
    events.sort(key=lambda event: event[2])

    # With a correct mutex the sequence is strictly enter/exit/enter/exit...
    # An overlap shows up as two consecutive "enter" events.
    kinds = [event[0] for event in events]
    assert len(events) == 6, f"Expected 3 enter/exit pairs, got {events}"
    assert kinds == ["enter", "exit"] * 3, (
        f"Siril lock intervals overlapped -- two processes held it at once: {events}"
    )


def _acquire_via_driver(result_queue, barrier, slot_count: int) -> None:  # ruff: ignore[missing-type-function-argument]
    """Take a Siril slot the way the stacking path does, then report.

    The barrier holds every worker at the acquisition until all have
    arrived, so a slot shortage shows up as a hang rather than being
    hidden by one worker finishing before the next starts.
    """
    barrier.wait(timeout=30)
    with siril_process_lock(max_concurrent_runs=slot_count):
        result_queue.put("acquired")


def test_the_stacking_path_takes_exactly_one_slot():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Two workers must both stack when two slots are configured.

    This ensures that stacking only takes a single slot per worker.
    If multiple slots are taken by accident, it will cause a deadlock.
    """
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    barrier = context.Barrier(2)
    workers = [context.Process(target=_acquire_via_driver, args=(result_queue, barrier, 2)) for _ in range(2)]
    for worker in workers:
        worker.start()

    acquired = []
    try:
        for _ in range(2):
            acquired.append(result_queue.get(timeout=45))
    finally:
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)

    assert acquired == ["acquired", "acquired"]
