"""Purpose: Read the job history, a job's log and warnings, and data lineage.

Description: A "job" is any piece of work slow enough to track: stacking a
target, running an analysis, downloading frames. The app records each job in
the logs database, with its status, its progress and the log lines it wrote,
and records for each target which pipeline runs produced its data (the
lineage). The warnings a job detected are the failed checks it stored in the
quality database.

These functions are the owners of the `Jobs` methods. They open both
databases read-only and never create or change a table, so a caller can look
at the history without any chance of altering it. They return every match,
with whole messages and log lines; a client that needs a short reply cuts it
itself.
"""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from astrometricslib._internal.drivers.provenance_store import ProvenanceStore
from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
from astrometricslib.foundation.arguments import check_include, check_limit
from astrometricslib.foundation.errors import NotFoundError
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.runner import PARAMETER_DEFAULTS_KEY
from astrometricslib.foundation.jobs.store import JobStore
from astrometricslib.models.job_queries import (
    JobLog,
    JobLogLine,
    JobSummary,
    JobWarning,
    JobWarnings,
    LineageRun,
)
from astrometricslib.models.page import Page
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.measurement import QualityMeasurement

__all__ = [
    "ACTIVE_STATUSES",
    "JOB_INCLUDE_PARTS",
    "STALE_AFTER_MINUTES",
    "get_job",
    "get_job_lineage",
    "get_job_log",
    "get_job_warnings",
    "query_jobs",
]

ACTIVE_STATUSES = ("started", "running")
"""Job statuses that mean the job has not finished."""

STALE_AFTER_MINUTES = 60
"""Minutes without an update after which an unfinished job looks dead.

A running job writes to its row whenever its progress changes, and a single
stage (one Siril stack takes about 75 seconds, a long one a few minutes)
finishes well inside this time. A row that has not changed for an hour was
left behind by a process that was stopped or crashed.
"""

JOB_INCLUDE_PARTS = ("metrics",)
"""The heavy parts a job list can add: ``"metrics"`` is each job's stored
input and output numbers and its log file path."""


def _logs_database(config: Any) -> Path:
    """Give the path of the logs database.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    path : `pathlib.Path`
        The logs database, which may not exist yet.
    """
    return Path(str(config.get_logs_db_path()))


def _minutes_since(timestamp: str | None) -> float | None:
    """Give the minutes from a stored time to now.

    The app writes job times as local time without a time zone.

    Parameters
    ----------
    timestamp : `str` or `None`
        An ISO-format time, or `None`.

    Returns
    -------
    minutes : `float` or `None`
        Minutes since that time, or `None` if it is missing or unreadable.
    """
    if not timestamp:
        return None
    try:
        then = datetime.fromisoformat(str(timestamp))
        now = datetime.now(then.tzinfo)
        return (now - then).total_seconds() / 60.0
    except ValueError:
        return None


def _parameter_defaults(job: ProcessingJob | None) -> list[str]:
    """List the parameters that used their defaults during a job.

    Parameters
    ----------
    job : `ProcessingJob` or `None`
        The stored job, or `None` when the logs database does not know it.

    Returns
    -------
    notices : `list` [`str`]
        One line for each parameter with no tuned or equipment value. Empty
        when none did or the job did not record any.
    """
    if job is None:
        return []
    stored = (job.output_metrics or {}).get(PARAMETER_DEFAULTS_KEY, [])
    return [str(notice) for notice in stored]


def _summarize(job: ProcessingJob, with_metrics: bool) -> JobSummary:
    """Describe one job.

    Parameters
    ----------
    job : `ProcessingJob`
        The job record.
    with_metrics : `bool`
        Also give the stored input and output numbers and the log file path.

    Returns
    -------
    summary : `JobSummary`
        Id, type, target, status, progress, message and times. An unfinished
        job that has not been updated for `STALE_AFTER_MINUTES` has
        ``looks_stale`` set, ``is_active`` false and ``idle_minutes`` filled
        in: its process most likely died and left the row at ``started`` or
        ``running``.
    """
    unfinished = job.status in ACTIVE_STATUSES
    idle_minutes = _minutes_since(job.updated_at or job.created_at) if unfinished else None
    looks_stale = idle_minutes is not None and idle_minutes > STALE_AFTER_MINUTES
    return JobSummary(
        id=job.id,
        job_type=job.job_type,
        target_id=job.target_id,
        status=job.status,
        progress_done=job.progress_current,
        progress_total=job.progress_total,
        message="" if job.message is None else str(job.message),
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        is_active=unfinished and not looks_stale,
        looks_stale=looks_stale,
        idle_minutes=None if idle_minutes is None else round(idle_minutes),
        input_metrics=dict(job.input_metrics or {}) if with_metrics else None,
        output_metrics=dict(job.output_metrics or {}) if with_metrics else None,
        log_file_path=job.log_file_path if with_metrics else None,
    )


def _stored_job(config: Any, job_id: str) -> ProcessingJob | None:
    """Read one job's row, read-only.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    job_id : `str`
        The job's id.

    Returns
    -------
    job : `ProcessingJob` or `None`
        The row, or `None` when there is no such job or no logs database.
    """
    database = _logs_database(config)
    if not database.is_file():
        return None
    return JobStore(str(database), read_only=True).get_job(job_id)


def get_job(config: Any, job_id: str, *, required: bool = False) -> JobSummary | None:
    """Read one job, with its stored input and output numbers.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the logs database.
    job_id : `str`
        The job's id.
    required : `bool`, optional
        Raise instead of returning `None` when there is no such job.

    Returns
    -------
    job : `JobSummary` or `None`
        The whole record, or `None` when there is no such job (or no logs
        database yet) and ``required`` is `False`.

    Raises
    ------
    NotFoundError
        If there is no such job and ``required`` is `True`.
    """
    job = _stored_job(config, job_id)
    if job is None:
        if required:
            raise NotFoundError(f"No job with id {job_id!r}.", details={"job_id": job_id})
        return None
    return _summarize(job, with_metrics=True)


def query_jobs(
    config: Any,
    *,
    target_id: str | None = None,
    job_type: str | None = None,
    status: str | None = None,
    active: bool = False,
    until: str | None = None,
    include: Iterable[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Page[JobSummary]:
    """List the jobs that match some filters, newest first.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    target_id : `str`, optional
        Only jobs recorded for this target id.
    job_type : `str`, optional
        Only jobs of this type, such as ``"stacking"``.
    status : `str`, optional
        Only jobs with this status, such as ``"failed"``.
    active : `bool`, optional
        Only jobs that are not finished. Defaults to `False`.
    until : `str`, optional
        Only jobs created strictly before this time, written as the job list
        writes ``created_at``.
    include : `Iterable` [`str`] or `None`, optional
        ``["metrics"]`` adds each job's stored numbers and log file path.
    limit : `int` or `None`, optional
        Most jobs to return, at least 1. `None` returns every match.
    offset : `int`, optional
        How many of the newest matching jobs to skip. Defaults to 0.

    Returns
    -------
    jobs : `Page` [`JobSummary`]
        The jobs. Empty when there is no logs database yet.

    Notes
    -----
    An unknown include part, or a ``limit`` or ``offset`` out of range,
    raises `InvalidArgumentError`.
    """
    parts = check_include(include, JOB_INCLUDE_PARTS)
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    database = _logs_database(config)
    if not database.is_file():
        return Page[JobSummary](items=(), total=0)
    store = JobStore(str(database), read_only=True)
    # SQLite reads a limit of -1 as no limit.
    sql_limit = -1 if limit is None else limit
    rows = store.query_jobs(target_id, job_type, status, active, sql_limit, offset, until)
    total = store.count_jobs(target_id, job_type, status, active, until)
    end = offset + len(rows)
    return Page[JobSummary](
        items=tuple(_summarize(job, "metrics" in parts) for job in rows),
        total=total,
        next_offset=end if end < total else None,
    )


def get_job_log(config: Any, job_id: str, lines: int = 50) -> JobLog:
    """Read the last lines of a job's log.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    job_id : `str`
        The job's id.
    lines : `int`, optional
        How many of the last lines to read, at least 1. Defaults to 50.

    Returns
    -------
    log : `JobLog`
        The lines, oldest first, whole, and how many the job wrote.

    Raises
    ------
    NotFoundError
        If there is no such job, or no logs database yet.

    Notes
    -----
    A ``lines`` below 1 raises `InvalidArgumentError`.
    """
    check_limit(lines, 1, None, name="lines", allow_none=False)
    if _stored_job(config, job_id) is None:
        raise NotFoundError(f"No job with id {job_id!r}.", details={"job_id": job_id})
    store = JobStore(str(_logs_database(config)), read_only=True)
    entries, total = store.get_recent_log_entries_for_job(job_id, lines)
    return JobLog(
        lines=[
            JobLogLine(time=entry["created_at"], level=entry["level"], text=str(entry["message"] or ""))
            for entry in entries
        ],
        total=total,
    )


def _warning_from_row(row: QualityMeasurement) -> JobWarning:
    """Describe one failed check as a warning.

    Parameters
    ----------
    row : `QualityMeasurement`
        A stored measurement whose check outcome is ``failed``.

    Returns
    -------
    warning : `JobWarning`
        The warning, with the value, the limit and the register entry.
    """
    limit = row.check_limit
    return JobWarning(
        pipeline=row.metric.split(".", 1)[0],
        metric=row.metric,
        subject=row.subject.subject_id,
        subject_level=row.subject.level.value,
        target_id=row.subject.target_id,
        reason=row.reason or "",
        outcome=GateStatus.FAILED.value,
        value=row.value,
        limit=None if limit is None else limit.value,
        unit=None if limit is None else limit.unit,
        limit_tier=None if limit is None else limit.tier.value,
        limit_derivation=None if limit is None else limit.derivation,
        register_entry=None if limit is None else limit.register_entry,
        action_taken=row.action_taken,
        measured_at=row.measured_at.isoformat(),
        row_id=row.row_id,
    )


def get_job_warnings(config: Any, job_id: str) -> JobWarnings:
    """List the warnings one job detected.

    A job's measurements are found by its id, so the rows of a job that the
    logs database no longer knows are still listed.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give both databases.
    job_id : `str`
        The job's id.

    Returns
    -------
    warnings : `JobWarnings`
        Every warning, oldest first, the number of measurements the job
        stored, the number of checks that could not run and the parameters
        that used their defaults.

    Raises
    ------
    NotFoundError
        If neither the logs database nor the measurement table knows the id.
    """
    job = _stored_job(config, job_id)
    rows = QualityStore(quality_database_path(config)).query(job_id=job_id, latest_only=False)
    if job is None and not rows:
        raise NotFoundError(f"No job with id {job_id!r}.", details={"job_id": job_id})
    return JobWarnings(
        warnings=[_warning_from_row(row) for row in rows if row.check_outcome == GateStatus.FAILED],
        measurement_count=len(rows),
        checks_not_run_count=sum(1 for row in rows if row.check_outcome == GateStatus.NOT_CHECKED),
        parameter_defaults=_parameter_defaults(job),
    )


def get_job_lineage(config: Any, target_id: str) -> Page[LineageRun]:
    """List the pipeline runs that produced a target's data, newest first.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    target_id : `str`
        The target's id.

    Returns
    -------
    runs : `Page` [`LineageRun`]
        Every run. Empty when there is no logs database yet.
    """
    database = _logs_database(config)
    if not database.is_file():
        return Page[LineageRun](items=(), total=0)
    activities = ProvenanceStore(str(database), read_only=True).get_lineage(target_id)
    runs = [
        LineageRun(
            id=activity.id,
            name=activity.name,
            start_time=activity.start_time.isoformat() if activity.start_time else None,
            end_time=activity.end_time.isoformat() if activity.end_time else None,
            comment=activity.comment or "",
            informed_by=list(activity.informant),
            inputs_used=len(activity.used),
        )
        for activity in activities
    ]
    return Page[LineageRun].of(runs)
