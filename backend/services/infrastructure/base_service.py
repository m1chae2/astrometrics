"""Base class for services that run background jobs on a thread pool."""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class BaseBackgroundService:
    """Base class for services that manage background tasks.

    Runs tasks on a `ThreadPoolExecutor` and provides unified job
    tracking and optional recording via `JobService`.
    """

    def __init__(self, max_workers: int | None = None, job_service=None, astrometrics_service=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._executor = ThreadPoolExecutor(max_workers=max_workers or os.cpu_count() or 4)
        # Unified job tracking: {job_id: {"future": Future, "type": str,
        # "target_id": str, "status": str}}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._job_service = job_service
        self._astrometrics_service = astrometrics_service

    def _update_central_processing_jobs(self):  # ruff: ignore[missing-return-type-private-function]
        """Update high-level interfaceservice with active jobs."""
        if self._astrometrics_service and self._job_service:
            active = self._job_service.get_active_jobs()
            jobs_payload = []
            for j in active:
                jobs_payload.append({"target_id": j.target_id, "job_id": j.id, "status": j.status})
            self._astrometrics_service.update_processing_jobs(jobs_payload)

    def _submit_job(self, target_id, job_type, task_fn, *args, log_file_path=None, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-args, missing-type-kwargs, missing-return-type-private-function]
        """Create a job record, submit the task, and return its id.

        Creates a `ProcessingJob` in the database (when a job service
        is configured), submits the task to the thread pool, and
        returns the job id.

        Returns
        -------
        job_id : `str`
            The id of the newly created or legacy in-memory job.
        """
        if not self._job_service:
            # Fallback to legacy behavior if job_service not provided
            job_id = str(uuid.uuid4())
            future = self._executor.submit(task_fn, job_id, target_id, *args, **kwargs)
            with self._lock:
                self._jobs[job_id] = {"future": future, "status": "started", "target_id": target_id}
            return job_id

        # Create job in database
        job = self._job_service.create_job(target_id=target_id, job_type=job_type, log_file=log_file_path)

        job_id = job.id

        # Wrap task to update database on completion/failure
        def job_wrapper(jid, tid, *a, **k):  # ruff: ignore[missing-type-function-argument, missing-type-args, missing-type-kwargs, missing-return-type-private-function]
            try:
                # Add log_file_path to kwargs if provided
                if log_file_path:
                    k["log_file_path"] = log_file_path
                result = task_fn(jid, tid, *a, **k)

                # Determine status based on result
                status = "completed"
                if not result:
                    status = "failed"
                elif isinstance(result, dict) and result.get("status") == "error":
                    status = "failed"
                elif isinstance(result, dict) and result.get("status") == "failed":
                    status = "failed"

                self._job_service.update_job(jid, status=status, progress=100.0)
                self._update_central_processing_jobs()
                return result
            except Exception as e:
                import traceback

                error_msg = f"{e!s}\n{traceback.format_exc()}"
                self._job_service.update_job(jid, status="failed", status_message=str(e))
                # Also log to file if it exists
                if log_file_path and os.path.exists(log_file_path):
                    with open(log_file_path, "a") as f:
                        f.write(f"\nFATAL ERROR: {error_msg}\n")
                self._update_central_processing_jobs()
                raise

        future = self._executor.submit(job_wrapper, job_id, target_id, *args, **kwargs)

        with self._lock:
            self._jobs[job_id] = {
                "future": future,
                "target_id": target_id,
                "type": job_type,
                "log_file": log_file_path,
            }

        self._update_central_processing_jobs()
        return job_id

    def get_job_status(self, job_id: str) -> dict | None:
        """Get the status of a specific job, prioritizing the database.

        Returns
        -------
        status : `dict` or `None`
            The job status dict, or `None` if `job_id` is unknown.
        """
        if self._job_service:
            job = self._job_service.get_job(job_id)
            if job:
                return job.model_dump(by_alias=True)

        # Fallback to memory
        with self._lock:
            return self._jobs.get(job_id)

    def get_target_jobs(self, target_id: str) -> list:
        """Get all jobs for a specific target from the database.

        Returns
        -------
        jobs : `list`
            The target's jobs, or an empty list if no job service is
            configured.
        """
        if self._job_service:
            jobs = self._job_service.get_jobs_for_target(target_id)
            return [j.model_dump() for j in jobs]
        return []

    def is_processing(self, job_id: str) -> bool:
        """Check if a specific job is active.

        Returns
        -------
        is_active : `bool`
            `True` if `job_id` exists and its future is not done.
        """
        with self._lock:
            if job_id in self._jobs:
                return not self._jobs[job_id]["future"].done()
        return False

    def get_all_processes(self) -> list[dict[str, Any]]:
        """Get a list of all active or recent jobs.

        Returns
        -------
        job_list : `list` of `dict`
            One status entry per tracked job.
        """
        job_list = []
        with self._lock:
            for jid, job in self._jobs.items():
                future = job["future"]
                if future.done():
                    if future.cancelled():
                        status = "cancelled"
                    else:
                        try:
                            # result() shouldn't block if done() is true
                            res = future.result(timeout=0)
                            status = "finished"
                            if not res:
                                status = "failed"
                            elif isinstance(res, dict) and res.get("status") in ["error", "failed"]:
                                status = "failed"
                        except Exception:
                            status = "failed"
                else:
                    status = "started"

                job_list.append({
                    "target_id": job.get("target_id"),
                    "job_id": jid,
                    "status": status,
                    "type": job.get("type"),
                    "progress": job.get("progress"),
                })
        return job_list

    def cancel_processing(self, job_id: str) -> bool:
        """Cancel a running job.

        Returns
        -------
        cancelled : `bool`
            `True` if the job was found and successfully cancelled.
        """
        with self._lock:
            if job_id in self._jobs:
                future = self._jobs[job_id]["future"]
                if not future.done():
                    return future.cancel()
        return False

    def _setup_worker_logger(self, name: str, log_file_path: str | None):  # ruff: ignore[missing-return-type-private-function]
        """Set up an isolated logger for a worker task.

        REQ: IMG-5.3

        Returns
        -------
        worker_logger : `logging.Logger` or `None`
            The configured logger, or `None` if `log_file_path` is
            not provided.
        """
        import logging

        if log_file_path:
            # Clear old logs for this target at the start of a new job
            handler = logging.FileHandler(log_file_path, mode="w")
            handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            worker_logger = logging.getLogger(name)
            worker_logger.propagate = False
            # Remove existing handlers to avoid duplicates if
            # re-using logger name
            for h in worker_logger.handlers[:]:
                worker_logger.removeHandler(h)
            worker_logger.addHandler(handler)
            worker_logger.setLevel(logging.INFO)
            return worker_logger
        return None

    def stream_log(self, job_id: str, log_file: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Yield log lines from `log_file` as they are written.

        REQ: IMG-5.3

        Yields
        ------
        line : `str`
            The next line read from `log_file`.
        """
        import time

        if not os.path.exists(log_file):
            yield f"Error: Log file not found at {log_file}\n"
            return

        try:
            with open(log_file) as f:
                # Send existing content
                f.seek(0)
                yield from f

                # Tail the file while processing is active
                while self.is_processing(job_id):
                    line = f.readline()
                    if line:
                        yield line
                    else:
                        time.sleep(0.5)

                # One last drain
                yield from f.readlines()
        except Exception as e:
            yield f"Error reading log: {e!s}\n"
