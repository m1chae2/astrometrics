"""Purpose: Unit tests for job registration in `ProcessingPipelines.stack`.

Description: A stacking run triggered from a script (as opposed to the
backend's own independent job tracking) must show up in the UI. Verifies the
job is recorded and closed out correctly on the success, no-output and error
paths.
"""

from collections.abc import Generator, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks


@pytest.fixture
def isolated_job_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
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
    from astrometricslib.foundation.jobs.store import JobStore

    return JobStore(logs_database_path).get_jobs_by_target(target_id)


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


def test_a_successful_stack_records_a_completed_job(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a successful stack is recorded as a completed job."""
    monkeypatch.setattr(stacking_tasks, "stack_frames", lambda *args, **kwargs: "/fake/stack.fits")
    result = _pipelines().stack(_target("StackJobTarget"))

    assert result.stacked_path == "/fake/stack.fits"
    jobs = _read_jobs(isolated_job_logging, "StackJobTarget")
    assert len(jobs) == 1
    assert jobs[0].job_type == "stacking"
    assert jobs[0].status == "completed"
    assert jobs[0].progress_current == 100


def test_a_stack_with_no_output_records_a_failed_job(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_a_stacking_error_records_a_failed_job_and_still_raises(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a stacking exception is recorded but never swallowed."""

    def _explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("siril blew up")

    monkeypatch.setattr(stacking_tasks, "stack_frames", _explode)
    with pytest.raises(RuntimeError, match="siril blew up"):
        _pipelines().stack(_target("StackErrorTarget"))

    jobs = _read_jobs(isolated_job_logging, "StackErrorTarget")
    assert len(jobs) == 1
    assert jobs[0].status == "failed"


def test_a_preview_remake_is_listed_while_it_waits_for_a_slot(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a remake shows a job row, with its id, before a slot frees."""
    import threading
    import time
    from contextlib import contextmanager

    from astrometricslib.foundation.jobs.runner import WAITING_FOR_SLOT_MESSAGE
    from astrometricslib.pipelines.stacking.post_processing import preview_remake

    slot_is_free = threading.Event()

    @contextmanager
    def busy_slot() -> Generator[None]:
        """Stand in for a job slot that stays held until the test frees it."""
        assert slot_is_free.wait(timeout=20.0)
        yield

    remade = MagicMock(shown_in_viewer=False)
    monkeypatch.setattr(preview_remake, "remake_stack_preview", lambda *args, **kwargs: remade)
    pipelines = _pipelines()
    monkeypatch.setattr(pipelines, "acquire_job_slot", busy_slot)
    monkeypatch.setattr(pipelines, "_resolve", lambda target: _target("PreviewWaitTarget"))

    worker = threading.Thread(target=lambda: pipelines.remake_preview("PreviewWaitTarget"), daemon=True)
    worker.start()
    # The job is registered before the grace period ends, but its waiting
    # message is written by the worker thread, which a slow runner may not
    # have reached yet, so the message is waited for.
    for _ in range(200):
        jobs = _read_jobs(isolated_job_logging, "PreviewWaitTarget")
        if jobs and jobs[0].message == WAITING_FOR_SLOT_MESSAGE:
            break
        time.sleep(0.05)
    assert [job.job_type for job in jobs] == ["preview"]
    assert jobs[0].message == WAITING_FOR_SLOT_MESSAGE

    slot_is_free.set()
    for _ in range(200):
        if _read_jobs(isolated_job_logging, "PreviewWaitTarget")[0].status == "completed":
            break
        time.sleep(0.05)
    assert _read_jobs(isolated_job_logging, "PreviewWaitTarget")[0].status == "completed"


def test_a_finished_preview_remake_has_a_final_message_and_a_completion_time(
    isolated_job_logging: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finished remake has a final message and a completion time."""
    from contextlib import nullcontext

    from astrometricslib.pipelines.stacking.post_processing import preview_remake

    remade = MagicMock(shown_in_viewer=False)
    monkeypatch.setattr(preview_remake, "remake_stack_preview", lambda *args, **kwargs: remade)
    pipelines = _pipelines()
    monkeypatch.setattr(pipelines, "acquire_job_slot", nullcontext)
    monkeypatch.setattr(pipelines, "_resolve", lambda target: _target("PreviewDoneTarget"))

    pipelines.remake_preview("PreviewDoneTarget")

    job = _read_jobs(isolated_job_logging, "PreviewDoneTarget")[0]
    assert job.status == "completed"
    assert job.message == "Preview made"
    assert job.completed_at is not None
