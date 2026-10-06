"""Repository for managing persistent processing job records.

Provides `LoggerInterface`, a SQLite-backed repository for
`ProcessingJob` records, agent long-term-memory tables, and per-job
log entries, plus `DbLogHandler`, a `logging.Handler` that records
emitted log records into that same database.


"""

import json
import logging
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.utilities.observing_night import observing_night_id
from astrometricslib.utilities.pipeline_models import ProcessingJob

logger = logging.getLogger(__name__)


class LoggerInterface:
    """Handle recording for `ProcessingJob` objects in astrometrics_log.db.

    Attributes
    ----------
    db_path : `str`
        Filesystem path to the SQLite database backing this
        repository.
    """

    def __init__(self, db_path: str, read_only: bool = False):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the repository and ensure its tables exist.

        Parameters
        ----------
        db_path : `str`
            Filesystem path to the high-level interface_log.db SQLite
            database.
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
        """Create job, interaction, knowledge, and log tables if missing."""
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

            # Interaction logging for audit and reflection
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_interactions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    response TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Distilled knowledge for LTM
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_knowledge (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    importance INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_recalled_at TIMESTAMP
                )
            """)

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

            # Telescope alignment sync history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alignment_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    target_name TEXT,
                    timestamp REAL NOT NULL,
                    status TEXT NOT NULL,
                    delta_ra_arcsec REAL,
                    delta_dec_arcsec REAL,
                    pointing_error_arcsec REAL,
                    mount_ra REAL,
                    mount_dec REAL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_alignment_logs_time ON alignment_logs(timestamp)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_alignment_logs_target ON alignment_logs(target_name)"
            )

            # Autoguiding drift, pulse, and SNR samples
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guiding_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    target_name TEXT,
                    timestamp REAL NOT NULL,
                    dra REAL NOT NULL,
                    ddec REAL NOT NULL,
                    pulse_ra REAL DEFAULT 0.0,
                    pulse_dec REAL DEFAULT 0.0,
                    snr REAL,
                    rms_ra REAL,
                    rms_dec REAL,
                    star_mass REAL,
                    source TEXT NOT NULL DEFAULT 'unverified'
                )
            """)
            # A database created before `source` existed lacks the column.
            # Rows written then keep the honest default 'unverified',
            # because nothing recorded whether they were measured.
            cursor.execute("PRAGMA table_info(guiding_logs)")
            guiding_columns = [row[1] for row in cursor.fetchall()]
            if "source" not in guiding_columns:
                cursor.execute(
                    "ALTER TABLE guiding_logs ADD COLUMN source TEXT NOT NULL DEFAULT 'unverified'"
                )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_guiding_logs_time ON guiding_logs(timestamp)")
            # Polar alignment assistant logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS polar_alignment_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp REAL NOT NULL,
                    status TEXT NOT NULL,
                    total_error_arcsec REAL,
                    alt_error_arcsec REAL,
                    az_error_arcsec REAL,
                    pole_ra REAL,
                    pole_dec REAL,
                    paa_points_json TEXT
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_polar_alignment_logs_time ON polar_alignment_logs(timestamp)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_polar_alignment_logs_session "
                "ON polar_alignment_logs(session_id)"
            )

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error initializing job database at {self.db_path}: {e}")
            raise e

    def upsert_job(self, job: ProcessingJob):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Insert or update a job record.

        Parameters
        ----------
        job : `ProcessingJob`
            Job record to insert (if `job.id` is new) or update (if
            `job.id` already exists in the processing_jobs table).

        Raises
        ------
        Exception
            Re-raised after logging if the underlying SQLite
            operation fails.
        """  # ruff: ignore[docstring-extraneous-exception] -- `raise e`
        # re-raises the generically-caught exception, which pydoclint
        # cannot resolve to the declared `Exception` type statically.
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
        except Exception as e:
            logger.error(f"Error upserting job {job.id}: {e}")
            raise e

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
        except Exception as e:
            logger.error(f"Error retrieving job {job_id}: {e}")
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
        except Exception as e:
            logger.error(f"Error retrieving jobs for target {target_id}: {e}")
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
        except Exception as e:
            logger.error(f"Error deleting job {job_id}: {e}")
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
        except Exception as e:
            logger.error(f"Error retrieving old jobs: {e}")
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
        except Exception as e:
            logger.error(f"Error retrieving recent jobs: {e}")
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
            except Exception:
                input_metrics = {}
        if "output_metrics" in row.keys() and row["output_metrics"]:
            try:
                output_metrics = json.loads(row["output_metrics"])
            except Exception:
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
        except Exception as error:
            logger.error("Could not close interrupted jobs: %s", error)
        return interrupted

    # --- Agent LTM Methods ---

    def log_interaction(self, interaction_id: str, session_id: str, prompt: str, response_json: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Log a raw LLM interaction.

        Parameters
        ----------
        interaction_id : `str`
            Unique identifier for this interaction record.
        session_id : `str`
            Identifier grouping interactions from the same session.
        prompt : `str`
            The prompt text sent to the LLM.
        response_json : `str`
            The LLM's response, serialized as a JSON string.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO agent_interactions (id, session_id, prompt, response) VALUES (?, ?, ?, ?)",
                (interaction_id, session_id, prompt, response_json),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error logging agent interaction: {e}")

    def get_session_interactions(self, session_id: str) -> list[dict]:
        """Retrieve all interactions for a specific session.

        Parameters
        ----------
        session_id : `str`
            Identifier of the session to retrieve interactions for.

        Returns
        -------
        interactions : `list` of `dict`
            Interaction rows for the session, ordered oldest first. An
            empty list is returned if the query fails.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM agent_interactions WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error retrieving interactions for session {session_id}: {e}")
            return []

    def add_knowledge(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self, knowledge_id: str, category: str, content: str, summary: str | None = None, importance: int = 1
    ):
        """Add a distilled piece of knowledge to the agent's long-term memory.

        Parameters
        ----------
        knowledge_id : `str`
            Unique identifier for this knowledge record.
        category : `str`
            Category label used to group related knowledge.
        content : `str`
            The knowledge content itself.
        summary : `str`, optional
            Short summary of `content`, default `None`.
        importance : `int`, optional
            Relative importance used for recall ranking, default 1.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_knowledge (id, category, content, summary, importance)
                VALUES (?, ?, ?, ?, ?)
            """,
                (knowledge_id, category, content, summary, importance),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error adding agent knowledge: {e}")

    # --- Log Entry Methods ---

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
        except Exception as exc:
            logger.debug("Error recording agent knowledge recall: %s", exc)

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
        except Exception as e:
            logger.error(f"Error retrieving log entries for job {job_id}: {e}")
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
        except Exception as e:
            logger.error(f"Error querying jobs: {e}")
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
        except Exception as e:
            logger.error(f"Error reading recent log entries for job {job_id}: {e}")
            return [], 0

    def get_relevant_knowledge(self, limit: int = 5) -> list[dict]:
        """Retrieve high-importance or recently added knowledge.

        Parameters
        ----------
        limit : `int`, optional
            Maximum number of knowledge records to return, default 5.

        Returns
        -------
        knowledge_records : `list` of `dict`
            Knowledge rows ordered by importance (descending) then
            recency. An empty list is returned if the query fails.

        Notes
        -----
        In a production system, this would use vector search. Here we
        use importance and recency.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM agent_knowledge
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            """,
                (limit,),
            )
            rows = cursor.fetchall()

            # Update last_recalled_at
            if rows:
                ids = [r["id"] for r in rows]
                cursor.execute(
                    "UPDATE agent_knowledge SET last_recalled_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({','.join(['?'] * len(ids))})",
                    ids,
                )
                conn.commit()

            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error retrieving agent knowledge: {e}")
            return []

    def record_alignment_attempt(self, attempt: dict[str, Any]) -> None:
        """Record a telescope plate-solve alignment attempt in SQLite.

        Parameters
        ----------
        attempt : `dict` [`str`, `Any`]
            Dictionary containing timestamp, status, deltaRaArcsec or
            delta_ra_arcsec, deltaDecArcsec or delta_dec_arcsec, target_name,
            and mount coordinates.
        """
        try:
            import math

            conn = self._connect()
            cursor = conn.cursor()
            d_ra = attempt.get("delta_ra_arcsec", attempt.get("deltaRaArcsec"))
            d_dec = attempt.get("delta_dec_arcsec", attempt.get("deltaDecArcsec"))
            pointing_error = attempt.get("pointing_error_arcsec", attempt.get("pointingErrorArcsec"))
            if pointing_error is None and d_ra is not None and d_dec is not None:
                pointing_error = math.hypot(d_ra, d_dec)

            # Reject mount driver tracking-loop heartbeat echoes
            # (< 0.5 arcsec or stationary Dec with tiny RA)
            if pointing_error is not None and pointing_error < 0.5 and not attempt.get("force_record"):
                logger.debug(f"Ignoring spurious alignment tracking echo ({pointing_error:.3f} arcsec)")
                conn.close()
                return

            attempt_timestamp = attempt.get("timestamp", attempt.get("time", datetime.now().timestamp()))
            cursor.execute(
                """
                INSERT INTO alignment_logs (
                    session_id, target_name, timestamp, status,
                    delta_ra_arcsec, delta_dec_arcsec, pointing_error_arcsec,
                    mount_ra, mount_dec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.get("session_id") or observing_night_id(attempt_timestamp),
                    attempt.get("target_name"),
                    attempt_timestamp,
                    attempt.get("status", "unknown"),
                    d_ra,
                    d_dec,
                    pointing_error,
                    attempt.get("ra", attempt.get("mount_ra")),
                    attempt.get("dec", attempt.get("mount_dec")),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error recording alignment attempt: {e}")

    def cleanup_spurious_alignment_logs(self, max_threshold_arcsec: float = 0.5) -> int:
        """Purge sub-arcsecond tracking loop echo records from alignment_logs.

        Parameters
        ----------
        max_threshold_arcsec : `float`, optional
            Maximum pointing error threshold below which records are considered
            driver tracking loop echoes rather than genuine plate-solve syncs.
            Defaults to 0.5 arcseconds.

        Returns
        -------
        deleted_count : `int`
            Number of purged echo records.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM alignment_logs
                WHERE pointing_error_arcsec < ?
                   OR (abs(delta_dec_arcsec) < 1e-4 AND abs(delta_ra_arcsec) < 1.0)
                """,
                (max_threshold_arcsec,),
            )
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            logger.info(f'Purged {deleted_count} spurious alignment log echoes (< {max_threshold_arcsec}")')
            return deleted_count
        except Exception as e:
            logger.error(f"Error cleaning up spurious alignment logs: {e}")
            return 0

    @staticmethod
    def _guiding_sample_rows(samples: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        """Turn guiding sample dictionaries into database rows.

        Returns
        -------
        rows : `list` [`tuple`]
            One tuple per sample, in the column order of
            `_INSERT_GUIDING_SAMPLE_SQL`.
        """
        rows = []
        for s in samples:
            sample_timestamp = s.get("timestamp", s.get("time", datetime.now().timestamp()))
            rows.append((
                s.get("session_id") or observing_night_id(sample_timestamp),
                s.get("target_name"),
                sample_timestamp,
                s.get("dra", 0.0),
                s.get("ddec", 0.0),
                s.get("pulse_ra", s.get("pulseRa", 0.0)),
                s.get("pulse_dec", s.get("pulseDec", 0.0)),
                s.get("snr"),
                s.get("rms_ra", s.get("rmsRa")),
                s.get("rms_dec", s.get("rmsDec")),
                s.get("star_mass", s.get("starMass")),
                s.get("source", "unverified"),
            ))
        return rows

    _INSERT_GUIDING_SAMPLE_SQL = """
        INSERT INTO guiding_logs (
            session_id, target_name, timestamp, dra, ddec,
            pulse_ra, pulse_dec, snr, rms_ra, rms_dec, star_mass, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def record_guiding_samples(self, samples: list[dict[str, Any]]) -> None:
        """Record autoguiding drift, pulse, and SNR telemetry in SQLite.

        Parameters
        ----------
        samples : `list` [`dict` [`str`, `Any`]]
            List of guiding sample dictionaries containing time/timestamp,
            dra, ddec, pulseRa/pulse_ra, pulseDec/pulse_dec, snr, rmsRa/rms_ra,
            rmsDec/rms_dec, star_mass, target_name, session_id, source.
            A sample with no ``session_id`` is filed under the observing
            night its timestamp falls in (see
            `astrometricslib.utilities.observing_night`). A sample with no
            ``source`` is stored as ``"unverified"``; callers should say
            where each sample came from, so that estimates are never
            mistaken for measurements.
        """
        if not samples:
            return
        try:
            conn = self._connect()
            conn.executemany(self._INSERT_GUIDING_SAMPLE_SQL, self._guiding_sample_rows(samples))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error recording guiding samples: {e}")

    def replace_guiding_samples(self, samples: list[dict[str, Any]]) -> int:
        """Record guiding samples, replacing any earlier copy of the same log.

        Reading the same log file twice must not double its samples. This
        deletes every stored sample with the same ``source`` whose time
        falls inside the batch's time span, then inserts the batch, all in
        one transaction. Samples from other sources are untouched, so a
        measurement is never replaced by an estimate covering the same time.

        Parameters
        ----------
        samples : `list` [`dict` [`str`, `Any`]]
            Samples, as for `record_guiding_samples`. Every sample must
            carry the same ``source``.

        Returns
        -------
        replaced_count : `int`
            How many earlier samples were removed.

        Raises
        ------
        InvalidArgumentError
            Raised if the samples do not all have one ``source``.
        sqlite3.Error
            Raised if the database cannot be written. Unlike the other
            ``record_*`` methods, this one does not swallow the error:
            ingestion reports how many samples it stored, and that count
            must not claim samples that never reached the database.
        """
        if not samples:
            return 0
        sources = {sample.get("source", "unverified") for sample in samples}
        if len(sources) != 1:
            raise InvalidArgumentError(
                f"replace_guiding_samples needs a single source, got {sorted(sources)}"
            )
        (source,) = sources
        timestamps = [sample.get("timestamp", sample.get("time")) for sample in samples]
        if any(timestamp is None for timestamp in timestamps):
            raise InvalidArgumentError("replace_guiding_samples needs a timestamp on every sample")
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                "DELETE FROM guiding_logs WHERE source = ? AND timestamp BETWEEN ? AND ?",
                (source, min(timestamps), max(timestamps)),
            )
            replaced_count = cursor.rowcount
            cursor.executemany(self._INSERT_GUIDING_SAMPLE_SQL, self._guiding_sample_rows(samples))
            connection.commit()
            connection.close()
        except sqlite3.Error:
            logger.exception("Error replacing guiding samples")
            raise
        return replaced_count

    def get_alignment_logs(self, target_name: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve recent plate-solve alignment logs from SQLite.

        Parameters
        ----------
        target_name : `str` | `None`, optional
            Filter by target name if provided.
        limit : `int`, optional
            Maximum rows to return, default 100.

        Returns
        -------
        logs : `list` [`dict` [`str`, `Any`]]
            List of alignment log rows as dictionaries.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if target_name:
                cursor.execute(
                    "SELECT * FROM alignment_logs WHERE target_name = ? ORDER BY timestamp DESC LIMIT ?",
                    (target_name, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM alignment_logs ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"Error fetching alignment logs: {e}")
            return []

    def get_guiding_logs(
        self,
        target_name: str | None = None,
        session_id: str | None = None,
        start_time: float | None = None,
        limit: int = 1000,
        sources: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve historical guiding telemetry logs from SQLite.

        Parameters
        ----------
        target_name : `str` | `None`, optional
            Filter by target name if provided.
        session_id : `str` | `None`, optional
            Filter by session identifier or observing-night date if
            provided. A night's date is the local date on which it began
            (see `astrometricslib.utilities.observing_night`), so a night
            that crosses midnight is one session.
        start_time : `float` | `None`, optional
            Earliest epoch timestamp to query.
        limit : `int`, optional
            Maximum rows to return, default 1000.
        sources : `Iterable` [`str`] | `None`, optional
            Keep only samples whose ``source`` is one of these. `None`
            keeps every sample, including estimates.

        Returns
        -------
        logs : `list` [`dict` [`str`, `Any`]]
            List of guiding log rows as dictionaries.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM guiding_logs WHERE 1=1"
            params: list[Any] = []
            if target_name:
                query += " AND target_name = ?"
                params.append(target_name)
            if session_id:
                query += (
                    " AND (session_id = ? OR "
                    "strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime') = ?)"
                )
                params.extend([session_id, session_id])
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if sources is not None:
                allowed_sources = list(sources)
                query += f" AND source IN ({','.join('?' * len(allowed_sources))})"
                params.extend(allowed_sources)
            query += " ORDER BY timestamp ASC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"Error fetching guiding logs: {e}")
            return []

    def record_polar_alignment(self, data: dict[str, Any]) -> None:
        """Record a Polar Alignment Assistant (PAA) measurement in SQLite.

        Parameters
        ----------
        data : `dict` [`str`, `Any`]
            Dictionary containing session_id, timestamp, status,
            total_error_arcsec, alt_error_arcsec, az_error_arcsec,
            pole_ra, pole_dec, and paa_points.
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            paa_pts = data.get("paa_points", data.get("paaPoints", []))
            paa_json = json.dumps(paa_pts) if paa_pts else None

            cursor.execute(
                """
                INSERT INTO polar_alignment_logs (
                    session_id, timestamp, status,
                    total_error_arcsec, alt_error_arcsec, az_error_arcsec,
                    pole_ra, pole_dec, paa_points_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("session_id", data.get("sessionId")),
                    data.get("timestamp", datetime.now().timestamp()),
                    data.get("status", "aligned"),
                    data.get("total_error_arcsec", data.get("totalErrorArcsec")),
                    data.get("alt_error_arcsec", data.get("altErrorArcsec")),
                    data.get("az_error_arcsec", data.get("azErrorArcsec")),
                    data.get("pole_ra", data.get("poleRa")),
                    data.get("pole_dec", data.get("poleDec")),
                    paa_json,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error recording polar alignment: {e}")

    def get_polar_alignment_logs(
        self, session_id: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Retrieve historical polar alignment logs from SQLite.

        Parameters
        ----------
        session_id : `str` | `None`, optional
            Filter by session identifier.
        limit : `int`, optional
            Maximum records to retrieve, defaults to 10.

        Returns
        -------
        logs : `list` [`dict` [`str`, `Any`]]
            List of polar alignment logs.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if session_id:
                cursor.execute(
                    "SELECT * FROM polar_alignment_logs WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (session_id, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM polar_alignment_logs ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            rows = []
            for r in cursor.fetchall():
                row_dict = dict(r)
                if row_dict.get("paa_points_json"):
                    try:
                        row_dict["paa_points"] = json.loads(row_dict["paa_points_json"])
                    except Exception:
                        row_dict["paa_points"] = []
                else:
                    row_dict["paa_points"] = []
                rows.append(row_dict)
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"Error fetching polar alignment logs: {e}")
            return []

    def get_alignment_sessions(self) -> list[dict[str, Any]]:
        """Retrieve observing sessions with alignment or guiding data.

        Returns
        -------
        sessions : `list` [`dict` [`str`, `Any`]]
            Summaries of past observing sessions including sync count,
            guiding sample count, dates, average pointing error, and
            polar alignment error.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                WITH combined AS (
                    SELECT
                        COALESCE(
                            session_id,
                            strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime')
                        ) AS session_id,
                        strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime') AS session_date,
                        timestamp,
                        pointing_error_arcsec,
                        1 AS is_sync,
                        0 AS is_guiding
                    FROM alignment_logs
                    UNION ALL
                    SELECT
                        COALESCE(
                            session_id,
                            strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime')
                        ) AS session_id,
                        strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime') AS session_date,
                        timestamp,
                        NULL AS pointing_error_arcsec,
                        0 AS is_sync,
                        1 AS is_guiding
                    FROM guiding_logs
                )
                SELECT
                    session_id,
                    session_date,
                    SUM(is_sync) AS sync_count,
                    SUM(is_guiding) AS guiding_count,
                    MIN(timestamp) AS start_time,
                    MAX(timestamp) AS end_time,
                    ROUND(AVG(pointing_error_arcsec), 2) AS avg_error_arcsec
                FROM combined
                GROUP BY session_id, session_date
                ORDER BY MAX(timestamp) DESC
                LIMIT 50
                """
            )
            sessions = [dict(r) for r in cursor.fetchall()]

            # Fetch matching polar alignment errors for each session
            for s in sessions:
                sid = s["session_id"]
                sdate = s["session_date"]
                cursor.execute(
                    """
                    SELECT total_error_arcsec, alt_error_arcsec, az_error_arcsec
                    FROM polar_alignment_logs
                    WHERE session_id = ?
                       OR strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime') = ?
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (sid, sdate),
                )
                polar_row = cursor.fetchone()
                if polar_row:
                    s["polar_error_arcsec"] = polar_row["total_error_arcsec"]
                    s["polar_alt_error_arcsec"] = polar_row["alt_error_arcsec"]
                    s["polar_az_error_arcsec"] = polar_row["az_error_arcsec"]
                else:
                    s["polar_error_arcsec"] = None
                    s["polar_alt_error_arcsec"] = None
                    s["polar_az_error_arcsec"] = None

            conn.close()
            return sessions
        except Exception as e:
            logger.error(f"Error fetching alignment sessions: {e}")
            return []

    def get_session_alignment_attempts(
        self, session_id: str | None = None, limit: int = 10000
    ) -> list[dict[str, Any]]:
        """Retrieve alignment attempts for a session or all sessions.

        Parameters
        ----------
        session_id : `str` or `None`, optional
            Target session identifier, date string (YYYY-MM-DD), or "all"
            for all sessions.
        limit : `int`, optional
            Maximum records when querying all sessions (default 10000).

        Returns
        -------
        attempts : `list` [`dict` [`str`, `Any`]]
            List of alignment attempts in chronological order.
        """
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if session_id in ("all", "*", None):
                cursor.execute(
                    """
                    SELECT * FROM alignment_logs
                    ORDER BY timestamp ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM alignment_logs
                    WHERE session_id = ?
                       OR strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime') = ?
                    ORDER BY timestamp ASC
                    """,
                    (session_id, session_id),
                )
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"Error fetching session alignment attempts: {e}")
            return []

    def get_session_target_telemetry(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve synthesized target tracking telemetry for a session.

        Inspects targets with frames captured during the session date,
        matches any concurrent guiding log samples, and returns
        AlignmentAttempt-compatible telemetry rows for each target.

        Parameters
        ----------
        session_id : `str` | `None`, optional
            Session identifier or date string (YYYY-MM-DD). If 'all' or None,
            returns targets for all recorded sessions.

        Returns
        -------
        telemetry : `list` [`dict` [`str`, `Any`]]
            List of alignment/tracking attempt dicts for each target.
        """
        import os

        from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

        target_db_path = os.path.join(os.path.dirname(self.db_path), "astrometrics.db")
        if not os.path.exists(target_db_path):
            return []

        try:
            # 1. Fetch guiding logs for the session
            conn_log = self._connect()
            conn_log.row_factory = sqlite3.Row
            c_log = conn_log.cursor()

            if session_id in ("all", "*", None):
                c_log.execute(
                    """
                    SELECT timestamp, dra, ddec, rms_ra, rms_dec, target_name
                    FROM guiding_logs
                    ORDER BY timestamp ASC
                    """
                )
            else:
                c_log.execute(
                    """
                    SELECT timestamp, dra, ddec, rms_ra, rms_dec, target_name
                    FROM guiding_logs
                    WHERE session_id = ?
                       OR strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime') = ?
                    ORDER BY timestamp ASC
                    """,
                    (session_id, session_id),
                )
            guiding_rows = [dict(r) for r in c_log.fetchall()]
            conn_log.close()

            # 2. Fetch targets and frames from astrometrics.db
            conn_target = sqlite3.connect(target_db_path)
            conn_target.row_factory = sqlite3.Row
            c_target = conn_target.cursor()
            c_target.execute("SELECT id, ra, dec, data_json FROM targets")
            target_records = c_target.fetchall()
            conn_target.close()

            attempts: list[dict[str, Any]] = []

            for row in target_records:
                tid = row["id"]
                ra_str = row["ra"]
                dec_str = row["dec"]
                data_json = row["data_json"]
                target_data = json.loads(data_json) if data_json else {}

                frames = target_data.get("frames", [])
                if not frames:
                    continue

                # Filter frames for this session
                session_frames = []
                for f in frames:
                    ts = f.get("timestamp")
                    if not ts:
                        continue
                    if session_id in ("all", "*", None):
                        session_frames.append(f)
                    else:
                        frame_session_date = datetime.fromtimestamp(ts - 43200).strftime("%Y-%m-%d")
                        if frame_session_date == session_id:
                            session_frames.append(f)

                if not session_frames:
                    continue

                # Parse target coordinates
                try:
                    target_ra_deg = parse_coordinate_string(ra_str, is_ra=True) if ra_str else 0.0
                    target_dec_deg = parse_coordinate_string(dec_str, is_ra=False) if dec_str else 0.0
                except Exception:
                    target_ra_deg = 0.0
                    target_dec_deg = 0.0

                # Coordinates that could not be read are stored as (0, 0).
                if not (target_ra_deg or target_dec_deg):
                    continue

                min_ts = min(f["timestamp"] for f in session_frames)
                max_ts = max(f["timestamp"] for f in session_frames)

                # Match guiding samples within the frame capture window
                # (+/- 60 s margin)
                matched_guiding = [
                    g
                    for g in guiding_rows
                    if (g.get("target_name") == tid) or (min_ts - 60 <= g["timestamp"] <= max_ts + 60)
                ]

                if matched_guiding:
                    for g in matched_guiding:
                        dra = float(g.get("dra") or 0.0)
                        ddec = float(g.get("ddec") or 0.0)
                        err = (dra**2 + ddec**2) ** 0.5
                        attempts.append({
                            "status": "aligned",
                            "deltaRaArcsec": dra,
                            "deltaDecArcsec": ddec,
                            "ra": target_ra_deg,
                            "dec": target_dec_deg,
                            "pointingErrorArcsec": err,
                            "timestamp": g["timestamp"],
                            "targetName": tid,
                        })
                else:
                    # Synthesize frame attempts from frame capture series
                    for idx, f in enumerate(session_frames):
                        f_ts = f.get("timestamp", min_ts + idx * 5)
                        attempts.append({
                            "status": "aligned",
                            "deltaRaArcsec": 0.0,
                            "deltaDecArcsec": 0.0,
                            "ra": target_ra_deg,
                            "dec": target_dec_deg,
                            "pointingErrorArcsec": 0.0,
                            "timestamp": f_ts,
                            "targetName": tid,
                        })

            return attempts
        except Exception as e:
            logger.error(f"Error fetching session target telemetry: {e}")
            return []


class DbLogHandler(logging.Handler):
    """Logging handler that records emitted records into log_entries.

    Bind a job_id to scope all records emitted through this handler
    instance to a specific processing job; leave it None for
    general/unscoped logs. Safe to call from any thread — each
    emit() opens and closes its own sqlite3 connection via
    LoggerInterface, matching its existing per-call connection
    pattern.

    Attributes
    ----------
    job_id : `str`, optional
        Processing job this handler's records are scoped to, or
        `None` for general/unscoped logs.
    """

    def __init__(self, logger_interface: LoggerInterface, job_id: str | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the handler.

        Parameters
        ----------
        logger_interface : `LoggerInterface`
            Repository used to record emitted log records.
        job_id : `str`, optional
            Processing job to scope records to, default `None` for
            general/unscoped logs.
        """
        super().__init__()
        self._logger_interface = logger_interface
        self.job_id = job_id

    def emit(self, record: logging.LogRecord) -> None:
        """Record a log record via the bound `LoggerInterface`.

        Parameters
        ----------
        record : `logging.LogRecord`
            The log record emitted by the logging framework.
        """
        try:
            component = record.name.split(".")[0] if record.name else "root"
            self._logger_interface.add_log_entry(
                component=component,
                logger_name=record.name,
                level=record.levelname,
                message=self.format(record),
                job_id=self.job_id,
            )
        except Exception:
            self.handleError(record)
