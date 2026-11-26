"""Service for managing processing job lifecycle and state."""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from astrometricslib.drivers import is_path_inside
from astrometricslib.exceptions import ConflictError, NotFoundError
from astrometricslib.jobs import JobStore, ProcessingJob, current_process_identity

logger = logging.getLogger(__name__)


class JobService:
    """Coordinates job creation, updates, and historical retrieval.

    Acts as a bridge between high-level services and the job store.
    """

    def __init__(self, job_repository: JobStore, logs_folder: Path | None = None) -> None:
        """Keep the job store this service reads and writes.

        Parameters
        ----------
        job_repository : `JobStore`
            The logs database's job list.
        logs_folder : `pathlib.Path`, optional
            The folder this app run keeps job log files in. Only files inside
            it are ever deleted. Default: the configured logs folder, looked
            up when a log file is about to be deleted.
        """
        self.repository = job_repository
        self._logs_folder = logs_folder

    def _owned_logs_folder(self) -> Path:
        """Give the one folder this service may delete job log files from.

        Returns
        -------
        folder : `pathlib.Path`
            The folder passed to the constructor, or the configured logs
            folder.
        """
        if self._logs_folder is not None:
            return self._logs_folder
        from astrometricslib import get_configuration

        return get_configuration().get_logs_path()

    def create_job(
        self,
        target_id: str,
        job_type: str,
        log_file: str | None = None,
        input_metrics: dict[str, Any] | None = None,
    ) -> ProcessingJob:
        """Create a new job and record it to the database.

        The job records this program (the backend) as its owner, the same way
        a job run by the library does, so a job the backend leaves open when
        it ends can be recognized and closed.

        Returns
        -------
        job : `ProcessingJob`
            The newly created job record.
        """
        owner_pid, owner_started_at = current_process_identity()
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
            input_metrics=input_metrics or {},
            output_metrics={},
            owner_pid=owner_pid,
            owner_started_at=owner_started_at,
        )
        self.repository.upsert_job(job)
        return job

    def update_job(
        self,
        job_id: str,
        status: str | None = None,
        progress: float | None = None,
        status_message: str | None = None,
        input_metrics: dict[str, Any] | None = None,
        output_metrics: dict[str, Any] | None = None,
    ) -> None:
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

        if input_metrics is not None:
            job.input_metrics = input_metrics

        if output_metrics is not None:
            job.output_metrics = output_metrics

        job.updated_at = datetime.now().isoformat()
        self.repository.upsert_job(job)

    def record_input_metrics(self, job_id: str, updates: dict[str, Any]) -> None:
        """Add keys to a job's input metrics, keeping the keys already there.

        The job list shows the steps a job ran (``steps``) and the kinds of
        stack it made (``stackKinds``) from these metrics.

        Parameters
        ----------
        job_id : `str`
            The job to change. An unknown job is ignored.
        updates : `dict` [`str`, `Any`]
            The keys to set.
        """
        job = self.repository.get_job(job_id)
        if not job:
            return
        self.update_job(job_id, input_metrics={**(job.input_metrics or {}), **updates})

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
        self,
        target_id: str,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
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

    def delete_job(self, job_id: str) -> bool:
        """Permanently delete a job record and its associated log file.

        Returns
        -------
        deleted : `bool`
            `True` if the job record was deleted, `False` otherwise.
        """
        job = self.get_job(job_id)
        if job and job.log_file_path:
            self._delete_log_file(job_id, Path(job.log_file_path))

        return self.repository.delete_job(job_id)

    def _delete_log_file(self, job_id: str, path: Path) -> None:
        """Delete one job's log file, but only inside the folder this app owns.

        A stored path outside that folder is left alone and logged at debug
        level. The record can name a file this run does not own, for example
        in a simulated world whose database was copied from the real one, and
        skipping it is not a failure.

        Parameters
        ----------
        job_id : `str`
            The job the log file belongs to (for the log message).
        path : `pathlib.Path`
            The path stored in the job record.
        """
        if not path.is_absolute():
            from astrometricslib import get_configuration

            path = get_configuration().get_project_root() / path
        folder = self._owned_logs_folder()
        if not is_path_inside(str(folder.resolve()), str(path.resolve())):
            logger.debug("Left the log file of job %s alone: %s is outside %s", job_id, path, folder)
            return
        if path.exists():
            try:
                os.remove(str(path))
                logger.info("Deleted log file for job %s: %s", job_id, path)
            except OSError:
                logger.exception("Error removing log file %s", path)

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
        NotFoundError
            If no job exists for ``job_id``.
        ConflictError
            If the job does not reach a terminal state within
            ``timeout`` seconds.
        """
        import asyncio

        start_time = datetime.now()
        while True:
            job = self.get_job(job_id)
            if not job:
                raise NotFoundError(f"Job {job_id} not found")

            if job.status in ["completed", "failed", "cancelled"]:
                return job

            if (datetime.now() - start_time).total_seconds() > timeout:
                raise ConflictError(f"Job {job_id} is still running after {timeout} seconds.", retryable=True)

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
