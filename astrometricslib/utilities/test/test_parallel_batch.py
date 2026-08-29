"""Purpose: Unit tests for the generic parallel batch engine.

Description: Verifies that `_run_worker_with_captured_output` captures a
worker's log records alongside its stdout. Worker processes never run
`logging.basicConfig`, so before this capture existed every `logger.info`
raised inside a worker was silently discarded and batch runs showed only
whatever the pipeline happened to `print`.
"""

import logging

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
