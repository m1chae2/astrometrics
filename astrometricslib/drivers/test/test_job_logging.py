"""Tests for the shared job recording and log capture helper.

This helper replaced four hand-written copies of the same block. The
tests below cover the behaviours those copies got wrong: leaving log
handlers attached to the shared logger, never closing the files they
opened, and letting a bookkeeping failure take down the real work.
"""

import logging
import time
from collections.abc import Callable

import pytest

from astrometricslib.drivers.job_logging import JobHandle, registered_job
from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration

PACKAGE_LOGGER_NAME = "astrometricslib"


@pytest.fixture
def isolated_logs(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Send the job database and job log files to a throwaway folder.

    Yields
    ------
    logs_database_path : `str`
        Path to the throwaway job database.
    """
    library_path = tmp_path / "library"
    library_path.mkdir(parents=True, exist_ok=True)
    logs_path = tmp_path / "logs"
    logs_path.mkdir(parents=True, exist_ok=True)

    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library_path)}})
    monkeypatch.setattr(configuration, "get_logs_path", lambda: logs_path)
    monkeypatch.setattr(config_loader, "get_configuration", lambda: configuration)

    yield configuration.get_logs_db_path()


def _our_handlers(logger_name: str) -> list:
    """List the handlers this library attached to a logger.

    pytest attaches its own capture handler while a test runs; that one
    is not ours and is filtered out here.

    Returns
    -------
    handlers : `list`
        Only the file and database handlers this library added.
    """
    from astrometricslib.drivers.logger_interface import DbLogHandler

    return [
        handler
        for handler in logging.getLogger(logger_name).handlers
        if isinstance(handler, logging.FileHandler | DbLogHandler)
    ]


def test_a_disabled_job_does_nothing_at_all():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify switching recording off leaves no trace and still works.

    The handle must still be usable so the wrapped work never has to ask
    whether recording happened.
    """
    handlers_before = list(logging.getLogger(PACKAGE_LOGGER_NAME).handlers)

    with registered_job(enabled=False, job_type="analysis", target_id="Vega") as job:
        job.info("this goes nowhere")
        job.mark("completed", 100)
        assert job.job_id is None
        assert job.log_file_path is None

    assert logging.getLogger(PACKAGE_LOGGER_NAME).handlers == handlers_before


def test_no_handler_is_ever_attached_to_the_shared_logger(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a job's handlers never sit on the shared logger.

    The job log router sends a job its own messages, so the shared logger
    keeps the handlers it had, and nothing is left behind when the job ends.
    """
    from astrometricslib.foundation.logging import get_job_log_router

    router = get_job_log_router()
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        assert job.job_id is not None
        assert _our_handlers(PACKAGE_LOGGER_NAME) == []
        assert job.job_id in router._sinks
        assert len(_our_handlers(f"job_{job.job_id}")) == 2

    assert job.job_id not in router._sinks
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []
    assert _our_handlers(f"job_{job.job_id}") == []


def test_messages_from_deeper_modules_reach_the_jobs_log_file(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a message from any module during the work is in the job log."""
    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        logging.getLogger("astrometricslib.pipelines.example").info("deep in the pipeline")
        log_paths = [
            handler.baseFilename
            for handler in job.job_logger.handlers
            if isinstance(handler, logging.FileHandler)
        ]
        for handler in job.job_logger.handlers:
            handler.flush()
        with open(log_paths[0]) as log_file:
            assert "deep in the pipeline" in log_file.read()


def test_the_log_files_are_closed_not_just_detached(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the opened log file is actually closed on the way out.

    Detaching a handler stops it receiving messages but leaves its file
    open. Python never throws a logger away, so an unclosed handler is a
    file handle held for the life of the process. None of the four
    hand-written copies this helper replaced did this.
    """
    captured: list[logging.Handler] = []

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured.extend(
            handler for handler in job.job_logger.handlers if isinstance(handler, logging.FileHandler)
        )

    assert captured, "expected a file handler to have been attached"
    assert all(handler.stream is None or handler.stream.closed for handler in captured)


def test_work_that_raises_marks_the_job_failed_and_reraises(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an error is recorded but never swallowed.

    Raises
    ------
    RuntimeError
        Raised on purpose inside the block, to check it comes back out.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with pytest.raises(RuntimeError, match="work blew up"):
        with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
            captured_job_id = job.job_id
            raise RuntimeError("work blew up")

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.progress_current == 0
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []


def test_work_that_finishes_quietly_is_marked_completed(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no news is good news when the work does not say otherwise."""
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured_job_id = job.job_id

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored.status == "completed"
    assert stored.progress_current == 100


def test_work_that_decides_its_own_outcome_is_believed(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an explicit outcome is not overwritten by the default.

    Stacking can finish without raising and still produce no image, so it
    marks itself failed. That must stick.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as job:
        captured_job_id = job.job_id
        job.mark("failed", 100)

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored.status == "failed"


def test_a_broken_logs_database_degrades_instead_of_raising(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a job we cannot record is still a job worth doing."""
    import astrometricslib.drivers.logger_interface as logger_interface_module

    def _unopenable(*args: object, **kwargs: object) -> object:
        raise OSError("logs database unavailable")

    monkeypatch.setattr(logger_interface_module, "LoggerInterface", _unopenable)
    did_the_work = False

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        did_the_work = True
        job.info("still fine")
        assert job.job_id is None

    assert did_the_work
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []


def test_an_explicit_log_file_is_used_as_given(isolated_logs, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a caller-supplied log path wins over a generated name.

    A caller that already has its own job log open (a script or
    notebook run, say) needs that exact path used, not a fresh one.
    """
    chosen = str(tmp_path / "chosen.log")

    with registered_job(enabled=True, job_type="stacking", target_id="Vega", log_file=chosen) as job:
        assert job.log_file_path == chosen


def test_a_generated_log_name_says_what_kind_of_job_it_was(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify generated names keep the job type and a safe target name."""
    with registered_job(enabled=True, job_type="analysis", target_id="NGC 7000") as job:
        assert "analysis_NGC_7000_" in job.log_file_path
        assert job.log_file_path.endswith(".log")


def test_a_bare_handle_is_safe_to_use():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the no-op handle tolerates every call without a database."""
    handle = JobHandle()

    handle.info("nothing")
    handle.error("nothing")
    handle.mark("completed", 100)


def test_mark_can_also_set_message_and_output_metrics(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the additive `mark()` fields round-trip through storage.

    `run_as_background_job` relies on this to stash a job's real result
    (and quality snapshots) somewhere a later poll can retrieve them.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured_job_id = job.job_id
        job.mark("completed", 100, message="3 of 3 succeeded", output_metrics={"result": {"path": "x.fits"}})

    stored = LoggerInterface(isolated_logs).get_job(captured_job_id)
    assert stored.message == "3 of 3 succeeded"
    assert stored.output_metrics == {"result": {"path": "x.fits"}}


def _stack_vega(result: dict, delay_seconds: float = 0.0) -> Callable[[], dict]:
    """Build work that records its own stacking job, as a library method does.

    Returns
    -------
    work : `Callable`
        Runs a "stacking" job for Vega and returns ``result``.
    """

    def work() -> dict:
        """Record the job, wait, and return the result.

        Returns
        -------
        result : `dict`
            The prepared result.
        """
        with registered_job(enabled=True, job_type="stacking", target_id="Vega"):
            time.sleep(delay_seconds)
            return result

    return work


class TestRunAsBackgroundJob:
    """Tests for the runner a server uses for `@background_job` methods."""

    def test_fast_work_returns_its_real_result_and_its_own_job_id(self, isolated_logs: str) -> None:
        """A quick call gets its result and the id of the job it recorded."""
        from astrometricslib.drivers.job_logging import run_as_background_job
        from astrometricslib.drivers.logger_interface import LoggerInterface

        outcome = run_as_background_job(
            _stack_vega({"stackedImage": "Vega_Stacked.fits"}), grace_period_seconds=2.0
        )

        assert outcome["result"] == {"stackedImage": "Vega_Stacked.fits"}
        stored = LoggerInterface(isolated_logs).get_job(outcome["jobId"])
        assert stored.target_id == "Vega"
        assert stored.output_metrics["result"] == {"stackedImage": "Vega_Stacked.fits"}

    def test_slow_work_returns_a_job_id_and_keeps_running(self, isolated_logs: str) -> None:
        """A call that outlasts the grace period hands back a job id.

        The work itself must keep running in the background rather than
        being abandoned: a caller giving up on waiting must not kill it.
        """
        from astrometricslib.drivers.job_logging import run_as_background_job
        from astrometricslib.drivers.logger_interface import LoggerInterface

        outcome = run_as_background_job(
            _stack_vega({"stackedImage": "Vega_Stacked.fits"}, delay_seconds=0.3), grace_period_seconds=0.1
        )

        assert outcome["status"] == "running"
        assert outcome["jobId"] is not None
        assert outcome["logFilePath"] is not None

        logger_interface = LoggerInterface(isolated_logs)
        deadline = time.monotonic() + 2.0
        stored = logger_interface.get_job(outcome["jobId"])
        while stored is not None and not stored.output_metrics and time.monotonic() < deadline:
            time.sleep(0.02)
            stored = logger_interface.get_job(outcome["jobId"])

        assert stored is not None and stored.status == "completed", "background work should keep running"
        assert stored.output_metrics["result"] == {"stackedImage": "Vega_Stacked.fits"}

    def test_failing_work_reports_the_error(self, isolated_logs: str) -> None:
        """A quick failure is raised to the caller, not swallowed."""
        from astrometricslib.drivers.job_logging import run_as_background_job

        def broken_work() -> None:
            """Fail inside a recorded job.

            Raises
            ------
            RuntimeError
                Always.
            """
            with registered_job(enabled=True, job_type="stacking", target_id="Vega"):
                raise RuntimeError("stacking blew up")

        with pytest.raises(RuntimeError, match="stacking blew up"):
            run_as_background_job(broken_work, grace_period_seconds=2.0)

    def test_work_that_records_no_job_still_returns_its_result(self, isolated_logs: str) -> None:
        """Without a job of its own, the result comes back with no job id."""
        from astrometricslib.drivers.job_logging import run_as_background_job

        outcome = run_as_background_job(lambda: {"success": True}, grace_period_seconds=2.0)

        assert outcome == {"jobId": None, "result": {"success": True}}


def test_a_nested_job_of_the_same_kind_joins_the_running_one(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the same work is not listed twice.

    The MCP wrapper around a stacking method and the method's own
    registration used to make two job rows for one stack.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as outer:
        with registered_job(enabled=True, job_type="stacking", target_id="Vega") as inner:
            assert inner is outer
            inner.mark("failed", 100)

    stacking_jobs = LoggerInterface(isolated_logs).get_jobs_by_target("Vega")
    assert len(stacking_jobs) == 1
    assert outer.terminal_status == "failed"


def test_a_nested_job_of_another_kind_gets_its_own_row(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a stacking job inside a batch job is still listed separately."""
    with registered_job(enabled=True, job_type="batch_processing", target_id="Vega") as batch:
        with registered_job(enabled=True, job_type="stacking", target_id="Vega") as stacking:
            assert stacking.job_id != batch.job_id


def test_each_job_log_holds_only_its_own_work(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify two jobs running at once do not write into each other's log."""
    import threading

    package_logger = logging.getLogger(f"{PACKAGE_LOGGER_NAME}.test_isolation")
    both_registered = threading.Barrier(2)
    log_paths: dict[str, str] = {}

    def run_job(target_id: str) -> None:
        """Log one line as a job, once both jobs are registered."""
        with registered_job(enabled=True, job_type="stacking", target_id=target_id) as job:
            log_paths[target_id] = job.log_file_path
            both_registered.wait(timeout=10)
            package_logger.info("line from %s", target_id)
            both_registered.wait(timeout=10)

    threads = [threading.Thread(target=run_job, args=(name,)) for name in ("Vega", "Deneb")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    with open(log_paths["Vega"]) as vega_log, open(log_paths["Deneb"]) as deneb_log:
        vega_text, deneb_text = vega_log.read(), deneb_log.read()
    assert "line from Vega" in vega_text
    assert "line from Deneb" not in vega_text
    assert "line from Deneb" in deneb_text
    assert "line from Vega" not in deneb_text


def test_stage_moves_progress_and_keeps_a_finished_status(isolated_logs):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify stage reports a running step and never undoes a finished job."""
    from astrometricslib.drivers.logger_interface import LoggerInterface

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as job:
        job.stage(40, "Stacking")
        stored = LoggerInterface(isolated_logs).get_job(job.job_id)
        assert (stored.status, stored.progress_current, stored.message) == ("running", 40, "Stacking")
        job.mark("failed", 100)
        job.stage(80, "Too late")
        assert LoggerInterface(isolated_logs).get_job(job.job_id).status == "failed"
