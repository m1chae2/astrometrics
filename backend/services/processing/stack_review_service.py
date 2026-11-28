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
job with ``jobs:query`` (``detail="full"``). When the job ends, its result is
stored in the job's ``output_metrics["result"]``. A call that fails inside the
short wait raises its error to the caller. A later failure shows up as a
failed job with a message.

The service also answers three quick reads and one write of the Image
Processing Display that adapt the app's parameters to the library: the stack
summary (``processing:stack_summary``), the objects the picture's stretch can
protect (``processing:list_bright_objects``) and the saved choice of one
(``processing:set_bright_object``).
"""

import logging
import re
import threading
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from astrometricslib import EnhancementSettings, PreviewBrightObject
from astrometricslib.arguments import check_frames
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.jobs import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    report_started_jobs,
    run_as_background_job,
)
from backend.services.processing.equipment_choice import frames_for_selection, stack_for_equipment
from backend.services.subject_resolver import SubjectResolver

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
        The job to follow with ``jobs:query``. `None` when the work
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


_NAME_PREFIX = "NAME "
"""SIMBAD's mark of a common name, which a spelling of a name may leave out."""


def _spelling_key(name: str) -> str:
    """Write an object name so case, spaces and SIMBAD's mark do not matter.

    Parameters
    ----------
    name : `str`
        A name, such as ``"ngc7635"`` or ``"NAME Bubble Nebula"``.

    Returns
    -------
    key : `str`
        Upper case, without spaces or underscores, and without a leading
        ``NAME``.
    """
    text = re.sub(r"\s+", " ", name.replace("_", " ")).strip().upper().removeprefix(_NAME_PREFIX)
    return text.replace(" ", "")


class _QuickReads:
    """The quick stack reads and the bright-object choice of the service."""

    _astrometrics: Any
    _subjects: SubjectResolver

    def get_stack_summary(self, target: str, kind: Literal["imaging", "spectral"] = "imaging") -> Any:
        """Summarize a target's current stack (``processing:stack_summary``).

        Parameters
        ----------
        target : `str`
            The target id.
        kind : `str`, optional
            ``"imaging"`` (default) or ``"spectral"`` stack.

        Returns
        -------
        summary : `StackSummary`
            The library's summary of the saved stack.
        """
        return self._astrometrics.processing.stacking.get_summary(self._subjects.target(target), kind)

    def list_bright_objects(self, target: str, refresh: bool = False) -> Any:
        """List the objects the picture's stretch can protect.

        Parameters
        ----------
        target : `str`
            The target id.
        refresh : `bool`, optional
            Ask SIMBAD again even when an answer is stored.

        Returns
        -------
        choices : `BrightObjectChoices`
            The objects, or none and the reason, with the saved choice.
        """
        return self._astrometrics.processing.enhancement.get_bright_objects(
            self._subjects.target(target), force=refresh
        )

    def set_bright_object(
        self,
        target: str,
        mode: Literal["auto", "off", "object"] = "auto",
        simbad_id: str | None = None,
        ra_deg: float | None = None,
        dec_deg: float | None = None,
        major_axis_arcmin: float | None = None,
        name: str | None = None,
    ) -> PreviewBrightObject:
        """Save which object the picture's masked stretch protects.

        The choice is saved on the target and read the next time its picture
        is made. ``"auto"`` lets the stretch use the target itself, when it
        is a nebula or a galaxy; ``"off"`` never uses the masked stretch;
        ``"object"`` protects the object given.

        Parameters
        ----------
        target : `str`
            The target id.
        mode : `str`, optional
            ``"auto"`` (default), ``"off"`` or ``"object"``.
        simbad_id : `str`, optional
            With ``"object"``, SIMBAD's name of the object.
        ra_deg, dec_deg : `float`, optional
            With ``"object"``, its position in degrees.
        major_axis_arcmin : `float`, optional
            With ``"object"``, its major axis in arcminutes, if known.
        name : `str`, optional
            With ``"object"``, instead of the name and position: any spelling
            of an object the stack's list holds (``"ngc7635"`` names
            ``NGC 7635``).

        Returns
        -------
        choice : `PreviewBrightObject`
            The choice as saved.

        Raises
        ------
        InvalidArgumentError
            If ``mode`` is unknown, ``"object"`` lacks a name or a position
            inside the sky, or ``name`` is not in the stack's list (the
            message lists the objects that are).
        """
        if mode not in ("auto", "off", "object"):
            raise InvalidArgumentError(f"Unknown mode {mode!r}. Choose from: auto, off, object.")
        subject = self._subjects.target(target)
        if mode == "object" and name and ra_deg is None and dec_deg is None:
            listing = self._astrometrics.processing.enhancement.get_bright_objects(subject)
            listed = next(
                (item for item in listing.objects if _spelling_key(item.simbad_id) == _spelling_key(name)),
                None,
            )
            if listed is None:
                names = ", ".join(item.simbad_id for item in listing.objects) or "none"
                raise InvalidArgumentError(
                    f"{name!r} is not an object in the frame of {listing.target_id!r}. Objects in the frame: "
                    f"{names}." + (f" {listing.message}" if listing.message else ""),
                    details={"target": listing.target_id, "name": name},
                )
            simbad_id, ra_deg, dec_deg = listed.simbad_id, listed.ra_deg, listed.dec_deg
            major_axis_arcmin = listed.major_axis_arcmin
        if mode == "object":
            if not simbad_id or ra_deg is None or dec_deg is None:
                raise InvalidArgumentError("Mode 'object' needs simbad_id, ra_deg and dec_deg.")
            if not (0.0 <= ra_deg < 360.0 and -90.0 <= dec_deg <= 90.0):
                raise InvalidArgumentError("The position of the object is outside the sky.")
            if major_axis_arcmin is not None and not major_axis_arcmin > 0.0:
                raise InvalidArgumentError("major_axis_arcmin must be positive.")
            choice = PreviewBrightObject(
                mode="object",
                simbad_id=simbad_id,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                major_axis_arcmin=major_axis_arcmin,
            )
        else:
            choice = PreviewBrightObject(mode=mode)
        self._astrometrics.targets.update(subject, preview_bright_object=choice)
        return choice


class StackReviewService(_QuickReads):
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
        self._subjects = SubjectResolver(astrometrics)
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
        NotFoundError
            If a spectral stack is asked for and the target has none.
        """
        enhancement = self._astrometrics.processing.enhancement
        target = self._subjects.target(target_id)
        configuration_key = None
        stack: Any = stack_path
        if camera_id or focal_length_mm is not None:
            if stack_path or kind == "spectral":
                raise InvalidArgumentError(
                    "A camera and optic choose an imaging stack; give them without stack_path, and not for "
                    "a spectral stack (spectral stacks are kept only for the main configuration).",
                    details={"target_id": target_id, "kind": kind},
                )
            stack = stack_for_equipment(self._astrometrics, target, camera_id, focal_length_mm)
            configuration_key = stack.configuration_key
        elif kind == "spectral" and not stack_path:
            stack = target.spectral_stacking.stacked_image
            if not stack:
                raise NotFoundError(
                    f"Target '{target_id}' has no spectral stack.", details={"target": target_id}
                )
        settings = EnhancementSettings(
            denoise=denoise,
            denoise_strength=denoise_strength,
            star_toning=star_toning,
            starnet=bool(starnet),
            star_strength=star_strength,
            star_stretch=star_stretch,
        )

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
                    # The button always makes the picture again, even when it
                    # is up to date.
                    return enhancement.run(target, settings, stack, force=True)
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
        stacks = self._astrometrics.quality.stacks

        def compare() -> Any:
            """Measure the chosen stack against the previous one.

            Returns
            -------
            report : `StackQualityReport`
                The library's report.

            Raises
            ------
            NotFoundError
                If the target has no spectral stack when one was asked for.
            """
            target = self._subjects.target(target_id)
            stack = None
            if kind == "spectral":
                stack = target.spectral_stacking.stacked_image
                if not stack:
                    raise NotFoundError(
                        f"Target '{target.id}' has no spectral stack.",
                        details={"target": target.id, "kind": kind},
                    )
            return stacks.measure(target, stack=stack, include=[], compare_to="previous")

        return self._start(compare)

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
        target = self._subjects.target(target_id)
        if image_files is not None:
            if any(value is not None for value in choices):
                raise InvalidArgumentError(
                    "Pass image_files, or choose the frames with kind, filter_name, first_file, "
                    "last_file, since, until, camera_id and focal_length_mm, not both."
                )
            # The frame-list check reads no frame, so a bad list is refused
            # before a job starts.
            frames = check_frames(self._frames_from_list(target_id, image_files), target)
        else:
            frames = frames_for_selection(
                self._astrometrics,
                target,
                kind=kind,
                filter_name=filter_name,
                first_file=first_file,
                last_file=last_file,
                since=since,
                until=until,
                camera_id=camera_id,
                focal_length_mm=focal_length_mm,
            )
        stacking = self._astrometrics.processing.stacking
        return self._start(lambda: stacking.predict(target, frames))

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
