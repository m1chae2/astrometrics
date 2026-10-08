"""Tests for the job runner: recording a job and collecting its log.

The runner replaced four hand-written copies of the same block. The tests
below cover the behaviours those copies got wrong: leaving log handlers
attached to a shared logger, never closing the files they opened, and
letting a bookkeeping failure take down the real work. They also check that
the runner never changes logging setup, which only a program may do.
"""

import logging
import time
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.jobs.runner import JobHandle, registered_job
from astrometricslib.foundation.logging import get_job_log_router

PACKAGE_LOGGER_NAME = "astrometricslib"


@pytest.fixture
def isolated_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
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


@pytest.fixture(autouse=True)
def _info_level_logging(caplog: pytest.LogCaptureFixture) -> None:
    """Let INFO messages through, as `configure_logging` does in a program."""
    caplog.set_level(logging.INFO)


def _job_sinks(job_id: str) -> list[logging.Handler]:
    """List the handlers the job log router holds for one job.

    Returns
    -------
    handlers : `list` [`logging.Handler`]
        Every handler registered under ``job_id``.
    """
    router = get_job_log_router()
    return [
        handler for owner, handlers, _ in router._sinks.values() if owner == job_id for handler in handlers
    ]


def _our_handlers(logger_name: str) -> list:
    """List the handlers this library attached to a logger.

    pytest attaches its own capture handler while a test runs; that one
    is not ours and is filtered out here.

    Returns
    -------
    handlers : `list`
        Only the file and database handlers this library added.
    """
    from astrometricslib.foundation.jobs.store import DbLogHandler

    return [
        handler
        for handler in logging.getLogger(logger_name).handlers
        if isinstance(handler, logging.FileHandler | DbLogHandler)
    ]


def test_a_disabled_job_does_nothing_at_all() -> None:
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


def test_no_handler_is_ever_attached_to_the_shared_logger(isolated_logs: str) -> None:
    """Verify a job's handlers never sit on the shared logger.

    The job log router sends a job its own messages, so the shared logger
    keeps the handlers it had, and nothing is left behind when the job ends.
    """
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []
    level_before = logging.getLogger(PACKAGE_LOGGER_NAME).level

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        assert job.job_id is not None
        assert _our_handlers(PACKAGE_LOGGER_NAME) == []
        assert len(_job_sinks(job.job_id)) == 2

    assert _job_sinks(job.job_id) == []
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == level_before


def test_messages_from_deeper_modules_reach_the_jobs_log_file(isolated_logs: str) -> None:
    """Verify a message from any module of either library is in the job log."""
    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        logging.getLogger("astrometricslib.pipelines.example").info("deep in the pipeline")
        logging.getLogger("wayfindinglib.tasks.example").info("deep in a control task")
        job.info("a milestone")
        for handler in _job_sinks(job.job_id):
            handler.flush()
        with open(job.log_file_path) as log_file:
            text = log_file.read()
    assert "deep in the pipeline" in text
    assert "deep in a control task" in text
    assert "a milestone" in text


def test_a_nested_capture_of_the_same_job_writes_each_line_once(isolated_logs: str, tmp_path: Path) -> None:
    """Verify a second log file for the same job does not double the first."""
    from astrometricslib.foundation.jobs.runner import capture_job_logs

    inner_file = tmp_path / "inner.log"
    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        with capture_job_logs(job_id=job.job_id, log_file_path=str(inner_file)):
            logging.getLogger("astrometricslib.pipelines.example").info("one line")
        for handler in _job_sinks(job.job_id):
            handler.flush()
        with open(job.log_file_path) as log_file:
            outer_text = log_file.read()
    assert outer_text.count("one line") == 1
    assert inner_file.read_text().count("one line") == 1


def test_the_log_files_are_closed_not_just_detached(isolated_logs: str) -> None:
    """Verify the opened log file is actually closed on the way out.

    Unregistering a handler stops it receiving messages but leaves its file
    open, and an unclosed handler is a file handle held for the life of the
    process. None of the four hand-written copies this runner replaced
    closed theirs.
    """
    captured: list[logging.Handler] = []

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured.extend(
            handler for handler in _job_sinks(job.job_id) if isinstance(handler, logging.FileHandler)
        )

    assert captured, "expected a file handler to have been registered"
    assert all(handler.stream is None or handler.stream.closed for handler in captured)


def test_work_that_raises_marks_the_job_failed_and_reraises(isolated_logs: str) -> None:
    """Verify an error is recorded but never swallowed.

    Raises
    ------
    RuntimeError
        Raised on purpose inside the block, to check it comes back out.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    with pytest.raises(RuntimeError, match="work blew up"):
        with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
            captured_job_id = job.job_id
            raise RuntimeError("work blew up")

    stored = JobStore(isolated_logs).get_job(captured_job_id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.progress_current == 0
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []


def test_work_that_finishes_quietly_is_marked_completed(isolated_logs: str) -> None:
    """Verify no news is good news when the work does not say otherwise."""
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured_job_id = job.job_id

    stored = JobStore(isolated_logs).get_job(captured_job_id)
    assert stored.status == "completed"
    assert stored.progress_current == 100


def test_work_that_decides_its_own_outcome_is_believed(isolated_logs: str) -> None:
    """Verify an explicit outcome is not overwritten by the default.

    Stacking can finish without raising and still produce no image, so it
    marks itself failed. That must stick.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as job:
        captured_job_id = job.job_id
        job.mark("failed", 100)

    stored = JobStore(isolated_logs).get_job(captured_job_id)
    assert stored.status == "failed"


def test_a_broken_logs_database_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a job we cannot record is still a job worth doing."""
    from astrometricslib.foundation.jobs import runner

    def _unopenable(*args: object, **kwargs: object) -> object:
        raise OSError("logs database unavailable")

    monkeypatch.setattr(runner, "JobStore", _unopenable)
    did_the_work = False

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        did_the_work = True
        job.info("still fine")
        assert job.job_id is None

    assert did_the_work
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []


def test_an_explicit_log_file_is_used_as_given(isolated_logs: str, tmp_path: Path) -> None:
    """Verify a caller-supplied log path wins over a generated name.

    A caller that already has its own job log open (a script or
    notebook run, say) needs that exact path used, not a fresh one.
    """
    chosen = str(tmp_path / "chosen.log")

    with registered_job(enabled=True, job_type="stacking", target_id="Vega", log_file=chosen) as job:
        assert job.log_file_path == chosen


def test_a_generated_log_name_says_what_kind_of_job_it_was(isolated_logs: str) -> None:
    """Verify generated names keep the job type and a safe target name."""
    with registered_job(enabled=True, job_type="analysis", target_id="NGC 7000") as job:
        assert "analysis_NGC_7000_" in job.log_file_path
        assert job.log_file_path.endswith(".log")


def test_a_bare_handle_is_safe_to_use() -> None:
    """Verify the no-op handle tolerates every call without a database."""
    handle = JobHandle()

    handle.info("nothing")
    handle.error("nothing")
    handle.mark("completed", 100)


def test_mark_can_also_set_message_and_output_metrics(isolated_logs: str) -> None:
    """Verify the additive `mark()` fields round-trip through storage.

    `run_as_background_job` relies on this to stash a job's real result
    (and quality snapshots) somewhere a later poll can retrieve them.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(enabled=True, job_type="analysis", target_id="Vega") as job:
        captured_job_id = job.job_id
        job.mark("completed", 100, message="3 of 3 succeeded", output_metrics={"result": {"path": "x.fits"}})

    stored = JobStore(isolated_logs).get_job(captured_job_id)
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
        from astrometricslib.foundation.jobs.runner import run_as_background_job
        from astrometricslib.foundation.jobs.store import JobStore

        outcome = run_as_background_job(
            _stack_vega({"stackedImage": "Vega_Stacked.fits"}), grace_period_seconds=2.0
        )

        assert outcome["result"] == {"stackedImage": "Vega_Stacked.fits"}
        stored = JobStore(isolated_logs).get_job(outcome["jobId"])
        assert stored.target_id == "Vega"
        assert stored.output_metrics["result"] == {"stackedImage": "Vega_Stacked.fits"}

    def test_slow_work_returns_a_job_id_and_keeps_running(self, isolated_logs: str) -> None:
        """A call that outlasts the grace period hands back a job id.

        The work itself must keep running in the background rather than
        being abandoned: a caller giving up on waiting must not kill it.
        """
        from astrometricslib.foundation.jobs.runner import run_as_background_job
        from astrometricslib.foundation.jobs.store import JobStore

        outcome = run_as_background_job(
            _stack_vega({"stackedImage": "Vega_Stacked.fits"}, delay_seconds=0.3), grace_period_seconds=0.1
        )

        assert outcome["status"] == "running"
        assert outcome["jobId"] is not None
        assert outcome["logFilePath"] is not None

        job_store = JobStore(isolated_logs)
        deadline = time.monotonic() + 2.0
        stored = job_store.get_job(outcome["jobId"])
        while stored is not None and not stored.output_metrics and time.monotonic() < deadline:
            time.sleep(0.02)
            stored = job_store.get_job(outcome["jobId"])

        assert stored is not None and stored.status == "completed", "background work should keep running"
        assert stored.output_metrics["result"] == {"stackedImage": "Vega_Stacked.fits"}

    def test_failing_work_reports_the_error(self, isolated_logs: str) -> None:
        """A quick failure is raised to the caller, not swallowed."""
        from astrometricslib.foundation.jobs.runner import run_as_background_job

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
        from astrometricslib.foundation.jobs.runner import run_as_background_job

        outcome = run_as_background_job(lambda: {"success": True}, grace_period_seconds=2.0)

        assert outcome == {"jobId": None, "result": {"success": True}}


def test_a_nested_job_of_the_same_kind_joins_the_running_one(isolated_logs: str) -> None:
    """Verify the same work is not listed twice.

    The MCP wrapper around a stacking method and the method's own
    registration used to make two job rows for one stack.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as outer:
        with registered_job(enabled=True, job_type="stacking", target_id="Vega") as inner:
            assert inner is outer
            inner.mark("failed", 100)

    stacking_jobs = JobStore(isolated_logs).get_jobs_by_target("Vega")
    assert len(stacking_jobs) == 1
    assert outer.terminal_status == "failed"


def test_a_nested_job_of_another_kind_gets_its_own_row(isolated_logs: str) -> None:
    """Verify a stacking job inside a batch job is still listed separately."""
    with registered_job(enabled=True, job_type="batch_processing", target_id="Vega") as batch:
        with registered_job(enabled=True, job_type="stacking", target_id="Vega") as stacking:
            assert stacking.job_id != batch.job_id


def test_each_job_log_holds_only_its_own_work(isolated_logs: str) -> None:
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


def test_stage_moves_progress_and_keeps_a_finished_status(isolated_logs: str) -> None:
    """Verify stage reports a running step and never undoes a finished job."""
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(enabled=True, job_type="stacking", target_id="Vega") as job:
        job.stage(40, "Stacking")
        stored = JobStore(isolated_logs).get_job(job.job_id)
        assert (stored.status, stored.progress_current, stored.message) == ("running", 40, "Stacking")
        job.mark("failed", 100)
        job.stage(80, "Too late")
        assert JobStore(isolated_logs).get_job(job.job_id).status == "failed"
