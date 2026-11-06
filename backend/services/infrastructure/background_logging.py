"""Purpose: Keep the log lines of background work out of running jobs' logs.

Description: The marker itself, `background_log_context`, lives in the public
`astrometricslib.jobs` module so both libraries and the backend share one
implementation. A job's log file is filled by the job log router, which copies
a line with no job id and no request id into every running job's log. The
marker gives background work (the status poll, the automatic frame download,
the cleanup loop) a request-style id, so its lines stay out of job logs. This
module re-exports it under its old name for the backend's callers.
"""

from astrometricslib.jobs import background_log_context

__all__ = ["background_log_context"]
