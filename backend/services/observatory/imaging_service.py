"""Purpose: Start main-camera captures as background jobs.

Description: The capture itself (filter change, exposures, dithering)
is `control.imaging.capture_image` in the wayfinding library. This
service only runs it on a background thread inside a ``capture`` job,
so the app gets a job id right away and can watch the progress. The
library moves the job's progress bar once per frame.
"""

import logging
import threading
from typing import Any

from astrometricslib import registered_job
from backend.services.processing.job_service import JobService
from wayfindinglib import ObservatoryControl

logger = logging.getLogger(__name__)

EARLY_FAILURE_WAIT_SECONDS = 0.5
"""How long a new capture is watched for an immediate failure, such as a
bad argument, so the caller gets that error directly instead of a job
that failed at once."""


class ImagingService:
    """Run main-camera captures in the background and list them."""

    def __init__(self, observatory_api: ObservatoryControl, job_service: JobService) -> None:
        """Keep the observatory control and the job store.

        Parameters
        ----------
        observatory_api : `ObservatoryControl`
            The Wayfinder's `control`, which runs the capture.
        job_service : `JobService`
            The job store the capture jobs are recorded in.
        """
        self._observatory = observatory_api
        self.job_service = job_service

    def capture_sequence(
        self,
        target_id: str,
        exposure_seconds: float,
        count: int,
        image_type: str = "LIGHT",
        filter_name: str | None = None,
        delay_seconds: float = 0.0,
        dither: bool = False,
    ) -> str:
        """Start a capture run in the background.

        Parameters
        ----------
        target_id : `str`
            Target the frames are recorded against.
        exposure_seconds : `float`
            Exposure time per frame.
        count : `int`
            Number of frames to take.
        image_type : `str`, optional
            Frame type (``"LIGHT"``, ``"DARK"``, ``"FLAT"``, ``"BIAS"``).
            Written to the job log; the camera sets the frame type itself.
        filter_name : `str`, optional
            Filter to select before the run starts. `None` (default)
            leaves the wheel where it is.
        delay_seconds : `float`, optional
            Settling pause between frames. Not applied after the last one.
        dither : `bool`, optional
            Shift the pointing slightly between frames.

        Returns
        -------
        job_id : `str`
            The job id to poll for progress.

        Notes
        -----
        A capture that fails at once, for example because an argument is
        out of range, raises its error here instead of returning a job id.
        """
        started = threading.Event()
        state: dict[str, Any] = {}

        def run() -> None:
            """Run the capture as a ``capture`` job on this thread."""
            try:
                with registered_job(enabled=True, job_type="capture", target_id=target_id) as job:
                    state["job_id"] = job.job_id
                    started.set()
                    job.info(f"Capturing {count} x {exposure_seconds} s {image_type} frames")
                    self._observatory.imaging.capture_image(
                        exposure_seconds,
                        count=count,
                        filter_name=filter_name,
                        dither=dither,
                        delay_seconds=delay_seconds,
                    )
            except Exception as error:  # the top of a background job: record it, never lose it
                state["error"] = error
                logger.exception("Capture for %s failed", target_id)
            finally:
                started.set()

        thread = threading.Thread(target=run, name=f"capture-{target_id}", daemon=True)
        thread.start()
        started.wait()
        thread.join(timeout=EARLY_FAILURE_WAIT_SECONDS)
        if "error" in state and not thread.is_alive():
            raise state["error"]
        return state.get("job_id") or ""

    def get_active_capture_jobs(self) -> list[Any]:
        """Return the capture jobs that are still running.

        Returns
        -------
        jobs : `list`
            The active `Job` records whose `job_type` is ``"capture"``.
        """
        return [job for job in self.job_service.get_active_jobs() if job.job_type == "capture"]
