"""Tools for processing many targets at the same time.

This file connects the target-processing steps to a background worker system,
allowing the computer to process multiple targets simultaneously without
freezing.
"""

from typing import Any

from astrometricslib.utilities import parallel_batch
from astrometricslib.utilities.concurrency import resolve_worker_counts


def _process_single_target_worker(
    target_id: str, photometry_workers: int, camera_name: str, focal_length_mm: float | None = None
) -> dict:
    """Run the full analysis process for a single target in the background.

    This runs inside its own isolated process so that errors don't crash
    the rest of the program.

    Parameters
    ----------
    target_id : `str`
        The name of the target to process.
    photometry_workers : `int`
        How many CPU cores to use for measuring star brightness.
    camera_name : `str`
        Only process images taken with this specific camera.

    Returns
    -------
    result : `dict`
        The outcome of the process. Includes "status" (success, skipped,
        or failed), "error" (if any), and "stack_outputs" (the final data).
    """
    from astrometricslib import Astrometrics
    from astrometricslib.pipelines.dispatch import (
        run_full_pipeline,
        select_frames_for_camera,
    )

    result = {"status": "failed", "error": None, "stack_outputs": {}}
    try:
        astrometrics = Astrometrics()
        target = astrometrics.targets.get(target_id)
        if target is None:
            result["error"] = "Target not found in catalog"
            return result

        # Reported separately from "success" so a run's headline numbers
        # describe work actually done. A full-catalog run on 2026-08-23
        # reported "40 succeeded" when only 12 targets had frames for the
        # requested camera; the other 28 were no-ops indistinguishable
        # from real successes.
        if not select_frames_for_camera(target, camera_name):
            result["status"] = "skipped"
            result["error"] = f"No frames matching camera '{camera_name}'"
            return result

        result["stack_outputs"] = run_full_pipeline(
            target,
            astrometrics,
            max_workers=photometry_workers,
            camera_name=camera_name,
            focal_length_mm=focal_length_mm,
        )
        result["status"] = "success"
    except Exception as processing_error:
        result["error"] = str(processing_error)

    return result


def process_all_targets(
    api: Any,
    target_ids: list[str] | None = None,
    *,
    camera_name: str,
    focal_length_mm: float | None = None,
) -> parallel_batch.BatchRunSummary:
    """Process many targets at the same time.

    This splits the workload across multiple CPU cores to finish faster.

    Parameters
    ----------
    api : `Any`
        The main program interface.
    target_ids : `list` [`str`], optional
        The specific targets to process. If None, processes all targets.
    camera_name : `str`
        Only process images taken with this specific camera.

    Returns
    -------
    summary : `parallel_batch.BatchRunSummary`
        A report showing how many targets succeeded, failed, or were skipped.
    """
    if target_ids is None:
        target_ids = [target.id for target in api.targets.list()]

    worker_counts = resolve_worker_counts(
        api.config.get_target_workers(), api.config.get_photometry_workers()
    )

    return parallel_batch.run_parallel_batch(
        target_ids,
        _process_single_target_worker,
        worker_arguments=(worker_counts.inner_worker_count, camera_name, focal_length_mm),
        max_workers=worker_counts.outer_worker_count,
        niceness=api.config.get_worker_niceness(),
    )
