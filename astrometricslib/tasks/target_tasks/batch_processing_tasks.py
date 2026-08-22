"""Batch target processing operations.

Thin adapter wiring the generic parallel-batch engine to target-level
processing. Owns only target-specific concerns (worker lookup,
worker-count resolution); the actual process-pool mechanics live in
astrometricslib.utilities.parallel_batch, which knows nothing about
targets. # REQ: BKD-5
"""

from typing import Any

from astrometricslib.utilities import parallel_batch
from astrometricslib.utilities.concurrency import resolve_worker_counts


def _process_single_target_worker(target_id: str, photometry_workers: int) -> dict:
    """Run one target's full pipeline in its own process.

    Module-level, picklable worker. Constructs a fresh Astrometrics
    astrometrics (its own DiskButler/config) so each worker process is
    self-contained, mirroring the pattern already used by
    variability_analyzer.py's _process_single_frame_worker.

    Parameters
    ----------
    target_id : `str`
        The identifier of the target to process.
    photometry_workers : `int`
        The number of inner worker processes to use for the
        photometry stage of the target's pipeline.

    Returns
    -------
    result : `dict`
        A dict with keys "status" (`str`, "success" or "failed"),
        "error" (`str` or `None`), and "stack_outputs" (`dict` of
        per-stack pipeline outputs).
    """
    from astrometricslib import Astrometrics
    from astrometricslib.tasks.target_tasks.pipeline_tasks import run_full_pipeline

    result = {"status": "failed", "error": None, "stack_outputs": {}}
    try:
        astrometrics = Astrometrics()
        target = astrometrics.targets.get(target_id)
        if target is None:
            result["error"] = "Target not found in catalog"
            return result

        result["stack_outputs"] = run_full_pipeline(target, astrometrics, max_workers=photometry_workers)
        result["status"] = "success"
    except Exception as processing_error:
        result["error"] = str(processing_error)

    return result


def process_all_targets(api: Any, target_ids: list[str] | None = None) -> parallel_batch.BatchRunSummary:
    """Process many targets' full pipelines concurrently.

    Uses the generic parallel-batch engine to run each target's
    pipeline in its own worker process.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing config and target lookup.
    target_ids : `List[str]`, optional
        Target ids to process; defaults to `None`, in which case
        every target currently in the catalog is processed.

    Returns
    -------
    summary : `parallel_batch.BatchRunSummary`
        Aggregated success/failure/result state across all targets.
    """
    if target_ids is None:
        target_ids = [target.id for target in api.targets.list()]

    worker_counts = resolve_worker_counts(
        api.config.get_target_workers(), api.config.get_photometry_workers()
    )

    return parallel_batch.run_parallel_batch(
        target_ids,
        _process_single_target_worker,
        worker_arguments=(worker_counts.inner_worker_count,),
        max_workers=worker_counts.outer_worker_count,
        niceness=api.config.get_worker_niceness(),
    )
