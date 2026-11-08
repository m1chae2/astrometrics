"""Unit tests for deleting frames through the image service.

A deleted frame must not leave its stored quality measurements behind,
whether or not the caller names the frame's target.
"""

from unittest.mock import MagicMock

from astrometricslib import Target
from backend.services.data.image_service import ImageService


def _service(deleted: list[str], target: Target | None) -> tuple[ImageService, MagicMock]:
    """Make an image service over a fake library handle.

    Returns
    -------
    service, astrometrics : `ImageService`, `~unittest.mock.MagicMock`
        The service and the fake handle it uses.
    """
    astrometrics = MagicMock()
    astrometrics.targets.delete_images.return_value.model_dump.return_value = {
        "deleted": deleted,
        "failed": [],
    }
    astrometrics.targets.get.return_value = target
    target_service = MagicMock()
    target_service.astrometrics = astrometrics
    return ImageService(target_service), astrometrics


def test_delete_without_target_forgets_measurements() -> None:
    """The target is read from the lights folder; its numbers are forgotten."""
    path = "/frames/lights/M 13/scope/cam/M_13_Light_001.fits"
    service, astrometrics = _service([path], Target(id="M 13"))

    result = service.delete_images([path])

    assert result["deleted"] == [path]
    astrometrics.targets.get.assert_called_once_with("M 13")
    astrometrics.quality.forget.assert_called_once_with(["M 13/scope/cam/M_13_Light_001.fits"])


def test_delete_with_target_leaves_forgetting_to_the_library() -> None:
    """With a target named, the library forgets; the service does not."""
    path = "/frames/lights/M 13/scope/cam/M_13_Light_001.fits"
    service, astrometrics = _service([path], Target(id="M 13"))

    service.delete_images([path], target_id="M 13")

    astrometrics.quality.forget.assert_not_called()


def test_delete_of_an_unknown_target_forgets_nothing() -> None:
    """A path of a target not in the library forgets nothing."""
    path = "/frames/lights/Nowhere/scope/cam/x.fits"
    service, astrometrics = _service([path], None)

    service.delete_images([path])

    astrometrics.quality.forget.assert_not_called()
