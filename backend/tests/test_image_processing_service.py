"""Unit tests for the background image processing service and its worker task.

Verifies that `start_siril_processing_task` runs the full stacking stage
(through ``processing.stacking.run``) on the frames the viewer chose, so a
stack made from the viewer gets the same post-processing as a batch stack,
then makes its picture (``processing.enhancement.run``), and that it saves the
target and reports the outcome.
"""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from astrometricslib import AppConfiguration, FrameRecord, ProcessingPipelines, Target
from astrometricslib.exceptions import NotFoundError, ProcessingError
from backend.services.processing.image_processing_service import (
    ImageProcessingService,
    start_siril_processing_task,
)

STAGE = "astrometricslib.pipelines.stacking.stage.stack_frames"


def _target_with_frames(target_id: str, paths: list[str]) -> Target:
    """Make a target that holds a light frame record for each path.

    Returns
    -------
    target : `Target`
        The target with its frames set.
    """
    target = Target(id=target_id)
    target.frames = [FrameRecord(path=path, role="LIGHT") for path in paths]
    return target


def _target_service(target: Target | None) -> MagicMock:
    """Make a target service that stacks through a real `ProcessingPipelines`.

    Returns
    -------
    target_service : `MagicMock`
        A service that finds ``target``. Its ``astrometrics.processing`` is
        a real `ProcessingPipelines` over a mock target catalog, so its
        ``astrometrics.processing._targets._save`` records the save.
    """
    target_service = MagicMock()
    target_service.get_targets.return_value = target
    target_service.astrometrics.processing = ProcessingPipelines(
        AppConfiguration(), MagicMock(), targets=MagicMock(_config=AppConfiguration())
    )
    # The picture step is checked on its own below.
    target_service.astrometrics.processing.enhancement = MagicMock()
    return target_service


def test_worker_runs_the_full_stacking_stage_on_the_chosen_frames() -> None:
    """The stage gets the target's own records for the chosen frames."""
    paths = ["/lights/M_42/lum_001.fits", "/lights/M_42/lum_002.fits", "/lights/M_42/lum_003.fits"]
    target = _target_with_frames("M 42", paths)
    target_service = _target_service(target)
    notification_service = MagicMock()
    stacked_path = "/stacks/M 42/M_42_L_Stacked.fits"

    with patch(STAGE, return_value=stacked_path) as stage:
        result = start_siril_processing_task(
            job_id="job-1",
            target_id="M 42",
            image_files=[{"path": paths[0]}, {"path": paths[1]}],
            target_service=target_service,
            notification_service=notification_service,
        )

    assert result == stacked_path
    stage.assert_called_once()
    assert stage.call_args.args == (target,)
    assert stage.call_args.kwargs["frames_to_stack"] == target.frames[:2]
    assert stage.call_args.kwargs["job_id"] == "job-1"
    target_service.astrometrics.processing._targets._save.assert_called_once()
    assert notification_service.notify.call_args.kwargs["status"] == "success"
    # The stack's picture is made after the stack, by the enhancement pipeline.
    target_service.astrometrics.processing.enhancement.run.assert_called_once_with(target, None, stacked_path)


def test_worker_sends_the_picture_settings_and_keeps_a_stack_whose_picture_failed() -> None:
    """The overrides reach the picture; its failure does not fail the stack."""
    from astrometricslib import EnhancementSettings

    paths = ["/lights/M_42/lum_001.fits"]
    target = _target_with_frames("M 42", paths)
    target_service = _target_service(target)
    target_service.astrometrics.processing.enhancement.run.side_effect = ProcessingError("no picture")
    notification_service = MagicMock()
    stacked_path = "/stacks/M 42/M_42_L_Stacked.fits"

    with patch(STAGE, return_value=stacked_path) as stage:
        result = start_siril_processing_task(
            job_id="job-1",
            target_id="M 42",
            image_files=paths,
            target_service=target_service,
            notification_service=notification_service,
            stack_options={"force": True, "denoise": False, "star_toning": True},
        )

    assert result == stacked_path
    assert stage.call_args.kwargs["force"] is True
    target_service.astrometrics.processing.enhancement.run.assert_called_once_with(
        target, EnhancementSettings(denoise=False, star_toning=True), stacked_path
    )
    assert notification_service.notify.call_args.kwargs["status"] == "success"


def test_worker_accepts_frame_paths_as_well_as_frame_dicts() -> None:
    """A flat list of path strings picks out the same frame records."""
    paths = ["/lights/Arcturus/spec_001.fits", "/lights/Arcturus/spec_002.fits"]
    target = _target_with_frames("Arcturus", paths)
    target_service = _target_service(target)

    with patch(STAGE, return_value="/stacks/Arcturus_SPEC_Stacked.fits") as stage:
        start_siril_processing_task(
            job_id="job-2",
            target_id="Arcturus",
            image_files=paths,
            target_service=target_service,
        )

    assert stage.call_args.kwargs["frames_to_stack"] == target.frames


def test_worker_reports_failure_and_does_not_save_when_nothing_was_stacked() -> None:
    """A stage with no stack leaves the target unsaved and sends an error."""
    target = _target_with_frames("M 42", ["/lights/M_42/lum_001.fits"])
    target_service = _target_service(target)
    notification_service = MagicMock()

    with patch(STAGE, return_value=None):
        result = start_siril_processing_task(
            job_id="job-3",
            target_id="M 42",
            image_files=[{"path": "/lights/M_42/lum_001.fits"}],
            target_service=target_service,
            notification_service=notification_service,
        )

    assert result is None
    target_service.astrometrics.processing._targets._save.assert_not_called()
    assert notification_service.notify.call_args.kwargs["status"] == "error"


def test_worker_refuses_a_target_that_is_not_in_the_library() -> None:
    """An unknown target raises instead of stacking nothing."""
    target_service = _target_service(None)

    with patch(STAGE) as stage, pytest.raises(NotFoundError, match="not in the library"):
        start_siril_processing_task(
            job_id="job-4",
            target_id="Nowhere",
            image_files=[],
            target_service=target_service,
        )

    stage.assert_not_called()


def test_worker_job_log_holds_what_the_stacking_stage_logs(tmp_path: Path) -> None:
    """Lines the stage writes through the package logger reach the job log."""
    paths = ["/lights/M_42/lum_001.fits"]
    target = _target_with_frames("M 42", paths)
    target_service = _target_service(target)
    log_file = tmp_path / "job.log"

    def fake_stage(*args: object, **kwargs: object) -> str:
        """Log like the preview step does, then return a stack path.

        Returns
        -------
        stacked_path : `str`
            A stack path.
        """
        logging.getLogger("astrometricslib.pipelines.stacking.post_processing.stack_preview").info(
            "Preview steps: GraXpert done; Siril stretch done."
        )
        return "/stacks/M 42/M_42_L_Stacked.fits"

    with patch(STAGE, side_effect=fake_stage):
        start_siril_processing_task(
            job_id="job-log",
            target_id="M 42",
            image_files=paths,
            log_file_path=str(log_file),
            target_service=target_service,
        )

    text = log_file.read_text(encoding="utf-8")
    assert "Starting new processing task for M 42" in text
    assert "Preview steps: GraXpert done; Siril stretch done." in text
    assert "Saved target M 42 with stacked image" in text


def _run_worker_with_saved_record(tmp_path: Path, saved_stack: str) -> str:
    """Run the worker with a read-back that names ``saved_stack``.

    Returns
    -------
    text : `str`
        The job log.
    """
    paths = ["/lights/M_42/lum_001.fits"]
    target = _target_with_frames("M 42", paths)
    saved = _target_with_frames("M 42", paths)
    saved.stacking.stacked_image = saved_stack
    saved.stacking.processed_image = "/stacks/M 42/M_42_L_Stacked_processed.fits"
    target_service = _target_service(target)
    target_service.read_saved_target.return_value = saved
    log_file = tmp_path / "job.log"

    with patch(STAGE, return_value="/stacks/M 42/M_42_L_Stacked.fits"):
        start_siril_processing_task(
            job_id="job-readback",
            target_id="M 42",
            image_files=paths,
            log_file_path=str(log_file),
            target_service=target_service,
        )
    return log_file.read_text(encoding="utf-8")


def test_worker_logs_that_the_saved_record_names_the_new_stack(tmp_path: Path) -> None:
    """A record that names the stack is confirmed in the job log."""
    text = _run_worker_with_saved_record(tmp_path, "/stacks/M 42/M_42_L_Stacked.fits")

    assert "The saved record for M 42 names the stack." in text
    assert "M_42_L_Stacked_processed.fits" in text
    assert "ERROR" not in text


def test_worker_logs_an_error_when_the_saved_record_names_another_stack(tmp_path: Path) -> None:
    """A record still naming an older stack is reported as an error."""
    text = _run_worker_with_saved_record(tmp_path, "/stacks/M 42/M_42_Stacked.fits")

    assert "ERROR" in text
    assert "does not name it" in text
    assert "M_42_Stacked.fits" in text


def _service(processing_api: MagicMock, target: Target | None) -> ImageProcessingService:
    """Make an image processing service over a fake library API and target.

    Returns
    -------
    service : `ImageProcessingService`
        The service.
    """
    target_service = MagicMock()
    target_service.get_targets.return_value = target
    config_service = MagicMock()
    config_service.get_logs_path.return_value = "/tmp/logs"
    return ImageProcessingService(
        processing_api=processing_api, target_service=target_service, config_service=config_service
    )


def test_open_siril_opens_the_stacked_image_through_the_stacking_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stack is opened by the library's public stacking driver."""
    import astrometricslib.drivers as drivers

    engine = MagicMock()
    monkeypatch.setattr(drivers, "get_stacking_driver", lambda: engine)
    stack = tmp_path / "stack.fits"
    stack.write_bytes(b"")
    target = Target(id="M 42")
    target.stacking.stacked_image = str(stack)

    assert _service(MagicMock(), target).open_siril("M 42") is True
    engine.open_in_viewer.assert_called_once_with(str(stack))


def test_open_siril_falls_back_to_the_first_frame_and_reports_a_library_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a stack the first frame opens; a library error returns False."""
    import astrometricslib.drivers as drivers

    engine = MagicMock()
    monkeypatch.setattr(drivers, "get_stacking_driver", lambda: engine)
    target = _target_with_frames("M 42", ["/lights/M_42/lum_001.fits"])

    assert _service(MagicMock(), target).open_siril("M 42") is True
    engine.open_in_viewer.assert_called_once_with("/lights/M_42/lum_001.fits")

    engine.open_in_viewer.side_effect = ProcessingError("no window")
    assert _service(MagicMock(), target).open_siril("M 42") is False


def test_worker_writes_job_log_lines_to_the_given_job_repository() -> None:
    """The job log capture receives the repository the service was given."""
    target_service = _target_service(_target_with_frames("M 42", ["/lights/M_42/lum_001.fits"]))
    repository = MagicMock()

    with patch(STAGE, return_value=None), patch("astrometricslib.jobs.capture_job_logs") as capture:
        start_siril_processing_task(
            job_id="job-repo",
            target_id="M 42",
            image_files=["/lights/M_42/lum_001.fits"],
            target_service=target_service,
            job_repository=repository,
        )

    assert capture.call_args.kwargs["job_store"] is repository


def test_worker_stage_progress_reaches_the_backend_job() -> None:
    """A stack that reports a stage moves the job row the backend made."""
    from astrometricslib.jobs import get_current_job

    target_service = _target_service(_target_with_frames("M 42", ["/lights/M_42/lum_001.fits"]))
    stored_job = MagicMock()
    repository = MagicMock()
    repository.get_job.return_value = stored_job

    def stack_that_reports_a_stage(*_args: Any, **_kwargs: Any) -> str:
        """Report a stage through the current job.

        Returns
        -------
        stacked_path : `str`
            A made-up stack path.
        """
        current = get_current_job()
        assert current is not None
        current.stage(30, "Stage 2 of 7")
        return "/stacks/M 42/stack.fits"

    with patch(STAGE, side_effect=stack_that_reports_a_stage):
        start_siril_processing_task(
            job_id="job-progress",
            target_id="M 42",
            image_files=["/lights/M_42/lum_001.fits"],
            target_service=target_service,
            job_repository=repository,
        )

    repository.get_job.assert_any_call("job-progress")
    assert stored_job.progress_current == 30
    assert stored_job.status == "running"


def test_a_stacking_job_cancelled_while_waiting_for_a_slot_never_runs_and_stays_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel in the slot wait: no stage runs, the job stays cancelled."""
    import threading

    from astrometricslib.foundation.jobs.models import ProcessingJob
    from astrometricslib.foundation.jobs.store import JobStore
    from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
    from backend.services.processing.image_processing_service import abandon_stacking_job

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    job_store = JobStore(str(tmp_path / "log.db"))
    job_store.upsert_job(ProcessingJob(id="job-1", target_id="M 42", job_type="stacking", status="started"))
    target = _target_with_frames("M 42", ["/lights/M_42/lum_001.fits"])
    target_service = _target_service(target)
    notification_service = MagicMock()
    stages_run: list[str] = []
    holder_has_slot = threading.Event()
    release_holder = threading.Event()
    outcome: list[Any] = []

    def holder() -> None:
        """Hold the only job slot, as another stack would."""
        with acquire_resource_slot(None, "job", 1, work_name="other stack"):
            holder_has_slot.set()
            release_holder.wait(timeout=20)

    def stack_that_waits_for_the_slot(*_args: Any, **_kwargs: Any) -> Any:
        """Wait for the job slot like the real stack, then run a stage."""
        with acquire_resource_slot(None, "job", 1, poll_interval_seconds=0.05):
            stages_run.append("stage 1")

    target_service.astrometrics.processing.stacking.run = stack_that_waits_for_the_slot
    holder_thread = threading.Thread(target=holder, daemon=True)
    holder_thread.start()
    assert holder_has_slot.wait(timeout=20)

    def worker() -> None:
        """Run the stacking task and keep what it returns."""
        outcome.append(
            start_siril_processing_task(
                "job-1",
                "M 42",
                ["/lights/M_42/lum_001.fits"],
                target_service=target_service,
                job_repository=job_store,
                notification_service=notification_service,
            )
        )

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    for _ in range(200):
        if abandon_stacking_job("job-1"):
            break
        threading.Event().wait(0.05)
    job_store.upsert_job(job_store.get_job("job-1").model_copy(update={"status": "cancelled"}))
    worker_thread.join(timeout=20)
    release_holder.set()
    holder_thread.join(timeout=20)

    assert not worker_thread.is_alive()
    assert outcome == [None]
    assert stages_run == []
    assert job_store.get_job("job-1").status == "cancelled"
    notification_service.notify.assert_not_called()
