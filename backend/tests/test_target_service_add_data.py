"""Unit tests for TargetService.add_target_data.

A bad image file raises InvalidArgumentError before any target is
created, instead of returning an error dictionary.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.exceptions import InvalidArgumentError
from backend.services.data.target_service import TargetService


def test_add_target_data_rejects_a_non_path() -> None:
    """An image file that is not a path raises and creates no target."""
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()

    with pytest.raises(InvalidArgumentError, match="file path"):
        service.add_target_data("M31", image_file=42)

    service.astrometrics.targets.create.assert_not_called()


def test_add_target_data_lets_the_library_place_a_finished_picture() -> None:
    """A picture goes to the library, which names it the processed image."""
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()

    result = service.add_target_data("M31", image_file="/m31.png")

    assert result == {"status": "success"}
    target = service.astrometrics.targets.get.return_value
    service.astrometrics.targets.update.assert_called_once_with(target, processed_image="/m31.png")
    service.astrometrics.targets.frames.add.assert_not_called()


def test_add_target_data_adds_a_fits_file_as_a_frame() -> None:
    """A FITS file goes to the library's frame add, with the camera."""
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    target = service.astrometrics.targets.get.return_value
    target.frames = []

    result = service.add_target_data("M31", image_file="/m31.fits", camera="ZWO")

    service.astrometrics.targets.frames.add.assert_called_once_with(target, ["/m31.fits"], camera="ZWO")
    assert result["status"] == "success"
