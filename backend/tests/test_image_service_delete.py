"""Purpose: Unit tests for deleting frames through the image service.

Description: The service hands the paths, and the target when the app names
one, to the library's ``targets.frames.delete``, which also clears the
deleted frames' stored quality measurements. The service no longer reads a
target out of a path itself.
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
    astrometrics.targets.frames.delete.return_value.model_dump.return_value = {
        "deleted": deleted,
        "failed": [],
    }
    astrometrics.targets.get.return_value = target
    target_service = MagicMock()
    target_service.astrometrics = astrometrics
    return ImageService(target_service), astrometrics


def test_delete_without_target_lets_the_library_find_it() -> None:
    """With no target named, the library gets `None` and the paths."""
    path = "/frames/lights/M 13/scope/cam/M_13_Light_001.fits"
    service, astrometrics = _service([path], Target(id="M 13"))

    result = service.delete_images([path])

    assert result["deleted"] == [path]
    astrometrics.targets.get.assert_not_called()
    astrometrics.targets.frames.delete.assert_called_once_with(None, [path])


def test_delete_with_target_passes_the_target() -> None:
    """With a target named, the service looks it up and passes it on."""
    path = "/frames/lights/M 13/scope/cam/M_13_Light_001.fits"
    target = Target(id="M 13")
    service, astrometrics = _service([path], target)

    service.delete_images([path], target_id="M 13")

    astrometrics.targets.get.assert_called_once_with("M 13")
    astrometrics.targets.frames.delete.assert_called_once_with(target, [path])
