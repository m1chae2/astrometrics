"""Unit tests for the background image processing service and its worker task.

Verifies that `start_siril_processing_task` runs the full stacking stage
(through `ProcessingPipelines.stack`) on the frames the viewer chose, so a
stack made from the viewer gets the same post-processing as a batch stack,
and that it saves the target and reports the outcome.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from astrometricslib import AppConfiguration, FrameRecord, NotFoundError, ProcessingPipelines, Target
from backend.services.processing.image_processing_service import (
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
        ``astrometrics.processing._targets.save`` records the save.
    """
    target_service = MagicMock()
    target_service.get_targets.return_value = target
    target_service.astrometrics.processing = ProcessingPipelines(
        AppConfiguration(), MagicMock(), targets=MagicMock()
    )
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
    target_service.astrometrics.processing._targets.save.assert_called_once()
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
    target_service.astrometrics.processing._targets.save.assert_not_called()
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
