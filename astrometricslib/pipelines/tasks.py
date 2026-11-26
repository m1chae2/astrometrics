"""Everything involved in running analyses across pipelines.

Three layers, smallest first:

- `stack_frames_with_timeout` runs Siril stacking as a tracked job (so
  it shows up in the UI's job tracker), abandoning it if it takes too
  long, since the underlying process can occasionally hang.
- `analyze_target` picks which single analysis pipeline runs, by name
  ("astrometry", "spectroscopy", "photometry", or "asteroid_detection"),
  via `PIPELINE_RUNNERS` -- adding a fifth mode means adding a module
  under `pipelines/` and one entry to that dict; nothing else here
  needs to change.
- `run_full_pipeline` runs every stage for one target, start to finish:
  stacks its raw frames, plate-solves the result, tracks star
  brightness (photometry), and pulls out light spectra (spectroscopy)
  when there are any. It saves the target's record after each stage,
  not just once at the end, so a later stage crashing (a bad frame, a
  timed-out solve, an OOM kill) does not throw away an earlier stage's
  results along with it -- the same per-stage save discipline
  `backend/services/analysis/analysis_orchestrator.py` already uses
  for UI-triggered single-stage runs. Its only caller is
  `target_batch.py`, which runs many targets through this same sequence
  in parallel worker processes; this module doesn't know or care about
  that, it just runs one target's full sequence.

Every runner `analyze_target` can dispatch to takes the same five
arguments (``target``, ``frames``, ``filter_type``, ``catalog_access``,
``path``, plus ``**kwargs``) even though most of them ignore some of it
-- astrometry and spectroscopy never look at ``frames``/``filter_type``,
and asteroid recovery does not even use ``catalog_access``. One shared
signature is what lets `PIPELINE_RUNNERS` dispatch on name alone,
instead of every call site needing to know which pipeline wants which
subset of arguments.
"""

import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.errors import (
    AstrometricsError,
    ConflictError,
    InvalidArgumentError,
    ProcessingError,
)
from astrometricslib.foundation.jobs.runner import current_job_id, registered_job, report_started_jobs
from astrometricslib.foundation.parameter_context import (
    equipment_parameter_context,
    frames_parameter_context,
    record_basis,
)
from astrometricslib.foundation.run_abandonment import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    raise_if_abandoned,
)
from astrometricslib.foundation.run_tally import RunTally
from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
from astrometricslib.models.processing_results import ProcessTargetResult
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.models.tuned_parameter import RunBasis
from astrometricslib.pipelines.analysis_stage_names import ANALYSIS_STAGES
from astrometricslib.pipelines.asteroid_detection.runner import run_asteroid_detection_analysis
from astrometricslib.pipelines.astrometry.runner import run_astrometry_analysis
from astrometricslib.pipelines.photometry.runner import run_photometry_analysis
from astrometricslib.pipelines.shared.analysis_configuration import (
    analysis_frames_and_configuration,
    context_filter_name,
    report_left_out,
)
from astrometricslib.pipelines.shared.camera_passes import ProcessingFrameSelection
from astrometricslib.pipelines.shared.frame_grouping import (
    frame_is_spectral,
    select_frames_for_processing,
    split_standard_and_spectral_frames,
)
from astrometricslib.pipelines.shared.provenance_recording import stacked_image_entity_id
from astrometricslib.pipelines.spectroscopy.runner import run_spectroscopy_analysis
from astrometricslib.pipelines.spectroscopy.spectral_variation import run_spectral_variation_analysis
from astrometricslib.utilities.exceptions import DATA_ERRORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

logger = logging.getLogger(__name__)

# -- Stacking, as a tracked job with a hard timeout --------------------

# Maximum allowed time for an external stacking task to run.
# The timeout grows with the number of frames (N) because adding more
# frames increases the amount of work the stacking process has to do.
#
# The base time (300s) and per-frame time (30s) act as a safety net
# to detect if the process is stuck, not as an expected run time. They
# make sure that slow color processing can finish without being cut off
# early, while giving plenty of extra time for fast black-and-white processing.
STACKING_TIMEOUT_BASE_SECONDS = 300
STACKING_TIMEOUT_PER_FRAME_SECONDS = 30

# Retained for callers that still pass an explicit budget; equivalent to
# the previous flat value and used only as a floor.
STACKING_TIMEOUT_SECONDS = 600

# Poll interval while waiting on a stacking thread. Small relative to
# the minimum 600s budget, so a genuine timeout overshoots by at most
# this much, while still letting the deadline pick up lock waits that
# accrue after the wait began.
_STACKING_TIMEOUT_POLL_SECONDS = 2.0

# How long to wait, after giving up on a stack and stopping its Siril, for the
# stack's thread to end and give its slot back. Stopping Siril takes about a
# quarter of a second (see `run_abandonment.STOP_GRACE_SECONDS`) and the
# thread's clean-up removes a scratch folder, so ten seconds is generous. A
# chosen bound, not a measured one: if it passes, the thread is simply left to
# finish on its own.
_ABANDONED_THREAD_JOIN_SECONDS = 10.0


def compute_stacking_timeout_seconds(frame_count: int) -> int:
    """Calculate how long the stacking process is allowed to run.

    Stacking takes longer when there are more images (frames). This
    function adds a base time allowance to a per-image allowance to
    set a deadline. If it takes longer than this, something is probably stuck.

    Parameters
    ----------
    frame_count : `int`
        The number of images being stacked.

    Returns
    -------
    timeout_seconds : `int`
        The maximum allowed time in seconds.
    """
    scaled_timeout = STACKING_TIMEOUT_BASE_SECONDS + STACKING_TIMEOUT_PER_FRAME_SECONDS * max(0, frame_count)
    return int(max(STACKING_TIMEOUT_SECONDS, scaled_timeout))


def _stack_with_job_tracking(
    target: Target,
    frames_to_stack: list[FrameRecord],
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
) -> str | None:
    """Run Siril stacking, registered as a job the UI can track.

    Parameters
    ----------
    target : Target
        The target being stacked.
    frames_to_stack : list
        The frames to combine.
    measured_at : `datetime`, optional
        When the run started, with a time zone, for the quality rows the
        stacking stage stores. The caller supplies it because pipeline code
        does not read the clock. `None` (for tests only) stores no rows.
    quality_recording : `FrameQualityRecording`, optional
        The configuration and shared calibration library for those rows.
        `None` stores no rows.

    Returns
    -------
    stacked_path : str or None
        The path to the final combined image file, or None if it failed.
    """
    with registered_job(
        enabled=True,
        job_type="stacking",
        target_id=target.id,
        completed_message=f"[{target.id}] Stacking completed",
        failed_message=f"[{target.id}] Stacking failed",
    ) as job:
        job.info(f"[{target.id}] Stacking job started (Job: {job.job_id}).")

        from astrometricslib.pipelines.stacking import stage as stacking_tasks

        stacked_path = stacking_tasks.stack_frames(
            target,
            frames_to_stack=frames_to_stack,
            job_id=job.job_id,
            measured_at=measured_at,
            quality_recording=quality_recording,
        )
        # Stacking can finish without raising and still produce no
        # image, so the outcome is decided here rather than left to the
        # context manager's "no exception means success" default.
        job.mark(
            "completed" if stacked_path else "failed",
            100,
            message="Stacking completed" if stacked_path else "Stacking failed",
        )
        return stacked_path


def _record_abandoned_stack(
    target: Target,
    measured_at: datetime | None,
    quality_recording: FrameQualityRecording | None,
    *,
    working_seconds: float,
    budget_seconds: float,
    job_id: str | None = None,
) -> None:
    """Store the quality row that says a stack was abandoned.

    The row is a processing-decision measurement (`stacking.abandoned_after_s`)
    on the stacking run. Its check outcome is ``failed``, so it is a warning
    of the stacking job. It is stored even when the target has no stack
    summary yet. Nothing is stored when the caller gave no time or no
    recording information, as in tests. A failure to store never reaches the
    caller.

    Parameters
    ----------
    target : `Target`
        The target whose stack was abandoned.
    measured_at : `datetime` or `None`
        When the run started, with a time zone.
    quality_recording : `FrameQualityRecording` or `None`
        The configuration that locates the quality database.
    working_seconds : `float`
        How long the stack worked, not counting time waiting for the Siril
        lock.
    budget_seconds : `float`
        The time budget the stack ran past.
    job_id : `str`, optional
        The stacking job that was abandoned. The row carries it, so the job
        can list the timeout as one of its warnings.
    """
    if measured_at is None or quality_recording is None:
        return
    from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
    from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
        PipelineRunContext,
        record_run_measurements,
    )
    from astrometricslib.pipelines.stacking.post_processing.record_quality import (
        abandoned_stack_measurements,
    )

    context = PipelineRunContext.from_options(
        target.id, "stacking", STACKING_PIPELINE_VERSION, {"job_id": job_id, "measured_at": measured_at}
    )
    if context is None:
        return
    record_run_measurements(
        abandoned_stack_measurements(context, working_seconds, budget_seconds),
        config=quality_recording.configuration,
    )


def _record_target_size(target: Target) -> None:
    """Record the target's catalog size as the run's target-size basis.

    The values sized to the target (issue #175) follow it. A target with no
    stored size (a point source, or one SIMBAD gives no size for) records
    nothing, and those values keep their reference-setup sizes.

    Parameters
    ----------
    target : `Target`
        The target being stacked or analyzed.
    """
    major_axis_arcmin = getattr(target, "major_axis_arcmin", None)
    if major_axis_arcmin:
        record_basis(RunBasis.TARGET_SIZE_ARCSEC, float(major_axis_arcmin) * 60.0)


def stack_frames_with_timeout(
    target: Target,
    frames_to_stack: list[FrameRecord],
    timeout_seconds: int | None = None,
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
) -> str | None:
    """Run the image stacker with a time limit so it doesn't freeze forever.

    The Siril stacking program can sometimes get stuck. This function
    runs it in the background. If it takes too long, this function stops
    that stack's own Siril processes (no others), so the background thread
    ends and gives back its place in the job queue, and returns.
    The time limit only counts time spent actually working, not time
    spent waiting in line for the CPU.

    Parameters
    ----------
    target : Target
        The target name being stacked.
    frames_to_stack : list
        The image frames we want to combine.
    timeout_seconds : int, optional
        The maximum time allowed in seconds. If blank, it calculates
        a budget based on the number of frames.
    measured_at : `datetime`, optional
        When the run started, with a time zone, for the quality rows the
        stacking stage stores. The caller supplies it because pipeline code
        does not read the clock. `None` (for tests only) stores no rows.
    quality_recording : `FrameQualityRecording`, optional
        The configuration and shared calibration library for those rows.
        `None` stores no rows.

    Returns
    -------
    stacked_path : str or None
        The path to the combined image file, or None if it timed out.

    Raises
    ------
    Exception
        Any error that happened during stacking is passed up to here.
    """  # ruff: ignore[docstring-extraneous-exception] -- `raise
    # outcome["error"]` re-raises a captured exception object,
    # which pydoclint cannot resolve to a declared type statically.
    from astrometricslib._internal.drivers import siril_interface

    if timeout_seconds is None:
        timeout_seconds = compute_stacking_timeout_seconds(len(frames_to_stack))

    outcome: dict[str, Any] = {}
    # This stack's own total of Siril-lock waiting. The stacking thread binds
    # it, so the waits of other stacks in the process are not mixed in.
    lock_wait = RunTally()
    # This stack's own Siril processes. Giving up on the stack stops them (and
    # only them), so the thread ends and gives its slot back.
    abandonment = RunAbandonment()
    # Filled by the stacking thread with the id of its job (see below).
    stacking_job_ids: list[str | None] = []

    def _run_stacking() -> None:
        try:
            # The thread does not inherit the caller's parameter context, so
            # it sets the one of the frames' setup and filter itself.
            with (
                siril_interface.bind_lock_wait(lock_wait),
                bind_abandonment(abandonment),
                report_started_jobs(lambda handle: stacking_job_ids.append(handle.job_id)),
                frames_parameter_context(frames_to_stack),
            ):
                _record_target_size(target)
                outcome["path"] = _stack_with_job_tracking(
                    target, frames_to_stack, measured_at, quality_recording
                )
        except RunAbandonedError:
            # The caller gave up and already returned; there is nobody to
            # report to, and nothing may be recorded as a result.
            logger.info("[%s] The abandoned stacking thread has stopped.", target.id)
        except Exception as stacking_error:
            # This runs in its own thread, so any error is kept here and
            # raised again in the calling thread below. The traceback is
            # logged at debug level in case that never happens (a timeout).
            logger.debug("[%s] Stacking thread raised an error.", target.id, exc_info=True)
            outcome["error"] = stacking_error

    stacking_thread = threading.Thread(target=_run_stacking, daemon=True)
    started_at = time.monotonic()
    stacking_thread.start()

    # Polled rather than a single join so the deadline can absorb lock
    # waits that only become known while this stack is queued. The
    # interval is short enough that the overshoot past a real timeout is
    # negligible against a budget measured in minutes.
    while True:
        stacking_thread.join(_STACKING_TIMEOUT_POLL_SECONDS)
        if not stacking_thread.is_alive():
            break
        elapsed_seconds = time.monotonic() - started_at
        if elapsed_seconds >= timeout_seconds + lock_wait.total:
            break

    if stacking_thread.is_alive():
        lock_wait_seconds = lock_wait.total
        logger.warning(
            "[%s] Stacking timed out after %s seconds of working time (%.0fs of Siril-lock wait "
            "excluded). Abandoning this stack.",
            target.id,
            timeout_seconds,
            lock_wait_seconds,
        )
        # Stop this stack's Siril, so its thread ends and gives up its slot.
        abandonment.abandon()
        # Let the thread unwind, so the slot is free when this returns.
        stacking_thread.join(_ABANDONED_THREAD_JOIN_SECONDS)
        # A timeout is a quality event, not just a log line. It is recorded
        # as a row on this run (`stacking.abandoned_after_s`), never on the
        # target's earlier stack summary, which describes a different run.
        _record_abandoned_stack(
            target,
            measured_at,
            quality_recording,
            working_seconds=max(0.0, time.monotonic() - started_at - lock_wait_seconds),
            budget_seconds=timeout_seconds,
            job_id=next((job_id for job_id in stacking_job_ids if job_id), None),
        )
        return None

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("path")


# -- Picking which single analysis pipeline runs, by name --------------

PIPELINE_RUNNERS = {
    "astrometry": run_astrometry_analysis,
    "spectroscopy": run_spectroscopy_analysis,
    "spectral_variation": run_spectral_variation_analysis,
    "photometry": run_photometry_analysis,
    "asteroid_detection": run_asteroid_detection_analysis,
}


def analyze_target(
    target: Target,
    frames: list[FrameRecord] | None = None,
    pipeline_type: str = "astrometry",
    filter_type: str | None = None,
    catalog_access=None,  # ruff: ignore[missing-type-function-argument]
    register_job: bool = True,
    path: str | None = None,
    measured_at: datetime | None = None,
    analyzed_stack: str | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Run a specific analysis pipeline on the given target.

    You can ask it to run "astrometry" (finding star positions),
    "spectroscopy" (light spectrum), "photometry" (brightness changes),
    or "asteroid_detection" (finding moving rocks).

    Parameters
    ----------
    register_job : bool, optional
        Set to True (default) if you want this run to automatically
        show up in the user interface's job tracker. Set to False if
        you are calling this from a tool that already tracks its own jobs
        (to prevent double-counting).
    measured_at : datetime, optional
        The time stamped on the quality measurements the pipeline stores,
        with a time zone. Pipeline code never reads the clock, so the caller
        supplies it. With `None` (for tests only) the pipeline stores no
        quality measurements and logs a warning.
    analyzed_stack : str, optional
        For a frame-based stage (photometry), the imaging stack whose
        camera and optic the frames must match, when it is not the target's
        recorded stack. `None` uses the recorded stack.

    Returns
    -------
    result : dict
        A dictionary with the final results and status info.

    Raises
    ------
    ConflictError
        If you ask for an unknown pipeline type, or if we don't have
        the right images needed to run it.
    """
    if catalog_access is None:
        from astrometricslib._internal.drivers.catalog_access import CatalogAccess

        catalog_access = CatalogAccess()

    # Record this run in the job list so work started from a script,
    # notebook, or the command line shows up in the user interface the
    # same way a run started from the interface does. Skipped when
    # register_job=False -- see that parameter's docstring.
    with registered_job(
        enabled=register_job,
        job_type="analysis",
        target_id=target.id,
        completed_message=f"[{target.id}] Analysis completed successfully.",
        failed_message=f"[{target.id}] Analysis failed.",
    ) as job:
        job.info(
            f"[{target.id}] Analysis job started for {target.id} (type: {pipeline_type}, Job: {job.job_id})"
        )

        # Resolve image path for astrometry/spectroscopy modes
        # if not explicitly provided
        if not path and pipeline_type in ("astrometry", "spectroscopy"):
            if frames:
                path = frames[0].path
            elif pipeline_type == "spectroscopy" and target.spectral_stacking.stacked_image:
                path = target.spectral_stacking.stacked_image
            elif pipeline_type == "astrometry" and target.stacking.stacked_image:
                path = target.stacking.stacked_image
            elif target.frames:
                path = target.frames[0].path
            else:
                raise ConflictError(
                    f"No frames or stacked image available for {pipeline_type} analysis"
                    f" on target {target.id}."
                )

        # The rows this run stores carry the id of the job that made them,
        # so `Jobs.query(detail="warnings")` can list them. With no job of its
        # own (register_job=False), the run uses the job it is running inside,
        # such as a caller's batch job. With neither, the rows have no job.
        kwargs["job_id"] = job.job_id or kwargs.get("job_id") or current_job_id()
        kwargs["measured_at"] = measured_at
        # Parameters are read for the camera and optic of the image analyzed:
        # the stack's own header for a stack-based stage, the stack's camera
        # and optic for a frame-based one. Frames of another camera or optic
        # are never mixed in: a frame-based stage keeps only the frames of
        # the analyzed configuration and reports the rest.
        analysis_camera, kept_frames, left_out = analysis_frames_and_configuration(
            target, pipeline_type, frames, path, get_configuration(), stack_path=analyzed_stack
        )
        if kept_frames is not None:
            frames = kept_frames
        if left_out and analysis_camera is not None:
            report_left_out(target.id, left_out, analysis_camera, job)
            kwargs["frames_left_out"] = left_out
        if analysis_camera is not None:
            context_manager = equipment_parameter_context(
                analysis_camera.camera,
                analysis_camera.focal_length_mm,
                context_filter_name(pipeline_type, frames or target.frames or [], analysis_camera),
            )
        else:
            context_manager = frames_parameter_context(frames or target.frames or [])
        with context_manager:
            _record_target_size(target)
            return _run_analysis_pipeline_match(
                target, frames, pipeline_type, filter_type, catalog_access, path, **kwargs
            )


def _run_analysis_pipeline_match(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument]
    pipeline_type,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    catalog_access,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument]
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    runner = PIPELINE_RUNNERS.get(pipeline_type)
    if runner is None:
        raise InvalidArgumentError(f"Unknown analysis type: {pipeline_type}")
    return runner(target, frames, filter_type, catalog_access, path, **kwargs)


# -- Running chosen analysis stages for one target ----------------------

STAGE_OPTIONS = {
    "astrometry": frozenset({"path"}),
    "photometry": frozenset({"frames", "filter_name", "use_astrometry_seed", "max_workers"}),
    "spectroscopy": frozenset({"path", "limit"}),
    "spectral_variation": frozenset({"frames", "max_workers"}),
    "asteroids": frozenset({"moving_object_config"}),
}
"""The options each stage accepts in its options dictionary."""

_PIPELINE_TYPES = {
    "astrometry": "astrometry",
    "photometry": "photometry",
    "spectroscopy": "spectroscopy",
    "spectral_variation": "spectral_variation",
    "asteroids": "asteroid_detection",
}

STAGE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    # Astrometry needs only a stack to solve.
    "astrometry": (),
    # Photometry names its stars with the plate solution of the imaging stack:
    # with `use_astrometry_seed` it borrows the solution of the solved stack
    # of the same camera and focal length for a session's first frame when
    # that frame has none (`photometry/batch.py`,
    # `_run_variability_analysis_for_session`). Without it, photometry
    # plate-solves each session itself, which costs more time and can fail
    # on a sparse frame. The code can still run without astrometry. The
    # rule is that photometry must not run on a guess: a project ruling of
    # 2026-11-22 makes astrometry a hard prerequisite, enforced here in the
    # stage order and not inside the pipelines (their fallbacks stay).
    "photometry": ("astrometry",),
    # Spectroscopy takes its field center from the solved imaging stack
    # taken with the same camera and telescope
    # (`shared/target_center_hint.py`, about lines 104-132; otherwise it
    # uses the mount's position, up to about 24 arcminutes off), and it names
    # the zero-order stars from the star list astrometry keeps next to the
    # stack (`shared/solved_star_list.py`, read by `spectroscopy/runner.py`,
    # `_registration_reference_candidates`). The spectral
    # stack is never plate-solved. Same ruling as photometry.
    "spectroscopy": ("astrometry",),
    # The spectral variation stage follows each star's spectrum across the
    # raw spectral frames. It reads the stars the master-stack spectroscopy
    # found and must not overwrite that result, so it needs spectroscopy
    # first (and spectroscopy needs astrometry, so the chain runs in order).
    "spectral_variation": ("spectroscopy",),
    # The asteroid search works on a stack and needs no solution.
    "asteroids": (),
}
"""The stages each stage needs first, by stage name.

Astrometry is a hard prerequisite of photometry and spectroscopy. A
prerequisite runs before the stage that needs it only when its result is
missing or out of date (see `stage_result_is_current`). Each stage appears
as a key, so a missing key is a coding error.
"""


def imaging_stack_path(target: Target) -> str | None:
    """Find the imaging stack that astrometry solves, if it exists.

    Parameters
    ----------
    target : `Target`
        The target to read.

    Returns
    -------
    stack_path : `str` or `None`
        The path of the target's imaging stack, or `None` when the target
        has none or the file is gone.
    """
    stack_path = target.stacking.stacked_image
    if stack_path and os.path.exists(stack_path):
        return stack_path
    return None


def _as_utc(moment: datetime) -> datetime:
    """Give a time a time zone, taking a bare time to be UTC.

    Parameters
    ----------
    moment : `datetime`
        A time with or without a time zone.

    Returns
    -------
    moment_utc : `datetime`
        The same time with a time zone.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def stage_result_is_current(target: Target, stage: str, image_path: str | None = None) -> bool:
    """Say whether a stage's saved result matches the target's current stack.

    The rule, for astrometry (the only stage that others depend on):

    - The result is current when the target has an astrometry summary and an
      imaging stack on disk.
    - The summary must not be older than the stack. The stack's time is the
      ``created_at`` of its quality summary, or the file's modification time
      when the stack has no summary.
    - The summary's ``upstream_entity_id`` must be `None` (the run recorded
      no stack, as when a job is not registered) or name the current stack.
      That id comes from the stack's path, so it changes only when the
      path changes. A restack at the same path is caught by the time check.
    - When the caller names an image of its own (``image_path``), the result
      is never current. A run records the stack it solved, not the file it
      was given, so nothing saved says which file the result came from.
      Rerunning is the safe answer.

    Parameters
    ----------
    target : `Target`
        The target to check.
    stage : `str`
        The stage, which must be ``"astrometry"``.
    image_path : `str`, optional
        An image the caller wants the stage to run on instead of the stack.

    Returns
    -------
    is_current : `bool`
        `True` when the stage's result can be used as it is. `False` when it
        is missing or out of date, or when there is no imaging stack.
    """
    return stage_staleness_reason(target, stage, image_path) is None


def stage_staleness_reason(target: Target, stage: str, image_path: str | None = None) -> str | None:
    """Say why a stage's saved result cannot be used, or `None` when it can.

    Applies the rule in `stage_result_is_current`, which gives the details.

    Parameters
    ----------
    target : `Target`
        The target to check.
    stage : `str`
        The stage, ``"astrometry"`` or ``"spectroscopy"``.
    image_path : `str`, optional
        An image the caller wants the stage to run on instead of the stack.

    Returns
    -------
    reason : `str` or `None`
        A short phrase such as "its result is missing", or `None` when the
        saved result is current.

    Raises
    ------
    InvalidArgumentError
        If the stage is neither ``"astrometry"`` nor ``"spectroscopy"``, the
        two stages another stage can need.
    """
    if stage == "spectroscopy":
        return _spectroscopy_staleness_reason(target, image_path)
    if stage != "astrometry":
        raise InvalidArgumentError(f"No staleness rule exists for the {stage!r} stage.")
    if image_path:
        return "an image was named, so the saved result may not match it"
    summary = target.quality.astrometry
    stack_path = imaging_stack_path(target)
    if summary is None:
        return "its result is missing"
    if stack_path is None:
        return "the target has no imaging stack to compare it with"
    stack_summary = target.stacking.quality_summary
    if stack_summary is not None:
        stack_time = _as_utc(stack_summary.created_at)
    else:
        stack_time = datetime.fromtimestamp(os.path.getmtime(stack_path), UTC)
    if _as_utc(summary.created_at) < stack_time:
        return "its result is older than the stack"
    upstream_entity_id = summary.upstream_entity_id
    if upstream_entity_id is not None and upstream_entity_id != stacked_image_entity_id(
        target.id, stack_path
    ):
        return "its result was made for a different stack"
    return None


def _is_same_file(first: str | None, second: str | None) -> bool:
    """Say whether two paths name the same file.

    A caller that passes the target's own stack as the image has named no
    other image, so the saved result still matches it.

    Parameters
    ----------
    first, second : `str` or `None`
        The paths.

    Returns
    -------
    same : `bool`
        `True` when both are given and name the same file.
    """
    return bool(first and second and os.path.realpath(first) == os.path.realpath(second))


def _spectroscopy_staleness_reason(target: Target, image_path: str | None) -> str | None:
    """Say why the saved spectroscopy result cannot be used, or `None`.

    The result is current when the target has a spectroscopy summary made
    from the spectral stack, and the summary is no older than that stack
    (its quality summary's time, or the file's modification time when it has
    none). A summary built from raw frames (an older run that followed the
    frames only) is not a stack result, so it is out of date. An image the
    caller names is never current, unless it is the target's own spectral
    stack, which the app always sends with the spectroscopy options.

    Parameters
    ----------
    target : `Target`
        The target to check.
    image_path : `str` or `None`
        An image the caller wants the stage to run on instead of the stack.

    Returns
    -------
    reason : `str` or `None`
        A short phrase, or `None` when the saved result is current.
    """
    if image_path and not _is_same_file(image_path, target.spectral_stacking.stacked_image):
        return "an image was named, so the saved result may not match it"
    summary = target.quality.spectroscopy
    if summary is None:
        return "its result is missing"
    if summary.upstream_quality_summary_reference == "raw_frames":
        return "its result came from raw frames, not from the spectral stack"
    stack_path = target.spectral_stacking.stacked_image
    if not stack_path or not os.path.exists(stack_path):
        return "the target has no spectral stack to compare it with"
    stack_summary = target.spectral_stacking.quality_summary
    if stack_summary is not None:
        stack_time = _as_utc(stack_summary.created_at)
    else:
        stack_time = datetime.fromtimestamp(os.path.getmtime(stack_path), UTC)
    if _as_utc(summary.created_at) < stack_time:
        return "its result is older than the spectral stack"
    return None


def _prerequisite_blocker(target: Target, stage: str, options: dict[str, Any]) -> str | None:
    """Say why a prerequisite stage cannot run, or `None` when it can.

    Astrometry runs on the image the caller names (``path`` in its options)
    or, when none is named, on the imaging stack.

    Parameters
    ----------
    target : `Target`
        The target the stage would run on.
    stage : `str`
        The prerequisite stage.
    options : `dict`
        The stage's options.

    Returns
    -------
    reason : `str` or `None`
        Why the stage cannot run, or `None`.
    """
    if stage == "spectroscopy":
        if options.get("path") or target.spectral_stacking.stacked_image:
            return None
        return "the target has no spectral stack to extract from and no image was given"
    if stage != "astrometry":
        return None
    named_path = options.get("path")
    if named_path:
        return None if os.path.exists(named_path) else f"the image {named_path} does not exist"
    if imaging_stack_path(target) is None:
        return "the target has no imaging stack to solve and no image was given"
    return None


def _has_spectral_input(
    target: Target, options: dict[str, Any], frame_selection: ProcessingFrameSelection | None
) -> bool:
    """Say whether the spectroscopy stage has anything to work on.

    Parameters
    ----------
    target : `Target`
        The target.
    options : `dict`
        The spectroscopy stage's options.
    frame_selection : `ProcessingFrameSelection` or `None`
        The chosen frames, or `None` to use every frame of the target.

    Returns
    -------
    has_input : `bool`
        `True` when a path was named, the target has a spectral stack, or the
        frames hold a spectral frame.
    """
    spectral_candidates = (
        frame_selection.spectral_frames if frame_selection is not None else target.frames or []
    )
    return (
        bool(options.get("path"))
        or bool(target.spectral_stacking.stacked_image)
        or any(frame_is_spectral(frame) for frame in spectral_candidates)
    )


def _has_raw_spectral_frames(
    target: Target, options: dict[str, Any], frame_selection: ProcessingFrameSelection | None
) -> bool:
    """Say whether the spectral variation stage has raw spectral frames.

    Parameters
    ----------
    target : `Target`
        The target.
    options : `dict`
        The spectral variation stage's options.
    frame_selection : `ProcessingFrameSelection` or `None`
        The chosen frames, or `None` to use every frame of the target.

    Returns
    -------
    has_frames : `bool`
        `True` when the frames the stage would follow hold a spectral frame.
    """
    candidates = options.get("frames")
    if candidates is None:
        candidates = frame_selection.spectral_frames if frame_selection is not None else target.frames or []
    return any(frame_is_spectral(frame) for frame in candidates)


def _stages_to_add(
    target: Target,
    requested: tuple[str, ...],
    skipped_for_no_input: set[str],
    stage_options: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """List the prerequisite stages to add to a run.

    Parameters
    ----------
    target : `Target`
        The target.
    requested : `tuple` [`str`, ...]
        The stages the caller asked for.
    skipped_for_no_input : `set` [`str`]
        Requested stages that will be skipped for lack of input. They need
        no prerequisites.
    stage_options : `dict` [`str`, `dict`]
        Each stage's options. An astrometry ``path`` is the image to solve.

    Returns
    -------
    added : `tuple` [`str`, ...]
        The prerequisites that are not requested and whose result is
        missing or out of date, in `ANALYSIS_STAGES` order.
    """
    wanted: set[str] = set()
    pending = [stage for stage in requested if stage not in skipped_for_no_input]
    while pending:
        stage = pending.pop()
        for prerequisite in STAGE_PREREQUISITES[stage]:
            image_path = (stage_options.get(prerequisite) or {}).get("path")
            if prerequisite in requested or prerequisite in wanted:
                continue
            reason = stage_staleness_reason(target, prerequisite, image_path)
            if reason is not None:
                logger.info(
                    "[%s] Running %s first: %s needs it and %s", target.id, prerequisite, stage, reason
                )
                wanted.add(prerequisite)
                # The added stage has prerequisites of its own.
                pending.append(prerequisite)
    return tuple(stage for stage in ANALYSIS_STAGES if stage in wanted)


def run_target_stages(
    target: Target,
    stages: tuple[str, ...],
    stage_options: dict[str, dict[str, Any]],
    catalog_access: Any,
    register_job: bool,
    drivers: Drivers | None = None,
    frame_selection: ProcessingFrameSelection | None = None,
    measured_at: datetime | None = None,
    run_prerequisites: bool = True,
    on_stages_planned: Callable[[tuple[str, ...], tuple[str, ...]], None] | None = None,
) -> ProcessTargetResult:
    """Run the chosen analysis stages for one target, always in the same order.

    The order is astrometry, photometry, spectroscopy, spectral variation,
    asteroids, whatever order ``stages`` lists them in. Spectroscopy is
    skipped, with a ``{"status": "skipped", ...}`` entry, when the target
    has no spectral data, and spectral variation when it has no raw
    spectral frames.

    With ``run_prerequisites``, a stage that another stage needs (see
    `STAGE_PREREQUISITES`) is added to the run when its result is missing or
    out of date for the current stack (see `stage_result_is_current`). A
    prerequisite that cannot run, because there is neither an imaging stack
    nor an image named in the stage's options, or the stage fails, blocks the
    stages that need it: they are skipped, with
    the reason ``needs astrometry: <why>``, and are not run on a guess.

    Parameters
    ----------
    target : `Target`
        The target to process. Each stage records its results on it.
    stages : `tuple` [`str`, ...]
        The stages to run, from `ANALYSIS_STAGES`.
    stage_options : `dict` [`str`, `dict`]
        Each stage's own options (see `STAGE_OPTIONS`), keyed by stage.
    catalog_access : `AbstractCatalogAccess`
        The star catalog the stages read and write.
    register_job : `bool`
        Whether each stage's run shows up in the job list.
    drivers : `Drivers`, optional
        The plate solver and SIMBAD driver for the stages that identify
        stars (astrometry and spectroscopy). Any left out is the built-in
        one.
    frame_selection : `ProcessingFrameSelection`, optional
        The camera's and optic's frames to work on (see
        `resolve_processing_frames`). Photometry measures these frames,
        unless its options name ``frames``. Spectroscopy runs only when
        the selection holds spectral frames or the target already has a
        spectral stack. Without it, every frame of the target is used.
    measured_at : `datetime`, optional
        The time stamped on the quality measurements each stage stores,
        with a time zone. The caller supplies it because pipeline code does
        not read the clock. `None` (for tests only) stores no measurements.
    run_prerequisites : `bool`, optional
        Whether to add the prerequisite stages that are missing or out of
        date. Pass `False` only when astrometry has just been run for this
        stack, or when a stage is meant to run alone.
    on_stages_planned : `Callable`, optional
        Called once before the first stage runs, as ``(requested, added)``:
        the stages asked for and the prerequisites that will be added, both
        in running order. A caller uses it to show the plan while the run is
        under way.

    Returns
    -------
    result : `ProcessTargetResult`
        One result per stage that ran, keyed by stage name, the prerequisites
        added (``prerequisites_run``), the stages skipped because a
        prerequisite could not run (``stages_skipped``), and the target's
        quality summaries afterwards.

    Raises
    ------
    RunAbandonedError
        If the run was given up on while a prerequisite was running.
    """
    result = ProcessTargetResult(target_id=target.id)
    requested = tuple(stage for stage in ANALYSIS_STAGES if stage in stages)
    skipped_for_no_input: set[str] = set()
    if "spectroscopy" in requested and not _has_spectral_input(
        target, dict(stage_options.get("spectroscopy") or {}), frame_selection
    ):
        skipped_for_no_input.add("spectroscopy")
    if "spectral_variation" in requested and not _has_raw_spectral_frames(
        target, dict(stage_options.get("spectral_variation") or {}), frame_selection
    ):
        skipped_for_no_input.add("spectral_variation")
    added = (
        _stages_to_add(target, requested, skipped_for_no_input, stage_options) if run_prerequisites else ()
    )
    if on_stages_planned is not None:
        on_stages_planned(requested, added)
    # The reason each prerequisite that cannot run gives, by stage.
    blocked: dict[str, str] = {}
    for stage in ANALYSIS_STAGES:
        if stage not in requested and stage not in added:
            continue
        # A cancelled job stops before the next stage starts.
        raise_if_abandoned()
        prerequisite_failures = [
            f"needs {prerequisite}: {blocked[prerequisite]}"
            for prerequisite in STAGE_PREREQUISITES[stage]
            if prerequisite in blocked
        ]
        if prerequisite_failures and stage in added:
            # An added stage whose own prerequisite failed is blocked too.
            blocked[stage] = "; ".join(prerequisite_failures)
            continue
        if prerequisite_failures and stage in requested:
            reason = "; ".join(prerequisite_failures)
            result.results[stage] = {"status": "skipped", "reason": reason}
            result.stages_skipped[stage] = reason
            logger.info("[%s] Skipping %s: %s", target.id, stage, reason)
            continue
        options = dict(stage_options.get(stage) or {})
        if "filter_name" in options:
            # The stage runner still names this argument ``filter_type``.
            options["filter_type"] = options.pop("filter_name")
        if drivers is not None and stage in (
            "astrometry",
            "photometry",
            "spectroscopy",
            "spectral_variation",
        ):
            options["drivers"] = drivers
        if stage == "photometry" and frame_selection is not None:
            options.setdefault("frames", frame_selection.frames)
            if frame_selection.stack_path:
                # The frames belong to the stack being analyzed, which may be
                # another configuration's stack than the recorded one.
                options.setdefault("analyzed_stack", frame_selection.stack_path)
        if stage == "spectral_variation" and frame_selection is not None:
            options.setdefault("frames", frame_selection.spectral_frames)
        if stage == "spectral_variation" and stage in skipped_for_no_input:
            result.results[stage] = {"status": "skipped", "reason": "target has no raw spectral frames"}
            result.stages_run.append(stage)
            continue
        if stage == "spectroscopy" and stage in skipped_for_no_input:
            result.results[stage] = {"status": "skipped", "reason": "target has no spectral data"}
            result.stages_run.append(stage)
            continue
        if stage in added:
            blocker = _prerequisite_blocker(target, stage, options)
            if blocker is not None:
                blocked[stage] = blocker
                logger.info("[%s] Could not run %s first: %s", target.id, stage, blocker)
                continue
            try:
                stage_result = analyze_target(
                    target,
                    pipeline_type=_PIPELINE_TYPES[stage],
                    catalog_access=catalog_access,
                    register_job=register_job,
                    measured_at=measured_at,
                    **options,
                )
            except RunAbandonedError:
                raise
            except (AstrometricsError, OSError, *DATA_ERRORS) as error:
                logger.warning(
                    "[%s] The %s stage added as a prerequisite failed: %s", target.id, stage, error
                )
                blocked[stage] = f"it failed ({error})"
                continue
            if isinstance(stage_result, dict) and "wcs" in stage_result and stage_result["wcs"] is None:
                blocked[stage] = "the stack could not be plate solved"
                logger.info("[%s] Could not run %s first: %s", target.id, stage, blocked[stage])
                continue
            result.results[stage] = stage_result
            result.stages_run.append(stage)
            result.prerequisites_run.append(stage)
            continue
        result.results[stage] = analyze_target(
            target,
            pipeline_type=_PIPELINE_TYPES[stage],
            catalog_access=catalog_access,
            register_job=register_job,
            measured_at=measured_at,
            **options,
        )
        result.stages_run.append(stage)
    result.quality = target.quality.model_copy()
    return result


# -- Running every stage for one target, start to finish ---------------


def run_full_pipeline(
    target: Target,
    catalog_access: Any,
    config: Any,
    max_workers: int | None = None,
    *,
    camera_name: str,
    focal_length_mm: float | None = None,
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
) -> dict[str, str]:
    """Run the complete start-to-finish processing pipeline for a target.

    This runs stacking, position solving (astrometry), brightness
    tracking (photometry), and light spectrum (spectroscopy) in order,
    then saves the target's data to the database.

    Parameters
    ----------
    target : Target
        The target we want to process.
    catalog_access : Any
        The connection to the stored catalog. Each stage reads from it, and
        the target is saved to it after each stage.
    config : `AppConfiguration`
        The settings, for how many analysis jobs may run at once.
    max_workers : int, optional
        How many parallel processes to use during the brightness tracking step.
    camera_name : str
        The name of the camera to process images for. Any images taken
        by a different camera will be ignored.
    measured_at : `datetime`, optional
        When the run started, with a time zone, for the quality rows the
        stacking stage stores. The caller supplies it because pipeline code
        does not read the clock. `None` (for tests only) stores no rows.
    quality_recording : `FrameQualityRecording`, optional
        The configuration and shared calibration library for those rows. The
        caller builds it once for the target. `None` stores no rows.

    Returns
    -------
    stack_outputs : dict
        A dictionary mapping the stack type ("standard" or "spectral")
        to the final saved image file path.
    """
    logger.info("==========================================")
    logger.info("STARTING BATCH PROCESSING FOR TARGET: %s", target.id)
    logger.info("==========================================")

    camera_frames = select_frames_for_processing(target, camera_name, focal_length_mm)
    if camera_frames is None:
        return {}

    standard_frames, spectral_frames = split_standard_and_spectral_frames(target, camera_frames)
    stack_outputs = _stack_camera_frames(
        target, camera_name, standard_frames, spectral_frames, measured_at, quality_recording
    )
    # The stacking stage moves frames with clouds or trailed stars out of the
    # target (see `stacking/pre_processing/frame_quarantine.py`) and no longer
    # lists them in `target.frames`. The frame lists above were made before
    # that, so drop the moved frames here: the later stages would otherwise
    # try to open files that are no longer where the list says.
    still_listed = {frame.path for frame in target.frames}
    camera_frames = [frame for frame in camera_frames if frame.path in still_listed]
    spectral_frames = [frame for frame in spectral_frames if frame.path in still_listed]
    _save_target(target, catalog_access)
    max_concurrent_jobs = config.get_max_concurrent_jobs()

    # 2. Astrometry Analysis and 3. Photometry Analysis both work from the
    # standard stack -- skip them (rather than fail the whole target) when
    # it didn't stack, so a spectral-only success is still saved below.
    if "standard" in stack_outputs:
        _run_astrometry_stage(target, catalog_access, measured_at)
        _save_target(target, catalog_access)
        _run_photometry_stage(
            target, catalog_access, config, camera_frames, max_workers, max_concurrent_jobs, measured_at
        )
        _save_target(target, catalog_access)
    elif standard_frames:
        logger.warning("[%s] Standard stacking failed; skipping astrometry and photometry.", target.id)

    # 4. Spectroscopy Analysis (only when this target actually has a
    # SPEC stack)
    if "spectral" in stack_outputs:
        _run_spectroscopy_stage(
            target, catalog_access, config, spectral_frames, max_concurrent_jobs, measured_at
        )
        _save_target(target, catalog_access)
    elif spectral_frames:
        logger.warning("[%s] Spectral stacking failed; skipping spectroscopy.", target.id)

    logger.info("[%s] Processing completed and metadata saved successfully.", target.id)

    return stack_outputs


def _save_target(target: Target, catalog_access: Any) -> None:
    """Persist a target's current in-memory state as its own database row.

    Called after each pipeline stage in `run_full_pipeline`, rather than
    once at the very end, so a later stage raising (a bad frame, a
    timed-out solve, an OOM kill) does not discard an earlier stage's
    already-computed results along with it. A save failure is logged
    rather than raised, so it cannot itself turn a successful stage into
    a failed target -- matching how
    `backend/services/analysis/analysis_orchestrator.py` already treats
    save failures for UI-triggered single-stage runs.
    """
    try:
        # Target-scoped write (safe under concurrent callers), unlike a
        # full-catalog resync.
        catalog_access.put(target, "target_record", {})
    except Exception:
        logger.exception("[%s] Failed to save target after pipeline stage", target.id)


def _stack_camera_frames(
    target: Target,
    camera_name: str,
    standard_frames: list[FrameRecord],
    spectral_frames: list[FrameRecord],
    measured_at: datetime | None = None,
    quality_recording: FrameQualityRecording | None = None,
) -> dict[str, str]:
    """Stack a target's standard and/or spectral frames.

    The two kinds are stacked independently, and one kind failing does
    not stop the other from being attempted: a "mixed" target with a
    full night of spectral frames alongside a couple of incidental
    standard-imaging frames should not lose the spectral analysis just
    because those two throwaway frames could not be stacked together.

    Returns
    -------
    stack_outputs : `dict`
        Maps the stack type ("standard" or "spectral") to the final
        saved image file path, for whichever kind(s) stacked
        successfully. A kind present in the input but absent from the
        result failed to stack; the reason is logged at the point of
        failure.
    """
    stack_outputs: dict[str, str] = {}

    # We do not lock the Siril process here because the `siril_interface`
    # already does it during the actual Siril launch. If we lock it here too,
    # two workers could grab the outer locks and then wait forever for each
    # other to release the inner locks, causing a deadlock.
    #
    # The driver is the right place for the lock because it protects
    # every Siril launch, not just the ones started by this batch script.
    if standard_frames and spectral_frames:
        logger.info(
            "[%s] Target contains mixed frames. Stacking standard and spectral frames separately.", target.id
        )

    for kind, frames in (("standard", standard_frames), ("spectral", spectral_frames)):
        if not frames:
            continue
        try:
            # Caught broadly and deliberately: this kind's failure, whatever
            # its cause, must not take down the other kind's stack.
            stacked_path = stack_frames_with_timeout(
                target, frames, measured_at=measured_at, quality_recording=quality_recording
            )
        except Exception:
            logger.exception("[%s] %s stacking raised an error", target.id, kind.capitalize())
            continue
        if not stacked_path or not os.path.exists(stacked_path):
            logger.warning(
                "[%s] %s stacking pipeline returned no valid output path.", target.id, kind.capitalize()
            )
            continue
        logger.info("[%s] %s stacking succeeded: %s", target.id, kind.capitalize(), stacked_path)
        stack_outputs[kind] = stacked_path

    if not standard_frames and not spectral_frames:
        logger.warning(
            "[%s] No valid frames matching camera '%s' found for stacking. Skipping stacking step.",
            target.id,
            camera_name,
        )

    return stack_outputs


def _run_astrometry_stage(
    target: Target, catalog_access: Any, measured_at: datetime | None = None
) -> dict[str, Any]:
    """Run astrometry analysis on the target's stacked image.

    Returns
    -------
    astrometry_results : `dict`
        The astrometry pipeline's result dict.

    Raises
    ------
    ProcessingError
        If astrometry analysis failed to produce a result.
    """
    logger.info("[%s] Running Astrometry Analysis...", target.id)
    astrometry_results = analyze_target(
        target, pipeline_type="astrometry", catalog_access=catalog_access, measured_at=measured_at
    )
    if astrometry_results is None:
        raise ProcessingError("Astrometry analysis failed.")
    logger.info(
        "[%s] Astrometry Analysis complete. Resolved WCS: %s",
        target.id,
        astrometry_results.get("wcs") is not None,
    )
    return astrometry_results


def _run_photometry_stage(
    target: Target,
    catalog_access: Any,
    config: Any,
    camera_frames: list[FrameRecord],
    max_workers: int | None,
    max_concurrent_jobs: int,
    measured_at: datetime | None = None,
) -> dict[str, Any]:
    """Run photometry/variability analysis on the target's camera frames.

    Returns
    -------
    photometry_results : `dict`
        The photometry pipeline's result dict.
    """
    logger.info("[%s] Running Photometry/Variability Analysis...", target.id)
    with acquire_resource_slot(config, "job", max_concurrent_jobs, work_name=f"photometry {target.id}"):
        photometry_results = analyze_target(
            target,
            pipeline_type="photometry",
            frames=camera_frames,
            catalog_access=catalog_access,
            max_workers=max_workers,
            measured_at=measured_at,
        )
    logger.info(
        "[%s] Photometry Analysis complete. Stars found: %s",
        target.id,
        photometry_results.get("starsFound", 0),
    )
    return photometry_results


def _run_spectroscopy_stage(
    target: Target,
    catalog_access: Any,
    config: Any,
    spectral_frames: list[FrameRecord],
    max_concurrent_jobs: int,
    measured_at: datetime | None = None,
) -> None:
    """Run spectroscopy analysis when the target has SPEC frames.

    Raises
    ------
    ProcessingError
        If spectroscopy analysis failed to produce a result.
    """
    if not spectral_frames:
        logger.info("[%s] No SPEC frames found for this target. Skipping spectroscopy analysis.", target.id)
        return

    logger.info("[%s] Running Spectroscopy Analysis...", target.id)
    with acquire_resource_slot(config, "job", max_concurrent_jobs, work_name=f"spectroscopy {target.id}"):
        spectroscopy_results = analyze_target(
            target,
            pipeline_type="spectroscopy",
            limit=10,
            catalog_access=catalog_access,
            measured_at=measured_at,
        )
    if spectroscopy_results is None:
        raise ProcessingError("Spectroscopy analysis failed.")
    logger.info("[%s] Spectroscopy Analysis complete.", target.id)
