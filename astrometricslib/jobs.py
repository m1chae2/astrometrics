"""Purpose: Job tracking and the logging context of a job.

Description: The `Jobs` class that `Astrometrics.jobs` is an instance of, the
job store and its records, the job context tools (`registered_job`,
`running_as_job`, `get_current_job`, `report_started_jobs` and the
`background_job` marker that tells a client a method is slow), the switch that
gives up on a running job (`RunAbandonment`, `raise_if_abandoned`), the program
identity that a job records as its owner (`current_process_identity`,
`process_is_alive`), and the logging context, including the marker for
background work. A pipeline run records a job whoever starts it; a client that
wants a slow call to answer with a job id runs it in a thread of its own
around these tools. Each name is imported from the module where its code lives
today.
"""

from typing import NoReturn

from astrometricslib._internal.renames import moved_module_name
from astrometricslib.api.jobs import Jobs
from astrometricslib.foundation.jobs.background import run_as_background_job
from astrometricslib.foundation.jobs.models import ProcessingJob
from astrometricslib.foundation.jobs.process_identity import current_process_identity, process_is_alive
from astrometricslib.foundation.jobs.runner import (
    background_job,
    capture_job_logs,
    close_interrupted_jobs,
    get_current_job,
    registered_job,
    report_started_jobs,
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


def __getattr__(name: str) -> NoReturn:
    """Refuse a name this module no longer offers, saying where it went.

    Parameters
    ----------
    name : `str`
        The name that was read.

    Notes
    -----
    Always raises: an `ImportError` that says where the name went for a
    name in the rename table, and an `AttributeError` for any other name.
    """
    moved_module_name(__name__, name)
