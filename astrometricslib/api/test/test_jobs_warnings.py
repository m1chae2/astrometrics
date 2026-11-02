"""Tests for `Jobs.query(detail="warnings")`.

A warning is a stored quality measurement of the job whose check failed. The
tests store rows with every outcome under two jobs and check what is listed,
what is only counted, and how an empty list differs from an unknown job.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.api.jobs import Jobs
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.store import JobStore
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)

MEASURED_AT = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _row(
    metric: str, job_id: str | None, outcome: GateStatus | None, subject_id: str = "M 13/run"
) -> QualityMeasurement:
    """Build one stored row of a job with a given check outcome.

    Returns
    -------
    row : `QualityMeasurement`
        A measurement. Passed and failed rows carry a limit.
    """
    limit = (
        QualityLimit(
            value=0.5,
            unit="fraction",
            tier=LimitTier.DESIGN_ESTIMATE,
            derivation="a test limit",
            registerEntry="STK-05",
        )
        if outcome in (GateStatus.PASSED, GateStatus.FAILED)
        else None
    )
    return QualityMeasurement(
        metric=metric,
        definitionVersion=1,
        subject=QualitySubject(level=SubjectLevel.RUN, subjectId=subject_id, targetId="M 13"),
        status="measured",
        value=0.7,
        reason=f"{metric} reason" if outcome is not None else "",
        checkOutcome=outcome,
        checkLimit=limit,
        origin=MeasurementOrigin.MEASURED,
        runId=job_id or "run-1",
        jobId=job_id,
        measuredAt=MEASURED_AT,
    )


@pytest.fixture
def jobs(tmp_path: Path) -> Jobs:
    """Make a `Jobs` over a logs database and a quality database.

    Job ``job-a`` has one failed, one passed and one not-checked row, and a
    plain value. Job ``job-b`` has only a passed row. Job ``job-c`` is in the
    logs database and stored no rows.

    Returns
    -------
    jobs : `Jobs`
        The interface under test.
    """
    logs = tmp_path / "log.db"
    job_store = JobStore(str(logs))
    for job_id in ("job-a", "job-b", "job-c"):
        job_store.upsert_job(
            ProcessingJob(
                id=job_id,
                target_id="M 13",
                job_type="stacking",
                status="completed",
                progress_current=1,
                progress_total=1,
                created_at=MEASURED_AT.isoformat(),
                updated_at=MEASURED_AT.isoformat(),
            )
        )
    (tmp_path / "library").mkdir()
    QualityStore(str(tmp_path / "library" / "astrometrics.db")).write_measurements([
        _row("stacking.gate_flat_noise", "job-a", GateStatus.FAILED),
        _row("stacking.gate_flat_level", "job-a", GateStatus.PASSED),
        _row("stacking.gate_dark_level", "job-a", GateStatus.NOT_CHECKED),
        _row("stacking.frames_stacked_count", "job-a", None),
        _row("stacking.gate_flat_noise", "job-b", GateStatus.PASSED),
        _row("photometry.gate_flat_noise", None, GateStatus.FAILED),
    ])
    configuration = SimpleNamespace(
        get_logs_db_path=lambda: str(logs), get_library_path=lambda: str(tmp_path / "library")
    )
    return Jobs(configuration, None)


def test_a_failed_check_is_a_warning_and_a_passed_one_is_not(jobs: Jobs) -> None:
    """List only the failed row, with value, limit and register entry."""
    answer = jobs.query(job_id="job-a", detail="warnings")
    assert answer.detail == "warnings"
    assert [warning.metric for warning in answer.warnings] == ["stacking.gate_flat_noise"]
    warning = answer.warnings[0]
    assert warning.pipeline == "stacking"
    assert warning.outcome == "failed"
    assert warning.value == pytest.approx(0.7)
    assert warning.limit == pytest.approx(0.5)
    assert warning.register_entry == "STK-05"
    assert warning.reason == "stacking.gate_flat_noise reason"
    assert answer.job.id == "job-a"


def test_checks_that_did_not_run_are_counted_but_not_listed(jobs: Jobs) -> None:
    """Count a not-checked row and all rows; list neither."""
    answer = jobs.query(job_id="job-a", detail="warnings")
    assert answer.checks_not_run_count == 1
    assert answer.measurement_count == 4


def test_a_job_with_rows_and_no_failure_has_an_empty_list(jobs: Jobs) -> None:
    """An empty list with rows means checked, nothing failed."""
    answer = jobs.query(job_id="job-b", detail="warnings")
    assert answer.warnings == []
    assert answer.measurement_count == 1


def test_a_known_job_with_no_rows_is_distinct_from_an_unknown_job(jobs: Jobs) -> None:
    """Give zero measurements for a quiet job and an error for a bad id."""
    answer = jobs.query(job_id="job-c", detail="warnings")
    assert answer.warnings == []
    assert answer.measurement_count == 0
    with pytest.raises(NotFoundError):
        jobs.query(job_id="no-such-job", detail="warnings")


def test_rows_of_a_job_missing_from_the_logs_are_still_listed(tmp_path: Path) -> None:
    """List rows whose job the logs lost, with no job summary."""
    (tmp_path / "library").mkdir()
    QualityStore(str(tmp_path / "library" / "astrometrics.db")).write_measurements([
        _row("stacking.gate_flat_noise", "job-lost", GateStatus.FAILED)
    ])
    configuration = SimpleNamespace(
        get_logs_db_path=lambda: str(tmp_path / "missing.db"),
        get_library_path=lambda: str(tmp_path / "library"),
    )
    answer = Jobs(configuration, None).query(job_id="job-lost", detail="warnings")
    assert answer.job is None
    assert len(answer.warnings) == 1


def test_rows_with_no_job_are_not_listed_for_any_job(jobs: Jobs) -> None:
    """A row with no job id belongs to no job."""
    answer = jobs.query(job_id="job-a", detail="warnings")
    assert all(warning.pipeline != "photometry" for warning in answer.warnings)


def test_warnings_need_a_job_id(jobs: Jobs) -> None:
    """The detail refuses a call that names no job."""
    with pytest.raises(InvalidArgumentError):
        jobs.query(detail="warnings")
