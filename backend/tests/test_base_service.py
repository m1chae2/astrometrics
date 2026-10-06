"""Unit tests for BaseBackgroundService job tracking.

Checks how a background task's ending is recorded: a task fails by
raising, and a falsy result also counts as failed.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib import ProcessingError
from backend.services.infrastructure.base_service import BaseBackgroundService


def _service_with_job_table() -> tuple[BaseBackgroundService, MagicMock]:
    """Build a service whose job table is a mock.

    Returns
    -------
    service : `BaseBackgroundService`
        The service under test.
    job_service : `unittest.mock.MagicMock`
        The mock job table.
    """
    job_service = MagicMock()
    job_service.create_job.return_value = MagicMock(id="job1")
    return BaseBackgroundService(max_workers=1, job_service=job_service), job_service


def test_task_that_returns_a_result_is_completed() -> None:
    """A task that returns a truthy value is recorded as completed."""
    service, job_service = _service_with_job_table()

    job_id = service._submit_job("T1", "analysis", lambda jid, tid: {"status": "finished"})
    service._jobs[job_id]["future"].result(timeout=5)

    job_service.update_job.assert_called_with("job1", status="completed", progress=100.0)
    assert service.get_all_processes()[0]["status"] == "finished"


def test_task_that_raises_is_failed() -> None:
    """A task that raises is recorded as failed with its message."""
    service, job_service = _service_with_job_table()

    def failing_task(jid: str, tid: str) -> None:
        """Fail the way a pipeline does.

        Raises
        ------
        ProcessingError
            Always.
        """
        raise ProcessingError("No stars found.")

    job_id = service._submit_job("T1", "analysis", failing_task)
    with pytest.raises(ProcessingError):
        service._jobs[job_id]["future"].result(timeout=5)

    job_service.update_job.assert_called_with("job1", status="failed", status_message="No stars found.")
    assert service.get_all_processes()[0]["status"] == "failed"


def test_task_that_returns_nothing_is_failed() -> None:
    """A task that returns None (no output) is recorded as failed."""
    service, job_service = _service_with_job_table()

    job_id = service._submit_job("T1", "stacking", lambda jid, tid: None)
    service._jobs[job_id]["future"].result(timeout=5)

    job_service.update_job.assert_called_with("job1", status="failed", progress=100.0)
