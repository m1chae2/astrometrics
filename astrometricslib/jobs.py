"""Purpose: Job tracking and the logging context of a job.

Description: The `Jobs` class that `Astrometrics.jobs` is an instance of, the
job store and its records, the decorators and helpers that run a function as a
tracked background job, the switch that gives up on a running job
(`RunAbandonment`), the program identity that a job records as its owner
(`current_process_identity`, `process_is_alive`), and the logging context,
including the marker for background work. Each name is imported from the
module where its code lives today.
"""

from astrometricslib.api.jobs import Jobs
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.process_identity import current_process_identity, process_is_alive
from astrometricslib.foundation.jobs.runner import (
    background_job,
    capture_job_logs,
    close_interrupted_jobs,
    get_current_job,
    registered_job,
    report_started_jobs,
    run_as_background_job,
    running_as_job,
)
from astrometricslib.foundation.jobs.store import DbLogHandler, JobStore
from astrometricslib.foundation.logging import (
    SkipJobStoredRecords,
    background_log_context,
    log_context,
    new_request_id,
)
from astrometricslib.foundation.run_abandonment import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    raise_if_abandoned,
)

__all__ = [
    "DbLogHandler",
    "JobStore",
    "Jobs",
    "ProcessingJob",
    "RunAbandonedError",
    "RunAbandonment",
    "SkipJobStoredRecords",
    "background_job",
    "background_log_context",
    "bind_abandonment",
    "capture_job_logs",
    "close_interrupted_jobs",
    "current_process_identity",
    "get_current_job",
    "log_context",
    "new_request_id",
    "process_is_alive",
    "raise_if_abandoned",
    "registered_job",
    "report_started_jobs",
    "run_as_background_job",
    "running_as_job",
]
