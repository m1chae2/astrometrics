"""Tests for the resource slots that limit how many jobs run at once.

The case that matters is a job that holds a slot and then asks for the same
resource again. With two slots and two such jobs, each held one slot and
waited for the other's, and both stacks hung.
"""

import json
import logging
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- a real short-lived process is the point
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.foundation.paths import locks_directory
from astrometricslib.foundation.storage import process_locks
from astrometricslib.foundation.storage.process_locks import (
    acquire_resource_slot,
    describe_slot_holders,
    list_slot_holders,
)

WAIT_SECONDS = 20.0


def make_configuration(tmp_path: Path) -> SimpleNamespace:
    """Build the stand-in settings the slot code reads.

    Returns
    -------
    configuration : `SimpleNamespace`
        Settings whose library folder is inside `tmp_path`.
    """
    return SimpleNamespace(get_library_path=lambda: tmp_path)


def run_in_threads(targets: list) -> list[bool]:
    """Run each callable in its own thread and report which finished.

    Returns
    -------
    finished : `list` [`bool`]
        For each callable, whether its thread ended within the time limit.
    """
    threads = [threading.Thread(target=target, daemon=True) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=WAIT_SECONDS)
    return [not thread.is_alive() for thread in threads]


def test_a_nested_request_for_a_held_slot_runs_at_once(tmp_path: Path) -> None:
    """With one slot, asking again inside the slot must not wait."""
    configuration = make_configuration(tmp_path)

    def job() -> None:
        """Take the one slot, then take it again inside."""
        with acquire_resource_slot(configuration, "job", 1):
            with acquire_resource_slot(configuration, "job", 1):
                pass

    assert run_in_threads([job]) == [True]


def test_two_jobs_that_each_nest_do_not_wait_for_each_other(tmp_path: Path) -> None:
    """Two jobs, two slots, each job asking again inside: both finish."""
    configuration = make_configuration(tmp_path)
    both_hold_a_slot = threading.Barrier(2, timeout=WAIT_SECONDS)

    def job() -> None:
        """Hold a slot until the other job holds one too, then nest."""
        with acquire_resource_slot(configuration, "job", 2):
            both_hold_a_slot.wait()
            with acquire_resource_slot(configuration, "job", 2):
                pass

    assert run_in_threads([job, job]) == [True, True]


def test_the_slot_is_free_again_after_a_job_ends(tmp_path: Path) -> None:
    """A finished job gives its slot back, and the held mark is cleared."""
    configuration = make_configuration(tmp_path)

    def jobs_one_after_another() -> None:
        """Take the slot twice in a row, one job after the other."""
        for _ in range(2):
            with acquire_resource_slot(configuration, "job", 1):
                pass

    assert run_in_threads([jobs_one_after_another]) == [True]


def test_a_different_resource_still_takes_its_own_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding the job slot does not stand in for another resource's slot."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    configuration = make_configuration(tmp_path)
    other_resource_was_locked = []

    def job() -> None:
        """Hold the job slot, then take the gpu slot inside it."""
        with acquire_resource_slot(configuration, "job", 1):
            with acquire_resource_slot(configuration, "gpu", 1):
                other_resource_was_locked.append(
                    (tmp_path / "astrometrics" / "locks" / "gpu_slot_0.lock").exists()
                )

    assert run_in_threads([job]) == [True]
    assert other_resource_was_locked == [True]


def _holder_path(resource_name: str) -> Path:
    """Give the holder file of slot 0 of a resource in the test locks folder.

    Returns
    -------
    holder_path : `pathlib.Path`
        The file that names the slot's holder.
    """
    return Path(locks_directory()) / f"{resource_name}_slot_0.holder.json"


def _take_and_release(tmp_path: Path, resource_name: str) -> None:
    """Take and give back one slot of the named resource."""
    with acquire_resource_slot(make_configuration(tmp_path), resource_name, 1):
        pass


def test_a_holder_is_recorded_while_the_slot_is_held_and_removed_after(tmp_path: Path) -> None:
    """Record the holder while held and remove it on release."""
    with acquire_resource_slot(make_configuration(tmp_path), "holder_record", 1, work_name="M 81"):
        holders = list_slot_holders("holder_record", 1)
        assert [(h["work_name"], h["process_id"]) for h in holders] == [("M 81", os.getpid())]
        assert "M 81" in describe_slot_holders("holder_record", 1)
    assert not _holder_path("holder_record").exists()
    assert list_slot_holders("holder_record", 1) == []


def test_a_holder_file_left_by_a_dead_process_is_not_reported() -> None:
    """A record whose process no longer exists is not a live holder."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _holder_path("dead_holder").write_text(
        json.dumps({"process_id": dead.pid, "work_name": "ghost", "started_at": time.time() - 60})
    )
    assert list_slot_holders("dead_holder", 1) == []
    assert "ghost" not in describe_slot_holders("dead_holder", 1)


@pytest.mark.parametrize("content", ["", "{not json", '{"process_id": "x"}', "[]", '{"process_id": 1}'])
def test_a_damaged_holder_file_is_ignored_and_does_not_block_the_slot(tmp_path: Path, content: str) -> None:
    """Skip damaged records and still take the slot."""
    _holder_path("damaged_holder").write_text(content)
    assert list_slot_holders("damaged_holder", 1) == []
    assert run_in_threads([lambda: _take_and_release(tmp_path, "damaged_holder")]) == [True]


def test_an_unwritable_record_never_stops_the_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the record cannot be written, the slot is still taken."""

    def failing_replace(*_: object) -> None:
        """Stand in for a disk that refuses the write.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    assert run_in_threads([lambda: _take_and_release(tmp_path, "unwritable_holder")]) == [True]
    assert list_slot_holders("unwritable_holder", 1) == []


def test_a_waiter_reports_the_live_holder_it_is_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log the holder the waiter is behind, after the report interval."""
    monkeypatch.setattr(process_locks, "HOLDER_REPORT_INTERVAL_SECONDS", 0.05)
    configuration = make_configuration(tmp_path)
    holder_has_slot = threading.Event()
    release_holder = threading.Event()
    waiter_logger = logging.getLogger("waiter_test")
    waiter_logger.setLevel(logging.INFO)
    messages: list[str] = []

    class _Collect(logging.Handler):
        """Keep the messages sent to the waiter's own logger."""

        def emit(self, record: logging.LogRecord) -> None:
            """Store one message."""
            messages.append(record.getMessage())

    waiter_logger.addHandler(_Collect())

    def holder() -> None:
        """Hold the only slot until told to let go."""
        with acquire_resource_slot(configuration, "report_wait", 1, work_name="NGC 2403"):
            holder_has_slot.set()
            release_holder.wait(timeout=WAIT_SECONDS)

    def waiter() -> None:
        """Wait for the slot behind the holder."""
        holder_has_slot.wait(timeout=WAIT_SECONDS)
        with acquire_resource_slot(
            configuration,
            "report_wait",
            1,
            poll_interval_seconds=0.02,
            work_name="M 13",
            wait_logger=waiter_logger,
        ):
            pass

    holder_thread = threading.Thread(target=holder, daemon=True)
    waiter_thread = threading.Thread(target=waiter, daemon=True)
    holder_thread.start()
    waiter_thread.start()
    deadline = time.monotonic() + WAIT_SECONDS
    while not any("NGC 2403" in message for message in messages) and time.monotonic() < deadline:
        time.sleep(0.02)
    release_holder.set()
    waiter_thread.join(timeout=WAIT_SECONDS)
    holder_thread.join(timeout=WAIT_SECONDS)
    assert any("M 13" in m and "NGC 2403" in m for m in messages)
    assert not waiter_thread.is_alive()
