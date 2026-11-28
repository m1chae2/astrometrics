"""Tests for the job runner: recording a job and collecting its log.

The runner replaced four hand-written copies of the same block. The tests
below cover the behaviours those copies got wrong: leaving log handlers
attached to a shared logger, never closing the files they opened, and
letting a bookkeeping failure take down the real work. They also check that
the runner never changes logging setup, which only a program may do.
"""

import logging
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from astrometricslib.foundation import config as config_loader
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.jobs.runner import JobHandle, registered_job
from astrometricslib.foundation.logging import get_job_log_router
from astrometricslib.foundation.parameter_context import parameter_context
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

PACKAGE_LOGGER_NAME = "astrometricslib"

FINISHES_IN_TIME_S = 60.0
"""A grace period long enough for quick work to end inside it on a slow
Windows runner. Waiting returns as soon as the work ends, so it costs
nothing."""


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


def test_registered_job_has_no_switch_to_skip_recording() -> None:
    """Every run records a job: the recording switch is gone."""
    import inspect

    assert "enabled" not in inspect.signature(registered_job).parameters


def test_no_handler_is_ever_attached_to_the_shared_logger(isolated_logs: str) -> None:
    """Verify a job's handlers never sit on the shared logger.

    The job log router sends a job its own messages, so the shared logger
    keeps the handlers it had, and nothing is left behind when the job ends.
    """
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []
    level_before = logging.getLogger(PACKAGE_LOGGER_NAME).level

    with registered_job(job_type="analysis", target_id="Vega") as job:
        assert job.job_id is not None
        assert _our_handlers(PACKAGE_LOGGER_NAME) == []
        assert len(_job_sinks(job.job_id)) == 2

    assert _job_sinks(job.job_id) == []
    assert _our_handlers(PACKAGE_LOGGER_NAME) == []
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == level_before


def test_messages_from_deeper_modules_reach_the_jobs_log_file(isolated_logs: str) -> None:
    """Verify a message from any module of either library is in the job log."""
    with registered_job(job_type="analysis", target_id="Vega") as job:
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
    with registered_job(job_type="analysis", target_id="Vega") as job:
        with capture_job_logs(job_id=job.job_id, log_file_path=str(inner_file)):
            logging.getLogger("astrometricslib.pipelines.example").info("one line")
        for handler in _job_sinks(job.job_id):
            handler.flush()
        with open(job.log_file_path) as log_file:
            outer_text = log_file.read()
    assert outer_text.count("one line") == 1
    assert inner_file.read_text().count("one line") == 1


def test_a_nested_capture_on_the_same_file_writes_each_line_once(isolated_logs: str) -> None:
    """Verify a Siril run on its job log file writes each line once."""
    from astrometricslib.foundation.jobs.runner import capture_job_logs

    siril_logger = logging.getLogger("astrometricslib._internal.drivers.siril.stacking.imaging_stacker")
    with registered_job(job_type="analysis", target_id="Vega") as job:
        with capture_job_logs(job_id=job.job_id, log_file_path=job.log_file_path):
            siril_logger.info("log: Conversion succeeded")
        siril_logger.info("log: after the run")
        for handler in _job_sinks(job.job_id):
            handler.flush()
        with open(job.log_file_path) as log_file:
            text = log_file.read()
    assert text.count("log: Conversion succeeded") == 1
    assert text.count("log: after the run") == 1


def test_the_log_files_are_closed_not_just_detached(isolated_logs: str) -> None:
    """Verify the opened log file is actually closed on the way out.

    Unregistering a handler stops it receiving messages but leaves its file
    open, and an unclosed handler is a file handle held for the life of the
    process. None of the four hand-written copies this runner replaced
    closed theirs.
    """
    captured: list[logging.Handler] = []

    with registered_job(job_type="analysis", target_id="Vega") as job:
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
        with registered_job(job_type="analysis", target_id="Vega") as job:
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

    with registered_job(job_type="analysis", target_id="Vega") as job:
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

    with registered_job(job_type="stacking", target_id="Vega") as job:
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

    with registered_job(job_type="analysis", target_id="Vega") as job:
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

    with registered_job(job_type="stacking", target_id="Vega", log_file=chosen) as job:
        assert job.log_file_path == chosen


def test_a_generated_log_name_says_what_kind_of_job_it_was(isolated_logs: str) -> None:
    """Verify generated names keep the job type and a safe target name."""
    with registered_job(job_type="analysis", target_id="NGC 7000") as job:
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

    The adapters' background job wrappers rely on this to stash a job's real
    result somewhere a later poll can retrieve them.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(job_type="analysis", target_id="Vega") as job:
        captured_job_id = job.job_id
        job.mark("completed", 100, message="3 of 3 succeeded", output_metrics={"result": {"path": "x.fits"}})

    stored = JobStore(isolated_logs).get_job(captured_job_id)
    assert stored.message == "3 of 3 succeeded"
    assert stored.output_metrics == {"result": {"path": "x.fits"}}


def test_a_nested_job_of_the_same_kind_joins_the_running_one(isolated_logs: str) -> None:
    """Verify the same work is not listed twice.

    The MCP wrapper around a stacking method and the method's own
    registration used to make two job rows for one stack.
    """
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(job_type="stacking", target_id="Vega") as outer:
        with registered_job(job_type="stacking", target_id="Vega") as inner:
            assert inner is outer
            inner.mark("failed", 100)

    stacking_jobs = JobStore(isolated_logs).get_jobs_by_target("Vega")
    assert len(stacking_jobs) == 1
    assert outer.terminal_status == "failed"


def test_a_nested_job_of_another_kind_joins_the_running_one(isolated_logs: str) -> None:
    """Verify work inside a job of another kind is part of that job."""
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(job_type="remote_sync", target_id="Vega") as outer:
        with registered_job(job_type="quality", target_id="Vega") as inner:
            assert inner is outer
    assert len(JobStore(isolated_logs).get_jobs_by_target("Vega")) == 1


def test_each_job_log_holds_only_its_own_work(isolated_logs: str) -> None:
    """Verify two jobs running at once do not write into each other's log."""
    import threading

    package_logger = logging.getLogger(f"{PACKAGE_LOGGER_NAME}.test_isolation")
    both_registered = threading.Barrier(2)
    log_paths: dict[str, str] = {}

    def run_job(target_id: str) -> None:
        """Log one line as a job, once both jobs are registered."""
        with registered_job(job_type="stacking", target_id=target_id) as job:
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

    with registered_job(job_type="stacking", target_id="Vega") as job:
        job.stage(40, "Stacking")
        stored = JobStore(isolated_logs).get_job(job.job_id)
        assert (stored.status, stored.progress_current, stored.message) == ("running", 40, "Stacking")
        job.mark("failed", 100)
        job.stage(80, "Too late")
        assert JobStore(isolated_logs).get_job(job.job_id).status == "failed"


def test_current_job_id_is_none_outside_any_job() -> None:
    """Outside a job there is no id to put on stored rows."""
    from astrometricslib.foundation.jobs.runner import current_job_id

    assert current_job_id() is None


def test_current_job_id_reads_the_job_named_in_the_log_context() -> None:
    """A job created by the backend names itself in the log context."""
    from astrometricslib.foundation.jobs.runner import current_job_id
    from astrometricslib.foundation.logging import log_context

    with log_context(job_id="job-from-backend"):
        assert current_job_id() == "job-from-backend"


def test_current_job_id_ignores_a_parallel_batch_item() -> None:
    """A batch item tag is not a job in the job list."""
    from astrometricslib.foundation.jobs.runner import current_job_id
    from astrometricslib.foundation.logging import log_context

    with log_context(job_id="batch-item:7"):
        assert current_job_id() is None


def test_report_started_jobs_gives_the_caller_the_handle() -> None:
    """Tell the caller of threaded work the id of the job it records."""
    from types import SimpleNamespace

    from astrometricslib.foundation.jobs import runner

    seen: list[str] = []
    with runner.report_started_jobs(lambda handle: seen.append(handle.job_id)):
        runner._job_started_listener.get()(SimpleNamespace(job_id="job-1"))
    assert seen == ["job-1"]
    assert runner._job_started_listener.get() is None


def test_report_count_stores_the_counts_and_keeps_a_finished_status(isolated_logs: str) -> None:
    """Verify report_count stores the counts and keeps a finished status."""
    from astrometricslib.foundation.jobs.store import JobStore

    with registered_job(job_type="quality", target_id="Vega") as job:
        job.report_count(24, 40, "Measured 24 of 40 frames")
        stored = JobStore(isolated_logs).get_job(job.job_id)
        assert (stored.status, stored.progress_current, stored.progress_total) == ("running", 24, 40)
        assert stored.message == "Measured 24 of 40 frames"
        job.mark("failed", 100)
        job.report_count(30, 40, "Too late")
        assert JobStore(isolated_logs).get_job(job.job_id).status == "failed"


def test_a_log_file_that_cannot_be_opened_still_frees_its_key(
    isolated_logs: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a failed log file open still frees the file key."""
    from astrometricslib.foundation.jobs import runner

    log_path = str(tmp_path / "sub" / "job.log")

    def fail(*_args: object, **_kwargs: object) -> None:
        """Stand in for a disk that refuses the new folder.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("disk full")

    monkeypatch.setattr(runner.os, "makedirs", fail)
    with (
        pytest.raises(OSError, match="disk full"),
        runner.capture_job_logs(job_id="job-x", log_file_path=log_path),
    ):
        pass
    assert not [key for key in runner._open_log_files if key[0] == "job-x"]


def test_running_as_job_makes_an_existing_row_current_and_reports_progress(isolated_logs: str) -> None:
    """Verify progress reaches an existing row, which is not ended."""
    from astrometricslib.foundation.jobs.models import ProcessingJob
    from astrometricslib.foundation.jobs.runner import get_current_job, running_as_job
    from astrometricslib.foundation.jobs.store import JobStore

    store = JobStore(isolated_logs)
    store.upsert_job(
        ProcessingJob(
            id="owner-1",
            job_type="stacking",
            target_id="Vega",
            status="running",
            progress_current=0,
            progress_total=100,
        )
    )
    assert get_current_job() is None

    with running_as_job("owner-1", store, job_type="stacking", target_id="Vega") as handle:
        assert get_current_job() is handle
        handle.stage(30, "Stage 2 of 7")

    assert get_current_job() is None
    stored = store.get_job("owner-1")
    assert (stored.status, stored.progress_current) == ("running", 30)
    assert len(store.get_jobs_by_target("Vega")) == 1
    assert not handle.reached_terminal_status


def test_a_job_of_any_new_kind_stores_its_log_rows_under_its_id(isolated_logs: str) -> None:
    """Verify an unknown job type still writes log rows readable by id."""
    from astrometricslib.foundation.jobs.store import JobStore

    package_logger = logging.getLogger(f"{PACKAGE_LOGGER_NAME}.test_new_kind")
    with registered_job(job_type="brand_new_kind", target_id="M 31") as job:
        package_logger.info("first step of a new kind of job")
        package_logger.info("second step of a new kind of job")
        job_id = job.job_id

    messages = [row["message"] for row in JobStore(str(isolated_logs)).get_log_entries_for_job(job_id)]
    assert any("first step" in message for message in messages)
    assert any("second step" in message for message in messages)


def test_a_job_waiting_for_a_slot_is_listed_with_a_waiting_message(isolated_logs: str) -> None:
    """Verify the job row and its message exist while the slot is held."""
    from contextlib import contextmanager

    from astrometricslib.foundation.jobs.runner import WAITING_FOR_SLOT_MESSAGE
    from astrometricslib.foundation.jobs.store import JobStore

    slot_is_free = threading.Event()
    job_is_listed = threading.Event()
    seen: dict[str, object] = {}

    @contextmanager
    def busy_slot() -> Generator[None]:
        """Stand in for a slot that frees only when the test says so."""
        assert slot_is_free.wait(timeout=20.0)
        yield

    def run_job() -> None:
        """Record a job and wait for the busy slot inside it."""
        with registered_job(job_type="preview", target_id="NGC 2244") as job:
            seen["job_id"] = job.job_id
            with job.holding_slot(busy_slot(), working_message="Making the preview"):
                seen["working_status"] = JobStore(isolated_logs).get_job(job.job_id).message

    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()
    store = JobStore(isolated_logs)
    for _ in range(200):
        jobs = store.get_jobs_by_target("NGC 2244")
        if jobs and jobs[0].message == WAITING_FOR_SLOT_MESSAGE:
            job_is_listed.set()
            break
        time.sleep(0.05)
    assert job_is_listed.is_set()
    assert jobs[0].status == "running"

    slot_is_free.set()
    thread.join(timeout=20.0)
    assert seen["working_status"] == "Making the preview"
    assert store.get_job(seen["job_id"]).status == "completed"


def _defaulted_parameter(name: str) -> TunedParameter:
    """Build an equipment-profile parameter that no equipment value sets.

    Returns
    -------
    parameter : `TunedParameter`
        A parameter that falls back to its default with a notice.
    """
    return TunedParameter(
        name=name,
        default=0.5,
        unit="fraction",
        description="A test value.",
        role=ParameterRole.JUDGMENT,
        source=ParameterSource.EQUIPMENT_PROFILE,
        equipment=frozenset({EquipmentKind.CAMERA}),
    )


def test_a_job_records_its_defaulted_parameters_and_logs_one_summary(
    isolated_logs: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Record both defaults with the job and log one INFO line, no warnings.

    A second job in the same process records its own notices again, which a
    once-per-process warning would not.
    """
    from astrometricslib.foundation.jobs.runner import PARAMETER_DEFAULTS_KEY
    from astrometricslib.foundation.jobs.store import JobStore

    first = _defaulted_parameter("stacking.first_value")
    second = _defaulted_parameter("stacking.second_value")
    stored_by_job = []
    for _ in range(2):
        with registered_job(job_type="analysis", target_id="Vega") as job:
            with parameter_context():
                resolve(first)
                resolve(second)
                resolve(second)
        stored_by_job.append(JobStore(isolated_logs).get_job(job.job_id))
    for stored in stored_by_job:
        notices = stored.output_metrics[PARAMETER_DEFAULTS_KEY]
        assert [notice.split(":")[0] for notice in notices] == [
            "stacking.first_value",
            "stacking.second_value",
        ]
        entries = JobStore(isolated_logs).get_log_entries_for_job(stored.id)
        summaries = [entry for entry in entries if "used their defaults" in entry["message"]]
        assert [(entry["level"], entry["message"]) for entry in summaries] == [
            ("INFO", "2 parameters used their defaults (no tuned or equipment value)")
        ]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_work_inside_a_borrowed_row_cannot_finish_it(isolated_logs: str) -> None:
    """A run that joins a caller's row reports progress but not the ending."""
    from astrometricslib.foundation.jobs.models import ProcessingJob
    from astrometricslib.foundation.jobs.runner import running_as_job
    from astrometricslib.foundation.jobs.store import JobStore

    store = JobStore(isolated_logs)
    store.upsert_job(ProcessingJob(id="row-1", target_id="Vega", job_type="stacking", status="running"))
    with running_as_job("row-1", store, job_type="stacking", target_id="Vega"):
        with registered_job(job_type="stacking", target_id="Vega") as job:
            job.stage(30, "Stage 2 of 7")
            job.mark("completed", 100)
    stored = store.get_job("row-1")
    assert (stored.status, stored.progress_current) == ("running", 30)
