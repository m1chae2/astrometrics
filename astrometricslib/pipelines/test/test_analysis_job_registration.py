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
from collections.abc import Generator
from pathlib import Path

import pytest

from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConflictError
from astrometricslib.models.target import Target
from astrometricslib.pipelines import tasks

PACKAGE_LOGGER_NAME = "astrometricslib"


@pytest.fixture
def isolated_job_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
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
    from astrometricslib.foundation.jobs.store import JobStore

    return JobStore(logs_database_path).get_jobs_by_target(target_id)


def _package_logger_handler_count() -> int:
    """Count handlers currently attached to the shared package logger.

    Returns
    -------
    count : `int`
        How many handlers are attached right now.
    """
    return len(logging.getLogger(PACKAGE_LOGGER_NAME).handlers)


def test_a_successful_run_records_a_completed_job(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a job row is written and ends up marked completed."""
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"})
    target = Target(id="JobSuccessTarget")

    result = tasks.analyze_target(target, pipeline_type="astrometry", path="unused.fits")

    assert result == {"status": "ok"}
    jobs = _read_jobs(isolated_job_logging, "JobSuccessTarget")
    assert len(jobs) == 1
    assert jobs[0].job_type == "analysis"
    assert jobs[0].status == "completed"
    assert jobs[0].progress_current == 100


def test_a_failing_run_records_a_failed_job_and_still_raises(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a pipeline error is recorded but never swallowed.

    The caller must still see the exception -- job bookkeeping is not
    allowed to turn a failure into a silent success.
    """

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", _explode)
    target = Target(id="JobFailureTarget")

    with pytest.raises(RuntimeError, match="pipeline blew up"):
        tasks.analyze_target(target, pipeline_type="astrometry", path="unused.fits")

    jobs = _read_jobs(isolated_job_logging, "JobFailureTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
    assert jobs[0].progress_current == 0


def test_register_job_false_records_nothing(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the opt-out really opts out.

    The backend's analysis orchestrator relies on this: it already
    tracks its own job for a UI-triggered run, so without this opt-out
    `analyze_target` would register a second, redundant job for the
    same run.
    """
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"})
    target = Target(id="NoJobTarget")

    tasks.analyze_target(target, pipeline_type="astrometry", path="unused.fits", register_job=False)

    assert _read_jobs(isolated_job_logging, "NoJobTarget") == []


def test_log_handlers_are_detached_after_a_successful_run(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the shared package logger is left exactly as it was found.

    Every run attaches its own handler instances to the shared
    "astrometricslib" logger. If they are not removed, this job's log
    file and database rows keep receiving every later job's messages.
    """
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"})
    handlers_before = _package_logger_handler_count()

    tasks.analyze_target(Target(id="HandlerCleanupTarget"), pipeline_type="astrometry", path="unused.fits")

    assert _package_logger_handler_count() == handlers_before


def test_log_handlers_are_detached_even_when_the_run_fails(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify cleanup happens on the error path too, not just on success."""

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", _explode)
    handlers_before = _package_logger_handler_count()

    with pytest.raises(RuntimeError):
        tasks.analyze_target(
            Target(id="HandlerCleanupOnFailureTarget"),
            pipeline_type="astrometry",
            path="unused.fits",
        )

    assert _package_logger_handler_count() == handlers_before


def test_no_job_logger_or_sink_is_left_behind(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an analysis run leaves no per-job logger and no registered sink.

    Python keeps every logger it has ever been asked for, so a logger made
    per job, holding a file handler, would leak one open file per run. The
    job runner makes no such logger; it registers the job's log sinks with
    the job log router and removes and closes them when the job ends.
    """
    from astrometricslib.foundation.logging import get_job_log_router

    router = get_job_log_router()
    sinks_during_run: list[int] = []

    def _capture(*args: object, **kwargs: object) -> object:
        sinks_during_run.append(len(router._sinks))
        return {"status": "ok"}

    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", _capture)
    sinks_before = len(router._sinks)

    tasks.analyze_target(Target(id="JobLoggerCleanupTarget"), pipeline_type="astrometry", path="unused.fits")

    assert sinks_during_run == [sinks_before + 1]
    assert len(router._sinks) == sinks_before
    assert not [name for name in logging.root.manager.loggerDict if name.startswith("job_")]


def test_a_broken_logs_database_does_not_stop_the_analysis(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify bookkeeping failures are logged and swallowed, not raised.

    Recording a job is a convenience for the user interface. If the logs
    database cannot be opened, the science work should still run and
    still return its result.
    """
    from astrometricslib.foundation.jobs import runner

    def _unopenable(*args: object, **kwargs: object) -> object:
        raise OSError("logs database unavailable")

    monkeypatch.setattr(runner, "JobStore", _unopenable)
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", lambda *args, **kwargs: {"status": "ok"})

    result = tasks.analyze_target(
        Target(id="BrokenLogDbTarget"), pipeline_type="astrometry", path="unused.fits"
    )

    assert result == {"status": "ok"}


def test_a_missing_image_path_fails_the_job_before_running_anything(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the pre-flight path check marks the job failed, not just raises.

    A target with no frames and no stacked image cannot be analyzed. The
    job row must still be closed out as failed rather than left sitting
    at "started" forever.
    """
    called = []
    monkeypatch.setattr(
        tasks,
        "_run_analysis_pipeline_match",
        lambda *args, **kwargs: called.append(1) or {"status": "ok"},
    )

    with pytest.raises(ConflictError, match="No frames or stacked image available"):
        tasks.analyze_target(Target(id="NoImageTarget"), pipeline_type="astrometry")

    assert called == []
    jobs = _read_jobs(isolated_job_logging, "NoImageTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
