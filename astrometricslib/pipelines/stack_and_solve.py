"""Stacks a target's frames, then chains an astrometry solve onto them.

`stack_and_solve` runs the Siril stacking tool and, when the result is
a standard (non-spectral) stack, immediately follows it with an
astrometry plate-solve -- one call for what would otherwise be two
separate steps. `stack_frames_with_timeout` wraps that call with a
time limit, since the underlying Siril process can occasionally hang.
"""

import logging
import threading
import time
from typing import Any

from astrometricslib.drivers.job_logging import registered_job
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.analysis_router import analyze_target

logger = logging.getLogger(__name__)

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
