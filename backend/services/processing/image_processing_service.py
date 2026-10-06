"""Background image stacking service: runs the full stacking stage as jobs."""

import logging
import os
import time
from typing import Any

from astrometricslib import NotFoundError
from backend.services.infrastructure.base_service import BaseBackgroundService

# Define stable log directory relative to this file
LOG_DIR = None  # Will be initialized from config


class ImageProcessingService(BaseBackgroundService):
    """Service for managing background image processing tasks."""

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        siril_driver: Any = None,
        target_service: Any = None,
        calibration_library: Any = None,
        config_service: Any = None,
        notification_service: Any = None,
        job_service: Any = None,
        astrometrics_service: Any = None,
    ):
        """Initialize the image processing orchestration service.

        Parameters
        ----------
        siril_driver : `Any`, optional
            The CLI driver for communicating with Siril.
        target_service : `Any`, optional
            Service for fetching target metadata and frame sets.
        calibration_library : `Any`, optional
            Service for finding matching calibration masters.
        config_service : `Any`, optional
            Provides filesystem paths and application settings.
        notification_service : `Any`, optional
            Pushes toast notifications to the frontend.
        job_service : `Any`, optional
            Manages persistent state for long-running stacking jobs.
        astrometrics_service : `Any`, optional
            Maintains the global event bus.
        """
        super().__init__(job_service=job_service, astrometrics_service=astrometrics_service)
        self.siril = siril_driver

        self._target_service = target_service
        self._calibration_library = calibration_library
        self._config_service = config_service
        global LOG_DIR
        LOG_DIR = str(self._config_service.get_logs_path())
        self._notification_service = notification_service

    def get_processing_status(self, target_id: str) -> dict:
        """Verify if a target has any running stacking jobs.

        Returns
        -------
        status : `dict`
            Dict with a `"processing"` bool indicating whether any
            stacking job is currently active for the target.
        """
        if not self._job_service:
            return {"processing": False}
        active = self._job_service.get_jobs_for_target(target_id, job_type="stacking", status="started")
        if not active:
            active = self._job_service.get_jobs_for_target(target_id, job_type="stacking", status="running")
        return {"processing": len(active) > 0}

    def cancel_processing_jobs(self, target_id: str) -> dict:
        """Cancel active stacking jobs for a target.

        Returns
        -------
        result : `dict`
            Dict with a `"status"` key: `"cancelled"` if at least
            one job was cancelled, otherwise `"none"`.
        """
        if not self._job_service:
            return {"status": "none"}
        active = self._job_service.get_jobs_for_target(target_id, job_type="stacking", status="started")
        if not active:
            active = self._job_service.get_jobs_for_target(target_id, job_type="stacking", status="running")

        cancelled = False
        for job in active:
            self.cancel_processing(job.id)
            self._job_service.update_job(
                job.id, status="cancelled", progress=0, status_message="Cancelled by user."
            )
            cancelled = True

        return {"status": "cancelled" if cancelled else "none"}

    def fetch_job_log_tail(self, job_id: str, lines: int = 100) -> list:
        """Fetch the tail of DB-recorded log entries for a job.

        Returns
        -------
        entries : `list`
            The last `lines` formatted log entry strings for the
            job, oldest first.
        """
        if not self._job_service:
            return []
        entries = self._job_service.get_log_entries_for_job(job_id)
        formatted = [f"{e['created_at']} - {e['level']} - {e['message']}" for e in entries]
        return formatted[-lines:]

    def open_siril(self, target_id: str) -> bool:
        """Open the Siril GUI for the specified target's stacked image.

        Returns
        -------
        launched : `bool`
            `True` if the Siril GUI was launched successfully,
            `False` otherwise.
        """
        if not self.siril or not self._target_service:
            return False

        target = self._target_service.get_targets(target_id)
        if not target:
            return False

        # Prioritize the stacked image if it exists
        path = target.stacking.stacked_image or target.spectral_stacking.stacked_image

        if not path or not os.path.exists(path):
            # Fallback: if no stacked image, try to find the first
            # frame to at least open Siril in the right spot
            if target.frames and len(target.frames) > 0:
                # Check if frames is a list of dicts or objects
                first_frame = target.frames[0]
                path = first_frame.path if hasattr(first_frame, "path") else first_frame.get("path")
            else:
                # Just open the executable without a specific file
                path = ""

        try:
            self.siril.launch_siril_gui(path)
            return True
        except Exception as e:
            logging.error(f"Failed to launch Siril for {target_id}: {e}")
            return False

    def process_target(self, target_id: str, image_files: list) -> dict:
        """Start a background processing job for a target using threads.

        Returns
        -------
        result : `dict`
            Dict describing the job: `"status"` (`"started"` or
            `"already_running"`), `"jobId"`, `"expectedOutput"`, and
            `"logFile"`.
        """
        # Rehydrate metadata if image_files is a flat list
        if isinstance(image_files, list) and self._target_service:
            try:
                target = self._target_service.get_targets(target_id)
                if target and target.frames:
                    # Check if first item is a path string
                    first = image_files[0] if image_files else None
                    if isinstance(first, str):
                        logging.info(
                            f"Rehydrating metadata for {len(image_files)} paths using "
                            f"Target {target_id} frames list"
                        )
                        path_map = {f.path: f.model_dump(by_alias=True) for f in target.frames}
                        rehydrated = [path_map[p] for p in image_files if p in path_map]
                        if rehydrated:
                            image_files = rehydrated
                    elif hasattr(first, "model_dump"):
                        # Already models, convert to dicts for processors
                        image_files = [f.model_dump(by_alias=True) for f in image_files]
            except Exception as e:
                logging.warning(f"Failed to rehydrate metadata for target {target_id}: {e}")

        # Create a unique log path for this job
        safe_target_id = target_id.replace(" ", "_")
        log_file = os.path.join(LOG_DIR, f"process_{safe_target_id}_{int(time.time())}.log")

        if self._job_service:
            active_jobs = self._job_service.get_jobs_for_target(
                target_id, job_type="stacking", status="started"
            )
            for job in active_jobs:
                return {
                    "status": "already_running",
                    "jobId": job.id,
                    "expectedOutput": f"processed_{target_id}.png",
                    "logFile": job.log_file_path or log_file,
                }

        # REQ: IMG-5.3: Persistent job history
        job_id = self._submit_job(
            target_id,
            "stacking",
            start_siril_processing_task,
            image_files,
            log_file_path=log_file,
            target_service=self._target_service,
            siril=self.siril,
            notification_service=self._notification_service,
        )

        return {
            "status": "started",
            "jobId": job_id,
            "expectedOutput": f"processed_{target_id}.png",
            "logFile": log_file,
        }


def _log_what_the_saved_record_names(
    target_service: Any, target_id: str, final_path: str, logger: logging.Logger
) -> None:
    """Read the target back and log whether it names the new stack.

    A save can report success and still leave the stored record unchanged,
    for example if another program wrote the target in between. Reading the
    record back shows that in the job log instead of leaving the viewer to
    show an older stack with no explanation.

    Parameters
    ----------
    target_service : `Any`
        The service that saved the target.
    target_id : `str`
        The target that was stacked.
    final_path : `str`
        The stack the job made.
    logger : `logging.Logger`
        The job's logger.
    """
    saved = target_service.read_saved_target(target_id)
    if saved is None:
        logger.error(f"The saved record for {target_id} could not be read back after the save.")
        return
    recorded_stacks = {saved.stacking.stacked_image, saved.spectral_stacking.stacked_image}
    recorded_stacks.update(result.stacked_image for result in saved.stacking.stacks_by_configuration.values())
    if final_path not in recorded_stacks:
        logger.error(
            f"The stack was made at {final_path}, but the saved record for {target_id} does not "
            f"name it (it names {saved.stacking.stacked_image or 'no stack'}). The viewer will "
            "not show this stack until the record is repaired."
        )
        return
    picture = saved.stacking.processed_image or saved.spectral_stacking.processed_image
    logger.info(f"The saved record for {target_id} names the stack. Processed image: {picture or 'none'}.")


def start_siril_processing_task(
    job_id: str,
    target_id: str,
    image_files: list[Any],
    log_file_path: str | None = None,
    target_service: Any = None,
    siril: Any = None,
    notification_service: Any = None,
    **kwargs: Any,
) -> str | None:
    """Worker task that stacks a target's frames with the full stacking stage.

    This is the same stage the batch script runs, so a stack made from the
    viewer gets the same quality summary, provenance record and processed
    picture as one made in a batch. The ``siril`` driver is used only to
    reach the job log database; the stage makes its own Siril driver.

    Returns
    -------
    final_path : `str` or `None`
        Path to the completed stacked image, or `None` if
        processing did not produce an output.

    Raises
    ------
    NotFoundError
        If the target is not in the library.
    """
    from astrometricslib import capture_job_logs

    if log_file_path:
        # Clear old logs for this target at the start of a new job. The
        # capture below then appends to the file.
        with open(log_file_path, "w", encoding="utf-8"):
            pass

    # The stacking stage writes its decisions (which frames were set aside,
    # which preview steps ran) through the shared "astrometricslib" logger,
    # not through this job's own logger. `capture_job_logs` sends both to the
    # job's log file and its database rows, so the job log shows them.
    job_repository = getattr(siril, "job_repository", None)
    with capture_job_logs(
        job_id=job_id, log_file_path=log_file_path, logger_interface=job_repository
    ) as logger:
        logger.info(f"Starting new processing task for {target_id} (Job: {job_id})")

        # The stacking stage works on the target's own frame records, so
        # the frames the viewer sent (as paths or as dicts) are matched back
        # to them.
        target = target_service.get_targets(target_id) if target_service else None
        if target is None:
            raise NotFoundError(f"Cannot stack '{target_id}': the target is not in the library.")
        requested_paths = {frame if isinstance(frame, str) else frame.get("path") for frame in image_files}
        frames_to_stack = [frame for frame in target.frames if frame.path in requested_paths]

        # `ProcessingPipelines.stack` runs the whole stacking stage, the same
        # one the batch script runs: it holds a stacking slot (an OS-level
        # lock that bounds Siril runs across processes), sets aside bad
        # frames, stacks (spectral frames of different exposure lengths one
        # length at a time), trims the noisy edges, records the quality
        # summary and provenance, makes the preview picture the viewer shows,
        # and saves the target. This job is already in the job list, so the
        # stack records its provenance against it instead of a new job.
        from astrometricslib import ProcessingError, frame_is_spectral, log_context

        kind = "spectral" if frames_to_stack and all(map(frame_is_spectral, frames_to_stack)) else "imaging"
        try:
            with log_context(job_id=job_id):
                stack_result = target_service.astrometrics.processing.stack(
                    target, frames=frames_to_stack, kind=kind, log_file=log_file_path, register_job=False
                )
            final_path = stack_result.stacked_path
        except ProcessingError as error:
            logger.error(f"Stacking {target_id} made no stack: {error}")
            final_path = None

        if notification_service:
            if final_path:
                message = f"Stacking complete for {target_id}. Output saved to {final_path}"
                status = "success"
            else:
                message = f"Stacking failed for {target_id}. Check logs for details."
                status = "error"

            notification_service.notify(target_id, message, status=status)

        # The stack call saved the target with its stack, quality summary
        # and processed picture; this checks what the saved record names.
        if final_path:
            logger.info(f"Saved target {target_id} with stacked image: {final_path}")
            _log_what_the_saved_record_names(target_service, target_id, final_path, logger)

        return final_path
