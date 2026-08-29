"""Background image stacking/processing service backed by Siril."""

import logging
import os
import time
from typing import Any

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

        target = self._target_service.get_target(target_id)
        if not target:
            return False

        # Prioritize the stacked image if it exists
        path = target.stacked_image or target.stacked_spectral_target

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
                target = self._target_service.get_target(target_id)
                if target and target.frames:
                    # Check if first item is a path string
                    first = image_files[0] if image_files else None
                    if isinstance(first, str):
                        logging.info(
                            f"Rehydrating metadata for {len(image_files)} paths using "
                            f"Target {target_id} frames list"
                        )
                        path_map = {f.path: f.model_dump() for f in target.frames}
                        rehydrated = [path_map[p] for p in image_files if p in path_map]
                        if rehydrated:
                            image_files = rehydrated
                    elif hasattr(first, "model_dump"):
                        # Already models, convert to dicts for processors
                        image_files = [f.model_dump() for f in image_files]
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


def start_siril_processing_task(  # ruff: ignore[missing-return-type-undocumented-public-function]
    job_id,  # ruff: ignore[missing-type-function-argument]
    target_id,  # ruff: ignore[missing-type-function-argument]
    image_files,  # ruff: ignore[missing-type-function-argument]
    log_file_path=None,  # ruff: ignore[missing-type-function-argument]
    target_service=None,  # ruff: ignore[missing-type-function-argument]
    siril=None,  # ruff: ignore[missing-type-function-argument]
    notification_service=None,  # ruff: ignore[missing-type-function-argument]
    **kwargs,  # ruff: ignore[missing-type-kwargs]
):
    """Worker task to execute Siril processing.

    Returns
    -------
    final_path : `str` or `None`
        Path to the completed stacked image, or `None` if
        processing did not produce an output.
    """
    import logging

    if log_file_path:
        # Clear old logs for this target at the start of a new job
        handler = logging.FileHandler(log_file_path, mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger = logging.getLogger(f"job_{job_id}")
        logger.propagate = False
        # Clear existing handlers if any
        for h in logger.handlers[:]:
            logger.removeHandler(h)
        logger.addHandler(handler)

        job_repository = getattr(siril, "job_repository", None)
        if job_repository is not None:
            from astrometricslib import DbLogHandler

            logger.addHandler(DbLogHandler(job_repository, job_id=job_id))

        logger.setLevel(logging.INFO)
        logger.info(f"Starting new processing task for {target_id} (Job: {job_id})")
    else:
        logger = None

    # Driver must be provided via DI
    if not siril:
        from astrometricslib import ImageProcessing

        # This fallback is discouraged but kept for safety if not
        # wired correctly
        logging.error("Siril driver NOT provided to task! Creating un-configured instance.")
        siril = ImageProcessing(None, None)

    # Bound how many Siril subprocesses run concurrently system-wide,
    # whether they were started here or by the offline batch script
    # (pipeline_tasks.run_full_pipeline) -- an OS-level lock, respected
    # across processes.
    from astrometricslib import ProcessingPipelines, get_configuration

    with ProcessingPipelines(get_configuration()).acquire_stacking_slot():
        final_path = siril.process_target(target_id, image_files, log_file=log_file_path, job_id=job_id)

    if notification_service:
        if final_path:
            message = f"Stacking complete for {target_id}. Output saved to {final_path}"
            status = "success"
        else:
            message = f"Stacking failed for {target_id}. Check logs for details."
            status = "error"

        notification_service.notify(target_id, message, status=status)

    # REQ: IMG-4.2 - Route spectral stacks to specialized property
    is_spectral = all(f.get("filter") == "SPEC" for f in image_files if isinstance(f, dict))

    if final_path and target_service:
        try:
            target = target_service.get_target(target_id)
            if target:
                if is_spectral:
                    target.stacked_spectral_target = final_path
                else:
                    target.stacked_image = final_path
                target_service.save_targets()
                if logger:
                    logger.info(f"Updated target {target_id} metadata with stacked image: {final_path}")
        except Exception as e:
            if logger:
                logger.error(f"Failed to update target metadata: {e}")

    return final_path
