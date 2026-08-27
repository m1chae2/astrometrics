"""Tests for the shared job recording and log capture helper.

This helper replaced four hand-written copies of the same block. The
tests below cover the behaviours those copies got wrong: leaving log
handlers attached to the shared logger, never closing the files they
opened, and letting a bookkeeping failure take down the real work.
"""

import logging

import pytest

from astrometricslib.drivers.job_logging import JobHandle, registered_job
from astrometricslib.utilities import config_loader
from astrometricslib.utilities.config_loader import AppConfiguration

PACKAGE_LOGGER_NAME = "astrometricslib"


@pytest.fixture
def isolated_logs(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Send the job database and job log files to a throwaway folder.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway job database.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(configuration, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: configuration)

    yield configuration.get_logs_db_path()


def _our_handlers(logger_name: str) -> list:
    """List the handlers this library attached to a logger.

    pytest attaches its own capture handler while a test runs; that one
    is not ours and is filtered out here.

    Returns
    -------
    handlers : `list`
        Only the file and database handlers this library added.
    """
    from astrometricslib.drivers.logger_interface import DbLogHandler

    return [
        handler
        for handler in logging.getLogger(logger_name).handlers
        if isinstance(handler, logging.FileHandler | DbLogHandler)
    ]


def test_a_disabled_job_does_nothing_at_all():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify switching recording off leaves no trace and still works.

    The handle must still be usable so the wrapped work never has to ask
    whether recording happened.
    """
    handlers_before = list(logging.getLogger(PACKAGE_LOGGER_NAME).handlers)

    with registered_job(enabled=False, job_type="analysis", target_id="Vega") as job:
        job.info("this goes nowhere")
        job.mark("completed", 100)
        assert job.job_id is None
        assert job.log_file_path is None

    assert logging.getLogger(PACKAGE_LOGGER_NAME).handlers == handlers_before


def test_handlers_are_attached_during_the_work_and_gone_afterwards(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the shared logger is listened to only while the work runs."""
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        assert job.job_id is not None
        assert len(_our_handlers(PACKAGE_LOGGER_NAME)) == 2

    assert _our_handlers(PACKAGE_LOGGER_NAME) == []
    assert _our_handlers(f"job_{job.job_id}") == []


def test_the_log_files_are_closed_not_just_detached(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the opened log file is actually closed on the way out.

    Detaching a handler stops it receiving messages but leaves its file
    open. Python never throws a logger away, so an unclosed handler is a
    file handle held for the life of the process. None of the four
    hand-written copies this helper replaced did this.
    """
    captured: list[logging.Handler] = []

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured.extend(
            handler for handler in job.job_logger.handlers if isinstance(handler, logging.FileHandler)
        )

    assert captured, "expected a file handler to have been attached"
    assert all(handler.stream is None or handler.stream.closed for handler in captured)


def test_work_that_raises_marks_the_job_failed_and_reraises(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an error is recorded but never swallowed.

    Raises
    ------
    RuntimeError
        Raised on purpose inside the block, to check it comes back out.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with pytest.raises(RuntimeError, match="work blew up"):
        with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
            captured_job_id = job.job_id
            raise RuntimeError("work blew up")

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.progress_current == 0
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []


def test_work_that_finishes_quietly_is_marked_completed(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no news is good news when the work does not say otherwise."""
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured_job_id = job.job_id

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored.status == "completed"
    assert stored.progress_current == 100


def test_work_that_decides_its_own_outcome_is_believed(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an explicit outcome is not overwritten by the default.

    Stacking can finish without raising and still produce no image, so it
    marks itself failed. That must stick.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as job:
        captured_job_id = job.job_id
        job.mark("failed", 100)

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored.status == "failed"


def test_a_broken_logs_database_degrades_instead_of_raising(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a job we cannot record is still a job worth doing."""
    import astrometricslib.drivers.logger_interface as logger_interface_module

    def _unopenable(*args: object, **kwargs: object) -> object:
        raise OSError("logs database unavailable")

    monkeypatch.setattr(logger_interface_module, "LoggerInterface", _unopenable)
    did_the_work = False

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        did_the_work = True
        job.info("still fine")
        assert job.job_id is None

    assert did_the_work
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []


def test_an_explicit_log_file_is_used_as_given(isolated_logs, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a caller-supplied log path wins over a generated name.

    `stack_and_solve` passes the log file it was already given.
    """
    chosen = str(tmp_path / "chosen.log")

    with registered_job(enabled=True, job_type="stacking", target_id="Vega", log_file=chosen) as job:
        assert job.log_file_path == chosen


def test_a_generated_log_name_says_what_kind_of_job_it_was(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify generated names keep the job type and a safe target name."""
    with registered_job(enabled=True, job_type="analysis", target_id="NGC 7000") as job:
        assert "analysis_NGC_7000_" in job.log_file_path
        assert job.log_file_path.endswith(".log")


def test_a_bare_handle_is_safe_to_use():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the no-op handle tolerates every call without a database."""
    handle = JobHandle()

    handle.info("nothing")
    handle.error("nothing")
    handle.mark("completed", 100)

    assert handle.job_id is None
