"""Tests for how JobStore handles database errors.

The read methods catch only `sqlite3.Error`. A database problem is logged
with its traceback and the method returns an empty result. Any other
error is a bug and must reach the caller.
"""

import logging
import sqlite3
from pathlib import Path

import pytest

from astrometricslib.foundation.jobs.store import JobStore


def test_database_error_is_logged_and_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken database gives an empty list and a logged traceback."""
    interface = JobStore(str(tmp_path / "log.db"))

    def broken_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        """Stand in for a database that cannot be opened.

        Raises
        ------
        sqlite3.OperationalError
            Always.
        """
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(interface, "_connect", broken_connect)

    with caplog.at_level(logging.ERROR, logger="astrometricslib.foundation.jobs.store"):
        jobs = interface.get_recent_jobs()

    assert jobs == []
    assert "Error retrieving recent jobs" in caplog.text
    assert caplog.records[-1].exc_info is not None


def test_non_database_error_reaches_the_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An error that is not a database error is not hidden."""
    interface = JobStore(str(tmp_path / "log.db"))

    def buggy_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        """Stand in for a programming error inside the method.

        Raises
        ------
        TypeError
            Always.
        """
        raise TypeError("bug")

    monkeypatch.setattr(interface, "_connect", buggy_connect)

    with pytest.raises(TypeError):
        interface.get_recent_jobs()


@pytest.mark.parametrize("store_count", [2, 8])
def test_stores_opened_together_on_a_new_database_all_succeed(tmp_path: Path, store_count: int) -> None:
    """Jobs starting together each set up and write a new logs database.

    Before the setup ran once under a lock, with the write lock taken up
    front, two first-time setups at the same moment made SQLite answer one of
    them "database is locked" at once, and that job lost its record and log
    file (about one start in four with two threads).
    """
    import threading

    from astrometricslib.foundation.jobs.models import ProcessingJob

    for trial in range(20):
        database_path = str(tmp_path / f"log_{trial}.db")
        barrier = threading.Barrier(store_count)
        errors: list[Exception] = []

        def start_job(
            index: int,
            path: str = database_path,
            gate: threading.Barrier = barrier,
            found: list[Exception] = errors,
        ) -> None:
            """Open a store on the shared new database and record one job."""
            gate.wait(timeout=10)
            try:
                JobStore(path).upsert_job(
                    ProcessingJob(id=f"job-{index}", target_id="Vega", job_type="stacking", status="started")
                )
            except sqlite3.Error as error:
                found.append(error)

        threads = [threading.Thread(target=start_job, args=(index,)) for index in range(store_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert len(JobStore(database_path).get_recent_jobs()) == store_count
