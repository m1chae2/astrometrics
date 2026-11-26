"""Tests for the owner of a backend job and what happens to a job that lost it.

Covers: a backend-created job records its owner, the startup cleanup closes
the job of a dead owner, Cancel closes a row nothing runs but refuses a row
owned by another live program, and the already-running check ignores dead
rows but blocks a live duplicate. Temporary databases and fake program
checks stand in for the real logs database and operating system.
"""

from datetime import datetime
from pathlib import Path

import pytest

from astrometricslib.jobs import JobStore, ProcessingJob, current_process_identity
from backend.services.infrastructure import base_service as base_service_module
from backend.services.infrastructure.base_service import BaseBackgroundService
from backend.services.processing.job_service import JobService

DEAD_PID = 111111
LIVE_OTHER_PID = 222222
START_TEXT = "1000.00"


@pytest.fixture
def job_service(tmp_path: Path) -> JobService:
    """Build a job service over a temporary logs database.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the temporary database.

    Returns
    -------
    job_service : `JobService`
        A service whose store is private to the test.
    """
    return JobService(JobStore(str(tmp_path / "logs.db")), logs_folder=tmp_path)


@pytest.fixture(autouse=True)
def fake_program_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make only `LIVE_OTHER_PID` and this test process count as running.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the operating system check.
    """
    own_pid, _ = current_process_identity()
    monkeypatch.setattr(
        base_service_module,
        "process_is_alive",
        lambda process_id, started_at: process_id in (LIVE_OTHER_PID, own_pid),
    )


def _store_row(
    job_service: JobService,
    job_id: str,
    status: str = "started",
    owner_pid: int | None = None,
    job_type: str = "stacking",
) -> None:
    """Write a job row with the given owner straight into the store.

    Parameters
    ----------
    job_service : `JobService`
        Where the row goes.
    job_id : `str`
        The row's id.
    status : `str`, optional
        The row's status.
    owner_pid : `int` or `None`, optional
        The recorded owner number, or `None` for a row from before owners.
    job_type : `str`, optional
        The job kind.
    """
    now = datetime.now().isoformat()
    job_service.repository.upsert_job(
        ProcessingJob(
            id=job_id,
            target_id="M 45",
            job_type=job_type,
            status=status,
            created_at=now,
            updated_at=now,
            owner_pid=owner_pid,
            owner_started_at=None if owner_pid is None else START_TEXT,
        )
    )


def test_job_created_by_the_backend_records_its_owner(job_service: JobService) -> None:
    """A new job names this program as its owner."""
    job = job_service.create_job("M 45", "stacking")

    stored = job_service.get_job(job.id)
    assert (stored.owner_pid, stored.owner_started_at) == current_process_identity()


def test_startup_cleanup_interrupts_a_job_whose_owner_is_dead(job_service: JobService) -> None:
    """The cleanup closes a dead owner's job at once and leaves a live one."""
    _store_row(job_service, "dead", owner_pid=DEAD_PID)
    _store_row(job_service, "alive", owner_pid=LIVE_OTHER_PID)

    closed = job_service.repository.interrupt_orphaned_jobs(
        lambda process_id, started_at: process_id == LIVE_OTHER_PID
    )

    assert [job.id for job in closed] == ["dead"]
    assert job_service.get_job("dead").status == "interrupted"
    assert job_service.get_job("alive").status == "started"


def test_cancel_closes_a_row_whose_owner_is_dead(job_service: JobService) -> None:
    """Cancel closes a dead owner's row instead of refusing."""
    _store_row(job_service, "dead", status="running", owner_pid=DEAD_PID)
    service = BaseBackgroundService(max_workers=1, job_service=job_service)

    outcome = service.cancel_processing("dead")

    assert outcome["cancelled"] is True
    assert job_service.get_job("dead").status == "cancelled"


def test_cancel_closes_a_row_without_owner_that_no_task_holds(job_service: JobService) -> None:
    """A row from before owners, with no task here, is closed by Cancel."""
    _store_row(job_service, "legacy", owner_pid=None)
    service = BaseBackgroundService(max_workers=1, job_service=job_service)

    outcome = service.cancel_processing("legacy")

    assert outcome["cancelled"] is True
    assert job_service.get_job("legacy").status == "cancelled"


def test_cancel_still_refuses_a_row_owned_by_another_live_program(job_service: JobService) -> None:
    """A row of a live other program keeps the existing message."""
    _store_row(job_service, "elsewhere", owner_pid=LIVE_OTHER_PID)
    service = BaseBackgroundService(max_workers=1, job_service=job_service)

    outcome = service.cancel_processing("elsewhere")

    assert outcome["cancelled"] is False
    assert "another process" in outcome["reason"]
    assert job_service.get_job("elsewhere").status == "started"


def test_already_running_check_ignores_dead_rows_and_blocks_a_live_duplicate(
    job_service: JobService,
) -> None:
    """Dead and ownerless rows do not block; a live row does."""
    _store_row(job_service, "dead-started", owner_pid=DEAD_PID)
    _store_row(job_service, "dead-running", status="running", owner_pid=DEAD_PID)
    _store_row(job_service, "legacy", owner_pid=None)
    service = BaseBackgroundService(max_workers=1, job_service=job_service)
    assert service.get_live_jobs_for_target("M 45", "stacking") == []

    _store_row(job_service, "live-running", status="running", owner_pid=LIVE_OTHER_PID)

    assert [job.id for job in service.get_live_jobs_for_target("M 45", "stacking")] == ["live-running"]
