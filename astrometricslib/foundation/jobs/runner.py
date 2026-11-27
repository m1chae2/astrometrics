"""Purpose: Run a long piece of work as a recorded job, and collect its log.

Description: A "job" is any piece of work slow enough that the user
interface wants to show progress for it: stacking a target, running an
analysis, downloading frames from the telescope. For each one the same
three things happen:

1. A row is written into the logs database (`JobStore`), so the job
   appears in the job list.
2. The job's log file and database rows are registered with the job log
   router (`astrometricslib.foundation.logging.JobLogRouter`). The work
   runs inside a log context that names the job, so every message any
   module of either library writes during the work reaches them.
3. When the work ends, the job is marked finished and its log sinks are
   unregistered and closed.

No handler is attached to a shared logger and no logger level is changed,
so nothing can be left behind when a job ends. The program decides how
loud logging is, through `configure_logging`.

This module also holds `background_job`, the marker for methods that a
client runs in the background (each client's adapter has the wrapper that
runs them), and `close_interrupted_jobs`, which closes the jobs a program
left open when it ended.
"""

import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.process_identity import current_process_identity, process_is_alive
from astrometricslib.foundation.jobs.store import DbLogHandler, JobStore
from astrometricslib.foundation.logging import (
    active_job_ids,
    get_job_log_router,
    get_log_context,
    log_context,
)
from astrometricslib.foundation.parameter_context import collect_default_notices

logger = logging.getLogger(__name__)

WAITING_FOR_SLOT_MESSAGE = "Waiting for a free job slot"
"""The message of a job that waits for a slot (`JobHandle.holding_slot`)."""

# Statuses that mean the job is over. Once the work sets one of these, the
# context manager stops second-guessing it -- see `registered_job`.
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


_current_job: ContextVar[JobHandle | None] = ContextVar("current_job", default=None)
"""The job being run, set by `registered_job`."""

_job_started_listener: ContextVar[Callable[[JobHandle], None] | None] = ContextVar(
    "job_started_listener", default=None
)
"""Told about each new job row `registered_job` makes in this thread. Set by
`report_started_jobs`, which a client's background job wrapper uses to learn
the id of the job its work records."""


def get_current_job() -> JobHandle | None:
    """Give the job the calling work is running as, if any.

    Work that runs inside `registered_job` (every method that records its
    own job) can use this to write log lines and report progress without
    being handed the job.

    Returns
    -------
    job : `JobHandle` or `None`
        The running job's handle, or `None` when the code is not running
        as a background job, such as in a plain function call or a test.
    """
    return _current_job.get()


@contextmanager
def report_started_jobs(callback: Callable[[JobHandle], None]) -> Generator[None]:
    """Call a function with the handle of each job the enclosed work records.

    A caller that runs work in a thread cannot see the job the work records
    inside it. This gives the caller the handle as soon as the job exists.

    A listener that is already active (the one a background job wrapper
    sets) is told first, so nesting this never hides a job from it.

    Parameters
    ----------
    callback : `Callable`
        Called with each new job's handle, outermost job first.

    Yields
    ------
    None
        The enclosed work runs while the callback is active.
    """
    outer_listener = _job_started_listener.get()

    def _tell_both(handle: JobHandle) -> None:
        """Tell the enclosing listener, if any, and then this one."""
        if outer_listener is not None:
            outer_listener(handle)
        callback(handle)

    token = _job_started_listener.set(_tell_both)
    try:
        yield
    finally:
        _job_started_listener.reset(token)


def current_job_id() -> str | None:
    """Give the id of the job the calling work is running as, if any.

    Rows that a run stores carry this id, so `Jobs.query` can list the
    warnings of the job. A run inside a larger job joins it and uses the
    enclosing job's id. The backend's analysis jobs are created by the backend
    and tracked with `capture_job_logs`, which names the job in the log
    context, so the log context is read too.

    Returns
    -------
    job_id : `str` or `None`
        The running job's id, or `None` when the code runs outside any
        recorded job.
    """
    job = _current_job.get()
    if job is not None and job.job_id:
        return job.job_id
    logged_id = get_log_context().get("job_id")
    # A parallel batch tags each item "batch-item:<id>" in the log context;
    # that is not a job in the job list.
    if logged_id is None or str(logged_id).startswith("batch-item:"):
        return None
    return str(logged_id)


class JobHandle:
    """A handle to one running job, used to log messages and report status.

    A handle is always returned, even when job recording is switched off
    or the logs database could not be opened. In that case every method
    quietly does nothing, so the work being wrapped never has to check
    whether recording succeeded.

    Attributes
    ----------
    job_id : `str` or `None`
        Unique id for this job, or `None` if nothing was recorded.
    log_file_path : `str` or `None`
        Where this job's log file is being written.
    job_type : `str` or `None`
        What kind of job this is, such as "stacking".
    target_id : `str` or `None`
        Which target the job is working on.
    reached_terminal_status : `bool`
        Whether the work has already said how the job ended.
    terminal_status : `str` or `None`
        How the job ended ("completed" or "failed") once the work has said
        so, otherwise `None`.
    """

    def __init__(
        self,
        job_id: str | None = None,
        log_file_path: str | None = None,
        job_store: JobStore | None = None,
        job_type: str | None = None,
        target_id: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.log_file_path = log_file_path
        self.job_type = job_type
        self.target_id = target_id
        self._job_store = job_store
        self.reached_terminal_status = False
        self.terminal_status: str | None = None

    def stage(self, progress_current: int, message: str) -> None:
        """Report that the job has reached a named step.

        Writes the step to the job's log and moves the job's progress bar, so
        a person watching the job list can tell a slow job from a stuck one.
        Does nothing to the status once the job has finished.

        Parameters
        ----------
        progress_current : `int`
            How far along the job is, out of 100.
        message : `str`
            A short description of the step, such as "Stacking frames".
        """
        self.info(f"Stage ({progress_current}%): {message}")
        if not self.reached_terminal_status:
            self.mark("running", progress_current, message=message)

    def report_count(self, done: int, total: int, message: str) -> None:
        """Report how many items the job has finished, as real counts.

        Unlike `stage`, which gives a percent out of 100, this stores the
        counts themselves as the job's progress, so a reader gets "24 of 40"
        from the job record and need not read the message text. The message
        is still written to the log and kept as the job's status line. Does
        nothing to the status once the job has finished.

        Parameters
        ----------
        done : `int`
            How many items are finished.
        total : `int`
            How many items the job has in all.
        message : `str`
            A short description, such as "Measured 24 of 40 frames".
        """
        self.info(message)
        if not self.reached_terminal_status:
            self.mark("running", done, progress_total=total, message=message)

    @contextmanager
    def holding_slot(
        self, slot: AbstractContextManager, *, working_message: str = "Starting"
    ) -> Generator[None]:
        """Wait for a job slot as this job, then hold it while the work runs.

        Call this inside `registered_job`, not around it. The job is then
        in the job list while it waits, with the message "Waiting for a free
        job slot", and its job id is known at once. A run given up on stops
        waiting (the slot raises `RunAbandonedError`) and its job is
        marked failed by `registered_job` like any other error.

        Parameters
        ----------
        slot : `contextlib.AbstractContextManager`
            The slot to take, such as the result of
            ``acquire_resource_slot(config, "job", ...)``.
        working_message : `str`, optional
            The job's message once the slot is won. The work's own stage
            reports replace it.

        Yields
        ------
        None
            The caller's work runs while the slot is held.
        """
        if not self.reached_terminal_status:
            self.mark("running", 0, message=WAITING_FOR_SLOT_MESSAGE)
        with slot:
            if not self.reached_terminal_status:
                self.mark("running", 0, message=working_message)
            yield

    def info(self, message: str) -> None:
        """Write an informational line to this job's log.

        The line goes through this module's logger. The job log router
        delivers it to the job's log file and rows, because the work runs
        inside the job's log context.

        Parameters
        ----------
        message : `str`
            The line to write.
        """
        if self.job_id:
            logger.info("%s", message)

    def error(self, message: str) -> None:
        """Write an error line to this job's log.

        Parameters
        ----------
        message : `str`
            The line to write.
        """
        if self.job_id:
            logger.error("%s", message)

    def mark(
        self,
        status: str,
        progress_current: int = 100,
        *,
        progress_total: int | None = None,
        message: str | None = None,
        output_metrics: dict | None = None,
    ) -> None:
        """Record how far along this job is, or that it has finished.

        Failures here are deliberately swallowed. Updating the job list is
        a convenience for the user interface; it must never take down the
        actual work.

        Parameters
        ----------
        status : `str`
            The job's new status, such as "completed" or "failed".
        progress_current : `int`, optional
            How far along the job is, out of 100. Defaults to 100.
        progress_total : `int`, optional
            What "all the way along" means, when it isn't the usual 100
            (for example, a batch's frame or target count). Left
            unchanged when omitted.
        message : `str`, optional
            A human-readable status line, such as a batch's final
            succeeded/failed/skipped counts. Left unchanged when omitted.
        output_metrics : `dict`, optional
            Structured results a later caller would want back -- the
            work's own return value, and/or the target(s)' quality
            summaries before and after, so a poller has something real to
            discuss, not just a status word. Left unchanged when omitted.
        """
        if status in _TERMINAL_STATUSES:
            self.reached_terminal_status = True
            self.terminal_status = status

        if not (self._job_store and self.job_id):
            return
        try:
            stored_job = self._job_store.get_job(self.job_id)
            if stored_job:
                stored_job.status = status
                stored_job.progress_current = progress_current
                if progress_total is not None:
                    stored_job.progress_total = progress_total
                if message is not None:
                    stored_job.message = message
                if output_metrics is not None:
                    # Merge, so keys the job already stored (such as the
                    # parameter defaults) are not lost.
                    stored_job.output_metrics = {**(stored_job.output_metrics or {}), **output_metrics}
                stored_job.updated_at = datetime.now().isoformat()
                if status in _TERMINAL_STATUSES and stored_job.completed_at is None:
                    stored_job.completed_at = stored_job.updated_at
                self._job_store.upsert_job(stored_job)
        except sqlite3.Error as update_error:
            logger.debug("Could not update job '%s' to '%s': %s", self.job_id, status, update_error)


#: The (job id, absolute log file path) pairs that have a file sink right now,
#: so a run nested inside a job does not open a second handler on the job's
#: own file, which would write every line twice.
_open_log_files: set[tuple[str, str]] = set()
_open_log_files_lock = threading.Lock()

#: The library packages whose messages reach a job's log even when the work
#: that wrote them lost the job's log context, such as a worker thread that
#: was started without copying it.
JOB_LOG_PACKAGES: tuple[str, ...] = ("astrometricslib", "wayfindinglib")


PARAMETER_DEFAULTS_KEY = "parameter_defaults"
"""The key in a job's ``output_metrics`` that lists the notices of the
parameters that used their defaults."""


class _BorrowedJobHandle(JobHandle):
    """The handle of a job row its caller made and finishes itself.

    Work that joins it may report progress and log lines, but how the job
    ends is the caller's decision, so a finished or failed status the work
    reports is not stored.
    """

    def mark(
        self,
        status: str,
        progress_current: int = 100,
        *,
        progress_total: int | None = None,
        message: str | None = None,
        output_metrics: dict | None = None,
    ) -> None:
        """Record progress, but leave a finished status to the caller.

        Parameters
        ----------
        status : `str`
            The status the work reports. A final one is ignored.
        progress_current : `int`, optional
            How far along the job is.
        progress_total : `int`, optional
            What "all the way along" means.
        message : `str`, optional
            A status line.
        output_metrics : `dict`, optional
            Structured results.
        """
        if status in _TERMINAL_STATUSES:
            return
        super().mark(
            status,
            progress_current,
            progress_total=progress_total,
            message=message,
            output_metrics=output_metrics,
        )


def _record_default_notices(job_id: str, job_store: JobStore | None, notices: list[str]) -> None:
    """Log one line about the defaults a job used and store the full list.

    A parameter with no tuned or equipment value uses its default and adds a
    notice (see `collect_default_notices`). One log line says how many, so
    the log is not filled with one warning per parameter. The notices are
    stored in the job's ``output_metrics`` so the job can list them later.

    Parameters
    ----------
    job_id : `str`
        The job.
    job_store : `JobStore` or `None`
        Where the job row is. `None` skips storing.
    notices : `list` [`str`]
        The notices the job collected.
    """
    if not notices:
        return
    logger.info("%d parameters used their defaults (no tuned or equipment value)", len(notices))
    if job_store is None:
        return
    try:
        stored_job = job_store.get_job(job_id)
        if stored_job is not None:
            stored_job.output_metrics = {**(stored_job.output_metrics or {}), PARAMETER_DEFAULTS_KEY: notices}
            job_store.upsert_job(stored_job)
    except sqlite3.Error as store_error:
        logger.debug("Could not store the default notices of job '%s': %s", job_id, store_error)


@contextmanager
def capture_job_logs(
    *,
    job_id: str,
    log_file_path: str | None,
    job_store: JobStore | None = None,
) -> Generator[None]:
    """Send the log messages written during a block to one job's log.

    This is the half every caller needs, whether the job row was just
    created here or already existed. The job's log file and database rows
    are registered with the job log router, and the block runs inside a log
    context that names the job. Every message any module writes inside the
    block, through its own ``logging.getLogger(__name__)`` logger, then
    reaches the job's log. Callers write their own milestone lines the same
    way.

    On the way out the log sinks are unregistered *and closed*. A file
    handler that is only removed stops receiving messages but leaves its
    file open, and an unclosed handler is a file handle held for the life of
    the process.

    Parameters
    ----------
    job_id : `str`
        Identifies the job.
    log_file_path : `str` or `None`
        Where to write this job's log file. `None` skips the file and
        keeps only the database rows.
    job_store : `JobStore`, optional
        Where to write the job's log rows. `None` skips the database rows.
        The rows are also skipped when the block already runs inside the
        same job, whose own capture writes them.

    Yields
    ------
    None
        The block runs with the job's log context.
    """
    sinks: list[logging.Handler] = []
    file_key: tuple[str, str] | None = None
    if log_file_path:
        candidate_key = (job_id, os.path.abspath(log_file_path))
        with _open_log_files_lock:
            # A nested capture for the same job and file adds nothing: the
            # outer capture already writes there, and a second handler would
            # write each line twice.
            if candidate_key not in _open_log_files:
                _open_log_files.add(candidate_key)
                file_key = candidate_key
    sink_key = None
    router = get_job_log_router()
    # Everything after the key is claimed sits in the try, so a failure while
    # opening the log file still frees the key.
    try:
        if log_file_path and file_key is not None:
            log_directory = os.path.dirname(log_file_path)
            if log_directory:
                os.makedirs(log_directory, exist_ok=True)
            file_handler = logging.FileHandler(log_file_path)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            sinks.append(file_handler)
        if job_store is not None and job_id not in active_job_ids():
            sinks.append(DbLogHandler(job_store, job_id=job_id))

        # The router sends each record that carries this job's id to the sinks.
        # Nothing is attached to a shared logger, so nothing is left behind.
        sink_key = router.register(job_id, sinks, JOB_LOG_PACKAGES)
        with log_context(job_id=job_id), ExitStack() as notice_scope:
            # A run nested in the same job adds to the outer run's list.
            collects_notices = job_id not in active_job_ids()[:-1]
            notices = notice_scope.enter_context(collect_default_notices()) if collects_notices else []
            try:
                yield
            finally:
                if collects_notices:
                    _record_default_notices(job_id, job_store, notices)
    finally:
        if sink_key is not None:
            for handler in router.unregister(sink_key):
                try:
                    handler.close()
                except OSError as close_error:
                    logger.debug("Could not close a job log handler: %s", close_error)
        if file_key is not None:
            with _open_log_files_lock:
                _open_log_files.discard(file_key)


def _create_job_row(*, job_type: str, target_id: str, log_file: str | None) -> tuple[str, str, JobStore]:
    """Write a "started" row for a new job into the logs database.

    Returns
    -------
    job_id : `str`
        The new job's unique id.
    log_file_path : `str`
        Where this job's log file should be written.
    job_store : `JobStore`
        The logs database the row was written to.
    """
    from astrometricslib.foundation.config import get_configuration

    configuration = get_configuration()
    job_store = JobStore(str(configuration.get_logs_db_path()))
    close_interrupted_jobs(configuration)
    owner_pid, owner_started_at = current_process_identity()
    job_id = str(uuid.uuid4())

    safe_target = target_id.replace(" ", "_").replace("/", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_directory = configuration.get_logs_path()
    os.makedirs(log_directory, exist_ok=True)
    log_file_path = log_file or str(log_directory / f"{job_type}_{safe_target}_{timestamp}.log")

    job_store.upsert_job(
        ProcessingJob(
            id=job_id,
            target_id=target_id,
            job_type=job_type,
            status="started",
            progress_current=0,
            progress_total=100,
            log_file_path=log_file_path,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            owner_pid=owner_pid,
            owner_started_at=owner_started_at,
        )
    )
    return job_id, log_file_path, job_store


InterruptedJobCleanup = Callable[[list[ProcessingJob], JobStore, Any], None]
"""A function that tidies up after some kinds of interrupted job.

It receives the jobs just closed, the job store, and the application
configuration.
"""

_interrupted_job_cleanups: list[InterruptedJobCleanup] = []


def register_interrupted_job_cleanup(cleanup: InterruptedJobCleanup) -> None:
    """Add a function that tidies up after jobs a program left open.

    `close_interrupted_jobs` calls each registered function with the jobs it
    closed. The stacking stage uses this to put back a stack that a restack
    left parked in a staging folder. A function registered twice runs once.

    Parameters
    ----------
    cleanup : `InterruptedJobCleanup`
        The function to call.
    """
    if cleanup not in _interrupted_job_cleanups:
        _interrupted_job_cleanups.append(cleanup)


def recover_interrupted_jobs(
    job_store: JobStore,
    configuration: Any,
    is_process_alive: Callable[[int, str], bool] = process_is_alive,
) -> list[ProcessingJob]:
    """Close the jobs a program left open, then run the registered cleanups.

    Parameters
    ----------
    job_store : `JobStore`
        The logs database that holds the job list.
    configuration : `AppConfiguration`
        Passed on to the cleanup functions, which find their folders in it.
    is_process_alive : `Callable`, optional
        Says whether a program, given its number and start time, still runs.
        Tests pass a stand-in.

    Returns
    -------
    interrupted : `list` [`ProcessingJob`]
        The jobs that were closed.
    """
    interrupted = job_store.interrupt_orphaned_jobs(is_process_alive)
    for job in interrupted:
        logger.warning(
            "Closed %s job %s for '%s': the program running it ended before it finished.",
            job.job_type,
            job.id,
            job.target_id,
        )
    if interrupted:
        for cleanup in list(_interrupted_job_cleanups):
            cleanup(interrupted, job_store, configuration)
    return interrupted


def close_interrupted_jobs(configuration: Any | None = None) -> list[ProcessingJob]:
    """Close the jobs a program left open, using the app's own settings.

    A program calls this when it starts, and the job runner calls it before
    each new job is recorded, so a stuck job does not stay in the list until
    someone notices it. It never raises: tidying the job list must not stop a
    program from starting or new work from running.

    Parameters
    ----------
    configuration : `AppConfiguration`, optional
        Where the logs database is found. Defaults to the app's
        configuration.

    Returns
    -------
    interrupted : `list` [`ProcessingJob`]
        The jobs that were closed. Empty if there were none or the job list
        could not be read.
    """
    try:
        if configuration is None:
            from astrometricslib.foundation.config import get_configuration

            configuration = get_configuration()
        return recover_interrupted_jobs(JobStore(str(configuration.get_logs_db_path())), configuration)
    except (AstrometricsError, sqlite3.Error, OSError) as recovery_error:
        logger.warning("Could not close interrupted jobs: %s", recovery_error)
        return []


@contextmanager
def registered_job(
    *,
    job_type: str,
    target_id: str,
    log_file: str | None = None,
    completed_message: str | None = None,
    failed_message: str | None = None,
) -> Generator[JobHandle]:
    """Record a job, capture its log messages, and always clean up after it.

    Wrap the slow work in this. On the way out the job is marked finished
    and the job's log sinks are unregistered and closed, whether the work
    succeeded, failed, or raised.

    If the work raises, the job is marked "failed" and the error is passed
    straight back up -- recording a job must never turn a failure into a
    silent success. If the work finishes without marking itself, it is
    assumed to have completed. Work that decides its own outcome (stacking
    can finish cleanly but still produce no image) should call
    `handle.mark(...)` itself; that choice is respected.

    Every pipeline run records a job this way, whoever starts it. Work that
    runs inside a job that is already current (one recorded here, or a row
    the caller made and entered with `running_as_job`) is part of that job:
    it joins it instead of listing the same work twice, and the outer owner
    decides how the job ends.

    Parameters
    ----------
    job_type : `str`
        What kind of job this is, such as "analysis" or "stacking". Also
        used as the log file name prefix.
    target_id : `str`
        Which target the job is working on.
    log_file : `str`, optional
        Write the log here instead of generating a name.
    completed_message : `str`, optional
        Line to write to the job log when the work finishes successfully.
    failed_message : `str`, optional
        Line to write to the job log when the work fails.

    Yields
    ------
    handle : `JobHandle`
        Used to log messages and report progress.
    """
    running_job = _current_job.get()
    if running_job is not None and running_job.job_id is not None:
        # The work already runs as a job, for example a stage of a target's
        # analysis or a call the backend or the MCP server runs as its own
        # job. Join it instead of listing the same work twice. The outer
        # owner decides how the job ends.
        try:
            yield running_job
        except Exception:
            if failed_message:
                running_job.error(failed_message)
            raise
        else:
            if completed_message:
                running_job.info(completed_message)
        return

    handle = JobHandle()

    with ExitStack() as log_capture:
        try:
            job_id, log_file_path, job_store = _create_job_row(
                job_type=job_type, target_id=target_id, log_file=log_file
            )
            log_capture.enter_context(
                capture_job_logs(job_id=job_id, log_file_path=log_file_path, job_store=job_store)
            )
            handle = JobHandle(
                job_id=job_id,
                log_file_path=log_file_path,
                job_store=job_store,
                job_type=job_type,
                target_id=target_id,
            )
            listener = _job_started_listener.get()
            if listener is not None:
                listener(handle)
        except (sqlite3.Error, OSError, AstrometricsError) as registration_error:
            # A job we cannot record is still a job worth doing.
            logger.warning("Could not register %s job: %s", job_type, registration_error)
            handle = JobHandle()

        job_token = _current_job.set(handle if handle.job_id else _current_job.get())
        try:
            yield handle
        except Exception:
            handle.mark("failed", 0)
            if failed_message:
                handle.error(failed_message)
            raise
        else:
            if not handle.reached_terminal_status:
                handle.mark("completed", 100)
            if completed_message:
                handle.info(completed_message)
        finally:
            _current_job.reset(job_token)


@contextmanager
def running_as_job(
    job_id: str,
    job_store: JobStore,
    *,
    job_type: str,
    target_id: str,
    log_file_path: str | None = None,
) -> Generator[JobHandle]:
    """Run the enclosed work as a job row that its caller already made.

    The backend makes its own job row, then calls library work. Without
    this, the library has no current job, so the work records a second job
    and its stage progress never reaches the row. Inside this block the row
    is the current job, so the work joins it, `get_current_job` gives its
    handle and progress reaches the store.

    This does not create a row and does not decide how the job ends: a
    finished or failed status the work reports is not stored. The caller
    still marks the job completed or failed.

    Parameters
    ----------
    job_id : `str`
        Id of the existing job row.
    job_store : `JobStore`
        The store that holds the row.
    job_type : `str`
        What kind of job this is, such as "stacking".
    target_id : `str`
        Which target the job is working on.
    log_file_path : `str`, optional
        The job's log file, kept on the handle for readers.

    Yields
    ------
    handle : `JobHandle`
        The handle of the existing job.
    """
    handle = _BorrowedJobHandle(
        job_id=job_id,
        log_file_path=log_file_path,
        job_store=job_store,
        job_type=job_type,
        target_id=target_id,
    )
    token = _current_job.set(handle)
    try:
        yield handle
    finally:
        _current_job.reset(token)


def background_job(job_type: str, *, grace_period_seconds: float = 5.0) -> Callable:
    """Mark a method as slow enough to run in the background when served.

    This is metadata only: it stamps the given job type and grace period
    onto the function and returns it unchanged. Calling the decorated
    method directly in Python runs it to completion, as the backend and the
    tests do. A server that must stay responsive (the MCP servers) checks
    for this marker and calls the method through its background job wrapper
    with this job type, so the call is recorded as a job and the server
    answers with its id when the work takes longer than the grace period.

    Parameters
    ----------
    job_type : `str`
        What kind of job this is (see `registered_job`).
    grace_period_seconds : `float`, optional
        How long a caller should wait for the work to finish before
        giving up and returning a job id to poll instead, by default 5.0.

    Returns
    -------
    decorator : `Callable`
        A decorator that stamps the given metadata onto a function.
    """

    def decorator(func: Callable) -> Callable:
        func.__background_job_type__ = job_type
        func.__background_job_grace_period__ = grace_period_seconds
        return func

    return decorator
