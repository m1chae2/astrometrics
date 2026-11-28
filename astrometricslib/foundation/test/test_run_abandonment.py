"""Tests for stopping the processes of a run that was given up on.

The processes here are real short-lived children (a Python process that
sleeps, with a grandchild of its own), because what matters is that the whole
process group is stopped and that other runs' processes are left alone.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import] -- real child processes are the point
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from astrometricslib.foundation.run_abandonment import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    launch_registered,
    raise_if_abandoned,
    run_registered,
    stop_process_group,
)

# A child that starts a sleeping grandchild, prints the grandchild's id and
# then sleeps itself. Sixty seconds is far longer than any test waits.
CHILD_WITH_GRANDCHILD = (
    "import subprocess, sys, time;"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "print(g.pid, flush=True);"
    "time.sleep(60)"
)


def start_child() -> subprocess.Popen:
    """Start a sleeping child that leads its own process group.

    Returns
    -------
    process : `subprocess.Popen`
        The child; its standard output carries the grandchild's id.
    """
    return subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", CHILD_WITH_GRANDCHILD],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def process_exists(process_id: int) -> bool:
    """Say whether a process is still running (a zombie counts as gone).

    Returns
    -------
    exists : `bool`
        `True` if the process can still be signalled and is not a zombie.
    """
    try:
        return psutil.Process(process_id).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def wait_until_gone(process_id: int, seconds: float = 5.0) -> bool:
    """Wait for a process to disappear.

    Returns
    -------
    gone : `bool`
        `True` if it was gone within the time.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not process_exists(process_id):
            return True
        time.sleep(0.05)
    return not process_exists(process_id)


def test_abandoning_stops_the_registered_process_and_its_grandchild() -> None:
    """The whole group of the run's process is stopped."""
    abandonment = RunAbandonment()
    child = abandonment.launch(start_child)
    grandchild_id = int(child.stdout.readline())
    assert process_exists(grandchild_id)

    abandonment.abandon()

    assert child.poll() is not None
    assert wait_until_gone(grandchild_id)


def test_abandoning_one_run_leaves_another_runs_process_alone() -> None:
    """Only the abandoned run's own process is stopped."""
    abandoned_run = RunAbandonment()
    other_run = RunAbandonment()
    abandoned_child = abandoned_run.launch(start_child)
    other_child = other_run.launch(start_child)
    other_grandchild_id = int(other_child.stdout.readline())
    try:
        abandoned_run.abandon()
        assert abandoned_child.poll() is not None
        assert other_child.poll() is None
        assert process_exists(other_grandchild_id)
    finally:
        other_run.abandon()


def test_a_run_abandoned_before_it_started_never_starts_the_process() -> None:
    """The start function is not called once the run was given up on."""
    abandonment = RunAbandonment()
    abandonment.abandon()
    started = []

    with pytest.raises(RunAbandonedError):
        abandonment.launch(lambda: started.append("started"))  # type: ignore[arg-type, return-value]

    assert started == []
    assert abandonment.registered_process_count == 0


def test_launch_registered_without_a_bound_run_starts_the_process_plainly() -> None:
    """Outside any run the process starts as before and is nobody's to stop."""
    process = launch_registered(start_child)
    try:
        assert process.poll() is None
    finally:
        stop_process_group(process)
        process.wait()


def test_raise_if_abandoned_follows_the_bound_run() -> None:
    """The check passes for a wanted run and fails for an abandoned one."""
    raise_if_abandoned()  # no run bound: nothing happens
    abandonment = RunAbandonment()
    with bind_abandonment(abandonment):
        raise_if_abandoned()
        abandonment.abandon()
        with pytest.raises(RunAbandonedError):
            raise_if_abandoned()
    raise_if_abandoned()  # unbound again


def test_a_program_run_to_the_end_is_stopped_when_its_run_is_abandoned() -> None:
    """Stop a program run to the end when its run is abandoned."""
    abandonment = RunAbandonment()
    outcome: dict[str, object] = {}

    def run_sleeper() -> None:
        """Run a long sleep under the run, as the preview step runs Siril."""
        with bind_abandonment(abandonment):
            try:
                run_registered([sys.executable, "-c", "import time; time.sleep(60)"], 120.0)
            except RunAbandonedError as error:
                outcome["error"] = error

    thread = threading.Thread(target=run_sleeper, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while abandonment.registered_process_count == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    started_waiting = time.monotonic()
    abandonment.abandon()
    thread.join(timeout=10.0)

    assert not thread.is_alive()
    assert "error" in outcome
    assert time.monotonic() - started_waiting < 5.0


def test_run_registered_without_a_bound_run_returns_the_output() -> None:
    """With no run bound this is a plain captured run."""
    completed = run_registered([sys.executable, "-c", "print('hello')"], 30.0)
    assert completed.returncode == 0
    assert completed.stdout.strip() == "hello"


def test_a_timeout_without_a_bound_run_ends_the_program_and_its_helper(tmp_path: Path) -> None:
    """A timed-out stand-in program leaves no helper process running.

    The stand-in starts a sleeping helper and writes its id to the file named
    by the first argument, as GraXpert starts helpers of
    their own. No run is bound, so this is the plain call that GraXpert
    and Cosmic Clarity use in scripts.
    """
    helper_file = tmp_path / "helper_pid.txt"
    script = CHILD_WITH_GRANDCHILD.replace(
        "print(g.pid, flush=True)", f"open({str(helper_file)!r}, 'w').write(str(g.pid))"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_registered([sys.executable, "-c", script], 1.5)

    helper_process_id = int(helper_file.read_text())
    assert wait_until_gone(helper_process_id)


def test_a_run_waiting_for_a_slot_gives_up_when_abandoned() -> None:
    """A run queued for a heavy-job slot stops waiting once it is abandoned."""
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

    slot_held = threading.Event()
    release_slot = threading.Event()

    def hold_the_only_slot() -> None:
        """Hold the slot, in a thread of its own, until released."""
        with acquire_resource_slot(None, "abandon_wait_test", 1):
            slot_held.set()
            release_slot.wait(30)

    holder = threading.Thread(target=hold_the_only_slot)
    holder.start()
    assert slot_held.wait(10)

    abandonment = RunAbandonment()
    outcome: list[BaseException | None] = []

    def wait_for_the_slot() -> None:
        """Queue for the slot as an abandonable run and record how it ended."""
        try:
            with (
                bind_abandonment(abandonment),
                acquire_resource_slot(None, "abandon_wait_test", 1, poll_interval_seconds=0.05),
            ):
                outcome.append(None)
        except RunAbandonedError as error:
            outcome.append(error)

    waiter = threading.Thread(target=wait_for_the_slot)
    waiter.start()
    time.sleep(0.3)
    assert waiter.is_alive()

    abandonment.abandon()
    waiter.join(10)
    release_slot.set()
    holder.join(10)

    assert not waiter.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], RunAbandonedError)
