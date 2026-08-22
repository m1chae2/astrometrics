"""Repository for managing persistent processing job records.

Provides `LoggerInterface`, a SQLite-backed repository for
`ProcessingJob` records, agent long-term-memory tables, and per-job
log entries, plus `DbLogHandler`, a `logging.Handler` that persists
emitted log records into that same database.

Notes
-----
Implements requirement REQ: IMG-5.6 (persistent job history
tracking).
"""

import logging
import sqlite3
from datetime import datetime

from astrometricslib.utilities.pipeline_models import ProcessingJob

logger = logging.getLogger(__name__)


class LoggerInterface:
    """Handle persistence for `ProcessingJob` objects in astrometrics_log.db.

    Attributes
    ----------
    db_path : `str`
        Filesystem path to the SQLite database backing this
        repository.
    """

    def __init__(self, db_path: str):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the repository and ensure its tables exist.

        Parameters
        ----------
        db_path : `str`
            Filesystem path to the high-level interface_log.db SQLite
            database.
        """
        self.db_path = db_path
        self._init_db()

    def _connect(self, timeout: float = 30.0) -> sqlite3.Connection:
        """Open a connection to astrometrics_log.db with WAL mode.

        Mirrors disk_interface._connect_db(): WAL lets readers and
        writers proceed concurrently, and the busy timeout makes a
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
        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_db(self):  # ruff: ignore[missing-return-type-private-function]
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
                    completed_at TIMESTAMP
                )
            """)

            # REQ: AGENT-1.7 - Interaction logging for audit and reflection
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_interactions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    response TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # REQ: AGENT-1.9 - Distilled knowledge for LTM
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

            # REQ: SYS-1.4 - Per-component / per-job log persistence
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
                        completed_at = ?
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
                        job.id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO processing_jobs (
                        id, target_id, job_type, status, progress_current,
                        progress_total, message, log_file_path, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        Implements requirement REQ: IMG-5.7.
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
        )

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
        """Persist a single emitted log record.

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
        """Retrieve all persisted log entries for a job, oldest first.

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


class DbLogHandler(logging.Handler):
    """Logging handler that persists emitted records into log_entries.

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
            Repository used to persist emitted log records.
        job_id : `str`, optional
            Processing job to scope records to, default `None` for
            general/unscoped logs.
        """
        super().__init__()
        self._logger_interface = logger_interface
        self.job_id = job_id

    def emit(self, record: logging.LogRecord) -> None:
        """Persist a log record via the bound `LoggerInterface`.

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
