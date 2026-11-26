"""Purpose: Start the slow stack review jobs for the Image Processing Display.

Description: Some checks on a finished stack take longer than a request should
wait. Making the preview picture again can take a minute. Comparing a stack
with the one before it measures both stacks, and a plate solve that fails can
take about ten minutes. Checking what a stack would do
(``processing:check_stack``) reads every frame. Each is a call of the science
library that records its own job.

This service starts such a call in the background (with the library's
`run_as_background_job`). A call that finishes within a short wait gives its
answer at once. A slower one gives back its job id, and the app follows the
job with ``processing:get_job``. When the job ends, its result is stored in the
job's ``outputMetrics.result``. A call that fails inside the short wait raises
its error to the caller. A later failure shows up as a failed job with a
message.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.jobs import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    report_started_jobs,
    run_as_background_job,
)

logger = logging.getLogger(__name__)

# How long a request waits for a job to finish before it answers with the job
# id instead, in seconds. It is long enough for a quick failure (a missing
# stack, a value out of range) to come back as an error, and short enough that
# the app is never left waiting for a slow job.
DEFAULT_WAIT_SECONDS = 2.0


# The running picture remakes of this process, by job id, so a cancel can stop
# the job's slot wait and its next step. A job is listed from the moment it is
# recorded until its work ends.
_preview_abandonments: dict[str, RunAbandonment] = {}
_preview_abandonments_lock = threading.Lock()


def is_preview_job_running(job_id: str) -> bool:
    """Say whether a picture remake of this process is running.

    Parameters
    ----------
    job_id : `str`
        The remake job to look for.

    Returns
    -------
    running : `bool`
        `True` if the job's work is running in this process.
    """
    with _preview_abandonments_lock:
        return job_id in _preview_abandonments


def abandon_preview_job(job_id: str) -> bool:
    """Give up on a running picture remake of this process.

    The remake stops waiting for a heavy-work slot, starts no further step,
    stops the programs it started and puts the old pictures back.

    Parameters
    ----------
    job_id : `str`
        The remake job to give up on.

    Returns
    -------
    abandoned : `bool`
        `True` if the job's work is running in this process.
    """
    with _preview_abandonments_lock:
        abandonment = _preview_abandonments.get(job_id)
    if abandonment is None:
        return False
    abandonment.abandon()
    return True


class StackReviewStart(BaseModel):
    """The answer to starting a slow stack review.

    Attributes
    ----------
    status : `str`
        ``"completed"`` when the work finished within the short wait,
        ``"running"`` when it is still going, or ``"cancelled"`` when the
        user cancelled it within the short wait.
    job_id : `str` or `None`
        The job to follow with ``processing:get_job``. `None` when the work
        has not recorded a job yet, for example because it waits for another
        job to finish.
    result : `dict` or `None`
        The library's result as plain data, when the status is
        ``"completed"``.
    configuration : `str` or `None`
        The key of the stack chosen by camera and optic (such as
        ``ZWO ASI 533MM Pro@405mm``), when one was chosen that way.
    """

    status: Literal["completed", "running", "cancelled"]
    job_id: str | None = None
    result: dict[str, Any] | None = None
    configuration: str | None = None


def _plain(result: Any) -> Any:
    """Turn a library result into plain data the app can read.

    Parameters
    ----------
    result : `Any`
        A library result, usually a Pydantic model.

    Returns
    -------
    plain : `Any`
        The model as a dictionary of plain values, or the value as it was.
    """
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else result


class StackReviewService:
    """Start the slow stack reviews as background jobs.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library, which does the work.
    wait_seconds : `float`, optional
        How long a request waits before it answers with the job id.
    """

    def __init__(self, astrometrics: Any, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> None:
        self._astrometrics = astrometrics
        self._wait_seconds = wait_seconds

    def _start(self, work: Callable[[], Any]) -> StackReviewStart:
        """Run a library call that records its own job.

        Parameters
        ----------
        work : `Callable` [[], `Any`]
            The library call, with no arguments.

        Returns
        -------
        reply : `StackReviewStart`
            The finished result, or the job to follow.
        """
        try:
            outcome = run_as_background_job(work, grace_period_seconds=self._wait_seconds)
        except RunAbandonedError:
            # The user cancelled the job. This is a status, not an error.
            logger.info("The job was cancelled.")
            return StackReviewStart(status="cancelled")
        if "result" in outcome:
            return StackReviewStart(
                status="completed", job_id=outcome["jobId"], result=_plain(outcome["result"])
            )
        return StackReviewStart(status="running", job_id=outcome.get("jobId"))

    def start_preview_remake(
        self,
        target_id: str,
        kind: str = "imaging",
        denoise: bool | None = None,
        denoise_strength: float | None = None,
        star_toning: bool | None = None,
        starnet: bool = False,
        star_strength: float | None = None,
        star_stretch: float | None = None,
        stack_path: str | None = None,
        camera_id: str | None = None,
        focal_length_mm: float | None = None,
    ) -> StackReviewStart:
        """Make a target's preview picture again, as a background job.

        The settings apply to this one run only. The old pictures are kept
        aside the way the library does by default; this service never asks
        for that to change.

        Parameters
        ----------
        target_id : `str`
            The target whose stack to make a picture of.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``.
        denoise : `bool`, optional
            `False` skips Cosmic Clarity for this run. `True` runs it even if
            the settings have it off. Left out, the settings apply.
        denoise_strength : `float`, optional
            Cosmic Clarity's strength for this run, from 0 to 1.
        star_toning : `bool`, optional
            Turn the star toning on or off for this run.
        starnet : `bool`, optional
            Stretch the stars and the rest of the picture on their own, with
            StarNet. Off by default.
        star_strength : `float`, optional
            With ``starnet``, how much of the stars layer is blended, 0 to 1.
        star_stretch : `float`, optional
            With ``starnet``, the strength of the stars' own stretch.
        stack_path : `str`, optional
            The stack to make the picture of: the main stack or the stack of
            one camera and optic. Left out, the main stack. Another camera's
            stack never changes the picture the target shows.
        camera_id : `str`, optional
            Instead of ``stack_path``: the camera of the stack (its full name,
            an alias or part of its name). The library finds the stack.
        focal_length_mm : `float`, optional
            Instead of ``stack_path``: the optic of the stack, in millimeters.

        Returns
        -------
        reply : `StackReviewStart`
            The result, when finished, is a `PreviewRemakeResult` as a dict.
            ``configuration`` names the stack chosen by camera and optic.

        Raises
        ------
        InvalidArgumentError
            If a camera or optic is given with ``stack_path`` or for a
            spectral stack, or names no stack of the target, or more than one.
        """
        processing = self._astrometrics.processing
        configuration_key = None
        if camera_id or focal_length_mm is not None:
            if stack_path or kind == "spectral":
                raise InvalidArgumentError(
                    "A camera and optic choose an imaging stack; give them without stack_path, and not for "
                    "a spectral stack (spectral stacks are kept only for the main configuration).",
                    details={"target_id": target_id, "kind": kind},
                )
            record = processing.find_stack_configuration(target_id, camera_id, focal_length_mm)
            stack_path = record.stacked_image
            configuration_key = record.configuration_key

        def remake() -> Any:
            """Run the remake so that a cancel can reach it.

            A cancelled job raises `RunAbandonedError`, with the old pictures
            put back.

            Returns
            -------
            result : `Any`
                The library's result.
            """
            abandonment = RunAbandonment()
            recorded_job_ids: list[str] = []

            def remember_job(handle: Any) -> None:
                """List the abandonment under the job the library records."""
                if handle.job_id:
                    with _preview_abandonments_lock:
                        _preview_abandonments[handle.job_id] = abandonment
                    recorded_job_ids.append(handle.job_id)

            try:
                with bind_abandonment(abandonment), report_started_jobs(remember_job):
                    return processing.remake_preview(
                        target_id,
                        kind=kind,
                        denoise=denoise,
                        denoise_strength=denoise_strength,
                        star_toning=star_toning,
                        starnet=starnet,
                        star_strength=star_strength,
                        star_stretch=star_stretch,
                        register_job=True,
                        stack_path=stack_path,
                    )
            finally:
                with _preview_abandonments_lock:
                    for job_id in recorded_job_ids:
                        _preview_abandonments.pop(job_id, None)

        reply = self._start(remake)
        return reply.model_copy(update={"configuration": configuration_key}) if configuration_key else reply

    def start_stack_comparison(self, target_id: str, kind: str = "imaging") -> StackReviewStart:
        """Compare a target's stack with the previous one, as a job.

        The library measures both stacks the same way. A failed plate solve
        can take about ten minutes, so this always runs as a job.

        Parameters
        ----------
        target_id : `str`
            The target whose current stack to measure.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"``.

        Returns
        -------
        reply : `StackReviewStart`
            The result, when finished, is a `StackQualityReport` as a dict. A
            target with no previous stack gives a report with no comparison and
            a note that says so.
        """
        quality = self._astrometrics.quality
        return self._start(
            lambda: quality.measure_stack(
                target_id, kind=kind, include=[], compare_to="previous", register_job=True
            )
        )

    def start_stack_check(
        self,
        target_id: str,
        image_files: list | None = None,
        kind: str | None = None,
        filter_name: str | None = None,
        first_file: str | None = None,
        last_file: str | None = None,
        since: str | None = None,
        until: str | None = None,
        camera_id: str | None = None,
        focal_length_mm: float | None = None,
    ) -> StackReviewStart:
        """Check what a stack would do with its frames, as a background job.

        The check takes the same selection as ``processing:stack``: the
        frames the app shows as ticked in ``image_files``, or a choice by
        kind, filter, file range, time window, camera and focal length. It
        reads every frame, so it runs as a job. Nothing is stacked, moved,
        stored or saved.

        Parameters
        ----------
        target_id : `str`
            The target to check.
        image_files : `list`, optional
            The frames to check, as paths or frame dictionaries with a
            ``path``. Cannot be combined with the frame choices below.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` frames, when the frames
            are chosen here.
        filter_name : `str`, optional
            Only frames of this filter.
        first_file : `str`, optional
            Only frames from this file onward.
        last_file : `str`, optional
            Only frames up to this file or number.
        since : `str`, optional
            Only frames taken at or after this ISO time.
        until : `str`, optional
            Only frames taken at or before this ISO time.
        camera_id : `str`, optional
            Only frames from this camera.
        focal_length_mm : `float`, optional
            Only frames taken at this focal length.

        Returns
        -------
        reply : `StackReviewStart`
            The result, when finished, is a `StackPrediction` as a dict.

        Raises
        ------
        InvalidArgumentError
            If ``image_files`` is combined with a frame choice.

        Notes
        -----
        A target that is not in the library, or a listed frame that is not
        one of its frames, raises `NotFoundError` before the job starts. Frames
        that mix imaging and spectral frames, or come from more than one
        camera or optic, raise `InvalidArgumentError` before the job starts.
        """
        choices = (kind, filter_name, first_file, last_file, since, until, camera_id, focal_length_mm)
        frames = None
        if image_files is not None:
            if any(value is not None for value in choices):
                raise InvalidArgumentError(
                    "Pass image_files, or choose the frames with kind, filter_name, first_file, "
                    "last_file, since, until, camera_id and focal_length_mm, not both."
                )
            frames = self._frames_from_list(target_id, image_files)
        processing = self._astrometrics.processing
        if frames:
            # A plan with the frame list runs the library's frame-list checks
            # (for example a mix of imaging and spectral frames) and stacks
            # nothing, so a bad list is refused before a job starts.
            processing.stack(target_id, frames=frames, plan_only=True, register_job=False)
        return self._start(
            lambda: processing.predict_stack(
                target_id,
                frames=frames,
                kind=kind or "imaging",
                filter_name=filter_name,
                first_file=first_file,
                last_file=last_file,
                since=since,
                until=until,
                camera_id=camera_id,
                focal_length_mm=focal_length_mm,
                register_job=True,
            )
        )

    def _frames_from_list(self, target_id: str, image_files: list) -> list[Any]:
        """Find the target's frame records for a list of paths or dictionaries.

        Parameters
        ----------
        target_id : `str`
            The target the frames belong to.
        image_files : `list`
            Paths, or dictionaries with a ``path``.

        Returns
        -------
        frames : `list`
            The target's records, in the order given.

        Raises
        ------
        NotFoundError
            If the target is not in the library, or a path is not one of its
            frames.
        """
        target = self._astrometrics.targets.get(target_id)
        if target is None:
            raise NotFoundError(f"No target {target_id!r} in the library.", details={"target": target_id})
        by_path = {frame.path: frame for frame in target.frames}
        paths = [item.get("path") if isinstance(item, dict) else item for item in image_files]
        missing = [path for path in paths if path not in by_path]
        if missing:
            raise NotFoundError(
                f"{len(missing)} of the listed frames are not frames of {target_id!r} "
                f"(first: {missing[0]!r}).",
                details={"target": target_id},
            )
        return [by_path[path] for path in paths]
