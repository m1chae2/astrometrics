"""Purpose: Test that background work stays out of a running job's log."""

import logging
from io import StringIO

from astrometricslib.foundation.logging import get_job_log_router
from astrometricslib.jobs import log_context
from backend.services.infrastructure.background_logging import background_log_context


def _register(job_id: str) -> tuple[StringIO, str]:
    """Register a job sink that writes bare messages to a string.

    Returns
    -------
    stream : `StringIO`
        What the job's log received.
    sink_key : `str`
        The key to unregister the sink.
    """
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    key = get_job_log_router().register(job_id, [handler], ("astrometricslib", "wayfindinglib"))
    return stream, key


def test_background_lines_are_not_copied_into_a_running_job() -> None:
    """Keep a background library line out of the stacking job."""
    stream, key = _register("stacking")
    library_logger = logging.getLogger("wayfindinglib.background_test")
    library_logger.setLevel(logging.INFO)
    try:
        with background_log_context("sync", target_id="M_31"):
            library_logger.info("from the sync")
    finally:
        get_job_log_router().unregister(key)
    assert stream.getvalue() == ""


def test_the_jobs_own_lines_still_reach_it() -> None:
    """Send a job-tagged line to its job, even in a background context."""
    stream, key = _register("stacking")
    library_logger = logging.getLogger("astrometricslib.background_test")
    library_logger.setLevel(logging.INFO)
    try:
        with background_log_context("sync"), log_context(job_id="stacking"):
            library_logger.info("job work")
        library_logger.info("lost context")
    finally:
        get_job_log_router().unregister(key)
    assert stream.getvalue().splitlines() == ["job work", "lost context"]
