"""Purpose: One way to set up logging, and one context that tags every message.

Description: Python's `logging` module lets any code write a message, and lets
a program decide where the messages go. The rules for this repository are:

* Library and service modules only write messages, through
  ``logging.getLogger(__name__)``. They never change where messages go.
* Each program (the backend, an MCP server, a script) calls
  `configure_logging` once at startup.
* A *log context* tags every message with the call or job that wrote it: a
  ``request_id``, a ``job_id``, a ``target_id``, and so on. A context
  variable is a value that follows the current thread or async task, so
  code deep in a pipeline does not have to pass the ids along.
* A job's own log file and database rows are filled by the *job log router*,
  which sends each message to the jobs named in its context. No handler is
  attached or removed while a job runs, so none can be left behind.
"""

import json
import logging
import logging.handlers
import sys
import threading
import uuid
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, TextIO

#: The context fields that are copied onto every log record.
CONTEXT_FIELDS: tuple[str, ...] = ("request_id", "job_id", "target_id", "session_id", "method")

#: Third-party loggers that are noisy at their default levels, and the level
#: `configure_logging` gives each.
QUIET_LOGGERS: dict[str, int] = {
    "httpx": logging.ERROR,
    "httpcore": logging.ERROR,
    "uvicorn.access": logging.ERROR,
    "uvicorn.error": logging.WARNING,
    "matplotlib": logging.WARNING,
    "PIL": logging.WARNING,
    "asyncio": logging.WARNING,
}

_MAX_LOG_BYTES = 10 * 1024 * 1024
_LOG_FILES_KEPT = 5

_context: ContextVar[Mapping[str, Any]] = ContextVar("log_context", default=MappingProxyType({}))
_job_ids: ContextVar[tuple[str, ...]] = ContextVar("log_job_ids", default=())


def new_request_id() -> str:
    """Make an id for one call.

    Returns
    -------
    request_id : `str`
        A short random id. It appears on every log line of the call and in the
        error reply, so a person can quote it and a developer can find the
        lines.
    """
    return uuid.uuid4().hex[:12]


def get_log_context() -> dict[str, Any]:
    """Give the fields that tag the calling work's log messages.

    Returns
    -------
    context : `dict` [`str`, `Any`]
        A copy of the current context. Empty outside any `log_context`.
    """
    return dict(_context.get())


@contextmanager
def log_context(**fields: Any) -> Generator[None]:
    """Tag every log message written inside the block.

    Contexts nest. A field set in an inner block hides the same field of an
    outer block until the inner block ends. A field set to `None` is ignored.

    A ``job_id`` field also adds the id to the list of jobs the work runs
    inside. The job log router uses that list, so a job that runs inside
    another job writes to both logs.

    Parameters
    ----------
    **fields : `Any`
        Context fields, such as ``request_id``, ``job_id``, ``target_id``,
        ``session_id`` and ``method``.

    Yields
    ------
    None
        The block runs with the extra context.
    """
    current = _context.get()
    updated = {**current, **{key: value for key, value in fields.items() if value is not None}}
    token = _context.set(updated)
    job_token = None
    if fields.get("job_id") is not None:
        job_token = _job_ids.set((*_job_ids.get(), str(fields["job_id"])))
    try:
        yield
    finally:
        _context.reset(token)
        if job_token is not None:
            _job_ids.reset(job_token)


def active_job_ids() -> tuple[str, ...]:
    """Give the ids of the jobs the calling work runs inside.

    Returns
    -------
    job_ids : `tuple` [`str`]
        The job ids, outermost first. Empty when the work is not part of a
        job.
    """
    return _job_ids.get()


class ContextFilter(logging.Filter):
    """Copy the log context onto each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add the context fields to a record.

        Parameters
        ----------
        record : `logging.LogRecord`
            The record being logged.

        Returns
        -------
        keep : `bool`
            Always `True`. The filter only adds fields.
        """
        context = _context.get()
        for field in CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, context.get(field))
        if not hasattr(record, "job_ids"):
            record.job_ids = _job_ids.get()
        return True


class JsonLinesFormatter(logging.Formatter):
    """Write each record as one JSON object on one line.

    A line has the keys ``time``, ``level``, ``logger`` and ``message``, then
    the context fields that are set, and ``exception`` when the record carries
    a traceback.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format one record.

        Parameters
        ----------
        record : `logging.LogRecord`
            The record to format.

        Returns
        -------
        line : `str`
            The JSON text of the record, without a newline.
        """
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class ReadableFormatter(logging.Formatter):
    """Write a record for a person reading a terminal.

    The line is ``time - logger - LEVEL - message``, followed by the context
    fields that are set.
    """

    def __init__(self) -> None:
        super().__init__("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """Format one record.

        Parameters
        ----------
        record : `logging.LogRecord`
            The record to format.

        Returns
        -------
        line : `str`
            The text of the record, with a traceback on later lines if the
            record has one.
        """
        text = super().format(record)
        tags = [
            f"{field}={getattr(record, field)}" for field in CONTEXT_FIELDS if getattr(record, field, None)
        ]
        if tags:
            first, separator, rest = text.partition("\n")
            text = f"{first} [{' '.join(tags)}]{separator}{rest}"
        return text


class JobLogRouter(logging.Handler):
    """Send each record to the log sinks of the jobs it belongs to.

    A sink is a list of handlers, usually a file handler and a database
    handler, registered for one job id. The router reads the job ids from the
    record's context, so it needs no per-job attach and detach on a logger.

    Work that has lost its context, such as a worker thread that was started
    without copying it, cannot be told apart. A record from such work is sent
    to every registered job, but only if its logger belongs to one of the
    packages the job registered for (`package_logger_names`).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self._sinks: dict[str, list[logging.Handler]] = {}
        self._packages: dict[str, tuple[str, ...]] = {}
        self._sinks_lock = threading.Lock()
        self.addFilter(ContextFilter())

    def register(
        self,
        job_id: str,
        handlers: Iterable[logging.Handler],
        package_logger_names: Iterable[str] = ("astrometricslib",),
    ) -> None:
        """Start sending a job's records to some handlers.

        Parameters
        ----------
        job_id : `str`
            The job's id.
        handlers : `~collections.abc.Iterable` [`logging.Handler`]
            Where to write the job's records.
        package_logger_names : `~collections.abc.Iterable` [`str`], optional
            Loggers whose records, when they carry no job context, are also
            written to this job.
        """
        with self._sinks_lock:
            self._sinks[job_id] = list(handlers)
            self._packages[job_id] = tuple(package_logger_names)

    def unregister(self, job_id: str) -> list[logging.Handler]:
        """Stop sending records to a job's handlers.

        Parameters
        ----------
        job_id : `str`
            The job's id.

        Returns
        -------
        handlers : `list` [`logging.Handler`]
            The handlers that were registered, so the caller can close them.
        """
        with self._sinks_lock:
            self._packages.pop(job_id, None)
            return self._sinks.pop(job_id, [])

    def emit(self, record: logging.LogRecord) -> None:
        """Pass a record to the handlers of each job it belongs to.

        Parameters
        ----------
        record : `logging.LogRecord`
            The record to route.
        """
        job_ids = getattr(record, "job_ids", ())
        with self._sinks_lock:
            if job_ids:
                sinks = [handler for job_id in job_ids for handler in self._sinks.get(job_id, ())]
            else:
                sinks = [
                    handler
                    for job_id, handlers in self._sinks.items()
                    if record.name.split(".")[0] in self._packages[job_id]
                    for handler in handlers
                ]
        for handler in sinks:
            if record.levelno >= handler.level:
                handler.handle(record)


_router: JobLogRouter | None = None
_router_lock = threading.Lock()


def get_job_log_router() -> JobLogRouter:
    """Give the one job log router, creating and installing it if needed.

    The router is attached to the root logger if it is not attached already.
    Programs that never call `configure_logging`, such as a test, therefore
    still get job logs.

    Returns
    -------
    router : `JobLogRouter`
        The shared router.
    """
    global _router
    with _router_lock:
        if _router is None:
            _router = JobLogRouter()
        root = logging.getLogger()
        if _router not in root.handlers:
            root.addHandler(_router)
        return _router


def configure_logging(
    program: str,
    *,
    level: int = logging.INFO,
    log_dir: str | Path | None = None,
    console: bool = True,
    console_stream: TextIO | None = None,
) -> Path | None:
    """Set up logging for a whole program. Call it once, at startup.

    The function installs:

    * A rotating log file, ``<log_dir>/<program>.jsonl``, in JSON Lines format.
    * A readable console handler. MCP servers carry their protocol on stdout,
      so the console handler writes to stderr unless told otherwise.
    * The context filter, so every record carries its ``request_id``,
      ``job_id`` and the other context fields.
    * The job log router.
    * Quieter levels for noisy third-party loggers.

    Calling it again replaces the handlers it installed before.

    Parameters
    ----------
    program : `str`
        Name of the program, such as ``"backend"``. It names the log file.
    level : `int`, optional
        The level of the root logger. Defaults to ``INFO``.
    log_dir : `str` or `pathlib.Path`, optional
        Folder for the log file. If `None`, the application configuration's
        logs folder is used. Pass an empty string to write no file.
    console : `bool`, optional
        Whether to write a readable copy to the console.
    console_stream : `typing.TextIO`, optional
        Where the console handler writes. Defaults to stderr.

    Returns
    -------
    log_file : `pathlib.Path` or `None`
        The log file, or `None` if no file is written.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_astrometrics_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    context_filter = ContextFilter()

    log_file: Path | None = None
    if log_dir != "":
        if log_dir is None:
            from astrometricslib.foundation.config import get_configuration

            log_dir = get_configuration().get_logs_path()
        folder = Path(log_dir)
        folder.mkdir(parents=True, exist_ok=True)
        log_file = folder / f"{program}.jsonl"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=_MAX_LOG_BYTES, backupCount=_LOG_FILES_KEPT, encoding="utf-8"
        )
        file_handler.setFormatter(JsonLinesFormatter())
        file_handler.addFilter(context_filter)
        file_handler._astrometrics_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(console_stream or sys.stderr)
        console_handler.setFormatter(ReadableFormatter())
        console_handler.addFilter(context_filter)
        console_handler._astrometrics_handler = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)

    get_job_log_router()
    for name, quiet_level in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(quiet_level)
    return log_file
