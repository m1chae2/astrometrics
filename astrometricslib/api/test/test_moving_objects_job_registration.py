"""Purpose: Unit tests for job registration in detect_asteroids.

Description: detect_asteroids previously ran the asteroid-detection
pipeline with no job bookkeeping, so a detection run triggered from a
script never showed up in the UI. Verifies the job is now recorded and
closed out correctly on both success and failure.
"""

import pytest

from astrometricslib.api.moving_objects import MovingObjectRecovery
from astrometricslib.models.target import Target
from astrometricslib.pipelines.asteroid_detection.pipeline import AsteroidDetectionPipeline
from astrometricslib.utilities import config_loader
from astrometricslib.utilities.config_loader import AppConfiguration

_ZERO_CANDIDATE_METRICS = {
    "frames_with_wcs_estimate": 0,
    "frames_excluded_missing_pointing_metadata": 0,
    "candidates_detected": 0,
    "candidates_persistence_confirmed": 0,
    "candidates_rate_linearity_confirmed": 0,
    "candidates_ephemeris_matched": 0,
}


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


def _fake_process_with_zero_candidates(self, *args: object, **kwargs: object) -> list:  # ruff: ignore[missing-type-function-argument]
    """Stand in for `AsteroidDetectionPipeline.process`, finding nothing.

    Returns
    -------
    candidates : `list`
        Always empty.
    """
    self.last_run_metrics = dict(_ZERO_CANDIDATE_METRICS)
    return []


def test_a_successful_run_records_a_completed_job(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a clean run, even with zero candidates, is recorded completed."""
    monkeypatch.setattr(AsteroidDetectionPipeline, "process", _fake_process_with_zero_candidates)
    recovery = MovingObjectRecovery()

    result = recovery.detect_asteroids(Target(id="AsteroidJobTarget"))

    assert result == []
    jobs = _read_jobs(isolated_job_logging, "AsteroidJobTarget")
    assert len(jobs) == 1
    # "analysis", not "asteroid_detection" -- detect_asteroids now goes
    # through analyze_target, the same shared entry point (and job type)
    # every other pipeline uses.
    assert jobs[0].job_type == "analysis"
    assert jobs[0].status == "completed"
    assert jobs[0].progress_current == 100


def test_a_failing_run_records_a_failed_job_and_still_raises(isolated_job_logging, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a pipeline error is recorded but never swallowed."""

    def _explode(self, *args: object, **kwargs: object) -> object:  # ruff: ignore[missing-type-function-argument]
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(AsteroidDetectionPipeline, "process", _explode)
    recovery = MovingObjectRecovery()

    with pytest.raises(RuntimeError, match="pipeline blew up"):
        recovery.detect_asteroids(Target(id="AsteroidJobFailureTarget"))

    jobs = _read_jobs(isolated_job_logging, "AsteroidJobFailureTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
