"""Purpose: Unit tests for the generic parallel batch engine.

Description: Verifies that `_run_worker_with_captured_output` captures a
worker's log records alongside its stdout. Worker processes never run
`logging.basicConfig`, so before this capture existed every `logger.info`
raised inside a worker was silently discarded and batch runs showed only
whatever the pipeline happened to `print`.
"""

import itertools
import logging
import os
import time
from typing import Any

from astrometricslib.utilities import parallel_batch


def _worker_that_logs(item_id, level_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    logger = logging.getLogger("astrometricslib.tasks.fake_pipeline")
    getattr(logger, level_name)(f"log line for {item_id}")
    print(f"print line for {item_id}")
    return {"status": "completed"}


class TestWorkerOutputCapture:
    """Unit test suite for _run_worker_with_captured_output."""

    def test_captures_worker_log_records(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """A worker's log records reach the captured output block."""
        result, output = parallel_batch._run_worker_with_captured_output(_worker_that_logs, "M 81", ("info",))

        assert result == {"status": "completed"}
        assert "log line for M 81" in output

    def test_log_and_print_share_one_buffer(self, capsys):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Logging and print output land in the same per-item block.

        Runs with pytest's capture disabled: pytest replaces
        `sys.stdout` for the duration of a test, which prevents the
        `redirect_stdout` under test from taking effect, so the print
        half would otherwise never reach the buffer here. The logging
        half is unaffected (its handler binds the buffer object
        directly), which is why the other tests need no such handling.
        """
        with capsys.disabled():
            _result, output = parallel_batch._run_worker_with_captured_output(
                _worker_that_logs, "M 81", ("info",)
            )

        assert "log line for M 81" in output
        assert "print line for M 81" in output

    def test_captures_warning_records(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Warnings are captured too, not just info."""
        _result, output = parallel_batch._run_worker_with_captured_output(
            _worker_that_logs, "Alnath", ("warning",)
        )

        assert "log line for Alnath" in output
        assert "WARNING" in output

    def test_handler_is_detached_after_each_item(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """The per-item handler must not leak onto the package logger.

        Worker processes are reused across items, so a leaked handler
        would keep writing into a previous item's buffer for every later
        item the same process handles.
        """
        package_logger = logging.getLogger("astrometricslib")
        handlers_before = list(package_logger.handlers)
        level_before = package_logger.level

        parallel_batch._run_worker_with_captured_output(_worker_that_logs, "Vega", ("info",))

        assert package_logger.handlers == handlers_before
        assert package_logger.level == level_before

    def test_one_items_output_does_not_leak_into_the_next(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Sequential items get separate, non-contaminated buffers."""
        _r1, first_output = parallel_batch._run_worker_with_captured_output(
            _worker_that_logs, "M 13", ("info",)
        )
        _r2, second_output = parallel_batch._run_worker_with_captured_output(
            _worker_that_logs, "M 101", ("info",)
        )

        assert "M 13" in first_output
        assert "M 101" not in first_output
        assert "M 101" in second_output
        assert "M 13" not in second_output

    def test_handler_detached_even_when_worker_raises(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """A failing worker still cleans up its logging handler."""
        package_logger = logging.getLogger("astrometricslib")
        handlers_before = list(package_logger.handlers)

        def _exploding_worker(item_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            raise RuntimeError("boom")

        try:
            parallel_batch._run_worker_with_captured_output(_exploding_worker, "NGC 2244", ())
        except RuntimeError:
            pass

        assert package_logger.handlers == handlers_before


def _simple_batch_worker(item_id: str) -> dict[str, Any]:
    """Process a simple test batch item.

    Returns
    -------
    result : `dict`
        Success status and processed item ID.
    """
    return {"status": "success", "processed_item": item_id}


def _oom_simulating_worker(item_id: str) -> dict[str, Any]:
    """Simulate a worker memory failure for targeted items.

    Returns
    -------
    result : `dict`
        Success status and processed item ID.

    Raises
    ------
    MemoryError
        Raised when processing item ID "OOM_TARGET".
    """
    if item_id == "OOM_TARGET":
        raise MemoryError("Process virtual memory limit exceeded")
    return {"status": "success", "processed_item": item_id}


def _crash_once_then_record_worker(item_id: str, marker_dir: str) -> dict[str, Any]:
    """Crash the whole worker process on an item's first attempt.

    Simulates a real worker-process crash (a native segfault or Rust
    panic, not a catchable Python exception): the first time an item
    runs, it writes a marker file and calls `os._exit`, which kills the
    process outright and poisons the whole pool for
    `concurrent.futures.process`. On the item's second attempt (after
    `run_parallel_batch` rebuilds the pool), it instead sleeps briefly
    and records its start/end time, so a test can check whether that
    retry ran one item at a time or several at once.

    Returns
    -------
    result : `dict`
        Success status and processed item ID.
    """
    marker_path = os.path.join(marker_dir, f"{item_id}.done")
    if not os.path.exists(marker_path):
        with open(marker_path, "w") as marker_file:
            marker_file.write("crashed once")
        os._exit(1)

    start_time = time.time()
    time.sleep(0.3)
    end_time = time.time()
    with open(os.path.join(marker_dir, "timings.log"), "a") as timings_file:
        timings_file.write(f"{item_id},{start_time},{end_time}\n")
    return {"status": "success", "processed_item": item_id}


class TestParallelBatchResourceGovernors:
    """Tests for resource controls and backpressure in run_parallel_batch."""

    def test_initialize_worker_process_runs_safely(self) -> None:
        """Initialize worker process with zero memory does not error."""
        parallel_batch._initialize_worker_process(niceness=0, max_memory_mb=0)

    def test_deprecated_set_worker_niceness_delegates_to_initializer(
        self,
    ) -> None:
        """Verify deprecated niceness setter delegates properly."""
        parallel_batch._set_worker_process_niceness(niceness=0)

    def test_sliding_window_execution_and_recycling(self) -> None:
        """Verify sliding-window dispatch processes items with recycling."""
        items = [f"Item_{i}" for i in range(8)]
        summary = parallel_batch.run_parallel_batch(
            items,
            _simple_batch_worker,
            max_workers=2,
            max_tasks_per_child=2,
            max_worker_memory_mb=1024,
        )

        assert len(summary.succeeded) == 8
        assert len(summary.failed) == 0
        assert set(summary.succeeded) == set(items)

    def test_worker_memory_error_is_caught_gracefully(self) -> None:
        """Verify worker MemoryError fails only the offending item."""
        items = ["Normal_1", "OOM_TARGET", "Normal_2"]
        summary = parallel_batch.run_parallel_batch(
            items,
            _oom_simulating_worker,
            max_workers=2,
            max_worker_memory_mb=1024,
        )

        assert "Normal_1" in summary.succeeded
        assert "Normal_2" in summary.succeeded
        assert len(summary.failed) == 1
        failed_item, failure_reason = summary.failed[0]
        assert failed_item == "OOM_TARGET"
        assert "memory limit" in failure_reason.lower()

    def test_broken_process_pool_falls_back_to_serial_retry(self, tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
        """A real worker-process crash under concurrency retries serially.

        Reproduces the real production failure this guards against: three
        outer workers crashed at once with native pyo3 panics, and every
        target came back as an unlabelled "Unknown failure". All three
        items here crash the same way on their first attempt; once the
        pool is rebuilt, this verifies concurrency was forced down to one
        worker rather than just blindly repeating the same crash.
        """
        marker_dir = str(tmp_path)
        items = ["A", "B", "C"]

        summary = parallel_batch.run_parallel_batch(
            items,
            _crash_once_then_record_worker,
            worker_arguments=(marker_dir,),
            max_workers=3,
            # Generous budget: under real scheduling variance, an item that
            # never actually got dispatched to a worker before the pool
            # broke still hasn't had its "crash once" attempt yet, so it
            # can still trigger its own restart during the serial retry.
            # This test is about the downgrade-to-serial behavior, not
            # about how tightly max_pool_restarts can be bounded.
            max_pool_restarts=10,
        )

        assert set(summary.succeeded) == set(items)
        assert len(summary.failed) == 0

        timing_lines = (tmp_path / "timings.log").read_text().strip().splitlines()
        assert len(timing_lines) == len(items)
        windows = sorted(
            (float(start), float(end)) for _item_id, start, end in (line.split(",") for line in timing_lines)
        )
        for (_earlier_start, earlier_end), (later_start, _later_end) in itertools.pairwise(windows):
            assert later_start >= earlier_end, (
                "items retried after a worker-process crash must run one at a time, not concurrently"
            )
