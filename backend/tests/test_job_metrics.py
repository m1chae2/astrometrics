"""Unit tests for pipeline input/output quality metrics persistence.

Verifies that ProcessingJob correctly stores input_metrics and output_metrics
in SQLite via LoggerInterface and JobService.
"""

from pathlib import Path

from astrometricslib import LoggerInterface, ProcessingJob
from backend.services.processing.job_service import JobService


def test_logger_interface_metrics_persistence(tmp_path: Path) -> None:
    """Verify input/output metrics are persisted and retrieved from SQLite."""
    db_path = tmp_path / "test_logs.db"
    logger_db = LoggerInterface(db_path=str(db_path))

    job = ProcessingJob(
        id="job-metrics-001",
        target_id="M 31",
        job_type="stacking",
        status="running",
        input_metrics={"snr_estimate": 18.2, "frame_count": 40},
        output_metrics={},
    )
    logger_db.upsert_job(job)

    retrieved = logger_db.get_job("job-metrics-001")
    assert retrieved is not None
    assert retrieved.input_metrics == {"snr_estimate": 18.2, "frame_count": 40}
    assert retrieved.output_metrics == {}

    # Update job with output metrics upon completion
    job.status = "completed"
    job.output_metrics = {"stacked_fwhm": 2.4, "rejection_rate": 0.05}
    logger_db.upsert_job(job)

    updated = logger_db.get_job("job-metrics-001")
    assert updated is not None
    assert updated.status == "completed"
    assert updated.output_metrics == {"stacked_fwhm": 2.4, "rejection_rate": 0.05}
    assert updated.input_metrics == {"snr_estimate": 18.2, "frame_count": 40}


def test_job_service_metrics_lifecycle(tmp_path: Path) -> None:
    """Verify JobService handles input and output metrics lifecycle."""
    db_path = tmp_path / "test_job_service.db"
    repo = LoggerInterface(db_path=str(db_path))
    service = JobService(job_repository=repo)

    job = service.create_job(
        target_id="NGC 7000",
        job_type="analysis",
        input_metrics={"ambient_temp_c": 12.0},
    )
    assert job.input_metrics == {"ambient_temp_c": 12.0}
    assert job.output_metrics == {}

    service.update_job(
        job.id,
        status="completed",
        output_metrics={"stars_detected": 1420},
    )

    persisted = service.get_job(job.id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.input_metrics == {"ambient_temp_c": 12.0}
    assert persisted.output_metrics == {"stars_detected": 1420}
