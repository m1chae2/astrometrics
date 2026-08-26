"""Tests for the job bookkeeping that wraps every analysis run.

When `analyze_target` runs, it records a job in the logs database so the
job shows up in the user interface, and it attaches log handlers so the
messages the pipeline prints along the way land in that job's log file.
That bookkeeping had no tests at all, even though it is the part most
likely to break quietly: if a handler is left attached, this job's log
file keeps collecting log lines from every future job too.

These tests stub out the actual science work (`_run_analysis_pipeline_match`)
on purpose. The point is to check the bookkeeping around the science, not
the science itself, so the tests stay fast and only fail for one reason.
"""

import logging

import pytest

from astrometricslib.models.target import Target
from astrometricslib.tasks.target_tasks import pipeline_tasks
from astrometricslib.utilities import config_loader
from astrometricslib.utilities.config_loader import AppConfiguration

PACKAGE_LOGGER_NAME = "astrometricslib"


@pytest.fixture
def isolated_job_logging(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Point the job database and job log files at a throwaway folder.

    Two separate redirections are needed. The logs *database* follows the
    configured library path, so pointing the library at `tmp_path` moves
    it. The log *files* do not: `get_logs_path` resolves against the
    project root, so it has to be overridden separately or the tests would
    scatter real log files into the repository.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway logs database the test should read back.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(config, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: config)

    yield config.get_logs_db_path()


def _read_jobs(logs_database_path: str, target_id: str) -> list:
    """Read back every job recorded for one target.

    Returns
    -------
    jobs : `list`
        The stored `ProcessingJob` records, newest first.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    return LoggerInterface(logs_database_path).get_jobs_by_target(target_id)


def _package_logger_handler_count() -> int:
    """Count handlers currently attached to the shared package logger.

    Returns
    -------
    count : `int`
        How many handlers are attached right now.
    """
    return len(logging.getLogger(PACKAGE_LOGGER_NAME).handlers)


def test_a_successful_run_records_a_completed_job(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a job row is written and ends up marked completed."""
    monkeypatch.setattr(
        pipeline_tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"}
    )
    target = Target(id="JobSuccessTarget")

    result = pipeline_tasks.analyze_target(target, pipeline_type="astrometry", path="unused.fits")

    assert result == {"status": "ok"}
    jobs = _read_jobs(isolated_job_logging, "JobSuccessTarget")
    assert len(jobs) == 1
    assert jobs[0].job_type == "analysis"
    assert jobs[0].status == "completed"
    assert jobs[0].progress_current == 100


def test_a_failing_run_records_a_failed_job_and_still_raises(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a pipeline error is recorded but never swallowed.

    The caller must still see the exception -- job bookkeeping is not
    allowed to turn a failure into a silent success.
    """

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(pipeline_tasks, "_run_analysis_pipeline_match", _explode)
    target = Target(id="JobFailureTarget")

    with pytest.raises(RuntimeError, match="pipeline blew up"):
        pipeline_tasks.analyze_target(target, pipeline_type="astrometry", path="unused.fits")

    jobs = _read_jobs(isolated_job_logging, "JobFailureTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].progress_current == 0


def test_register_job_false_records_nothing(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the opt-out really opts out.

    `stack_and_solve` relies on this when it calls `analyze_target`
    itself: without it, one user action would produce two unrelated
    "started" rows in the job list.
    """
    monkeypatch.setattr(
        pipeline_tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"}
    )
    target = Target(id="NoJobTarget")

    pipeline_tasks.analyze_target(target, pipeline_type="astrometry", path="unused.fits", register_job=False)

    assert _read_jobs(isolated_job_logging, "NoJobTarget") == []


def test_log_handlers_are_detached_after_a_successful_run(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the shared package logger is left exactly as it was found.

    Every run attaches its own handler instances to the shared
    "astrometricslib" logger. If they are not removed, this job's log
    file and database rows keep receiving every later job's messages.
    """
    monkeypatch.setattr(
        pipeline_tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"}
    )
    handlers_before = _package_logger_handler_count()

    pipeline_tasks.analyze_target(
        Target(id="HandlerCleanupTarget"), pipeline_type="astrometry", path="unused.fits"
    )

    assert _package_logger_handler_count() == handlers_before


def test_log_handlers_are_detached_even_when_the_run_fails(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify cleanup happens on the error path too, not just on success."""

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(pipeline_tasks, "_run_analysis_pipeline_match", _explode)
    handlers_before = _package_logger_handler_count()

    with pytest.raises(RuntimeError):
        pipeline_tasks.analyze_target(
            Target(id="HandlerCleanupOnFailureTarget"),
            pipeline_type="astrometry",
            path="unused.fits",
        )

    assert _package_logger_handler_count() == handlers_before


def test_the_per_job_logger_is_left_clean(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the throwaway per-job logger does not keep our handlers.

    Each run makes a logger named ``job_<uuid>`` and hangs a file handler
    on it. Python keeps every logger it has ever been asked for, so if the
    handler is never removed and closed, each run leaves behind one more
    logger holding one more open file. Over a long batch run that is a
    real file-handle leak.

    Only the handlers this library attaches are checked. pytest adds its
    own capture handler to these loggers as part of running the test, and
    that one is not ours to remove.
    """
    from astrometricslib.drivers.logger_interface import DbLogHandler

    our_handler_types = (logging.FileHandler, DbLogHandler)
    captured_job_ids: list[str] = []

    def _capture(*args: object, **kwargs: object) -> object:
        captured_job_ids.extend(
            name.removeprefix("job_") for name in logging.root.manager.loggerDict if name.startswith("job_")
        )
        return {"status": "ok"}

    monkeypatch.setattr(pipeline_tasks, "_run_analysis_pipeline_match", _capture)

    pipeline_tasks.analyze_target(
        Target(id="JobLoggerCleanupTarget"), pipeline_type="astrometry", path="unused.fits"
    )

    assert captured_job_ids, "no per-job logger was created, so this test proves nothing"
    leftover = {
        job_id: [
            handler
            for handler in logging.getLogger(f"job_{job_id}").handlers
            if isinstance(handler, our_handler_types)
        ]
        for job_id in captured_job_ids
    }
    assert not any(leftover.values()), f"job loggers kept our handlers: {leftover}"


def test_a_broken_logs_database_does_not_stop_the_analysis(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify bookkeeping failures are logged and swallowed, not raised.

    Recording a job is a convenience for the user interface. If the logs
    database cannot be opened, the science work should still run and
    still return its result.
    """
    import astrometricslib.drivers.logger_interface as logger_interface_module

    def _unopenable(*args: object, **kwargs: object) -> object:
        raise OSError("logs database unavailable")

    monkeypatch.setattr(logger_interface_module, "LoggerInterface", _unopenable)
    monkeypatch.setattr(
        pipeline_tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"}
    )

    result = pipeline_tasks.analyze_target(
        Target(id="BrokenLogDbTarget"), pipeline_type="astrometry", path="unused.fits"
    )

    assert result == {"status": "ok"}


def test_a_missing_image_path_fails_the_job_before_running_anything(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the pre-flight path check marks the job failed, not just raises.

    A target with no frames and no stacked image cannot be analyzed. The
    job row must still be closed out as failed rather than left sitting
    at "started" forever.
    """
    called = []
    monkeypatch.setattr(
        pipeline_tasks,
        "_run_analysis_pipeline_match",
        lambda *args, **kwargs: called.append(1) or {"status": "ok"},
    )

    with pytest.raises(ValueError, match="No frames or stacked image available"):
        pipeline_tasks.analyze_target(Target(id="NoImageTarget"), pipeline_type="astrometry")

    assert called == []
    jobs = _read_jobs(isolated_job_logging, "NoImageTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
