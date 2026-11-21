"""Collect the warnings a photometry run logs, so the job can list them.

A photometry run calls many modules: the plate solver, the catalog drivers,
the camera profiles and its own steps. Each of them reports a problem by
logging a warning (a catalog server that did not answer, a camera profile
with no gain for the frame's settings, a period search that failed). A log
line is easy to miss, and `Jobs.query(detail="warnings")` only lists stored
quality rows. This module keeps those warnings so the run can store them in
one notice row (see `record_quality.logged_warnings_notice_measurement`).

`collect_logged_warnings` registers a small handler with the job log router
(`astrometricslib.foundation.logging.JobLogRouter`) for the length of the
run. No handler is attached to a shared logger and no logger level changes,
so nothing is left behind when the run ends. The handler groups the records
by logger and message pattern, so a warning repeated for every frame is kept
once, with a count.

A warning that already has a notice row of its own is logged with
`SESSION_NOTICE_LOG_EXTRA`. The handler skips it, so the job does not list
the same problem twice.

Warnings logged inside the worker processes that measure frames never reach
this process, so they are not collected; those workers report their
problems through their results instead.
"""

import logging
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from astrometricslib.foundation.jobs.runner import current_job_id
from astrometricslib.foundation.logging import get_job_log_router

__all__ = [
    "NOTICE_RECORD_ATTRIBUTE",
    "SESSION_NOTICE_LOG_EXTRA",
    "LoggedWarning",
    "LoggedWarningCollector",
    "SessionWithoutComparisonStars",
    "collect_logged_warnings",
]

NOTICE_RECORD_ATTRIBUTE = "photometry_notice"
"""Set on a log record whose warning is stored as a notice row of its own."""

SESSION_NOTICE_LOG_EXTRA = {NOTICE_RECORD_ATTRIBUTE: True}
"""The ``extra`` to log such a warning with, so the collector skips it."""

_MAXIMUM_KEPT_WARNINGS = 50
"""The most distinct warnings kept. Later new ones are only counted."""


@dataclass(frozen=True)
class LoggedWarning:
    """One warning the run logged, with how often it was logged.

    Attributes
    ----------
    logger_name : `str`
        The logger that wrote it, which names the module.
    message : `str`
        The text of its first occurrence.
    count : `int`
        How many times a warning with the same logger and message pattern
        was logged.
    """

    logger_name: str
    message: str
    count: int


class LoggedWarningCollector(logging.Handler):
    """A log handler that keeps the warnings it is given, grouped.

    Records below ``WARNING`` are ignored. Records are grouped by logger
    name and message pattern (the format string before its arguments are
    filled in), so "frame 1 failed" and "frame 2 failed" count as one
    warning logged twice.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._lock_for_counts = threading.Lock()
        self._first_text: dict[tuple[str, str], str] = {}
        self._counts: dict[tuple[str, str], int] = {}
        self.dropped_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Keep one warning record.

        Parameters
        ----------
        record : `logging.LogRecord`
            The record. It is skipped when it carries
            `NOTICE_RECORD_ATTRIBUTE`.
        """
        if getattr(record, NOTICE_RECORD_ATTRIBUTE, False):
            return
        key = (record.name, str(record.msg))
        try:
            text = record.getMessage()
        except TypeError, ValueError:
            # A record whose arguments do not fit its pattern is kept as the
            # pattern itself, so the warning is not lost.
            text = str(record.msg)
        with self._lock_for_counts:
            if key in self._counts:
                self._counts[key] += 1
            elif len(self._counts) < _MAXIMUM_KEPT_WARNINGS:
                self._counts[key] = 1
                self._first_text[key] = text
            else:
                self.dropped_count += 1

    def warnings(self) -> list[LoggedWarning]:
        """Give the warnings kept so far, in the order they were first seen.

        Returns
        -------
        warnings : `list` [`LoggedWarning`]
            One entry for each logger and message pattern.
        """
        with self._lock_for_counts:
            return [
                LoggedWarning(logger_name=key[0], message=self._first_text[key], count=count)
                for key, count in self._counts.items()
            ]


@contextmanager
def collect_logged_warnings() -> Generator[LoggedWarningCollector]:
    """Collect the warnings logged while the enclosed work runs.

    The collector is registered with the job log router under the running
    job's id, so it receives the job's records. Outside any job a private
    id is used; the router then passes it the library's records that carry
    no job (see `JobLogRouter.emit`).

    Yields
    ------
    collector : `LoggedWarningCollector`
        Read its `warnings` after the work is done.
    """
    collector = LoggedWarningCollector()
    router = get_job_log_router()
    sink_key = router.register(current_job_id() or f"photometry-run-{uuid.uuid4().hex}", [collector])
    try:
        yield collector
    finally:
        router.unregister(sink_key)


@dataclass(frozen=True)
class SessionWithoutComparisonStars:
    """A session whose light curves could not be divided by comparison stars.

    Its light curves hold raw flux. Raw flux follows the sky's transparency
    and the airmass, so it cannot be compared with another session's
    normalized flux, and a change in it is no sign of a variable star.

    Attributes
    ----------
    session_id : `str`
        The session.
    reason : `str`
        Why no comparison set could be built (see
        `comparison_ensemble.describe_missing_comparison_stars`).
    excluded_from_merge : `bool`
        `True` when the session's light curves were left out of the
        merged light curves because another session was normalized.
    dropped_candidate_count : `int`
        How many of the session's variable-star candidates were dropped,
        because they were found in raw flux.
    """

    session_id: str
    reason: str
    excluded_from_merge: bool
    dropped_candidate_count: int
