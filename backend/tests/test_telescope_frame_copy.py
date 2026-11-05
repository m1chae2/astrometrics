"""Purpose: Check the telescope status reports the copy of new frames.

Description: `TelescopeService.get_status` adds ``frameCopy``. It says
whether a copy of the target's frames is running and, while the measuring
job runs, how many frames are measured out of how many. These tests use fake
sync and job services.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.services.infrastructure.system_status_service import TelescopePulse
from backend.services.observatory.telescope_service import TelescopeService


def _service(syncing: bool, jobs: list[object]) -> tuple[TelescopeService, MagicMock, MagicMock]:
    """Build a telescope service over a fake Wayfinder with fake copy sources.

    Returns
    -------
    service : `TelescopeService`
        The service under test.
    sync_service : `MagicMock`
        The fake sync service.
    job_service : `MagicMock`
        The fake job service.
    """
    wayfinder = MagicMock()
    wayfinder.control.mount.status.return_value.model_dump.return_value = {"targetName": "M 31"}
    wayfinder.control.imaging.status.return_value.camera_status = "Idle"
    sync_service = MagicMock()
    sync_service.is_syncing.return_value = syncing
    job_service = MagicMock()
    job_service.get_jobs_for_target.return_value = jobs
    service = TelescopeService(wayfinder=wayfinder)
    service.set_frame_copy_sources(sync_service, job_service)
    return service, sync_service, job_service


def test_idle_status_is_cheap() -> None:
    """With no copy running, the job list is not read."""
    service, _sync, job_service = _service(False, [])
    assert service.get_status()["frameCopy"] == {
        "active": False,
        "targetId": "M 31",
        "measured": None,
        "total": None,
    }
    job_service.get_jobs_for_target.assert_not_called()


def test_copy_without_a_measuring_job_has_no_counts() -> None:
    """During the file copy the status is active with no counts."""
    service, _sync, _jobs = _service(True, [])
    status = service.get_status()["frameCopy"]
    assert status["active"] is True
    assert status["measured"] is None and status["total"] is None


def test_running_quality_job_gives_counts() -> None:
    """The counts come from the message of the running quality job."""
    running = SimpleNamespace(status="running", message="Measured 24 of 40 frames")
    service, _sync, job_service = _service(True, [running])
    status = service.get_status()["frameCopy"]
    assert (status["active"], status["measured"], status["total"]) == (True, 24, 40)
    job_service.get_jobs_for_target.assert_called_once_with("M 31", job_type="quality", limit=3)


def test_running_quality_job_without_a_count_reports_zero_measured() -> None:
    """A measuring job that has not posted a count yet still means "analyzing".

    The job posts "Measured X of Y" only after a frame is done, so during a
    single-frame measurement the message is empty.
    """
    running = SimpleNamespace(status="running", message=None)
    service, _sync, _jobs = _service(True, [running])
    status = service.get_status()["frameCopy"]
    assert (status["active"], status["measured"], status["total"]) == (True, 0, None)


def test_finished_quality_job_is_ignored() -> None:
    """An old finished job does not give counts."""
    done = SimpleNamespace(status="completed", message="Measured 40 of 40 frames")
    service, _sync, _jobs = _service(True, [done])
    assert service.get_status()["frameCopy"]["measured"] is None


def test_status_without_sources_is_inactive() -> None:
    """A service with no copy sources reports no copy."""
    wayfinder = MagicMock()
    wayfinder.control.mount.status.return_value.model_dump.return_value = {"targetName": "M 31"}
    assert TelescopeService(wayfinder=wayfinder).get_status()["frameCopy"]["active"] is False


def test_pulse_model_carries_frame_copy() -> None:
    """The telescope pulse model accepts and keeps the ``frameCopy`` field."""
    pulse = TelescopePulse(frameCopy={"active": True, "targetId": "M 31", "measured": 1, "total": 2})
    assert pulse.frame_copy is not None and pulse.frame_copy.total == 2
