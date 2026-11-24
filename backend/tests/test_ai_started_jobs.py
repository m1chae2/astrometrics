"""Purpose: Tests that work an AI starts is the app's own job.

Description: The AI's job tools (``mcp_servers/backend/app_jobs.py``) call
the same RPC methods as the app's buttons. These tests check the backend
side: ``processing:stack`` can choose the frames itself and still starts the
one ``stacking`` job the Stack button starts, with its progress and Cancel;
the frame and log syncs record a ``remote_sync`` job and can report a plan;
and the app hears about a job that another program recorded in the shared
job list. The library calls are stand-ins.
"""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.jobs import JobStore, ProcessingJob
from backend import startup
from backend.services.infrastructure import thread_management
from backend.services.infrastructure.astrometrics_service import AstrometricsService
from backend.services.infrastructure.sync_service import SyncService
from backend.services.processing import image_processing_service
from backend.services.processing.image_processing_service import (
    ImageProcessingService,
    start_siril_processing_task,
)
from backend.services.processing.job_service import JobService


class _Plan:
    """A stand-in for the library's `StackResult` of a plan."""

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        """Give the plan as plain data.

        Returns
        -------
        plan : `dict` [`str`, `Any`]
            Twelve frames of one camera.
        """
        return {"target_id": "M 13", "kind": "imaging", "frames_selected": 12, "plan_only": True}


def _stacking_service(tmp_path: Path) -> tuple[ImageProcessingService, MagicMock, JobService]:
    """Build the stacking service with a real job list and a stand-in library.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test folder.

    Returns
    -------
    parts : `tuple`
        The service, the stand-in ``processing`` API and the job service.
    """
    processing = MagicMock()
    processing.stack.return_value = _Plan()
    config = MagicMock()
    config.get_logs_path.return_value = tmp_path
    job_service = JobService(JobStore(str(tmp_path / "jobs.db")))
    service = ImageProcessingService(
        processing_api=processing,
        job_repository=job_service.repository,
        target_service=MagicMock(),
        config_service=config,
        job_service=job_service,
    )
    return service, processing, job_service


def test_a_stack_chosen_by_frame_choice_is_the_stack_buttons_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frame choice is checked with a plan, then one stacking job starts.

    The job is the one the Stack button starts: same type, same row.
    """
    service, processing, job_service = _stacking_service(tmp_path)
    release = threading.Event()
    seen: dict[str, Any] = {}

    def fake_task(job_id: str, target_id: str, image_files: Any, **kwargs: Any) -> str:
        """Record what the worker was given and wait to be released.

        Returns
        -------
        final_path : `str`
            A stack path, so the job counts as completed.
        """
        seen.update(kwargs, image_files=image_files)
        release.wait(timeout=5.0)
        return "/stacks/M_13.fits"

    monkeypatch.setattr(image_processing_service, "start_siril_processing_task", fake_task)

    reply = service.process_target("M 13", kind="imaging", filter_name="L", first_file="013", force=True)

    assert reply["status"] == "started"
    processing.stack.assert_called_once_with(
        "M 13", plan_only=True, register_job=False, kind="imaging", filter_name="L", first_file="013"
    )
    job = job_service.get_job(reply["jobId"])
    assert (job.job_type, job.target_id, job.status) == ("stacking", "M 13", "started")
    assert [active.id for active in job_service.get_active_jobs()] == [reply["jobId"]]

    release.set()
    service._jobs[reply["jobId"]]["future"].result(timeout=5.0)
    assert seen["selection"] == {"kind": "imaging", "filter_name": "L", "first_file": "013"}
    assert seen["stack_options"] == {"force": True}
    assert seen["image_files"] is None
    assert job_service.get_job(reply["jobId"]).status == "completed"


def test_a_stack_plan_starts_no_job(tmp_path: Path) -> None:
    """``plan_only`` answers with the library's plan and records nothing."""
    service, _, job_service = _stacking_service(tmp_path)

    reply = service.process_target("M 13", plan_only=True)

    assert reply == {"status": "plan", "plan": _Plan().model_dump()}
    assert job_service.get_recent_jobs() == []


def test_a_frame_list_and_a_frame_choice_are_refused_together(tmp_path: Path) -> None:
    """The Stack button's list and a frame choice cannot be mixed."""
    service, processing, job_service = _stacking_service(tmp_path)

    with pytest.raises(InvalidArgumentError, match="not both"):
        service.process_target("M 13", image_files=["/lights/a.fits"], filter_name="L")

    processing.stack.assert_not_called()
    assert job_service.get_recent_jobs() == []


def test_a_mixed_frame_list_is_refused_and_starts_no_job(tmp_path: Path) -> None:
    """A refused mix of imaging and spectral frames makes no job."""
    service, processing, job_service = _stacking_service(tmp_path)
    frames = [SimpleNamespace(path="/lights/a.fits"), SimpleNamespace(path="/lights/b.fits")]
    service._target_service.get_targets.return_value = SimpleNamespace(frames=frames)
    processing.stack.side_effect = InvalidArgumentError(
        "These frames mix imaging and spectral frames (1 imaging, 1 spectral). "
        "Select only one kind and stack again."
    )
    with pytest.raises(InvalidArgumentError, match="mix imaging and spectral"):
        service.process_target("M 57", image_files=["/lights/a.fits", "/lights/b.fits"])
    assert job_service.get_recent_jobs() == []


def test_the_apps_cancel_reaches_a_stack_the_ai_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``processing:cancel`` marks it cancelled, as for the Stack button."""
    service, _, job_service = _stacking_service(tmp_path)
    release = threading.Event()
    monkeypatch.setattr(
        image_processing_service,
        "start_siril_processing_task",
        lambda *arguments, **kwargs: release.wait(timeout=5.0),
    )

    reply = service.process_target("M 13", kind="spectral")
    outcome = service.cancel_processing_jobs("M 13")
    release.set()

    assert outcome == {"status": "cancelled", "message": None}
    assert job_service.get_job(reply["jobId"]).status in ("cancelled", "completed")


def test_the_worker_lets_the_library_choose_the_frames() -> None:
    """With a frame choice the library's stack gets the choice, not a list."""
    target_service = MagicMock()
    target_service.astrometrics.processing.stack.return_value = SimpleNamespace(stacked_path="/stacks/x.fits")
    target_service.read_saved_target.return_value = None

    final_path = start_siril_processing_task(
        job_id="job-1",
        target_id="M 13",
        image_files=None,
        target_service=target_service,
        selection={"kind": "spectral", "since": "2026-11-20T00:00:00"},
        stack_options={"denoise": False},
    )

    assert final_path == "/stacks/x.fits"
    call = target_service.astrometrics.processing.stack.call_args
    assert call.kwargs["kind"] == "spectral"
    assert call.kwargs["since"] == "2026-11-20T00:00:00"
    assert call.kwargs["denoise"] is False
    assert call.kwargs["register_job"] is False
    assert "frames" not in call.kwargs


class _Remote:
    """A stand-in `control.remote` that records its calls."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.frame_calls: list[dict[str, Any]] = []
        self.log_calls: list[dict[str, Any]] = []

    def sync_frames(self, target: str, **kwargs: Any) -> SimpleNamespace:
        """Record the call.

        Returns
        -------
        result : `types.SimpleNamespace`
            A plan or a successful sync with no measurements.
        """
        self.frame_calls.append({"target": target, **kwargs})
        return SimpleNamespace(model_dump=lambda mode="python": {"to_transfer": 3}, measurements=None)

    def sync_logs(self, **kwargs: Any) -> SimpleNamespace:
        """Record the call.

        Returns
        -------
        result : `types.SimpleNamespace`
            A plan, or one new guide log and no samples.
        """
        self.log_calls.append(kwargs)
        return SimpleNamespace(
            model_dump=lambda mode="python": {"guide_logs": {"to_download": 1}},
            guide_logs=SimpleNamespace(supported=True, to_download=1),
            ingested={"guide_samples_stored": 0},
        )


def test_a_frame_sync_records_a_remote_sync_job() -> None:
    """The app's sync asks the library to record the copy as a job."""
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))

    assert service.start_sync("M 13") == {"started": True, "target_id": "M 13"}
    thread_management._syncing["M 13"].join(timeout=5.0)

    assert remote.frame_calls[0]["register_job"] is True


def test_a_frame_sync_dry_run_gives_the_plan_and_starts_nothing() -> None:
    """A dry run answers at once with the library's plan."""
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))

    reply = service.start_sync("M 13", dry_run=True)

    assert reply == {"started": False, "target_id": "M 13", "dry_run": True, "plan": {"to_transfer": 3}}
    assert remote.frame_calls == [{"target": "M 13", "dry_run": True}]
    assert not service.is_syncing("M 13")


def test_a_log_sync_records_a_job_and_its_dry_run_does_not() -> None:
    """A real log sync is a job; a dry run only reports the plan."""
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))

    plan = service.sync_telescope_logs(dry_run=True)
    summary = service.sync_telescope_logs()

    assert plan == {"status": "plan", "plan": {"guide_logs": {"to_download": 1}}}
    assert summary["status"] == "success"
    assert remote.log_calls == [{"dry_run": True, "register_job": False}, {"register_job": True}]


def test_the_app_hears_about_a_job_another_program_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row another process wrote reaches the app's running jobs once."""
    store = JobStore(str(tmp_path / "jobs.db"))
    store.upsert_job(
        ProcessingJob(
            id="other-process-job",
            target_id="M 13",
            job_type="diagnostics",
            status="running",
            created_at="2026-11-21T20:00:00",
            updated_at="2026-11-21T20:00:00",
            owner_pid=999999,
        )
    )
    state = AstrometricsService(socket_manager=None)
    sent: list[Any] = []
    monkeypatch.setattr(state, "_broadcast_event", lambda action, payload: sent.append(payload["processing"]))
    fake_container = SimpleNamespace(
        initialized=True, job_service=JobService(store), astrometrics_service=state
    )
    monkeypatch.setattr(startup, "container", fake_container)

    startup.publish_running_jobs()
    startup.publish_running_jobs()

    expected = [{"target_id": "M 13", "job_id": "other-process-job", "status": "running"}]
    assert state.get_state()["processing"] == expected
    assert sent == [expected]
