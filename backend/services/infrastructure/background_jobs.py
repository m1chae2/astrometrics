"""Purpose: Run a slow library call in a thread, and answer with its job.

Description: Some library calls the app starts take longer than a request
should wait: making a preview again, comparing two stacks, measuring the
calibration frames. `run_as_background_job` runs such a call in a thread of
its own. A call that ends within a short wait gives back its real result; a
slower one gives back the id of the job it recorded, which the app follows
with ``jobs:query``. The thread keeps running whatever the app does next.

Every pipeline run records its own job. This wrapper only listens for that
job with the library's public job context tools (`report_started_jobs`,
`registered_job`) and stores the call's result on it. The MCP servers have
their own copy (`mcp_servers.common.job_wrapper`), since the backend and the
MCP servers may not import each other.
"""

import dataclasses
import logging
import threading
from collections.abc import Callable
from typing import Any

from astrometricslib.jobs import registered_job, report_started_jobs

logger = logging.getLogger(__name__)

__all__ = ["JOB_RECORD_WAIT_SECONDS", "run_as_background_job", "to_plain"]

JOB_RECORD_WAIT_SECONDS = 3.0
"""How long to keep waiting, after the grace period, for slow work to record
its job row. A slow disk can take a second or two to write it."""


def to_plain(value: Any) -> Any:
    """Turn a result into plain data that JSON can hold.

    Parameters
    ----------
    value : `Any`
        The result: a pydantic model, a dataclass, or lists and dicts of
        them.

    Returns
    -------
    plain : `Any`
        The same data as dicts, lists and plain values.
    """
    if hasattr(value, "model_dump"):
        return to_plain(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


def run_as_background_job(
    work: Callable[[], Any],
    *,
    grace_period_seconds: float = 5.0,
    job_type: str | None = None,
    target_id: str | None = None,
) -> dict[str, Any]:
    """Run work in a background thread and answer with its result or its job.

    Parameters
    ----------
    work : `Callable` [[], `Any`]
        The library call, with no arguments.
    grace_period_seconds : `float`, optional
        How long to wait for the work to end before answering with the job
        id instead. Defaults to 5.
    job_type : `str`, optional
        Record the call as a job of this type; a pipeline it runs joins that
        job. Without it, the work records its own job, or none.
    target_id : `str`, optional
        The target the job works on, with ``job_type``. Defaults to the job
        type.

    Returns
    -------
    outcome : `dict` [`str`, `Any`]
        ``{"jobId", "result"}`` when the work ended within the grace period
        (the job id is `None` when it recorded no job), else
        ``{"status": "running", "jobId", "logFilePath", "message"}``.

    Notes
    -----
    Work that fails within the grace period raises its own error here. A
    later failure shows as a failed job.
    """
    finished = threading.Event()
    recorded = threading.Event()
    started_jobs: list[Any] = []
    outcome: dict[str, Any] = {}

    def remember_first_job(handle: Any) -> None:
        """Keep the first job the work records, the outermost one."""
        if not started_jobs:
            started_jobs.append(handle)
        recorded.set()

    def run() -> None:
        """Run the work, then store its result on its job."""
        try:
            with report_started_jobs(remember_first_job):
                if job_type is None:
                    outcome["result"] = work()
                else:
                    with registered_job(job_type=job_type, target_id=target_id or job_type):
                        outcome["result"] = work()
        except Exception as error:  # the thread is a boundary: the caller reads the error below
            outcome["error"] = error
            logger.debug("Background work failed.", exc_info=True)
        if "result" in outcome and started_jobs:
            job = started_jobs[0]
            # The work already decided how its job ended (stacking that makes
            # no image is "failed"); only the result is added.
            job.mark(
                job.terminal_status or "completed",
                100,
                output_metrics={"result": to_plain(outcome["result"])},
            )
        recorded.set()
        finished.set()

    threading.Thread(target=run, daemon=True).start()

    if finished.wait(timeout=grace_period_seconds):
        if "error" in outcome:
            raise outcome["error"]
        return {"jobId": started_jobs[0].job_id if started_jobs else None, "result": outcome["result"]}

    # The grace period is over, but the job row may still be being written.
    recorded.wait(timeout=JOB_RECORD_WAIT_SECONDS)
    job = started_jobs[0] if started_jobs else None
    return {
        "status": "running",
        "jobId": job.job_id if job else None,
        "logFilePath": job.log_file_path if job else None,
        "message": (
            f"Still running after {grace_period_seconds:.0f}s; follow it in the job list with this job id."
            if job
            else f"Still running after {grace_period_seconds:.0f}s in the background. It records no job "
            "to follow; check its effect once it has had time to finish."
        ),
    }
