"""Purpose: Job tracking and the logging context of a job.

Description: The `Jobs` class that `Astrometrics.jobs` is an instance of, the
job store and its records, the decorators and helpers that run a function as a
tracked background job, and the logging context, including the marker for
background work. Each name is imported from the module where its code lives
today.
"""

from astrometricslib.api.jobs import Jobs
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.runner import (
    background_job,
    capture_job_logs,
    close_interrupted_jobs,
    get_current_job,
    registered_job,
    run_as_background_job,
    running_as_job,
)
from astrometricslib.foundation.jobs.store import DbLogHandler, JobStore
from astrometricslib.foundation.logging import background_log_context, log_context, new_request_id

__all__ = [
    "DbLogHandler",
    "JobStore",
    "Jobs",
    "ProcessingJob",
    "background_job",
    "background_log_context",
    "capture_job_logs",
    "close_interrupted_jobs",
    "get_current_job",
    "log_context",
    "new_request_id",
    "registered_job",
    "run_as_background_job",
    "running_as_job",
]
