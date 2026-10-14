"""Purpose: Keep the job list and each job's log lines in the logs database.

Description: `JobStore` reads and writes two tables in the logs database
(``astrometrics_log.db``):

* ``processing_jobs``: one `ProcessingJob` row per background job, with
  its status, progress, and results.
* ``log_entries``: the log lines a job wrote, so the app can show them.

`DbLogHandler` is a `logging.Handler` that writes each record it receives
into ``log_entries``. The job runner (`astrometricslib.foundation.jobs.runner`)
registers one for each job with the job log router.
"""

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from astrometricslib.foundation.jobs.models import ProcessingJob

logger = logging.getLogger(__name__)


class JobStore:
    """Read and write job records and job log lines in the logs database.

    Attributes
    ----------
    db_path : `str`
        Filesystem path to the SQLite database backing this
        repository.
    """

    def __init__(self, db_path: str, read_only: bool = False) -> None:
        """Initialize the repository and ensure its tables exist.

        Parameters
        ----------
        db_path : `str`
            Path of the logs database file.
        read_only : `bool`, optional
            Open the database read-only and do not create tables. Use it
            for code that only looks at the records, so it can never
            change the database. The file must already exist. Defaults to
            `False`.
        """
        self.db_path = db_path
        self.read_only = read_only
        if not read_only:
            self._init_db()

    def _connect(self, timeout: float = 30.0) -> sqlite3.Connection:
        """Open a connection to astrometrics_log.db with WAL mode.

        Mirrors `connect_db` in `astrometricslib.foundation.storage`: WAL lets
        readers and writers proceed concurrently, and the busy timeout makes a
        concurrent writer retry briefly instead of immediately
        raising "database is locked" once multiple processes (not
        just threads within one process) write to this database at
        the same time.

        Returns
        -------
        connection : `sqlite3.Connection`
            Open connection with WAL mode, normal synchronous mode,
            and a 30-second busy timeout configured.
        """
        if self.read_only:
            conn = sqlite3.connect(
                f"{Path(self.db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=timeout
            )
            conn.execute("PRAGMA busy_timeout=30000;")
            return conn
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_db(self) -> None:
        """Create the job and log line tables if they are missing.

        Raises
        ------
        sqlite3.Error
            Re-raised after logging if the database cannot be set up.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processing_jobs (
                    id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_current INTEGER DEFAULT 0,
                    progress_total INTEGER DEFAULT 0,
                    message TEXT,
                    log_file_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    input_metrics TEXT,
                    output_metrics TEXT,
                    owner_pid INTEGER,
                    owner_started_at TEXT
                )
            """)

            # Migration: ensure input_metrics and output_metrics columns
            # exist on existing databases
            cursor.execute("PRAGMA table_info(processing_jobs)")
            cols = {row[1] for row in cursor.fetchall()}
            if "input_metrics" not in cols:
                cursor.execute("ALTER TABLE processing_jobs ADD COLUMN input_metrics TEXT")
            if "output_metrics" not in cols:
                cursor.execute("ALTER TABLE processing_jobs ADD COLUMN output_metrics TEXT")
            if "owner_pid" not in cols:
                cursor.execute("ALTER TABLE processing_jobs ADD COLUMN owner_pid INTEGER")
            if "owner_started_at" not in cols:
                cursor.execute("ALTER TABLE processing_jobs ADD COLUMN owner_started_at TEXT")

            # Per-component / per-job log recording
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS log_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    component TEXT NOT NULL,
                    logger_name TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_log_entries_job_id ON log_entries(job_id, id)")

            conn.commit()
            conn.close()
        except sqlite3.Error:
            logger.exception("Error initializing job database at %s", self.db_path)
            raise

    def upsert_job(self, job: ProcessingJob) -> None:
        """Insert or update a job record.

        Parameters
        ----------
        job : `ProcessingJob`
            Job record to insert (if `job.id` is new) or update (if
            `job.id` already exists in the processing_jobs table).

        Raises
        ------
        sqlite3.Error
            Re-raised after logging if the underlying SQLite
            operation fails.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()

            # Check if exists
            cursor.execute("SELECT id FROM processing_jobs WHERE id = ?", (job.id,))
            exists = cursor.fetchone()

            now = datetime.now().isoformat()

            input_metrics_json = json.dumps(job.input_metrics) if job.input_metrics else None
            output_metrics_json = json.dumps(job.output_metrics) if job.output_metrics else None

            if exists:
                cursor.execute(
                    """
                    UPDATE processing_jobs SET
                        status = ?,
                        progress_current = ?,
                        progress_total = ?,
                        message = ?,
                        log_file_path = ?,
                        updated_at = ?,
                        completed_at = ?,
                        input_metrics = ?,
                        output_metrics = ?
                    WHERE id = ?
                """,
                    (
                        job.status,
                        job.progress_current,
                        job.progress_total,
                        job.message,
                        job.log_file_path,
                        now,
                        job.completed_at,
                        input_metrics_json,
                        output_metrics_json,
                        job.id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO processing_jobs (
                        id, target_id, job_type, status, progress_current,
                        progress_total, message, log_file_path, created_at, updated_at,
                        input_metrics, output_metrics, owner_pid, owner_started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        job.id,
                        job.target_id,
                        job.job_type,
                        job.status,
                        job.progress_current,
                        job.progress_total,
                        job.message,
                        job.log_file_path,
                        job.created_at or now,
                        now,
                        input_metrics_json,
                        output_metrics_json,
                        job.owner_pid,
                        job.owner_started_at,
                    ),
                )

            conn.commit()
            conn.close()
        except sqlite3.Error:
            logger.exception("Error upserting job %s", job.id)
            raise

    def get_job(self, job_id: str) -> ProcessingJob | None:
        """Retrieve a specific job by ID.

        Parameters
        ----------
        job_id : `str`
            Identifier of the job to retrieve.

        Returns
        -------
        job : `ProcessingJob` or `None`
            The matching job record, or `None` if no such job exists
            or the query fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM processing_jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            conn.close()

            if row:
                return self._row_to_job(row)
            return None
        except sqlite3.Error:
            logger.exception("Error retrieving job %s", job_id)
            return None

    def get_jobs_by_target(
        self, target_id: str, job_type: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[ProcessingJob]:
        """Retrieve all jobs for a specific target, sorted by newest first.

        Parameters
        ----------
        target_id : `str`
            Identifier of the target to filter jobs by.
        job_type : `str`, optional
            If given, restrict results to jobs of this type. Default
            `None` (no filter).
        status : `str`, optional
            If given, restrict results to jobs with this status.
            Default `None` (no filter).
        limit : `int`, optional
            Maximum number of jobs to return, default 50.

        Returns
        -------
        jobs : `list` of `ProcessingJob`
            Matching jobs ordered by creation time, newest first. An
            empty list is returned if the query fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM processing_jobs WHERE target_id = ?"
            params = [target_id]

            if job_type:
                query += " AND job_type = ?"
                params.append(job_type)

            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            conn.close()

            return [self._row_to_job(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Error retrieving jobs for target %s", target_id)
            return []

    def delete_job(self, job_id: str) -> bool:
        """Delete a job entry from the database.

        Parameters
        ----------
        job_id : `str`
            Identifier of the job to delete.

        Returns
        -------
        was_deleted : `bool`
            `True` if a row was deleted, `False` if no matching row
            was found or the operation failed.

        Notes
        -----
        Implements persistent job history tracking.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM processing_jobs WHERE id = ?", (job_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
            conn.close()
            return deleted
        except sqlite3.Error:
            logger.exception("Error deleting job %s", job_id)
            return False

    def get_jobs_older_than(self, days: int) -> list[ProcessingJob]:
        """Retrieve all jobs older than the specified number of days.

        Parameters
        ----------
        days : `int`
            Age threshold in days; jobs created before "now - days"
            are returned.

        Returns
        -------
        jobs : `list` of `ProcessingJob`
            Matching jobs. An empty list is returned if the query
            fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Using SQLite's datetime functions to compare
            cursor.execute(
                """
                SELECT * FROM processing_jobs
                WHERE created_at < datetime('now', ?)
            """,
                (f"-{days} days",),
            )

            rows = cursor.fetchall()
            conn.close()

            return [self._row_to_job(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Error retrieving old jobs")
            return []

    def get_recent_jobs(self, limit: int = 50, job_type: str | None = None) -> list[ProcessingJob]:
        """Retrieve the most recent jobs across all targets.

        Parameters
        ----------
        limit : `int`, optional
            Maximum number of jobs to return, default 50.
        job_type : `str`, optional
            If given, restrict results to jobs of this type. Default
            `None` (no filter).

        Returns
        -------
        jobs : `list` of `ProcessingJob`
            Matching jobs ordered by creation time, newest first. An
            empty list is returned if the query fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM processing_jobs"
            params = []

            if job_type:
                query += " WHERE job_type = ?"
                params.append(job_type)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            conn.close()

            return [self._row_to_job(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Error retrieving recent jobs")
            return []

    def _row_to_job(self, row: sqlite3.Row) -> ProcessingJob:
        """Convert a database row to a ProcessingJob pydantic model.

        Returns
        -------
        job : `ProcessingJob`
            Pydantic model constructed from `row`.
        """
        input_metrics = {}
        output_metrics = {}
        if "input_metrics" in row.keys() and row["input_metrics"]:
            try:
                input_metrics = json.loads(row["input_metrics"])
            except TypeError, ValueError:
                input_metrics = {}
        if "output_metrics" in row.keys() and row["output_metrics"]:
            try:
                output_metrics = json.loads(row["output_metrics"])
            except TypeError, ValueError:
                output_metrics = {}

        return ProcessingJob(
            id=row["id"],
            target_id=row["target_id"],
            job_type=row["job_type"],
            status=row["status"],
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            message=row["message"],
            log_file_path=row["log_file_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            input_metrics=input_metrics,
            output_metrics=output_metrics,
            owner_pid=row["owner_pid"] if "owner_pid" in row.keys() else None,
            owner_started_at=row["owner_started_at"] if "owner_started_at" in row.keys() else None,
        )

    def interrupt_orphaned_jobs(
        self,
        is_process_alive: Callable[[int, str], bool],
        stale_after_minutes: int = 60,
    ) -> list[ProcessingJob]:
        """Close the jobs that a program left open when it ended.

        A job is closed by the code that runs it. If that program is killed or
        crashes, the job stays "started" or "running" forever. This finds those
        jobs and marks them "interrupted". Two kinds are found:

        - A job whose owner program (number and start time) no longer runs.
        - A job recorded before owners were kept, which has not been updated
          for `stale_after_minutes`. Nothing says who owns it, so only age
          can.

        A job owned by a program that still runs is never touched, even when
        that program is a different one, such as the backend while the MCP
        server runs this.

        Parameters
        ----------
        is_process_alive : `Callable`
            Given a program's number and start time, says if it still runs.
        stale_after_minutes : `int`, optional
            Minutes without an update after which a job with no recorded owner
            counts as left behind. Defaults to 60.

        Returns
        -------
        interrupted : `list` [`ProcessingJob`]
            The jobs that were closed, as they were before.
        """
        interrupted: list[ProcessingJob] = []
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM processing_jobs WHERE status IN ('started', 'running')")
            open_jobs = [self._row_to_job(row) for row in cursor.fetchall()]
            now = datetime.now()
            for job in open_jobs:
                if job.owner_pid is not None and job.owner_started_at is not None:
                    orphaned = not is_process_alive(job.owner_pid, job.owner_started_at)
                else:
                    last_update = datetime.fromisoformat(job.updated_at) if job.updated_at else None
                    orphaned = (
                        last_update is not None
                        and (now - last_update).total_seconds() > stale_after_minutes * 60
                    )
                if not orphaned:
                    continue
                cursor.execute(
                    "UPDATE processing_jobs SET status = 'interrupted', message = ?, "
                    "updated_at = ?, completed_at = ? WHERE id = ? AND status IN ('started', 'running')",
                    (
                        "The program running this job ended before it finished.",
                        now.isoformat(),
                        now.isoformat(),
                        job.id,
                    ),
                )
                if cursor.rowcount:
                    interrupted.append(job)
            conn.commit()
            conn.close()
        except sqlite3.Error:
            logger.exception("Could not close interrupted jobs")
        return interrupted

    def add_log_entry(
        self,
        component: str,
        logger_name: str,
        level: str,
        message: str,
        job_id: str | None = None,
    ) -> None:
        """Record a single emitted log record.

        Called from DbLogHandler.emit(), which runs inside the logging
        framework itself. Failures here must stay silent (no logger.error
        call) to avoid feeding a new log record back into this same handler
        and recursing.

        Parameters
        ----------
        component : `str`
            Top-level component name the log record originated from.
        logger_name : `str`
            Full dotted name of the originating logger.
        level : `str`
            Log level name (e.g. "INFO", "ERROR").
        message : `str`
            Formatted log message text.
        job_id : `str`, optional
            Processing job this entry is scoped to, default `None`
            for general/unscoped log entries.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO log_entries (job_id, component, logger_name, level, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    component,
                    logger_name,
                    level,
                    message,
                    datetime.now().isoformat(sep=" ", timespec="milliseconds"),
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            logger.debug("Error recording a job log line: %s", exc)

    def get_log_entries_for_job(self, job_id: str) -> list[dict]:
        """Retrieve all recorded log entries for a job, oldest first.

        Parameters
        ----------
        job_id : `str`
            Identifier of the job to retrieve log entries for.

        Returns
        -------
        entries : `list` of `dict`
            Log entry rows for the job, ordered oldest first. An
            empty list is returned if the query fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM log_entries WHERE job_id = ? ORDER BY id ASC", (job_id,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Error retrieving log entries for job %s", job_id)
            return []

    def query_jobs(
        self,
        target_id: str | None = None,
        job_type: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[ProcessingJob]:
        """Find jobs by target, type and status, newest first.

        Parameters
        ----------
        target_id : `str`, optional
            Only jobs for this target.
        job_type : `str`, optional
            Only jobs of this type, such as ``"stacking"``.
        status : `str`, optional
            Only jobs with this status.
        active_only : `bool`, optional
            Only jobs that are still running (status ``started`` or
            ``running``). Defaults to `False`.
        limit : `int`, optional
            Most jobs to return. Defaults to 50.

        Returns
        -------
        jobs : `list` of `ProcessingJob`
            Matching jobs, newest first. Empty if the query fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM processing_jobs WHERE (? IS NULL OR target_id = ?) "
                "AND (? IS NULL OR job_type = ?) AND (? IS NULL OR status = ?) "
                "AND (? = 0 OR status IN ('started', 'running')) ORDER BY created_at DESC LIMIT ?",
                (target_id, target_id, job_type, job_type, status, status, int(active_only), limit),
            ).fetchall()
            conn.close()
            return [self._row_to_job(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Error querying jobs")
            return []

    def get_recent_log_entries_for_job(self, job_id: str, limit: int = 50) -> tuple[list[dict], int]:
        """Read the last few log entries of a job without loading them all.

        Parameters
        ----------
        job_id : `str`
            Identifier of the job.
        limit : `int`, optional
            Most entries to return. Defaults to 50.

        Returns
        -------
        entries : `list` of `dict`
            The last ``limit`` entries, oldest first.
        total : `int`
            How many entries the job has in all.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) FROM log_entries WHERE job_id = ?", (job_id,)).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM log_entries WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit)
            ).fetchall()
            conn.close()
            return [dict(row) for row in reversed(rows)], total
        except sqlite3.Error:
            logger.exception("Error reading recent log entries for job %s", job_id)
            return [], 0


class DbLogHandler(logging.Handler):
    """Logging handler that records emitted records into log_entries.

    Bind a job_id to scope all records emitted through this handler
    instance to a specific processing job; leave it None for
    general/unscoped logs. Safe to call from any thread: each
    emit() opens and closes its own sqlite3 connection through the
    `JobStore`.

    Attributes
    ----------
    job_id : `str`, optional
        Processing job this handler's records are scoped to, or
        `None` for general/unscoped logs.
    """

    def __init__(self, job_store: JobStore, job_id: str | None = None) -> None:
        """Initialize the handler.

        Parameters
        ----------
        job_store : `JobStore`
            Where emitted log records are written.
        job_id : `str`, optional
            Processing job to scope records to, default `None` for
            general/unscoped logs.
        """
        super().__init__()
        self._job_store = job_store
        self.job_id = job_id

    def emit(self, record: logging.LogRecord) -> None:
        """Write a log record through the bound `JobStore`.

        Parameters
        ----------
        record : `logging.LogRecord`
            The log record emitted by the logging framework.
        """
        try:
            component = record.name.split(".")[0] if record.name else "root"
            self._job_store.add_log_entry(
                component=component,
                logger_name=record.name,
                level=record.levelname,
                message=self.format(record),
                job_id=self.job_id,
            )
        except TypeError, ValueError, KeyError:
            # The message could not be formatted. Let the logging system
            # report it in its usual way.
            self.handleError(record)
