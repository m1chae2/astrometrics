"""Purpose: Unit tests for job registration in `ProcessingPipelines.stack`.

Description: A stacking run triggered from a script (as opposed to the
backend's own independent job tracking) must show up in the UI. Verifies the
job is recorded and closed out correctly on the success, no-output and error
paths.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks


@pytest.fixture
def isolated_job_logging(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Point the job database and job log files at a throwaway folder.

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


def _target(target_id: str) -> Target:
    """Build a target with one light frame to stack.

    Returns
    -------
    target : `Target`
        The target.
    """
    return Target(id=target_id, frames=[FrameRecord(path="/frames/a.fits", role="LIGHT", camera="Cam")])


def _pipelines() -> ProcessingPipelines:
    """Build the processing API with a stand-in target catalog.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The API. Saving the catalog does nothing.
    """
    return ProcessingPipelines(AppConfiguration(), MagicMock(), targets=MagicMock())


def test_a_successful_stack_records_a_completed_job(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a successful stack is recorded as a completed job."""
    monkeypatch.setattr(stacking_tasks, "stack_frames", lambda *args, **kwargs: "/fake/stack.fits")
    result = _pipelines().stack(_target("StackJobTarget"))

    assert result.stacked_path == "/fake/stack.fits"
    jobs = _read_jobs(isolated_job_logging, "StackJobTarget")
    assert len(jobs) == 1
    assert jobs[0].job_type == "stacking"
    assert jobs[0].status == "completed"
    assert jobs[0].progress_current == 100


def test_a_stack_with_no_output_records_a_failed_job(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a stack that finishes without an output file is marked failed.

    Stacking can return `None` without raising, so this outcome must be
    decided explicitly rather than left to the context manager's
    "no exception means success" default.
    """
    monkeypatch.setattr(stacking_tasks, "stack_frames", lambda *args, **kwargs: None)
    with pytest.raises(ProcessingError, match="without making a stack"):
        _pipelines().stack(_target("StackNoOutputTarget"))

    jobs = _read_jobs(isolated_job_logging, "StackNoOutputTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"


def test_a_stacking_error_records_a_failed_job_and_still_raises(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a stacking exception is recorded but never swallowed."""

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("siril blew up")

    monkeypatch.setattr(stacking_tasks, "stack_frames", _explode)
    with pytest.raises(RuntimeError, match="siril blew up"):
        _pipelines().stack(_target("StackErrorTarget"))

    jobs = _read_jobs(isolated_job_logging, "StackErrorTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
