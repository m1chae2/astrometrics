"""Purpose: Check the telescope poll feeds the automatic download service.

Description: `TelescopeService.get_status` passes the camera status and the
target name on, and works the same with no service attached.
"""

from unittest.mock import MagicMock

from backend.services.observatory.telescope_service import TelescopeService


def _service(auto_download: object | None) -> TelescopeService:
    """Build a telescope service over a fake Wayfinder.

    Returns
    -------
    service : `TelescopeService`
        The service under test.
    """
    wayfinder = MagicMock()
    wayfinder.control.mount.status.return_value.model_dump.return_value = {"targetName": "M 31"}
    wayfinder.control.imaging.status.return_value.camera_status = "Idle"
    return TelescopeService(wayfinder=wayfinder, auto_download_service=auto_download)


def test_status_is_passed_to_the_auto_download_service() -> None:
    """The camera status and the target reach the service."""
    auto_download = MagicMock()
    _service(auto_download).get_status()
    auto_download.on_camera_status.assert_called_once_with("Idle", "M 31")


def test_status_works_without_the_service() -> None:
    """No service means the feature is off and the status is still built."""
    assert _service(None).get_status()["cameraStatus"] == "Idle"
