"""Purpose: Generic parallel batch-processing engine.

Description: Workload-agnostic ProcessPoolExecutor-based runner. Handles
worker process niceness, per-item stdout buffering so concurrent items
never interleave console output, and BrokenProcessPool recovery. Has no
knowledge of what any particular worker function actually does, so any
future heavy per-item pipeline can reuse it directly rather than
re-deriving this machinery.
"""

import contextlib
import io
import logging
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BatchRunSummary:
    """Aggregated outcome of a run_parallel_batch() call.

    `skipped` holds items a worker reported as having no work to do, kept
    apart from `succeeded` so a run's headline counts describe work that
    actually happened rather than counting no-ops as successes.
    """

    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)


def _set_worker_process_niceness(niceness: int) -> None:
    """Lower this worker process's OS scheduling priority.

    Used as a ``ProcessPoolExecutor`` initializer. Runs once per worker
    process at pool startup, so the Linux scheduler favors
    interactive/foreground processes under contention without capping
    batch throughput when the machine is otherwise idle.
    """
    if niceness:
        os.nice(niceness)


def _run_worker_with_captured_output(
    worker_function: Callable[..., dict], item_id: str, worker_arguments: tuple
) -> tuple[dict, str]:
    """Run worker_function for a single item, capturing its stdout.

    Executes inside a worker process. Buffering each item's output here,
    rather than writing straight to the terminal, is what lets the parent
    print each item's output as one contiguous block instead of
    interleaving lines from concurrently-running items.

    Log records are captured alongside stdout. Worker processes never run
    `logging.basicConfig` -- the pool's only initializer sets niceness, and
    under the "forkserver"/"spawn" start methods a worker does not inherit
    the parent's handlers -- so without this every `logger.info`/`warning`
    raised inside a worker was silently discarded, leaving batch runs with
    only whatever the pipeline happened to `print`.

    The handler is attached here rather than in the pool initializer
    because `redirect_stdout` swaps `sys.stdout` per item: a handler bound
    once at worker startup would hold the *original* stdout and write past
    the capture, interleaving lines from concurrent items onto the
    terminal. Binding it to this item's buffer keeps each item's logs in
    the same contiguous block as its prints.

    Returns
    -------
    result : `tuple` [`dict`, `str`]
        The worker_function's returned dict, paired with the captured
        stdout and log output produced while it ran.
    """
    output_buffer = io.StringIO()

    # Matches the package logger that pipeline_tasks' job logging already
    # targets, so both mechanisms observe the same records.
    package_logger = logging.getLogger("astrometricslib")
    log_handler = logging.StreamHandler(output_buffer)
    log_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    previous_level = package_logger.level
    package_logger.addHandler(log_handler)
    if not package_logger.isEnabledFor(logging.INFO):
        # NOTSET here would defer to root, which defaults to WARNING and
        # would drop the INFO-level pipeline diagnostics entirely.
        package_logger.setLevel(logging.INFO)

    try:
        with contextlib.redirect_stdout(output_buffer):
            result = worker_function(item_id, *worker_arguments)
    finally:
        # Always detach: workers are reused across items, so a leaked
        # handler would keep writing this item's buffer for every later
        # item the same process handles.
        package_logger.removeHandler(log_handler)
        package_logger.setLevel(previous_level)

    return result, output_buffer.getvalue()


def run_parallel_batch(
    item_ids: list[str],
    worker_function: Callable[..., dict],
    worker_arguments: tuple = (),
    max_workers: int = 4,
    niceness: int = 10,
    max_pool_restarts: int = 2,
    on_item_complete: Callable[[str, dict, int, int], None] | None = None,
) -> BatchRunSummary:
    """Run worker_function once per item, in parallel, across a pool.

    worker_function must be a module-level (picklable) callable with the
    signature (item_id, *worker_arguments) -> dict, where the returned
    dict has at least a "status" key ("success", "skipped", or "failed")
    and an "error" key populated when status is "failed" or "skipped"
    (carrying the reason, in the latter case). Any other keys are passed
    through to the summary's results mapping unchanged. A worker that
    never reports "skipped" simply leaves that summary list empty.

    Handles four concerns generically, regardless of what worker_function
    actually does:
      - Worker process niceness, so batch runs don't starve interactive
        foreground work on the same machine.
      - Per-item stdout buffering, so concurrent items' console output
        never interleaves.
      - BrokenProcessPool recovery: if a worker process crashes outright
        (e.g. a segfault), the pool is rebuilt and the still-pending items
        are resubmitted, up to max_pool_restarts, so one crashed item
        degrades the run instead of aborting it entirely.
      - Progress reporting via on_item_complete, invoked once per item at
        whichever of the four terminal points it reaches (success, soft
        failure, worker exception, or pool-exhausted-after-restarts), so a
        caller can track completion progress without reinventing a
        completed/total counter or needing cross-process IPC.

    Parameters
    ----------
    item_ids : `list` [`str`]
        The items to process, one worker_function call each.
    worker_function : `Callable`
        Module-level callable: ``(item_id, *worker_arguments) -> dict``.
    worker_arguments : `tuple`, optional
        Extra positional arguments passed to every worker_function
        call.
    max_workers : `int`, optional
        Maximum number of concurrent worker processes.
    niceness : `int`, optional
        OS niceness applied to each worker process; 0 disables
        throttling.
    max_pool_restarts : `int`, optional
        How many times to rebuild the pool after a BrokenProcessPool
        before giving up on the still-pending items.
    on_item_complete : `Callable`, optional
        Callback invoked as ``on_item_complete(item_id, result_dict,
        completed_count, total_count)`` each time an item reaches a
        terminal state. completed_count and total_count are owned by
        this engine, not the caller. Exceptions raised by the callback
        are caught and ignored so a bug in progress reporting cannot
        fail an otherwise-successful item.

    Returns
    -------
    summary : `BatchRunSummary`
        Aggregated success/failure/result state across all items.
    """  # ruff: ignore[docstring-missing-exception] -- BrokenProcessPool
    # is raised and caught within this same function (see the
    # `except BrokenProcessPool` restart-handling block below); it
    # never propagates to the caller.
    summary = BatchRunSummary()
    pending_item_ids = list(item_ids)
    pool_restart_count = 0
    total_item_count = len(item_ids)
    completed_item_count = 0

    def report_item_complete(item_id: str, result: dict) -> None:
        nonlocal completed_item_count
        completed_item_count += 1
        if on_item_complete is None:
            return
        try:
            on_item_complete(item_id, result, completed_item_count, total_item_count)
        except Exception as exc:
            logger.debug("on_item_complete callback raised for item '%s': %s", item_id, exc)

    while pending_item_ids:
        item_ids_for_this_pass = pending_item_ids
        pending_item_ids = []
        processed_item_ids = set()

        executor = ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_set_worker_process_niceness,
            initargs=(niceness,),
        )
        futures_by_item_id = {
            executor.submit(
                _run_worker_with_captured_output, worker_function, item_id, worker_arguments
            ): item_id
            for item_id in item_ids_for_this_pass
        }

        try:
            for future in as_completed(futures_by_item_id):
                item_id = futures_by_item_id[future]
                try:
                    result, captured_output = future.result()
                except BrokenProcessPool:
                    raise
                except Exception as worker_error:
                    failure_result = {"status": "failed", "error": str(worker_error)}
                    summary.failed.append((item_id, str(worker_error)))
                    processed_item_ids.add(item_id)
                    report_item_complete(item_id, failure_result)
                    continue

                processed_item_ids.add(item_id)
                if captured_output:
                    print(captured_output, end="")

                summary.results[item_id] = result
                item_status = result.get("status")
                if item_status == "success":
                    summary.succeeded.append(item_id)
                elif item_status == "skipped":
                    summary.skipped.append((item_id, result.get("error") or "No work for this item"))
                else:
                    summary.failed.append((item_id, result.get("error") or "Unknown failure"))
                report_item_complete(item_id, result)
        except BrokenProcessPool as broken_pool_error:
            still_pending_item_ids = [
                item_id for item_id in item_ids_for_this_pass if item_id not in processed_item_ids
            ]

            if pool_restart_count >= max_pool_restarts:
                for item_id in still_pending_item_ids:
                    crash_result = {
                        "status": "failed",
                        "error": f"Worker pool crashed: {broken_pool_error}",
                    }
                    summary.failed.append((item_id, crash_result["error"]))
                    report_item_complete(item_id, crash_result)
            else:
                pool_restart_count += 1
                pending_item_ids = still_pending_item_ids
        finally:
            executor.shutdown(wait=False)

    return summary
