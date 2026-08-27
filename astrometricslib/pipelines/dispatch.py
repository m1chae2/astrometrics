"""Main control center for processing target images.

This file acts as the conductor, calling the different analysis
programs (like astrometry and photometry) in the right order.
It keeps the actual image data models separate from the processing logic.
"""

import logging
import os
import threading
import time
from typing import Any

from astrometricslib.drivers import disk_interface
from astrometricslib.drivers.job_logging import registered_job
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import (
    frame_configuration_key,
    frames_missing_focal_length,
    select_frames_for_camera,
)
from astrometricslib.pipelines.shared.star_persistence import (
    StarIdentificationBreakdown,
    _drop_unresolved_stars,
    _reconcile_position_only_star_ids,
    merge_astrometry_stellar_object,
    merge_photometry_stellar_object,
    merge_spectroscopy_stellar_object,
)
from astrometricslib.utilities.enums import FilterType

# Re-exported so external callers and tests that import these by their
# dispatch path keep working -- this module used to define them
# directly, before they moved to pipelines/shared/star_persistence.py.
__all__ = [
    "StarIdentificationBreakdown",
    "_drop_unresolved_stars",
    "_reconcile_position_only_star_ids",
    "merge_astrometry_stellar_object",
    "merge_photometry_stellar_object",
    "merge_spectroscopy_stellar_object",
]

logger = logging.getLogger(__name__)

# Maximum allowed time for an external stacking task to run.
# The timeout grows with the number of frames (N) because adding more
# frames increases the amount of work the stacking process has to do.
#
# The base time (300s) and per-frame time (30s) act as a safety net
# to detect if the process is stuck, not as an expected run time. These
# limits come from tests on different targets (e.g., the 2026-08-24 DSLR
# survey of NGC 7000). They make sure that slow color processing
# (~9.5s/frame) can finish without being cut off early, while giving
# plenty of extra time for fast black-and-white processing (<1s/frame).
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


def analyze_target(
    target: Target,
    frames: list[FrameRecord] | None = None,
    pipeline_type: str = "astrometry",
    filter_type: str | None = None,
    butler=None,  # ruff: ignore[missing-type-function-argument]
    register_job: bool = True,
    path: str | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Run a specific analysis pipeline on the given target.

    You can ask it to run "astrometry" (finding star positions),
    "spectroscopy" (light spectrum), "photometry" (brightness changes),
    or "asteroid_recovery" (finding moving rocks).

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
    ValueError
        If you ask for an unknown pipeline type, or if we don't have
        the right images needed to run it.
    """
    if butler is None:
        from astrometricslib.data_access.butler import DiskButler

        butler = DiskButler()

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
            elif pipeline_type == "spectroscopy" and target.stacked_spectral_target:
                path = target.stacked_spectral_target
            elif pipeline_type == "astrometry" and target.stacked_image:
                path = target.stacked_image
            elif target.frames:
                path = target.frames[0].path
            else:
                raise ValueError(
                    f"No frames or stacked image available for {pipeline_type} analysis"
                    f" on target {target.id}."
                )

        return _run_analysis_pipeline_match(
            target, frames, pipeline_type, filter_type, butler, path, **kwargs
        )


def _run_analysis_pipeline_match(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument]
    pipeline_type,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    butler,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument]
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    from astrometricslib.pipelines import PIPELINE_RUNNERS

    runner = PIPELINE_RUNNERS.get(pipeline_type)
    if runner is None:
        raise ValueError(f"Unknown analysis type: {pipeline_type}")
    return runner(target, frames, filter_type, butler, path, **kwargs)


def get_header_information(target: Target, frame_path: str) -> list[dict[str, str]]:
    """Get the raw metadata (FITS header info) for an image.

    Checks to make sure the target name actually owns this image
    before reading it.

    Parameters
    ----------
    target : Target
        The target the image should belong to.
    frame_path : str
        The file path to the FITS image.

    Returns
    -------
    header_cards : list of dict
        A list of key-value pairs from the image header.

    Raises
    ------
    ValueError
        If the image file doesn't belong to this target.
    """
    is_valid = any(f.path == frame_path for f in target.frames)
    if not is_valid:
        if frame_path in [target.processed_image, target.stacked_image, target.stacked_spectral_target]:
            is_valid = True

    if not is_valid:
        raise ValueError(f"Path {frame_path} does not belong to target {target.id}")

    from astrometricslib.data_access import image_conversions

    return image_conversions.get_fits_header(frame_path)


def stack_and_solve(
    target: Target,
    log_file: str | None = None,
    frames_to_stack: list[FrameRecord] | None = None,
    filter_type: Any | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    register_job: bool = True,
) -> str | None:
    """Run the Siril stacking tool to combine the target's images.

    You can optionally provide a specific list of frames or a filter
    type. You can also override settings like the star roundness limit
    or rejection sigma. If you leave these blank, it uses the defaults.

    Parameters
    ----------
    register_job : bool, optional
        Set to True (default) if you want this run to automatically
        show up in the user interface's job tracker. Set to False if
        you are calling this from a tool that already tracks its own jobs.

    Returns
    -------
    stacked_path : str or None
        The path to the final combined image file, or None if it failed.
    """
    with registered_job(
        enabled=register_job,
        job_type="stacking",
        target_id=target.id,
        log_file=log_file,
        completed_message=f"[{target.id}] Stacking completed successfully.",
        failed_message=f"[{target.id}] Stacking failed.",
    ) as job:
        job.info(f"[{target.id}] Stacking job started (Job: {job.job_id}).")

        from astrometricslib.pipelines.stacking import stage as stacking_tasks

        stacked_path = stacking_tasks.stack_frames(
            target,
            log_file,
            frames_to_stack,
            filter_type,
            rejection_sigma=rejection_sigma,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
        )
        # analyze_target(pipeline_type="astrometry") plate-solves
        # target.stacked_image
        # specifically (see analyze_target's path-resolution logic) -- it has
        # no notion of a spectral stack, so only run it when this call just
        # produced a *standard* stack. Checking which of
        # stacked_image/stacked_spectral_target now equals stacked_path
        # tells us which one stacking_tasks.stack_frames just set,
        # without needing a separate return value for it. analyze_target
        # builds and assigns target.astrometry_quality_summary itself
        # (including flagging a failed-but-attempted solve) -- the only
        # case it can't cover is a hard solver error, which raises before
        # analyze_target gets to build the summary at all, so that's
        # handled here instead.
        if stacked_path and stacked_path == target.stacked_image:
            try:
                # register_job=False: this stacking run already registered
                # its own job above, and analyze_target's docstring is
                # explicit about why a nested call must suppress its own
                # registration -- otherwise one stack_and_solve(solve=True)
                # call produces two ownerless "started" rows in the UI job
                # manager ("stacking" and "analysis") for what the caller
                # sees as a single action.
                analyze_target(target, pipeline_type="astrometry", register_job=False)
            except Exception as stacking_error:
                logger.warning(f"Astrometry plate solving failed after stacking: {stacking_error}")
                from astrometricslib.models.quality_summary import (
                    AstrometryPipelineQualityMetrics,
                    AstrometryQualitySummary,
                )

                target.astrometry_quality_summary = AstrometryQualitySummary(
                    target_id=target.id,
                    flagged=True,
                    flag_reasons=["plate solve failed"],
                    astrometry_metrics=AstrometryPipelineQualityMetrics(
                        sources_detected=0,
                        solve_attempted=False,
                        plate_solve_succeeded=False,
                        simbad_matched_count=0,
                    ),
                )
        # Stacking can finish without raising and still produce no
        # image, so the outcome is decided here rather than left to the
        # context manager's "no exception means success" default.
        job.mark("completed" if stacked_path else "failed", 100)
        return stacked_path


def _stack_frames_with_timeout(
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
            outcome["path"] = stack_and_solve(target, frames_to_stack=frames_to_stack)
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
        print(
            f"[{target.id}] Stacking timed out after {timeout_seconds} seconds "
            f"of working time ({lock_wait_seconds:.0f}s of Siril-lock wait excluded). "
            f"Abandoning this stack."
        )
        # A timeout is a quality event, not just a log line: recorded on
        # the target's existing stack summary when there is one, so the
        # abandoned stack is queryable rather than only discoverable by
        # reading the run's output. The summary may be absent entirely --
        # stack_frames builds it, and this stack never got that far -- in
        # which case the timeout stays a log-only fact.
        summary = getattr(target, "stack_quality_summary", None)
        metrics = getattr(summary, "stacking_metrics", None) if summary else None
        if metrics is not None:
            metrics.timed_out = True
            summary.flagged = True
            summary.flag_reasons.append(f"stacking timed out after {timeout_seconds}s")
        return None

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("path")


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

    Raises
    ------
    ValueError
        If the stacking process fails to make a final image file.
    """
    stack_outputs: dict[str, str] = {}
    print("\n==========================================")
    print(f"STARTING BATCH PROCESSING FOR TARGET: {target.id}")
    print("==========================================")

    # Restrict all processing to frames captured with the requested
    # camera; every other camera's frames on this target are excluded.
    camera_frames = select_frames_for_camera(target, camera_name)

    # Then narrow to a single optic. Frames of different focal length
    # image at different scales -- this library's 300mm and 405mm optics
    # differ by 1.35x -- so a stack blending them has no single pixel
    # scale, cannot be plate solved accurately, and produces fluxes that
    # are not comparable between frames. Seven targets were being
    # stacked that way, NGC 7023 worst at 424 frames of one optic mixed
    # with 111 of the other.
    if focal_length_mm is not None:
        requested_key_suffix = f"@{round(float(focal_length_mm))}mm"
        selected_frames = [
            frame
            for frame in camera_frames
            if (frame_configuration_key(frame) or "").endswith(requested_key_suffix)
        ]
        if not selected_frames:
            print(
                f"[{target.id}] No frames at {focal_length_mm:g}mm for camera '{camera_name}'. "
                "Skipping all processing steps."
            )
            return stack_outputs
        unassignable = frames_missing_focal_length(target, camera_name)
        if unassignable:
            # Never dropped silently: a frame with no FOCALLEN cannot be
            # grouped, and on this library that is 602 frames. See
            # scripts/backfill_focal_length.
            print(
                f"[{target.id}] {len(unassignable)} frame(s) excluded: no FOCALLEN recorded, "
                "so their optic is unknown."
            )
        camera_frames = selected_frames

    if not camera_frames:
        print(
            f"[{target.id}] No frames matching camera '{camera_name}' found for this target. "
            "Skipping all processing steps."
        )
        return stack_outputs

    print(f"[{target.id}] Stacking frames...")
    target_frames = [
        frame
        for frame in camera_frames
        if not any(k in frame.path.lower() for k in ("_stacked", "starless", "starmask"))
    ]

    # Check if there is a mixed set of spectral and standard frames.
    # If so, run standard stacking on standard frames, and spectral
    # stacking on spectral frames.
    standard_frames = []
    spectral_frames = []
    for frame in target_frames:
        is_spectral = (
            frame.filter == FilterType.SPEC
            or getattr(frame.filter, "name", None) == "SPEC"
            or str(frame.filter).upper() in ("SPEC", "STAR ANALYZER 200")
        )
        if is_spectral:
            spectral_frames.append(frame)
        else:
            standard_frames.append(frame)

    # Deliberately no slot acquired here. `siril_interface.siril_process_lock`
    # takes one around the Siril launch itself, and taking a second from the
    # same "siril" semaphore here nests them: with two slots and two workers
    # each takes this outer slot, then both wait forever for an inner slot
    # neither can release. Observed on 2026-08-24 as two workers parked on
    # "Waiting for a free Siril slot" with no Siril process running.
    #
    # The driver is also the better place for it -- it covers every Siril
    # launch, including the backend service's, not just this batch path.
    if standard_frames and spectral_frames:
        print(
            f"[{target.id}] Target contains mixed frames. Stacking standard and spectral frames separately."
        )
        stacked_output = _stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking failed on mixed target.")
        stacked_spectral = _stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking failed on mixed target.")
        print(f"[{target.id}] Stacking succeeded: Standard={stacked_output}, Spectral={stacked_spectral}")
        stack_outputs["standard"] = stacked_output
        stack_outputs["spectral"] = stacked_spectral
    elif standard_frames:
        stacked_output = _stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Standard stacking succeeded: {stacked_output}")
        stack_outputs["standard"] = stacked_output
    elif spectral_frames:
        stacked_spectral = _stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Spectral stacking succeeded: {stacked_spectral}")
        stack_outputs["spectral"] = stacked_spectral
    else:
        print(
            f"[{target.id}] No valid frames matching camera '{camera_name}' found for stacking. "
            "Skipping stacking step."
        )

    # 1b. Tracking Analysis. Read-only over the registration data
    # stacking just wrote, so it belongs right after stacking and
    # before any pipeline that could fail and end this run early --
    # rig-quality findings are worth having even if astrometry or
    # photometry later fails on this target.
    from astrometricslib.pipelines.stacking.tracking_analysis import build_tracking_quality_summary

    try:
        target.tracking_quality_summary = build_tracking_quality_summary(target)
    except Exception as tracking_error:
        logger.warning(f"[{target.id}] Tracking analysis failed: {tracking_error}")

    # 2. Astrometry Analysis
    print(f"[{target.id}] Running Astrometry Analysis...")
    astrometry_results = analyze_target(target, pipeline_type="astrometry", butler=astrometrics.butler)
    if astrometry_results is None:
        raise ValueError("Astrometry analysis failed.")
    print(
        f"[{target.id}] Astrometry Analysis complete. "
        f"Resolved WCS: {astrometry_results.get('wcs') is not None}"
    )

    # 3. Photometry Analysis
    analysis_concurrency = astrometrics.config.get_analysis_concurrency()
    print(f"[{target.id}] Running Photometry/Variability Analysis...")
    with disk_interface.acquire_resource_slot(astrometrics.config, "analysis", analysis_concurrency):
        photometry_results = analyze_target(
            target,
            pipeline_type="photometry",
            frames=camera_frames,
            butler=astrometrics.butler,
            max_workers=max_workers,
        )
    if photometry_results.get("status") == "failed":
        raise ValueError(f"Photometry analysis failed: {photometry_results.get('message')}")
    print(
        f"[{target.id}] Photometry Analysis complete. Stars found: {photometry_results.get('starsFound', 0)}"
    )

    # 4. Spectroscopy Analysis (only when this target actually has a
    # SPEC stack)
    if spectral_frames:
        print(f"[{target.id}] Running Spectroscopy Analysis...")
        with disk_interface.acquire_resource_slot(astrometrics.config, "analysis", analysis_concurrency):
            spectroscopy_results = analyze_target(
                target, pipeline_type="spectroscopy", limit=10, butler=astrometrics.butler
            )
        if spectroscopy_results is None:
            raise ValueError("Spectroscopy analysis failed.")
        print(f"[{target.id}] Spectroscopy Analysis complete.")
    else:
        print(f"[{target.id}] No SPEC frames found for this target. Skipping spectroscopy analysis.")

    # Save this target's own record (safe under concurrent callers,
    # unlike a full-catalog resync)
    astrometrics.butler.put(target, "target_record", {})
    print(f"[{target.id}] Processing completed and metadata saved successfully.")

    return stack_outputs
