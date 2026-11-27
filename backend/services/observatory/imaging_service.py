"""Purpose: Start main-camera captures as background jobs.

Description: The capture itself (filter change, exposures, dithering)
is `control.imaging.capture` in the wayfinding library. This service
first asks the library to check the capture (`control.imaging.check_capture`:
the arguments, and whether monitor mode refuses it), so a refusal reaches
the caller as an error before any thread starts. It then runs the capture
on a background thread inside a ``capture`` job, so the app gets a job id
right away and can watch the progress. The library moves the job's
progress bar once per frame. `stop_capture` asks the library to end the
run it started.
"""

import logging
import threading
from typing import Any

from astrometricslib.exceptions import AstrometricsError
from astrometricslib.jobs import registered_job
from backend.services.error_logging import log_failure
from backend.services.processing.job_service import JobService
from wayfindinglib import ObservatoryControl

logger = logging.getLogger(__name__)


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
        exposure_s: float,
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
        exposure_s : `float`
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
        A capture the library refuses (an argument out of range, or monitor
        mode) raises its error here, before any thread starts, instead of
        returning a job id.
        """
        self._observatory.imaging.check_capture(
            exposure_s, count=count, filter_name=filter_name, dither=dither, delay_s=delay_seconds
        )
        started = threading.Event()
        state: dict[str, Any] = {}

        def run() -> None:
            """Run the capture as a ``capture`` job on this thread."""
            try:
                with registered_job(job_type="capture", target_id=target_id) as job:
                    state["job_id"] = job.job_id
                    started.set()
                    job.info(f"Capturing {count} x {exposure_s} s {image_type} frames")
                    self._observatory.imaging.capture(
                        exposure_s,
                        count=count,
                        filter_name=filter_name,
                        dither=dither,
                        delay_s=delay_seconds,
                    )
            except AstrometricsError as error:
                log_failure(logger, error, "Capture for %s failed", target_id)
            except Exception:  # the top of a background job: record it, never lose it
                logger.exception("Capture for %s failed", target_id)
            finally:
                started.set()

        thread = threading.Thread(target=run, name=f"capture-{target_id}", daemon=True)
        thread.start()
        started.wait()
        return state.get("job_id") or ""

    def stop_capture(self) -> bool:
        """Stop the capture run the app started, keeping the frames taken.

        The run's job ends as completed, with a "stopped after N frames"
        line in its log. A run started by another program is not touched.

        Returns
        -------
        stopped : `bool`
            `True` if a run was stopped, `False` if none was going. Not an
            error: stopping nothing does nothing.

        Notes
        -----
        In monitor mode the library raises `PermissionDeniedError`, even
        when no run is going; this service lets the error through.
        """
        running = bool(self._observatory.imaging.get_status(include=["capture"]).capture_running)
        self._observatory.imaging.stop()
        return running
