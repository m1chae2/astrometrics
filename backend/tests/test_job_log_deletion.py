"""Tests that a job log is deleted only inside the app's own logs folder."""

import logging
from pathlib import Path

import pytest

from astrometricslib.jobs import JobStore, ProcessingJob
from backend.services.processing.job_service import JobService


def _service_with_job(tmp_path: Path, log_file: Path) -> tuple[JobService, JobStore, Path]:
    """Build a service over a temporary job store with one old job.

    Returns
    -------
    service, store, logs_folder : `tuple`
        The service, its store and the folder the service owns.
    """
    logs_folder = tmp_path / "own_logs"
    logs_folder.mkdir()
    store = JobStore(db_path=str(tmp_path / "jobs.db"))
    store.upsert_job(
        ProcessingJob(
            id="old-job",
            target_id="M 27",
            job_type="stacking",
            status="completed",
            log_file_path=str(log_file),
            created_at="2000-01-01T00:00:00",
            updated_at="2000-01-01T00:00:00",
        )
    )
    return JobService(store, logs_folder=logs_folder), store, logs_folder


def test_log_file_outside_the_logs_folder_is_kept_and_record_pruned(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored path outside the owned folder is left alone."""
    outside = tmp_path / "real_logs" / "stacking.log"
    outside.parent.mkdir()
    outside.write_text("keep me")
    service, store, _logs = _service_with_job(tmp_path, outside)
    with caplog.at_level(logging.DEBUG):
        assert service.prune_old_jobs(days=7) == 1
    assert outside.read_text() == "keep me"
    assert store.get_job("old-job") is None
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_log_file_inside_the_logs_folder_is_deleted(tmp_path: Path) -> None:
    """A stored path inside the owned folder is deleted with its record."""
    inside = tmp_path / "own_logs" / "stacking.log"
    service, store, _logs = _service_with_job(tmp_path, inside)
    inside.write_text("old log")
    assert service.delete_job("old-job") is True
    assert not inside.exists()
    assert store.get_job("old-job") is None


def test_dotdot_path_that_escapes_the_logs_folder_is_kept(tmp_path: Path) -> None:
    """A path that climbs out of the logs folder is left alone."""
    victim = tmp_path / "victim.log"
    victim.write_text("keep me")
    service, _store, logs_folder = _service_with_job(tmp_path, tmp_path / "own_logs" / ".." / "victim.log")
    assert logs_folder.is_dir()
    assert service.delete_job("old-job") is True
    assert victim.read_text() == "keep me"
