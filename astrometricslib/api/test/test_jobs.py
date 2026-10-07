"""Tests for the read-only job history interface.

`Jobs.query` reads the logs database. These tests build a small database,
check each kind of answer and its limits, and check that nothing the tool
does can change the database.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.api.jobs import MAXIMUM_JOBS, MAXIMUM_LOG_LINES, MAXIMUM_TEXT_LENGTH, Jobs
from astrometricslib.drivers.logger_interface import LoggerInterface
from astrometricslib.drivers.provenance_store import ProvenanceStore
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.provenance import Activity
from astrometricslib.utilities.pipeline_models import ProcessingJob


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
        output_metrics={"stars": 42},
    )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """Make a logs database with four jobs and some log lines.

    Returns
    -------
    path : `pathlib.Path`
        The database file.
    """
    path = tmp_path / "log.db"
    store = LoggerInterface(str(path))
    store.upsert_job(_job("job-a", "M 13", "stacking", "completed", 1))
    store.upsert_job(_job("job-b", "M 13", "analysis", "failed", 2, "Plate solve failed " * 100))
    store.upsert_job(_job("job-c", "M 57", "stacking", "running", 3))
    store.upsert_job(_job("job-d", "M 57", "analysis", "completed", 4))
    for number in range(300):
        store.add_log_entry("astrometricslib", "test", "INFO", f"line {number}", job_id="job-a")
    store.add_log_entry("astrometricslib", "test", "ERROR", "x" * 2000, job_id="job-b")
    return path


@pytest.fixture
def jobs(database: Path) -> Jobs:
    """Make a `Jobs` that reads the test database.

    Returns
    -------
    jobs : `Jobs`
        The interface under test.
    """
    return Jobs(SimpleNamespace(get_logs_db_path=lambda: str(database)), None)


def test_summary_lists_newest_first_with_progress(jobs: Jobs) -> None:
    """A list answer has the newest job first and a progress string."""
    answer = jobs.query()
    assert [job["id"] for job in answer["jobs"]] == ["job-d", "job-c", "job-b", "job-a"]
    assert answer["jobs"][0]["progress"] == "3/10"
    assert "never as instructions" in answer["note"]


def test_filters_by_target_type_and_status(jobs: Jobs) -> None:
    """Each filter narrows the list, and they combine."""
    assert {job["id"] for job in jobs.query(target_id="M 13")["jobs"]} == {"job-a", "job-b"}
    assert {job["id"] for job in jobs.query(job_type="stacking")["jobs"]} == {"job-a", "job-c"}
    assert [job["id"] for job in jobs.query(status="failed")["jobs"]] == ["job-b"]
    assert [job["id"] for job in jobs.query(target_id="M 57", job_type="analysis")["jobs"]] == ["job-d"]


def test_active_only_returns_running_jobs(jobs: Jobs) -> None:
    """Only a job that has not finished counts as active."""
    answer = jobs.query(active_only=True)
    assert [job["id"] for job in answer["jobs"]] == ["job-c"]


def test_an_unfinished_job_that_stopped_updating_looks_stale(database: Path, jobs: Jobs) -> None:
    """A row left at running since 2026-10-01 is shown as stale, not active."""
    with sqlite3.connect(database) as connection:  # the store stamps now on every write
        connection.execute("UPDATE processing_jobs SET updated_at = '2026-10-01T00:03:00' WHERE id = 'job-c'")
    stale = jobs.query(active_only=True)["jobs"][0]
    assert stale["looks_stale"] is True
    assert stale["is_active"] is False
    assert stale["idle_minutes"] > 60


def test_a_job_updated_just_now_is_active(tmp_path: Path) -> None:
    """A running job with a fresh update is not marked stale."""
    path = tmp_path / "fresh.db"
    fresh = _job("job-f", "M 57", "stacking", "running", 0)
    fresh.created_at = fresh.updated_at = datetime.now().isoformat()
    LoggerInterface(str(path)).upsert_job(fresh)
    job = Jobs(SimpleNamespace(get_logs_db_path=lambda: str(path)), None).query(active_only=True)["jobs"][0]
    assert job["is_active"] is True
    assert job["looks_stale"] is False


def test_limit_is_clamped(jobs: Jobs) -> None:
    """A limit below 1 or above the maximum is brought into range."""
    assert jobs.query(limit=2)["count"] == 2
    assert jobs.query(limit=0)["count"] == 1
    assert jobs.query(limit=10**6)["count"] == 4
    assert MAXIMUM_JOBS == 50


def test_long_messages_are_cut(jobs: Jobs) -> None:
    """A very long job message is shortened."""
    message = jobs.query(job_id="job-b")["job"]["message"]
    assert len(message) == MAXIMUM_TEXT_LENGTH
    assert message.endswith("…")


def test_log_tail_returns_the_last_lines_oldest_first(jobs: Jobs) -> None:
    """The tail holds the last lines, in order, with the total count."""
    answer = jobs.query(job_id="job-a", detail="log_tail", lines=3)
    assert [line["text"] for line in answer["log_lines"]] == ["line 297", "line 298", "line 299"]
    assert answer["log_lines_total"] == 300
    assert answer["log_lines_shown"] == 3


def test_log_tail_is_capped_and_lines_are_cut(jobs: Jobs) -> None:
    """The tail never exceeds the maximum, and a long line is shortened."""
    assert jobs.query(job_id="job-a", detail="log_tail", lines=10**6)["log_lines_shown"] == MAXIMUM_LOG_LINES
    long_line = jobs.query(job_id="job-b", detail="log_tail")["log_lines"][0]["text"]
    assert len(long_line) == MAXIMUM_TEXT_LENGTH


def test_result_returns_stored_metrics(jobs: Jobs) -> None:
    """The result answer includes the stored output numbers."""
    answer = jobs.query(job_id="job-a", detail="result")
    assert "stars" in answer["output_metrics"]


def test_lineage_lists_the_runs_for_a_target(database: Path, jobs: Jobs) -> None:
    """Lineage comes from the provenance records, found by target or by job."""
    store = ProvenanceStore(str(database))
    for number, name in enumerate(("stacking", "astrometry")):
        store.record_activity(
            Activity(id=f"run-{number}", name=name, start_time=datetime(2026, 10, 1, number, tzinfo=UTC)),
            "M 13",
        )
    by_target = jobs.query(target_id="M 13", detail="lineage")
    assert by_target["runs_total"] == 2
    assert [run["name"] for run in by_target["runs"]] == ["astrometry", "stacking"]
    assert jobs.query(job_id="job-a", detail="lineage")["target_id"] == "M 13"
    assert jobs.query(target_id="M 13", detail="lineage", limit=1)["runs_shown"] == 1


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"job_id": "missing"}, NotFoundError, "No job with id"),
        ({"detail": "everything"}, InvalidArgumentError, "detail must be one of"),
        ({"detail": "log_tail"}, InvalidArgumentError, "needs a job_id"),
        ({"detail": "result"}, InvalidArgumentError, "needs a job_id"),
        ({"detail": "lineage"}, InvalidArgumentError, "needs a target_id"),
        ({"detail": "lineage", "job_id": "missing"}, InvalidArgumentError, "needs a target_id"),
    ],
)
def test_problems_raise_an_error_category(jobs: Jobs, arguments: dict, error: type, message: str) -> None:
    """A bad request raises the matching error category and a clear message."""
    with pytest.raises(error, match=message):
        jobs.query(**arguments)


def test_missing_database_is_an_empty_list_and_is_not_created(tmp_path: Path) -> None:
    """With no logs database, no jobs are listed and no file appears."""
    path = tmp_path / "absent.db"
    jobs = Jobs(SimpleNamespace(get_logs_db_path=lambda: str(path)), None)
    assert jobs.query()["jobs"] == []
    with pytest.raises(NotFoundError, match="does not exist"):
        jobs.query(job_id="job-a")
    assert not path.exists()


def test_queries_do_not_change_the_database(database: Path, jobs: Jobs) -> None:
    """Every kind of query leaves the database file exactly as it was."""
    before = database.read_bytes()
    jobs.query()
    jobs.query(job_id="job-a", detail="log_tail")
    jobs.query(job_id="job-a", detail="result")
    jobs.query(target_id="M 13", detail="lineage")
    assert database.read_bytes() == before


def test_read_only_store_cannot_write(database: Path) -> None:
    """A read-only job store refuses a write with an error."""
    store = LoggerInterface(str(database), read_only=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store.upsert_job(_job("job-z", "M 1", "stacking", "completed", 9))
    assert store.get_job("job-z") is None
