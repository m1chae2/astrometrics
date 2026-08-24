"""Tests for the machine-wide Siril serialization lock.

Siril takes the whole CPU when it runs, so the batch runner's concurrent
target workers each launching their own Siril oversubscribed the machine
badly enough to push stacks past their timeout on the 2026-08-23 run
("[Sun] Stacking timed out after 600 seconds").
"""

import multiprocessing
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- a real second process is the point
import sys
import time

from astrometricslib.drivers.siril_interface import (
    SIRIL_PROCESS_LOCK_PATH,
    siril_process_lock,
)


def test_lock_is_reentrant_across_sequential_uses():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Releasing the lock lets the next acquisition through immediately."""
    for _ in range(3):
        with siril_process_lock():
            pass

    start = time.monotonic()
    with siril_process_lock():
        pass
    assert time.monotonic() - start < 5.0


def test_lock_file_path_is_shared_and_absolute():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Every process must resolve the same rendezvous file."""
    assert os.path.isabs(SIRIL_PROCESS_LOCK_PATH)
    assert SIRIL_PROCESS_LOCK_PATH.endswith("astrometricslib-siril.lock")


def test_lock_serializes_two_processes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A second process must wait rather than run concurrently.

    Uses real subprocesses, not threads: the lock exists to serialize
    separate worker *processes*, which is exactly what `fcntl.flock`
    scopes to, so a thread-based test would not exercise it.
    """
    repository_root = os.getcwd()
    holder_source = (
        "import time, sys;"
        f"sys.path.insert(0, {repository_root!r});"
        "from astrometricslib.drivers.siril_interface import siril_process_lock;"
        "print('holding', flush=True);"
        "ctx = siril_process_lock();"
        "ctx.__enter__();"
        "time.sleep(3);"
        "ctx.__exit__(None, None, None);"
        "print('released', flush=True)"
    )
    holder = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv, no shell
        [sys.executable, "-c", holder_source], stdout=subprocess.PIPE, text=True
    )
    # Wait for the holder to actually take the lock before racing it.
    assert holder.stdout.readline().strip() == "holding"
    time.sleep(0.5)

    start = time.monotonic()
    with siril_process_lock():
        waited_seconds = time.monotonic() - start

    holder.wait(timeout=30)

    # The holder sleeps 3s while holding; this process must have blocked
    # for a meaningful portion of that rather than sailing through.
    assert waited_seconds > 1.0, (
        f"Second process acquired the lock after only {waited_seconds:.2f}s -- "
        "it did not wait for the holder, so Siril runs are not serialized."
    )


def _acquire_and_record(order_queue: multiprocessing.Queue, worker_index: int) -> None:
    """Take the lock, record entry/exit, and hold it briefly."""
    with siril_process_lock():
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
