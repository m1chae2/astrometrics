"""Tests for paging back through job history with a ``before`` cutoff."""

from pathlib import Path

from astrometricslib.jobs import JobStore, ProcessingJob
from backend.services.processing.job_service import JobService


def _make_service(tmp_path: Path) -> JobService:
    """Build a `JobService` over a temporary job database holding three jobs.

    The jobs are created on three different days for ``M 31``, plus one
    job for another target that must never appear.

    Returns
    -------
    service : `JobService`
        A service over the temporary database.
    """
    store = JobStore(db_path=str(tmp_path / "jobs.db"))
    for job_id, target_id, created_at in [
        ("night-1", "M 31", "2026-11-03T21:00:00"),
        ("night-2", "M 31", "2026-11-04T21:00:00"),
        ("night-3", "M 31", "2026-11-05T21:00:00"),
        ("other", "M 13", "2026-11-02T21:00:00"),
    ]:
        store.upsert_job(
            ProcessingJob(
                id=job_id,
                target_id=target_id,
                job_type="stacking",
                status="completed",
                created_at=created_at,
            )
        )
    return JobService(job_repository=store)


def test_before_keeps_only_older_jobs(tmp_path: Path) -> None:
    """Only jobs created strictly before the cutoff come back, newest first."""
    service = _make_service(tmp_path)
    jobs = service.get_jobs_for_target("M 31", before="2026-11-05T21:00:00")
    assert [job.id for job in jobs] == ["night-2", "night-1"]


def test_before_with_limit_pages_backward(tmp_path: Path) -> None:
    """A limit plus the oldest time seen walks back one page at a time."""
    service = _make_service(tmp_path)
    first_page = service.list_jobs("M 31", limit=2)
    assert [job.id for job in first_page] == ["night-3", "night-2"]
    second_page = service.list_jobs("M 31", limit=2, before=first_page[-1].created_at)
    assert [job.id for job in second_page] == ["night-1"]


def test_no_before_returns_everything(tmp_path: Path) -> None:
    """Without a cutoff the call behaves as it did before."""
    service = _make_service(tmp_path)
    assert len(service.get_jobs_for_target("M 31")) == 3
