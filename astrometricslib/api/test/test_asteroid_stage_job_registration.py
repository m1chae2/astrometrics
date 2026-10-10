"""Purpose: Unit tests for job registration in the asteroid stage.

Description: `ProcessingPipelines.process_target(stages=["asteroids"])` runs
the asteroid search through the same entry point as the other stages. The
run must show up in the job list, closed out correctly on both success and
failure, so a search started from a script appears in the app.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.target import Target
from astrometricslib.pipelines.asteroid_detection.pipeline import AsteroidDetectionPipeline

_ZERO_CANDIDATE_METRICS = {
    "frames_with_wcs_estimate": 0,
    "frames_excluded_missing_pointing_metadata": 0,
    "candidates_detected": 0,
    "candidates_persistence_confirmed": 0,
    "candidates_rate_linearity_confirmed": 0,
    "candidates_ephemeris_matched": 0,
}


@pytest.fixture
def isolated_job_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, AppConfiguration]]:
    """Point the job database and job log files at a throwaway folder.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway logs database the test should read back.
    config : `AppConfiguration`
        The settings that point at the throwaway folder.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(config, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: config)

    yield config.get_logs_db_path(), config


def _read_jobs(logs_database_path: str, target_id: str) -> list:
    """Read back every job recorded for one target.

    Returns
    -------
    jobs : `list`
        The stored `ProcessingJob` records, newest first.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    return JobStore(logs_database_path).get_jobs_by_target(target_id)


def _fake_process_with_zero_candidates(
    self: AsteroidDetectionPipeline, *args: object, **kwargs: object
) -> list:
    """Stand in for `AsteroidDetectionPipeline.process`, finding nothing.

    Returns
    -------
    candidates : `list`
        Always empty.
    """
    self.last_run_metrics = dict(_ZERO_CANDIDATE_METRICS)
    return []


def _pipelines(config: AppConfiguration) -> ProcessingPipelines:
    """Build the processing API on a storage stand-in.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The API, with a mock storage the asteroid stage never touches.
    """
    from unittest.mock import MagicMock

    return ProcessingPipelines(config, MagicMock())


def test_a_successful_run_records_a_completed_job(
    isolated_job_logging: tuple[str, AppConfiguration], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a clean run, even with zero candidates, is recorded completed."""
    logs_database_path, config = isolated_job_logging
    monkeypatch.setattr(AsteroidDetectionPipeline, "process", _fake_process_with_zero_candidates)

    result = _pipelines(config).process_target(Target(id="AsteroidJobTarget"), stages=["asteroids"])

    assert result.stages_run == ["asteroids"]
    jobs = _read_jobs(logs_database_path, "AsteroidJobTarget")
    assert len(jobs) == 1
    # "analysis", the job type every analysis stage records.
    assert jobs[0].job_type == "analysis"
    assert jobs[0].status == "completed"
    assert jobs[0].progress_current == 100


def test_a_failing_run_records_a_failed_job_and_still_raises(
    isolated_job_logging: tuple[str, AppConfiguration], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a pipeline error is recorded but never swallowed."""
    logs_database_path, config = isolated_job_logging

    def _explode(self: AsteroidDetectionPipeline, *args: object, **kwargs: object) -> object:
        """Stand in for a pipeline that fails.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(AsteroidDetectionPipeline, "process", _explode)

    with pytest.raises(RuntimeError, match="pipeline blew up"):
        _pipelines(config).process_target(Target(id="AsteroidJobFailureTarget"), stages=["asteroids"])

    jobs = _read_jobs(logs_database_path, "AsteroidJobFailureTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"
