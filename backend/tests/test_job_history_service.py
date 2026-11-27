"""Purpose: Tests for ``jobs:query``, which the job history service answers.

Description: The library answers each job question with its own method and in
full. `JobHistoryService` keeps the reply the app reads: one method chosen by
``detail``, short messages and log lines unless asked for whole, capped lists
and clear errors. These tests build a small logs database and read it through
the real library.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.jobs import Jobs
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.store import JobStore
from backend.services.processing.job_history_service import JobHistoryService


def _job(
    job_id: str, target_id: str, job_type: str, status: str, minute: int, message: str = ""
) -> ProcessingJob:
    """Build a job record created at a given minute.

    Returns
    -------
    job : `ProcessingJob`
        The record.
    """
    created = (datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minute)).isoformat()
    return ProcessingJob(
        id=job_id,
        target_id=target_id,
        job_type=job_type,
        status=status,
        progress_current=3,
        progress_total=10,
        message=message,
        created_at=created,
        updated_at=created,
        output_metrics={"stars": 42, "parameter_defaults": ["a.value: using the default 1"]},
    )


@pytest.fixture
def service(tmp_path: Path) -> JobHistoryService:
    """Make a service over a logs database with four jobs and some log lines.

    Returns
    -------
    service : `JobHistoryService`
        The service under test.
    """
    path = tmp_path / "log.db"
    store = JobStore(str(path))
    store.upsert_job(_job("job-a", "M 13", "stacking", "completed", 1))
    store.upsert_job(_job("job-b", "M 13", "analysis", "failed", 2, "Plate solve failed " * 100))
    store.upsert_job(_job("job-c", "M 57", "stacking", "running", 3))
    store.upsert_job(_job("calibration-job", "calibration", "diagnostics", "completed", 4))
    for number in range(30):
        store.add_log_entry("astrometricslib", "test", "INFO", f"line {number}", job_id="job-a")
    store.add_log_entry("astrometricslib", "test", "ERROR", "x" * 2000, job_id="job-b")
    jobs = Jobs(SimpleNamespace(get_logs_db_path=lambda: str(path)), None)
    targets = MagicMock()
    targets.get.return_value = None
    return JobHistoryService(SimpleNamespace(jobs=jobs, targets=targets))


def test_a_summary_list_is_short_and_capped(service: JobHistoryService) -> None:
    """A list cuts long messages, leaves out the stored numbers and pages."""
    answer = service.query(limit=2)
    assert [job["id"] for job in answer.jobs] == ["calibration-job", "job-c"]
    assert (answer.count, answer.offset, answer.truncated) == (2, 0, True)
    assert answer.jobs[0]["output_metrics"] is None
    assert answer.note
    short = service.query(job_id="job-b").job["message"]
    assert len(short) == 500 and short.endswith("…")


def test_a_full_list_keeps_the_whole_records(service: JobHistoryService) -> None:
    """``detail="full"`` gives the stored numbers and whole messages."""
    answer = service.query(detail="full", target_id="M 13")
    assert {job["id"] for job in answer.jobs} == {"job-a", "job-b"}
    assert all(job["output_metrics"]["stars"] == 42 for job in answer.jobs)


def test_a_job_with_no_stored_target_can_still_be_listed(service: JobHistoryService) -> None:
    """A target id that names no target, such as calibration, still filters."""
    assert [job["id"] for job in service.query(target_id="calibration").jobs] == ["calibration-job"]


def test_the_log_tail_is_cut_unless_asked_whole(service: JobHistoryService) -> None:
    """Lines are cut to 500 characters by default, and whole with `None`."""
    tail = service.query(job_id="job-a", detail="log_tail", lines=3)
    assert [line["text"] for line in tail.log_lines] == ["line 27", "line 28", "line 29"]
    assert (tail.log_lines_total, tail.log_lines_shown) == (30, 3)
    assert len(service.query(job_id="job-b", detail="log_tail").log_lines[0]["text"]) == 500
    whole = service.query(job_id="job-b", detail="log_tail", max_line_chars=None)
    assert whole.log_lines[0]["text"] == "x" * 2000


def test_the_result_gives_the_stored_numbers_and_defaults(service: JobHistoryService) -> None:
    """``detail="result"`` gives the stored numbers and the defaults used."""
    answer = service.query(job_id="job-a", detail="result")
    assert "stars" in answer.output_metrics
    assert answer.parameter_defaults == ["a.value: using the default 1"]


def test_lineage_of_a_target_the_library_does_not_hold_is_empty(service: JobHistoryService) -> None:
    """A target id that names no target has no runs."""
    answer = service.query(job_id="job-a", detail="lineage")
    assert (answer.target_id, answer.runs_total, answer.runs) == ("M 13", 0, [])


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"job_id": "missing"}, NotFoundError, "No job with id"),
        ({"detail": "everything"}, InvalidArgumentError, "detail must be one of"),
        ({"detail": "log_tail"}, InvalidArgumentError, "needs a job_id"),
        ({"detail": "warnings"}, InvalidArgumentError, "needs a job_id"),
        ({"detail": "lineage"}, InvalidArgumentError, "needs a target_id"),
        ({"detail": "lineage", "job_id": "missing"}, InvalidArgumentError, "needs a target_id"),
        ({"limit": 501}, InvalidArgumentError, "limit"),
        ({"lines": 0}, InvalidArgumentError, "lines"),
    ],
)
def test_problems_raise_an_error_category(
    service: JobHistoryService, arguments: dict, error: type, message: str
) -> None:
    """A bad request raises the matching error category and a clear message."""
    with pytest.raises(error, match=message):
        service.query(**arguments)
