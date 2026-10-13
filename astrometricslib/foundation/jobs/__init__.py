"""Purpose: The job framework that every part of the repository shares.

Description: A job is a slow piece of work that the app lists with its
progress and its own log. This package holds everything about jobs:

* `models`: `ProcessingJob`, one row of the job list.
* `store`: `JobStore`, which keeps the job list and each job's log lines in
  the logs database, and `DbLogHandler`, which writes log records there.
* `runner`: `registered_job` and `capture_job_logs`, which record a job and
  collect its log; `get_current_job`; the `background_job` marker and
  `run_as_background_job`; and `close_interrupted_jobs`, which closes the
  jobs a program left open when it ended.
* `process_identity`: tells whether the program that owns a job still runs.

Import these names from the top-level `astrometricslib` package.
"""

from astrometricslib.foundation.jobs.models import ProcessingJob, ProcessStatus
from astrometricslib.foundation.jobs.runner import (
    JOB_LOG_PACKAGES,
    JobHandle,
    background_job,
    capture_job_logs,
    close_interrupted_jobs,
    get_current_job,
    recover_interrupted_jobs,
    register_interrupted_job_cleanup,
    registered_job,
    run_as_background_job,
)
from astrometricslib.foundation.jobs.store import DbLogHandler, JobStore

__all__ = [
    "JOB_LOG_PACKAGES",
    "DbLogHandler",
    "JobHandle",
    "JobStore",
    "ProcessStatus",
    "ProcessingJob",
    "background_job",
    "capture_job_logs",
    "close_interrupted_jobs",
    "get_current_job",
    "recover_interrupted_jobs",
    "register_interrupted_job_cleanup",
    "registered_job",
    "run_as_background_job",
]
