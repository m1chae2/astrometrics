"""Tests for closing jobs that a program left open.

A program that is killed or hangs leaves its jobs "started" or "running"
forever, and a restack that stopped half way leaves the old stack parked in a
staging folder. These tests cover finding such jobs without touching jobs a
live program still runs, and putting the parked stack back.
"""

from datetime import datetime, timedelta
from pathlib import Path

from astrometricslib.drivers.logger_interface import LoggerInterface
from astrometricslib.pipelines.shared.interrupted_jobs import (
    recover_interrupted_jobs,
    restore_orphaned_stack_staging,
)
from astrometricslib.utilities.pipeline_models import ProcessingJob
from astrometricslib.utilities.process_identity import current_process_identity, process_is_alive


def add_job(
    logger_interface: LoggerInterface,
    job_id: str,
    target_id: str = "M 1",
    status: str = "running",
    owner: tuple[int, str] | None = None,
    idle_minutes: float = 0.0,
) -> None:
    """Record a job that was last updated `idle_minutes` ago."""
    updated_at = (datetime.now() - timedelta(minutes=idle_minutes)).isoformat()
    logger_interface.upsert_job(
        ProcessingJob(
            id=job_id,
            target_id=target_id,
            job_type="stacking",
            status=status,
            created_at=updated_at,
            updated_at=updated_at,
            owner_pid=owner[0] if owner else None,
            owner_started_at=owner[1] if owner else None,
        )
    )
    # `upsert_job` stamps its own time on an update; set the age by hand.
    import sqlite3

    with sqlite3.connect(logger_interface.db_path) as connection:
        connection.execute("UPDATE processing_jobs SET updated_at = ? WHERE id = ?", (updated_at, job_id))


def alive_unless(dead_pids: set[int]):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Build a stand-in for `process_is_alive` that says these programs ended.

    Returns
    -------
    is_alive : `Callable`
        Says whether a program number is not among the ended ones.
    """
    return lambda process_id, started_at: process_id not in dead_pids


def test_a_job_whose_owner_ended_is_closed(tmp_path: Path) -> None:
    """A job left open by a program that no longer runs becomes interrupted."""
    logger_interface = LoggerInterface(str(tmp_path / "logs.db"))
    add_job(logger_interface, "dead", owner=(111, "1.00"))

    closed = logger_interface.interrupt_orphaned_jobs(alive_unless({111}))

    assert [job.id for job in closed] == ["dead"]
    stored = logger_interface.get_job("dead")
    assert stored.status == "interrupted"
    assert stored.completed_at is not None


def test_a_job_owned_by_a_running_program_is_left_alone(tmp_path: Path) -> None:
    """A job another live program runs must not be closed, however old."""
    logger_interface = LoggerInterface(str(tmp_path / "logs.db"))
    add_job(logger_interface, "alive", owner=(222, "2.00"), idle_minutes=500)

    closed = logger_interface.interrupt_orphaned_jobs(alive_unless({111}))

    assert closed == []
    assert logger_interface.get_job("alive").status == "running"


def test_a_job_with_no_owner_is_closed_only_when_old(tmp_path: Path) -> None:
    """Jobs recorded before owners were kept can only be judged by age."""
    logger_interface = LoggerInterface(str(tmp_path / "logs.db"))
    add_job(logger_interface, "old", idle_minutes=120)
    add_job(logger_interface, "recent", idle_minutes=5)

    closed = logger_interface.interrupt_orphaned_jobs(alive_unless(set()))

    assert [job.id for job in closed] == ["old"]
    assert logger_interface.get_job("recent").status == "running"


def test_a_finished_job_is_never_touched(tmp_path: Path) -> None:
    """Only started and running jobs are candidates."""
    logger_interface = LoggerInterface(str(tmp_path / "logs.db"))
    add_job(logger_interface, "done", status="completed", owner=(111, "1.00"))

    assert logger_interface.interrupt_orphaned_jobs(alive_unless({111})) == []
    assert logger_interface.get_job("done").status == "completed"


def test_this_program_is_alive_and_a_reused_number_is_not() -> None:
    """A staging folder a live restack is using is left alone."""
    process_id, started_at = current_process_identity()

    assert process_is_alive(process_id, started_at)
    assert not process_is_alive(process_id, "1.00")
    assert not process_is_alive(2_000_000_000, "1.00")


def test_a_parked_stack_is_put_back(tmp_path: Path) -> None:
    """Files in a staging folder go back beside the stack."""
    target_folder = tmp_path / "M 1"
    staging = target_folder / "_previous.staging.M_1_L_Stacked"
    staging.mkdir(parents=True)
    (staging / "M_1_L_Stacked.fits").write_bytes(b"old stack")

    restored = restore_orphaned_stack_staging(str(target_folder))

    assert restored == [str(staging)]
    assert (target_folder / "M_1_L_Stacked.fits").read_bytes() == b"old stack"
    assert not staging.exists()


def test_recovery_puts_back_the_stack_of_a_closed_stacking_job(tmp_path: Path) -> None:
    """Closing a dead stacking job also restores the stack it had parked."""
    logger_interface = LoggerInterface(str(tmp_path / "logs.db"))
    add_job(logger_interface, "dead", target_id="M 1", owner=(111, "1.00"))
    staging = tmp_path / "stacks" / "lights" / "M 1" / "_previous.staging.M_1_L_Stacked"
    staging.mkdir(parents=True)
    (staging / "M_1_L_Stacked.fits").write_bytes(b"old stack")

    closed = recover_interrupted_jobs(
        str(tmp_path / "logs.db"), str(tmp_path / "stacks"), alive_unless({111})
    )

    assert [job.id for job in closed] == ["dead"]
    assert (tmp_path / "stacks" / "lights" / "M 1" / "M_1_L_Stacked.fits").exists()


def test_recovery_keeps_the_staging_of_a_target_with_another_live_job(tmp_path: Path) -> None:
    """A staging folder a live restack is using is not touched."""
    logger_interface = LoggerInterface(str(tmp_path / "logs.db"))
    add_job(logger_interface, "dead", target_id="M 1", owner=(111, "1.00"))
    add_job(logger_interface, "live", target_id="M 1", owner=(222, "2.00"))
    staging = tmp_path / "stacks" / "lights" / "M 1" / "_previous.staging.M_1_L_Stacked"
    staging.mkdir(parents=True)
    (staging / "M_1_L_Stacked.fits").write_bytes(b"old stack")

    recover_interrupted_jobs(str(tmp_path / "logs.db"), str(tmp_path / "stacks"), alive_unless({111}))

    assert staging.exists()
