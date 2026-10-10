"""Tests for the resource slots that limit how many jobs run at once.

The case that matters is a job that holds a slot and then asks for the same
resource again. With two slots and two such jobs, each held one slot and
waited for the other's, and both stacks hung.
"""

import threading
from pathlib import Path
from types import SimpleNamespace

from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

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


def test_a_different_resource_still_takes_its_own_slot(tmp_path: Path) -> None:
    """Holding the job slot does not stand in for another resource's slot."""
    configuration = make_configuration(tmp_path)
    other_resource_was_locked = []

    def job() -> None:
        """Hold the job slot, then take the gpu slot inside it."""
        with acquire_resource_slot(configuration, "job", 1):
            with acquire_resource_slot(configuration, "gpu", 1):
                other_resource_was_locked.append((tmp_path / "locks" / "gpu_slot_0.lock").exists())

    assert run_in_threads([job]) == [True]
    assert other_resource_was_locked == [True]
