"""Tests for the read-only job history interface.

`Jobs` reads the logs database through the owners in
`pipelines.shared.job_history`. These tests build a small database, check
each method's answer and its limits, and check that nothing the methods do
can change the database.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers.provenance_store import ProvenanceStore
from astrometricslib.api.jobs import Jobs
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.store import JobStore
from astrometricslib.models.provenance import Activity
from astrometricslib.models.target import Target


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
    store = JobStore(str(path))
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


def test_query_lists_every_job_newest_first_with_progress(jobs: Jobs) -> None:
    """With no filter every job is listed, newest first, with progress."""
    page = jobs.query()
    assert [job.id for job in page] == ["job-d", "job-c", "job-b", "job-a"]
    assert page.total == 4
    assert page.next_offset is None
    assert (page[0].progress_done, page[0].progress_total) == (3, 10)
    assert page[0].output_metrics is None


def test_metrics_come_with_include_and_with_get(jobs: Jobs) -> None:
    """The stored numbers are a heavy part of a list and part of `get`."""
    listed = jobs.query(target_id="M 13", include=["metrics"])
    assert all(job.output_metrics == {"stars": 42} for job in listed)
    one = jobs.get("job-b")
    assert one.output_metrics == {"stars": 42}
    assert one.message == "Plate solve failed " * 100
    with pytest.raises(InvalidArgumentError):
        jobs.query(include=["everything"])


def test_get_of_an_unknown_job(jobs: Jobs) -> None:
    """An unknown id gives `None`, or raises with ``required``."""
    assert jobs.get("missing") is None
    with pytest.raises(NotFoundError, match="No job with id"):
        jobs.get("missing", required=True)


def test_until_pages_back_through_the_history(jobs: Jobs) -> None:
    """``until`` gives the jobs created before a job of the page before."""
    first = jobs.query(target_id="M 13", limit=1)
    second = jobs.query(target_id="M 13", limit=1, until=first[-1].created_at)
    assert [first[0].id, second[0].id] == ["job-b", "job-a"]
    assert len(jobs.query(until=second[0].created_at)) == 0


def test_filters_by_target_type_and_status(jobs: Jobs) -> None:
    """Each filter narrows the list, and they combine."""
    assert {job.id for job in jobs.query(target_id="M 13")} == {"job-a", "job-b"}
    assert {job.id for job in jobs.query(job_type="stacking")} == {"job-a", "job-c"}
    assert [job.id for job in jobs.query(status="failed")] == ["job-b"]
    assert [job.id for job in jobs.query(target_id="M 57", job_type="analysis")] == ["job-d"]


def test_active_returns_running_jobs(jobs: Jobs) -> None:
    """Only jobs that have not finished are listed."""
    assert [job.id for job in jobs.query(active=True)] == ["job-c"]


def test_an_unfinished_job_that_stopped_updating_looks_stale(database: Path, jobs: Jobs) -> None:
    """A row left at running since 2026-10-01 is shown as stale, not active."""
    with sqlite3.connect(database) as connection:  # the store stamps now on every write
        connection.execute("UPDATE processing_jobs SET updated_at = '2026-10-01T00:03:00' WHERE id = 'job-c'")
    stale = jobs.query(active=True)[0]
    assert stale.looks_stale is True
    assert stale.is_active is False
    assert stale.idle_min > 60


def test_a_job_updated_just_now_is_active(tmp_path: Path) -> None:
    """A running job with a fresh update is not marked stale."""
    path = tmp_path / "fresh.db"
    fresh = _job("job-f", "M 57", "stacking", "running", 0)
    fresh.created_at = fresh.updated_at = datetime.now().isoformat()
    JobStore(str(path)).upsert_job(fresh)
    job = Jobs(SimpleNamespace(get_logs_db_path=lambda: str(path)), None).query(active=True)[0]
    assert job.is_active is True
    assert job.looks_stale is False


def test_limit_and_offset_page_through_the_jobs(jobs: Jobs) -> None:
    """A page says how many match and where the next page starts."""
    every = [job.id for job in jobs.query()]
    first = jobs.query(limit=2)
    second = jobs.query(limit=2, offset=2)
    assert [job.id for job in first] + [job.id for job in second] == every
    assert (first.total, first.next_offset) == (4, 2)
    assert second.next_offset is None


@pytest.mark.parametrize("bad", [{"limit": 0}, {"offset": -1}])
def test_a_count_out_of_range_is_refused(jobs: Jobs, bad: dict) -> None:
    """A limit below 1 or a negative offset raises."""
    with pytest.raises(InvalidArgumentError):
        jobs.query(**bad)


def test_get_log_returns_the_last_lines_whole_and_oldest_first(jobs: Jobs) -> None:
    """The log holds the last lines in order, uncut, and the total count."""
    log = jobs.get_log(jobs.get("job-a"), lines=3)
    assert [line.text for line in log.lines] == ["line 297", "line 298", "line 299"]
    assert log.total == 300
    assert jobs.get_log(jobs.get("job-b")).lines[0].text == "x" * 2000
    with pytest.raises(InvalidArgumentError):
        jobs.get_log(jobs.get("job-a"), lines=0)


def test_get_lineage_lists_the_runs_of_a_target(database: Path, jobs: Jobs) -> None:
    """Lineage comes from the target's provenance records, newest first."""
    store = ProvenanceStore(str(database))
    for number, name in enumerate(("stacking", "astrometry")):
        store.record_activity(
            Activity(id=f"run-{number}", name=name, start_time=datetime(2026, 10, 1, number, tzinfo=UTC)),
            "M 13",
        )
    runs = jobs.get_lineage(Target(id="M 13"))
    assert runs.total == 2
    assert [run.name for run in runs] == ["astrometry", "stacking"]
    assert len(jobs.get_lineage(Target(id="M 57"))) == 0


def test_missing_database_is_an_empty_list_and_is_not_created(tmp_path: Path) -> None:
    """With no logs database, no jobs are listed and no file appears."""
    path = tmp_path / "absent.db"
    jobs = Jobs(SimpleNamespace(get_logs_db_path=lambda: str(path)), None)
    assert len(jobs.query()) == 0
    assert jobs.get("job-a") is None
    assert len(jobs.get_lineage(Target(id="M 13"))) == 0
    assert not path.exists()


def test_reads_do_not_change_the_database(database: Path, jobs: Jobs) -> None:
    """Every read leaves the database file exactly as it was."""
    before = database.read_bytes()
    jobs.query(include=["metrics"])
    job = jobs.get("job-a")
    jobs.get_log(job)
    jobs.get_lineage(Target(id="M 13"))
    assert database.read_bytes() == before


def test_read_only_store_cannot_write(database: Path) -> None:
    """A read-only job store refuses a write with an error."""
    store = JobStore(str(database), read_only=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store.upsert_job(_job("job-z", "M 1", "stacking", "completed", 9))
    assert store.get_job("job-z") is None
