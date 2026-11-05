"""Purpose: Keep the log lines of background work out of running jobs' logs.

Description: A job's log file is filled by the job log router. A line with no
job id and no request id might come from job work that lost its context, so
the router copies it into every running job's log. The backend's own
background work (the status poll, the automatic frame download, the cleanup
loop) is not job work. This module gives that work a request-style id, which
the router reads as "this belongs to something else; do not copy it into a
job".
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager

from astrometricslib.jobs import log_context


@contextmanager
def background_log_context(name: str, **fields: str | None) -> Generator[None]:
    """Mark the log lines written inside the block as background work.

    A job started inside the block (for example a quality job) still writes
    to its own log, because its job id takes priority over this marker.

    Parameters
    ----------
    name : `str`
        What the work is, such as ``sync`` or ``telemetry``. It appears in
        the log line's ``request_id``.
    **fields : `str` or `None`
        More context fields, such as ``target_id``.

    Yields
    ------
    None
        The block runs with the marker.
    """
    with log_context(request_id=f"background:{name}:{uuid.uuid4().hex[:6]}", **fields):
        yield
