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
`analyze_target`, `stack_and_solve`, the backend's analysis orchestrator,
and the wayfinding library's transfer task. None of the four closed their
handlers, and only one of them detached from both loggers.
"""

import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

# Statuses that mean the job is over. Once the work sets one of these, the
# context manager stops second-guessing it -- see `registered_job`.
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


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
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        job_id: str | None = None,
        job_logger: logging.Logger | None = None,
        log_file_path: str | None = None,
        logger_interface: object | None = None,
    ):
        self.job_id = job_id
        self.job_logger = job_logger
        self.log_file_path = log_file_path
        self._logger_interface = logger_interface
        self._reached_terminal_status = False

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

    def mark(self, status: str, progress_current: int = 100) -> None:
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
        """
        if status in _TERMINAL_STATUSES:
            self._reached_terminal_status = True

        if not (self._logger_interface and self.job_id):
            return
        try:
            stored_job = self._logger_interface.get_job(self.job_id)
            if stored_job:
                stored_job.status = status
                stored_job.progress_current = progress_current
                stored_job.updated_at = datetime.now().isoformat()
                self._logger_interface.upsert_job(stored_job)
        except Exception as update_error:
            logger.debug("Could not update job '%s' to '%s': %s", self.job_id, status, update_error)


def _build_job_handle(
    *,
    job_type: str,
    target_id: str,
    log_file: str | None,
    package_logger_name: str,
) -> tuple[JobHandle, logging.Logger | None, list[logging.Handler]]:
    """Create the job row and attach its log handlers.

    Returns
    -------
    handle : `JobHandle`
        The handle the caller will use.
    package_logger : `logging.Logger` or `None`
        The shared logger the handlers were attached to, or `None` if
        nothing was attached.
    attached_handlers : `list` [`logging.Handler`]
        The handlers that need taking off again afterwards.
    """
    from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
    from astrometricslib.utilities.config_loader import get_configuration
    from astrometricslib.utilities.pipeline_models import ProcessingJob

    configuration = get_configuration()
    logger_interface = LoggerInterface(configuration.get_logs_db_path())
    job_id = str(uuid.uuid4())

    safe_target = target_id.replace(" ", "_").replace("/", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_directory = configuration.get_logs_path()
    os.makedirs(log_directory, exist_ok=True)
    log_file_path = log_file or str(log_directory / f"{job_type}_{safe_target}_{timestamp}.log")

    job_logger = logging.getLogger(f"job_{job_id}")
    job_logger.propagate = False
    job_logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_file_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    database_handler = DbLogHandler(logger_interface, job_id=job_id)
    attached_handlers = [file_handler, database_handler]
    for handler in attached_handlers:
        job_logger.addHandler(handler)

    # Also listen on the shared package logger. Every module deeper in the
    # work logs through it, so without this the only messages reaching the
    # job's log file would be the handful this function's caller writes by
    # hand -- none of the decisions the work actually made along the way.
    package_logger = logging.getLogger(package_logger_name)
    for handler in attached_handlers:
        package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)

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
        )
    )

    handle = JobHandle(
        job_id=job_id,
        job_logger=job_logger,
        log_file_path=log_file_path,
        logger_interface=logger_interface,
    )
    return handle, package_logger, attached_handlers


def _detach_handlers(
    job_logger: logging.Logger | None,
    package_logger: logging.Logger | None,
    handlers: list[logging.Handler],
) -> None:
    """Take the job's log handlers back off and close their files.

    Both steps matter. Removing a handler stops it receiving new
    messages, but the file it opened stays open until it is closed, and
    Python never throws the logger away, so an unclosed handler is a
    file handle leaked for the life of the process.

    Parameters
    ----------
    job_logger : `logging.Logger` or `None`
        This job's own logger.
    package_logger : `logging.Logger` or `None`
        The shared logger the handlers were also attached to.
    handlers : `list` [`logging.Handler`]
        The handlers to remove and close.
    """
    for handler in handlers:
        for attached_logger in (job_logger, package_logger):
            if attached_logger is not None:
                attached_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception as close_error:
            logger.debug("Could not close a job log handler: %s", close_error)


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
) -> Iterator[JobHandle]:
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
        The shared logger to capture messages from. Defaults to
        "astrometricslib"; the wayfinding library passes its own.

    Yields
    ------
    handle : `JobHandle`
        Used to log messages and report progress.
    """
    handle = JobHandle()
    package_logger: logging.Logger | None = None
    attached_handlers: list[logging.Handler] = []

    if enabled:
        try:
            handle, package_logger, attached_handlers = _build_job_handle(
                job_type=job_type,
                target_id=target_id,
                log_file=log_file,
                package_logger_name=package_logger_name,
            )
        except Exception as registration_error:
            # A job we cannot record is still a job worth doing.
            logger.warning("Could not register %s job: %s", job_type, registration_error)
            handle = JobHandle()

    try:
        yield handle
    except Exception:
        handle.mark("failed", 0)
        if failed_message:
            handle.error(failed_message)
        raise
    else:
        if not handle._reached_terminal_status:
            handle.mark("completed", 100)
        if completed_message:
            handle.info(completed_message)
    finally:
        _detach_handlers(handle.job_logger, package_logger, attached_handlers)
