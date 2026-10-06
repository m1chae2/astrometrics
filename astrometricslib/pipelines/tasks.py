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
from typing import Any

from astrometricslib.drivers.job_logging import registered_job
from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError, ProcessingError
from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
from astrometricslib.models.processing_results import ProcessTargetResult
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.asteroid_detection.runner import run_asteroid_detection_analysis
from astrometricslib.pipelines.astrometry.runner import run_astrometry_analysis
from astrometricslib.pipelines.photometry.runner import run_photometry_analysis
from astrometricslib.pipelines.shared.frame_grouping import (
    frame_is_spectral,
    select_frames_for_processing,
    split_standard_and_spectral_frames,
)
from astrometricslib.pipelines.spectroscopy.runner import run_spectroscopy_analysis

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


def _stack_with_job_tracking(target: Target, frames_to_stack: list[FrameRecord]) -> str | None:
    """Run Siril stacking, registered as a job the UI can track.

    Returns
    -------
    stacked_path : str or None
        The path to the final combined image file, or None if it failed.
    """
    with registered_job(
        enabled=True,
        job_type="stacking",
        target_id=target.id,
        completed_message=f"[{target.id}] Stacking completed successfully.",
        failed_message=f"[{target.id}] Stacking failed.",
    ) as job:
        job.info(f"[{target.id}] Stacking job started (Job: {job.job_id}).")

        from astrometricslib.pipelines.stacking import stage as stacking_tasks

        stacked_path = stacking_tasks.stack_frames(target, frames_to_stack=frames_to_stack, job_id=job.job_id)
        # Stacking can finish without raising and still produce no
        # image, so the outcome is decided here rather than left to the
        # context manager's "no exception means success" default.
        job.mark("completed" if stacked_path else "failed", 100)
        return stacked_path


def stack_frames_with_timeout(
    target: Target, frames_to_stack: list[FrameRecord], timeout_seconds: int | None = None
) -> str | None:
    """Run the image stacker with a time limit so it doesn't freeze forever.

    The Siril stacking program can sometimes get stuck. This function
    runs it in the background and will kill it if it takes too long.
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
    from astrometricslib.drivers import siril_interface

    if timeout_seconds is None:
        timeout_seconds = compute_stacking_timeout_seconds(len(frames_to_stack))

    outcome: dict[str, Any] = {}

    def _run_stacking() -> None:
        try:
            outcome["path"] = _stack_with_job_tracking(target, frames_to_stack)
        except Exception as stacking_error:
            outcome["error"] = stacking_error

    siril_interface.reset_siril_lock_wait_seconds()
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
        if elapsed_seconds >= timeout_seconds + siril_interface.get_siril_lock_wait_seconds():
            break

    if stacking_thread.is_alive():
        lock_wait_seconds = siril_interface.get_siril_lock_wait_seconds()
        logger.warning(
            "[%s] Stacking timed out after %s seconds of working time (%.0fs of Siril-lock wait "
            "excluded). Abandoning this stack.",
            target.id,
            timeout_seconds,
            lock_wait_seconds,
        )
        # A timeout is a quality event, not just a log line: recorded on
        # the target's existing stack summary when there is one, so the
        # abandoned stack is queryable rather than only discoverable by
        # reading the run's output. The summary may be absent entirely --
        # stack_frames builds it, and this stack never got that far -- in
        # which case the timeout stays a log-only fact.
        summary = target.stacking.quality_summary
        metrics = getattr(summary, "stacking_metrics", None) if summary else None
        if metrics is not None:
            metrics.timed_out = True
            summary.flagged = True
            summary.flag_reasons.append(f"stacking timed out after {timeout_seconds}s")
        return None

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("path")


# -- Picking which single analysis pipeline runs, by name --------------

PIPELINE_RUNNERS = {
    "astrometry": run_astrometry_analysis,
    "spectroscopy": run_spectroscopy_analysis,
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
        from astrometricslib.drivers.catalog_access import CatalogAccess

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

        kwargs["job_id"] = job.job_id
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

ANALYSIS_STAGES = ("astrometry", "photometry", "spectroscopy", "asteroids")
"""The analysis stages `run_target_stages` can run, in the order run."""

DEFAULT_ANALYSIS_STAGES = ("astrometry", "photometry", "spectroscopy")
"""The stages that run for one target when the caller names none."""

STAGE_OPTIONS = {
    "astrometry": frozenset({"path"}),
    "photometry": frozenset({"frames", "filter_type", "use_astrometry_seed", "max_workers"}),
    "spectroscopy": frozenset({"path", "limit"}),
    "asteroids": frozenset({"moving_object_config"}),
}
"""The options each stage accepts in its options dictionary."""

_PIPELINE_TYPES = {
    "astrometry": "astrometry",
    "photometry": "photometry",
    "spectroscopy": "spectroscopy",
    "asteroids": "asteroid_detection",
}


def run_target_stages(
    target: Target,
    stages: tuple[str, ...],
    stage_options: dict[str, dict[str, Any]],
    catalog_access: Any,
    register_job: bool,
) -> ProcessTargetResult:
    """Run the chosen analysis stages for one target, always in the same order.

    The order is astrometry, photometry, spectroscopy, asteroids, whatever
    order ``stages`` lists them in. Spectroscopy is skipped, with a
    ``{"status": "skipped", ...}`` entry, when the target has no spectral
    data, and otherwise receives photometry's result.

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

    Returns
    -------
    result : `ProcessTargetResult`
        One result per stage that ran, keyed by stage name.
    """
    result = ProcessTargetResult(target_id=target.id)
    for stage in ANALYSIS_STAGES:
        if stage not in stages:
            continue
        options = dict(stage_options.get(stage) or {})
        if stage == "spectroscopy":
            has_spectral_input = bool(target.spectral_stacking.stacked_image) or any(
                frame_is_spectral(frame) for frame in target.frames or []
            )
            if not has_spectral_input:
                result.results[stage] = {"status": "skipped", "reason": "target has no spectral data"}
                result.stages_run.append(stage)
                continue
            options["photometry_result"] = result.results.get("photometry")
        result.results[stage] = analyze_target(
            target,
            pipeline_type=_PIPELINE_TYPES[stage],
            catalog_access=catalog_access,
            register_job=register_job,
            **options,
        )
        result.stages_run.append(stage)
    return result


# -- Running every stage for one target, start to finish ---------------


def run_full_pipeline(
    target: Target,
    astrometrics: Any,
    max_workers: int | None = None,
    *,
    camera_name: str,
    focal_length_mm: float | None = None,
) -> dict[str, str]:
    """Run the complete start-to-finish processing pipeline for a target.

    This runs stacking, position solving (astrometry), brightness
    tracking (photometry), and light spectrum (spectroscopy) in order,
    then saves the target's data to the database.

    Parameters
    ----------
    target : Target
        The target we want to process.
    astrometrics : Any
        The system interface that gives us config settings and database access.
    max_workers : int, optional
        How many parallel processes to use during the brightness tracking step.
    camera_name : str
        The name of the camera to process images for. Any images taken
        by a different camera will be ignored.

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
    stack_outputs = _stack_camera_frames(target, camera_name, standard_frames, spectral_frames)
    # The stacking stage moves frames with clouds or trailed stars out of the
    # target (see `stacking/pre_processing/frame_quarantine.py`) and no longer
    # lists them in `target.frames`. The frame lists above were made before
    # that, so drop the moved frames here: the later stages would otherwise
    # try to open files that are no longer where the list says.
    still_listed = {frame.path for frame in target.frames}
    camera_frames = [frame for frame in camera_frames if frame.path in still_listed]
    spectral_frames = [frame for frame in spectral_frames if frame.path in still_listed]
    _save_target(target, astrometrics)
    max_concurrent_jobs = astrometrics.config.get_max_concurrent_jobs()

    # 2. Astrometry Analysis and 3. Photometry Analysis both work from the
    # standard stack -- skip them (rather than fail the whole target) when
    # it didn't stack, so a spectral-only success is still saved below.
    if "standard" in stack_outputs:
        _run_astrometry_stage(target, astrometrics)
        _save_target(target, astrometrics)
        _run_photometry_stage(target, astrometrics, camera_frames, max_workers, max_concurrent_jobs)
        _save_target(target, astrometrics)
    elif standard_frames:
        logger.warning("[%s] Standard stacking failed; skipping astrometry and photometry.", target.id)

    # 4. Spectroscopy Analysis (only when this target actually has a
    # SPEC stack)
    if "spectral" in stack_outputs:
        _run_spectroscopy_stage(target, astrometrics, spectral_frames, max_concurrent_jobs)
        _save_target(target, astrometrics)
    elif spectral_frames:
        logger.warning("[%s] Spectral stacking failed; skipping spectroscopy.", target.id)

    logger.info("[%s] Processing completed and metadata saved successfully.", target.id)

    return stack_outputs


def _save_target(target: Target, astrometrics: Any) -> None:
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
        astrometrics.catalog_access.put(target, "target_record", {})
    except Exception:
        logger.exception("[%s] Failed to save target after pipeline stage", target.id)


def _stack_camera_frames(
    target: Target,
    camera_name: str,
    standard_frames: list[FrameRecord],
    spectral_frames: list[FrameRecord],
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
            stacked_path = stack_frames_with_timeout(target, frames)
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


def _run_astrometry_stage(target: Target, astrometrics: Any) -> dict[str, Any]:
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
        target, pipeline_type="astrometry", catalog_access=astrometrics.catalog_access
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
    astrometrics: Any,
    camera_frames: list[FrameRecord],
    max_workers: int | None,
    max_concurrent_jobs: int,
) -> dict[str, Any]:
    """Run photometry/variability analysis on the target's camera frames.

    Returns
    -------
    photometry_results : `dict`
        The photometry pipeline's result dict.
    """
    logger.info("[%s] Running Photometry/Variability Analysis...", target.id)
    with acquire_resource_slot(astrometrics.config, "job", max_concurrent_jobs):
        photometry_results = analyze_target(
            target,
            pipeline_type="photometry",
            frames=camera_frames,
            catalog_access=astrometrics.catalog_access,
            max_workers=max_workers,
        )
    logger.info(
        "[%s] Photometry Analysis complete. Stars found: %s",
        target.id,
        photometry_results.get("starsFound", 0),
    )
    return photometry_results


def _run_spectroscopy_stage(
    target: Target,
    astrometrics: Any,
    spectral_frames: list[FrameRecord],
    max_concurrent_jobs: int,
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
    with acquire_resource_slot(astrometrics.config, "job", max_concurrent_jobs):
        spectroscopy_results = analyze_target(
            target, pipeline_type="spectroscopy", limit=10, catalog_access=astrometrics.catalog_access
        )
    if spectroscopy_results is None:
        raise ProcessingError("Spectroscopy analysis failed.")
    logger.info("[%s] Spectroscopy Analysis complete.", target.id)
