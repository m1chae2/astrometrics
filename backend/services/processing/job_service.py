"""Service for managing processing job lifecycle and state.

REQ: IMG-5.6: Persistent job history tracking.
"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from astrometricslib import LoggerInterface, ProcessingJob

logger = logging.getLogger(__name__)


class JobService:
    """Coordinates job creation, updates, and historical retrieval.

    Acts as a bridge between high-level services and the JobRepository.
    """

    def __init__(self, job_repository: LoggerInterface):  # ruff: ignore[missing-return-type-special-method]
        self.repository = job_repository

    def create_job(self, target_id: str, job_type: str, log_file: str | None = None) -> ProcessingJob:
        """Create a new job and record it to the database.

        Returns
        -------
        job : `ProcessingJob`
            The newly created job record.
        """
        job = ProcessingJob(
            id=str(uuid.uuid4()),
            target_id=target_id,
            job_type=job_type,
            status="started",
            progress_current=0,
            progress_total=100,
            log_file_path=log_file,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        self.repository.upsert_job(job)
        return job

    def update_job(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        job_id: str,
        status: str | None = None,
        progress: float | None = None,
        status_message: str | None = None,
    ):
        """Update job state in the database."""
        job = self.repository.get_job(job_id)
        if not job:
            return

        if status:
            job.status = status
            if status in ["completed", "failed", "cancelled"]:
                job.completed_at = datetime.now().isoformat()

        if progress is not None:
            job.progress_current = int(progress)

        if status_message:
            job.message = status_message

        job.updated_at = datetime.now().isoformat()
        self.repository.upsert_job(job)

    def get_job(self, job_id: str) -> ProcessingJob | None:
        """Get details for a specific job.

        Returns
        -------
        job : `ProcessingJob` or `None`
            The matching job, or `None` if no job has the given id.
        """
        return self.repository.get_job(job_id)

    def get_log_entries_for_job(self, job_id: str) -> list[dict]:
        """Retrieve recorded log entries for a job, oldest first.

        Returns
        -------
        entries : `list`
            The recorded log entries for the job, oldest first.
        """
        return self.repository.get_log_entries_for_job(job_id)

    def get_jobs_for_target(
        self, target_id: str, job_type: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[ProcessingJob]:
        """Retrieve historical jobs for a target.

        Returns
        -------
        jobs : `list`
            Historical jobs for the target, matching the given filters.
        """
        return self.repository.get_jobs_by_target(target_id, job_type, status, limit)

    def get_recent_jobs(self, limit: int = 50, job_type: str | None = None) -> list[ProcessingJob]:
        """Retrieve most recent jobs globally.

        Returns
        -------
        jobs : `list`
            The most recent jobs, optionally filtered by job type.
        """
        return self.repository.get_recent_jobs(limit=limit, job_type=job_type)

    def get_active_jobs(self) -> list[ProcessingJob]:
        """Get all currently running jobs.

        Returns
        -------
        jobs : `list`
            Jobs whose status is ``started`` or ``running``.
        """
        # Simple filter for now
        all_recent = self.get_recent_jobs(limit=100)
        return [j for j in all_recent if j.status in ["started", "running"]]

    def list_jobs(
        self, target_id: str | None = None, job_type: str | None = None, limit: int = 50
    ) -> list[ProcessingJob]:
        """List jobs from history, optionally filtered.

        Returns
        -------
        jobs : `list`
            Jobs for the target if given, otherwise the most recent
            jobs globally.
        """
        if target_id:
            return self.get_jobs_for_target(target_id, job_type=job_type, limit=limit)
        else:
            return self.get_recent_jobs(limit=limit, job_type=job_type)

    def delete_job(self, job_id: str) -> bool:
        """Permanently delete a job record and its associated log file.

        REQ: IMG-5.7

        Returns
        -------
        deleted : `bool`
            `True` if the job record was deleted, `False` otherwise.
        """
        job = self.get_job(job_id)
        if job and job.log_file_path:
            # Ensure path is absolute for reliable deletion
            path = Path(job.log_file_path)
            if not path.is_absolute() and hasattr(self.repository, "db_path"):
                # Try to resolve relative to project root
                from astrometricslib import get_configuration

                config = get_configuration()
                path = config.get_project_root() / path

            if path.exists():
                try:
                    os.remove(str(path))
                    logger.info(f"Deleted log file for job {job_id}: {path}")
                except Exception as e:
                    logger.error(f"Error removing log file {path}: {e}")

        return self.repository.delete_job(job_id)

    async def wait_for_job(
        self, job_id: str, timeout: int = 3600, poll_interval: float = 1.0
    ) -> ProcessingJob:
        """Wait for a job to reach a terminal state.

        Terminal states are ``completed``, ``failed``, or
        ``cancelled``.

        Returns
        -------
        job : `ProcessingJob`
            The job once it reaches a terminal state.

        Raises
        ------
        ValueError
            If no job exists for ``job_id``.
        TimeoutError
            If the job does not reach a terminal state within
            ``timeout`` seconds.
        """
        import asyncio

        start_time = datetime.now()
        while True:
            job = self.get_job(job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            if job.status in ["completed", "failed", "cancelled"]:
                return job

            if (datetime.now() - start_time).total_seconds() > timeout:
                raise TimeoutError(f"Job {job_id} timed out after {timeout} seconds")

            await asyncio.sleep(poll_interval)

    def prune_old_jobs(self, days: int = 7) -> int:
        """Prune jobs and log files older than the threshold.

        Returns
        -------
        count : `int`
            The number of jobs removed.
        """
        old_jobs = self.repository.get_jobs_older_than(days)
        count = 0
        for job in old_jobs:
            if self.delete_job(job.id):
                count += 1
        return count

    # --- Agent LTM Methods ---

    def log_agent_interaction(self, session_id: str, prompt: str, response_json: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Log a raw LLM interaction for auditing and future reflection."""
        interaction_id = str(uuid.uuid4())
        self.repository.log_interaction(interaction_id, session_id, prompt, response_json)

    def get_session_interactions(self, session_id: str) -> list[dict]:
        """Retrieve interaction history for reflection.

        Returns
        -------
        interactions : `list`
            The logged interactions for the session.
        """
        return self.repository.get_session_interactions(session_id)

    def add_agent_knowledge(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self, category: str, content: str, summary: str | None = None, importance: int = 1
    ):
        """Record distilled agent knowledge."""
        knowledge_id = str(uuid.uuid4())
        self.repository.add_knowledge(knowledge_id, category, content, summary, importance)

    def get_relevant_agent_knowledge(self, limit: int = 5) -> list[dict]:
        """Retrieve the most relevant knowledge for prompt injection.

        Returns
        -------
        knowledge : `list`
            The most relevant knowledge entries.
        """
        return self.repository.get_relevant_knowledge(limit)
