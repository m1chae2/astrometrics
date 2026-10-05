"""Purpose: Run the full pipeline for many targets at once, in workers.

Description: `ProcessingPipelines.process_target` hands a list of targets (or
every target) to `process_targets_in_parallel`. Each target runs in its own
worker process, so one target that crashes (a bad frame, a Siril hang, a
memory error) cannot take the others down with it. A worker runs
`tasks.run_full_pipeline`: stack the target's frames from one camera, plate
solve the stack, measure star brightness, and extract spectra when there are
spectroscopy frames.
"""

import logging
import traceback
from collections.abc import Callable

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.utilities import parallel_batch
from astrometricslib.utilities.concurrency import resolve_worker_counts

__all__ = ["process_targets_in_parallel"]

logger = logging.getLogger(__name__)


def _process_single_target_worker(
    target_id: str, photometry_workers: int, camera_id: str, focal_length_mm: float | None = None
) -> dict:
    """Run the full pipeline for one target, inside a worker process.

    This runs inside its own process so that an error does not crash the
    rest of the batch.

    Parameters
    ----------
    target_id : `str`
        The target to process.
    photometry_workers : `int`
        How many processes the brightness measurement may use.
    camera_id : `str`
        Only frames taken with this camera are processed.
    focal_length_mm : `float`, optional
        Only frames taken at this focal length are processed.

    Returns
    -------
    result : `dict`
        ``status`` (``"success"``, ``"skipped"`` or ``"failed"``),
        ``error`` (the reason, if any) and ``stack_outputs`` (the stacks
        made, by kind).
    """
    from astrometricslib import Astrometrics
    from astrometricslib.pipelines.shared.frame_grouping import select_frames_for_camera
    from astrometricslib.pipelines.tasks import run_full_pipeline

    result = {"status": "failed", "error": None, "stack_outputs": {}}
    try:
        astrometrics = Astrometrics()
        target = astrometrics.targets.get(target_id)
        if target is None:
            result["error"] = "Target not found in catalog"
            return result

        # "skipped", not "success", when there is no work to do, so the
        # success count only includes targets that were really processed.
        if not select_frames_for_camera(target, camera_id):
            result["status"] = "skipped"
            result["error"] = f"No frames matching camera '{camera_id}'"
            return result

        result["stack_outputs"] = run_full_pipeline(
            target,
            astrometrics,
            max_workers=photometry_workers,
            camera_name=camera_id,
            focal_length_mm=focal_length_mm,
        )
        result["status"] = "success"
    except Exception as processing_error:
        # A loop over independent targets: one failure must not stop the
        # rest. `str(error)` can be empty (a bare `raise SomeError()`, or a
        # Rust panic that pyo3 turns into an exception), so the error keeps
        # its type through `repr` and the traceback.
        logger.exception("Target '%s' failed during processing", target_id)
        result["error"] = f"{processing_error!r}\n{traceback.format_exc()}"

    return result


def process_targets_in_parallel(
    config: AppConfiguration,
    target_ids: list[str],
    *,
    camera_id: str,
    focal_length_mm: float | None = None,
    on_item_complete: Callable[[str, dict, int, int], None] | None = None,
) -> parallel_batch.BatchRunSummary:
    """Run the full pipeline for each target, several at the same time.

    Parameters
    ----------
    config : `AppConfiguration`
        Supplies the worker counts and how politely the workers run.
    target_ids : `list` [`str`]
        The targets to process.
    camera_id : `str`
        Only frames taken with this camera are processed.
    focal_length_mm : `float`, optional
        Only frames taken at this focal length are processed.
    on_item_complete : `Callable`, optional
        Called as ``(target_id, result, completed_count, total_count)``
        after each target finishes.

    Returns
    -------
    summary : `parallel_batch.BatchRunSummary`
        Which targets succeeded, failed or were skipped.
    """
    worker_counts = resolve_worker_counts(config.get_target_workers(), config.get_photometry_workers())
    return parallel_batch.run_parallel_batch(
        target_ids,
        _process_single_target_worker,
        worker_arguments=(worker_counts.inner_worker_count, camera_id, focal_length_mm),
        max_workers=worker_counts.outer_worker_count,
        niceness=config.get_worker_niceness(),
        on_item_complete=on_item_complete,
    )
