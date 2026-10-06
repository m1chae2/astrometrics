"""One place to record a long-running job and capture its log messages.

A "job" here is any piece of work slow enough that the user interface
wants to show progress for it -- stacking a target, running an analysis,
downloading frames from the telescope. For each one we do the same three
things:

1. Write a row into the logs database so the job appears in the job list.
2. Attach log handlers so the messages the work prints along the way get
   saved to that job's own log file and database rows.
3. Take those handlers back off again when the work finishes.

Step 3 is the one that is easy to get wrong. The handlers get attached to
the shared "astrometricslib" logger, which every module in the library
logs through. If they are left attached, this job's log file keeps
collecting messages from every job that runs afterwards. And if the
handlers are never closed, each run leaves an open file behind -- Python
keeps every logger it has ever created, so a long batch run slowly runs
out of file handles.

This module existed as four separate hand-written copies before, in
`analyze_target`, the pipeline's stacking job wrapper, the backend's
analysis orchestrator, and the wayfinding library's transfer task. None
of the four closed their handlers, and only one of them detached from
both loggers.
"""

import dataclasses
import logging
import os
import threading
import uuid
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from astrometricslib.foundation.logging import get_job_log_router, log_context

logger = logging.getLogger(__name__)

# Statuses that mean the job is over. Once the work sets one of these, the
# context manager stops second-guessing it -- see `registered_job`.
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


_current_job: ContextVar[JobHandle | None] = ContextVar("current_job", default=None)
"""The job being run, set by `registered_job` and `run_as_background_job`."""


def get_current_job() -> JobHandle | None:
    """Give the job the calling work is running as, if any.

    Work started through `run_as_background_job` (every tool marked with
    `background_job`) can use this to write log lines and report progress
    without being handed the job.

    Returns
    -------
    job : `JobHandle` or `None`
        The running job's handle, or `None` when the code is not running
        as a background job, such as in a plain function call or a test.
    """
    return _current_job.get()


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
    job_logger : `logging.Logger` or `None`
        Logger writing to this job's own log file and database rows.
    log_file_path : `str` or `None`
        Where this job's log file is being written.
    job_type : `str` or `None`
        What kind of job this is, such as "stacking".
    target_id : `str` or `None`
        Which target the job is working on.
    terminal_status : `str` or `None`
        How the job ended ("completed" or "failed") once the work has said
        so, otherwise `None`.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        job_id: str | None = None,
        job_logger: logging.Logger | None = None,
        log_file_path: str | None = None,
        logger_interface: object | None = None,
        job_type: str | None = None,
        target_id: str | None = None,
    ):
        self.job_id = job_id
        self.job_logger = job_logger
        self.log_file_path = log_file_path
        self.job_type = job_type
        self.target_id = target_id
        self._logger_interface = logger_interface
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

    def info(self, message: str) -> None:
        """Write an informational line to this job's log.

        Parameters
        ----------
        message : `str`
            The line to write.
        """
        if self.job_logger:
            self.job_logger.info(message)

    def error(self, message: str) -> None:
        """Write an error line to this job's log.

        Parameters
        ----------
        message : `str`
            The line to write.
        """
        if self.job_logger:
            self.job_logger.error(message)

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

        if not (self._logger_interface and self.job_id):
            return
        try:
            stored_job = self._logger_interface.get_job(self.job_id)
            if stored_job:
                stored_job.status = status
                stored_job.progress_current = progress_current
                if progress_total is not None:
                    stored_job.progress_total = progress_total
                if message is not None:
                    stored_job.message = message
                if output_metrics is not None:
                    stored_job.output_metrics = output_metrics
                stored_job.updated_at = datetime.now().isoformat()
                self._logger_interface.upsert_job(stored_job)
        except Exception as update_error:
            logger.debug("Could not update job '%s' to '%s': %s", self.job_id, status, update_error)


@contextmanager
def capture_job_logs(
    *,
    job_id: str,
    log_file_path: str | None,
    logger_interface: object | None = None,
    package_logger_name: str = "astrometricslib",
) -> Generator[logging.Logger]:
    """Send this job's log messages to its own log file and database rows.

    This is the half every caller needs, whether the job row was just
    created here or already existed. The job's handlers receive two kinds of
    message: those the caller writes by hand to the job's own logger, and those
    that every module deeper in the work logs. The second kind reaches them
    through the job log router, because the work runs inside a log context
    that names this job. Without it, the job's log would only contain the
    handful of milestone lines the caller wrote, and none of the decisions the
    work actually made along the way.

    On the way out the handlers are unregistered *and closed*. A handler that
    is only removed stops receiving messages but leaves its file open, and an
    unclosed handler is a file handle held for the life of the process.

    Parameters
    ----------
    job_id : `str`
        Identifies the job, and names its private logger.
    log_file_path : `str` or `None`
        Where to write this job's log file. `None` skips the file and
        keeps only the database rows.
    logger_interface : `Any`, optional
        Somewhere to write log rows to, such as a `LoggerInterface` or the
        backend job service's repository. `None` skips the database rows.
    package_logger_name : `str`, optional
        The shared logger whose messages are captured even when they lost the
        job context. Defaults to "astrometricslib"; the wayfinding library
        passes its own.

    Yields
    ------
    job_logger : `logging.Logger`
        The job's own logger, for writing milestone messages.
    """
    from astrometricslib.drivers.logger_interface import DbLogHandler

    job_logger = logging.getLogger(f"job_{job_id}")
    job_logger.propagate = False
    job_logger.setLevel(logging.INFO)

    attached_handlers: list[logging.Handler] = []
    if log_file_path:
        log_directory = os.path.dirname(log_file_path)
        if log_directory:
            os.makedirs(log_directory, exist_ok=True)
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        attached_handlers.append(file_handler)
    if logger_interface is not None:
        attached_handlers.append(DbLogHandler(logger_interface, job_id=job_id))

    for handler in attached_handlers:
        job_logger.addHandler(handler)
    # The router sends each record that carries this job's id to the handlers.
    # No handler is attached to a shared logger, so none can be left behind.
    router = get_job_log_router()
    router.register(job_id, attached_handlers, (package_logger_name,))
    logging.getLogger(package_logger_name).setLevel(logging.INFO)

    try:
        with log_context(job_id=job_id):
            yield job_logger
    finally:
        router.unregister(job_id)
        for handler in attached_handlers:
            job_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception as close_error:
                logger.debug("Could not close a job log handler: %s", close_error)


def _create_job_row(*, job_type: str, target_id: str, log_file: str | None) -> tuple[str, str, object]:
    """Write a "started" row for a new job into the logs database.

    Returns
    -------
    job_id : `str`
        The new job's unique id.
    log_file_path : `str`
        Where this job's log file should be written.
    logger_interface : `Any`
        The open connection to the logs database.
    """
    from astrometricslib.drivers.logger_interface import LoggerInterface
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.utilities.pipeline_models import ProcessingJob
    from astrometricslib.utilities.process_identity import current_process_identity

    configuration = get_configuration()
    logger_interface = LoggerInterface(configuration.get_logs_db_path())
    _recover_interrupted_jobs(configuration)
    owner_pid, owner_started_at = current_process_identity()
    job_id = str(uuid.uuid4())

    safe_target = target_id.replace(" ", "_").replace("/", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_directory = configuration.get_logs_path()
    os.makedirs(log_directory, exist_ok=True)
    log_file_path = log_file or str(log_directory / f"{job_type}_{safe_target}_{timestamp}.log")

    logger_interface.upsert_job(
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
    return job_id, log_file_path, logger_interface


def _recover_interrupted_jobs(configuration: Any) -> None:
    """Close jobs that a program left open, before a new job is recorded.

    Parameters
    ----------
    configuration : `AppConfiguration`
        Where the logs database and the stacks folder are found.
    """
    from astrometricslib.pipelines.shared.interrupted_jobs import close_interrupted_jobs

    close_interrupted_jobs(configuration)


@contextmanager
def registered_job(
    *,
    enabled: bool,
    job_type: str,
    target_id: str,
    log_file: str | None = None,
    completed_message: str | None = None,
    failed_message: str | None = None,
    package_logger_name: str = "astrometricslib",
) -> Generator[JobHandle]:
    """Record a job, capture its log messages, and always clean up after it.

    Wrap the slow work in this. On the way out the job is marked finished
    and the log handlers are removed and closed, whether the work
    succeeded, failed, or raised.

    If the work raises, the job is marked "failed" and the error is passed
    straight back up -- recording a job must never turn a failure into a
    silent success. If the work finishes without marking itself, it is
    assumed to have completed. Work that decides its own outcome (stacking
    can finish cleanly but still produce no image) should call
    `handle.mark(...)` itself; that choice is respected.

    Callers that already have a job row, such as the backend's analysis
    orchestrator, want `capture_job_logs` instead.

    Parameters
    ----------
    enabled : `bool`
        Whether to record anything at all. When `False` the handle still
        works, it just does nothing.
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
    package_logger_name : `str`, optional
        The shared logger to capture messages from.

    Yields
    ------
    handle : `JobHandle`
        Used to log messages and report progress.
    """
    running_job = _current_job.get()
    if (
        enabled
        and running_job is not None
        and running_job.job_id is not None
        and running_job.job_type == job_type
        and running_job.target_id == target_id
    ):
        # The caller is already running as this very job, for example the
        # MCP wrapper around a stacking method that registers its own job.
        # Join it instead of listing the same work twice. The outer owner
        # decides how the job ends.
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
        if enabled:
            try:
                job_id, log_file_path, logger_interface = _create_job_row(
                    job_type=job_type, target_id=target_id, log_file=log_file
                )
                job_logger = log_capture.enter_context(
                    capture_job_logs(
                        job_id=job_id,
                        log_file_path=log_file_path,
                        logger_interface=logger_interface,
                        package_logger_name=package_logger_name,
                    )
                )
                handle = JobHandle(
                    job_id=job_id,
                    job_logger=job_logger,
                    log_file_path=log_file_path,
                    logger_interface=logger_interface,
                    job_type=job_type,
                    target_id=target_id,
                )
            except Exception as registration_error:
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


def _to_plain(value: Any) -> Any:
    """Convert a result into plain, JSON-safe data.

    `ProcessingJob.output_metrics` is stored via `json.dumps` (see
    `LoggerInterface.upsert_job`), which chokes on pydantic models and
    dataclasses -- both of which pipeline results are made of throughout
    this library (`BatchRunSummary`, the various `*QualitySummary`
    models). Recurses through dicts/lists so a pydantic model or
    dataclass nested arbitrarily deep still comes out as plain data.

    Returns
    -------
    plain : `Any`
        `value`, with any pydantic model or dataclass replaced by its
        plain dict form.
    """
    if hasattr(value, "model_dump"):
        return _to_plain(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return value


def background_job(job_type: str, *, grace_period_seconds: float = 5.0) -> Callable:
    """Mark a method as safe to run as a background MCP job.

    This is metadata only: it stamps the given job type and grace period
    onto the function and returns it unchanged. Calling the decorated
    method directly in Python -- as the backend's own processing flow and
    the test suite do -- runs synchronously to completion exactly as
    before. Only `astrometricslib.mcp.reflection`'s dispatch checks for
    this marker and, when present, routes the call through
    `run_as_background_job` instead of calling it directly. See that
    module for why: a slow call blocks the whole MCP connection, not just
    its own request.

    Parameters
    ----------
    job_type : `str`
        What kind of job this is (see `registered_job`).
    grace_period_seconds : `float`, optional
        How long an MCP caller should wait for the work to finish before
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


def run_as_background_job(
    job_type: str,
    target_id: str,
    work_fn: Callable[[JobHandle], Any],
    *,
    grace_period_seconds: float = 5.0,
    snapshot_fn: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run `work_fn` under job tracking, in a background thread.

    Waits briefly for the job row to exist, then a while longer for the
    work itself to finish, so a fast call can still hand back its real
    result directly -- same shape as calling `work_fn` synchronously,
    plus a job id for reference. A slow call instead gets a job id (and
    log file path) to poll with, and critically, the background thread
    keeps running to completion regardless of what the caller does next:
    a caller disconnecting can no longer kill the work partway through,
    which a synchronous call wrapped in a cancellable request could.

    Parameters
    ----------
    job_type : `str`
        What kind of job this is (see `registered_job`).
    target_id : `str`
        Which target the job is working on, or a synthetic label for work
        spanning several (a batch run, for instance).
    work_fn : `Callable`
        The work to run, given the job's `JobHandle` so it can log
        progress or decide its own outcome. Its return value is stashed
        in the job's `output_metrics["result"]` for later retrieval.
    grace_period_seconds : `float`, optional
        How long to wait before giving up and returning a job id instead
        of the real result, by default 5.0.
    snapshot_fn : `Callable`, optional
        Called once right before `work_fn` starts and once right after it
        finishes; both results are stashed under `output_metrics
        ["quality"]["pre"/"post"]`. Meant for a target's persisted
        quality summaries, so a later poll has something concrete to
        compare, not just a status word. Skipped (with a logged note, not
        a failure) if it raises.

    Returns
    -------
    outcome : `dict`
        `{"jobId": ..., "result": ...}` if the work finished within
        `grace_period_seconds`, or `{"status": "running", "jobId": ...,
        "logFilePath": ...}` if it is still going. If `work_fn` fails
        within `grace_period_seconds`, its exception is raised here again,
        and the job record is marked failed as well.
    """
    job_created = threading.Event()
    job_finished = threading.Event()
    job_info: dict[str, Any] = {}
    outcome: dict[str, Any] = {}

    def _snapshot(when: str) -> dict[str, Any] | None:
        if snapshot_fn is None:
            return None
        try:
            return snapshot_fn()
        except Exception as snapshot_error:
            logger.debug("Could not take %s-processing quality snapshot: %s", when, snapshot_error)
            return None

    def _run() -> None:
        try:
            with registered_job(enabled=True, job_type=job_type, target_id=target_id) as job:
                job_info["job_id"] = job.job_id
                job_info["log_file_path"] = job.log_file_path
                job_created.set()

                pre_quality = _snapshot("pre")
                try:
                    result = work_fn(job)
                except BaseException as work_error:
                    outcome["error"] = work_error
                    raise
                outcome["result"] = result
                metrics: dict[str, Any] = {"result": _to_plain(result)}
                if snapshot_fn is not None:
                    metrics["quality"] = _to_plain({"pre": pre_quality, "post": _snapshot("post")})
                # The work may already have decided the job failed (stacking
                # that makes no image); do not turn that into a success.
                job.mark(job.terminal_status or "completed", 100, output_metrics=metrics)
        except Exception:
            # This thread is the job runner, a boundary. `registered_job`
            # already marked the job failed and re-raises by contract, for
            # callers that run it synchronously and want the exception
            # back. Nobody is waiting to catch it here -- `outcome["error"]`
            # above already has it for a caller still waiting, so letting
            # it propagate would only add a second unhandled-thread log.
            logger.debug("Background job '%s' failed.", job_type, exc_info=True)
        finally:
            job_finished.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Just the DB insert -- should be near-instant. A generous cap keeps a
    # genuinely broken logs DB from hanging the caller forever instead of
    # falling through to `registered_job`'s own no-op-handle fallback.
    job_created.wait(timeout=10.0)

    if job_finished.wait(timeout=grace_period_seconds):
        if "error" in outcome:
            raise outcome["error"]
        return {"jobId": job_info.get("job_id"), "result": outcome["result"]}

    return {
        "status": "running",
        "jobId": job_info.get("job_id"),
        "logFilePath": job_info.get("log_file_path"),
        "message": (
            f"Still running after {grace_period_seconds:.0f}s; poll with job_get_status/"
            "job_tail_log using this job id."
        ),
    }
