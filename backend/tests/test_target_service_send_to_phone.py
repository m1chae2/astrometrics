"""Unit tests for TargetService.send_to_phone.

The method checks the target's JPEG picture and then hands it to
`send_file_to_phone`. These tests replace that function, so nothing is sent.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrometricslib import NotFoundError
from backend.services.data import target_service
from backend.services.data.target_service import TargetService


def _service_with(processed: str | None, stacked: str | None) -> TargetService:
    """Make a service whose one target has the given stack files.

    Parameters
    ----------
    processed : `str` or `None`
        The processed image path, or `None` for none.
    stacked : `str` or `None`
        The stacked FITS path, or `None` for none.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library.
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    stacking = service.astrometrics.targets.get.return_value.stacking
    stacking.processed_image = processed
    stacking.stacked_image = stacked
    return service


def test_send_to_phone_sends_the_stack_preview_not_the_processed_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer's FITS is not sent. The JPEG beside the stack is."""
    stacked = tmp_path / "M_57_Stacked.fits"
    preview = tmp_path / "M_57_Stacked_preview.jpg"
    preview.write_bytes(b"jpeg")
    sent = MagicMock(return_value={"method": "gsconnect", "device": "Pixel 9"})
    monkeypatch.setattr(target_service, "send_file_to_phone", sent)

    result = _service_with(str(tmp_path / "M_57_Stacked_processed.fits"), str(stacked)).send_to_phone("M 57")

    sent.assert_called_once_with(str(preview))
    assert result == {"path": str(preview), "method": "gsconnect", "device": "Pixel 9"}


def test_send_to_phone_sends_a_processed_jpeg_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-attached JPEG is the processed image and is sent unchanged."""
    picture = tmp_path / "mine.jpeg"
    picture.write_bytes(b"jpeg")
    sent = MagicMock(return_value={"method": "localsend"})
    monkeypatch.setattr(target_service, "send_file_to_phone", sent)

    _service_with(str(picture), None).send_to_phone("M 57")

    sent.assert_called_once_with(str(picture))


@pytest.mark.parametrize(
    ("processed", "stacked", "message"),
    [
        (None, None, "no stack"),
        (None, "/nowhere/M_57_Stacked.fits", "missing"),
    ],
)
def test_send_to_phone_refuses_a_picture_it_cannot_send(
    processed: str | None, stacked: str | None, message: str
) -> None:
    """No stack, or a missing JPEG, raises `NotFoundError`."""
    with pytest.raises(NotFoundError, match=message):
        _service_with(processed, stacked).send_to_phone("M 57")


def test_send_to_phone_refuses_an_unknown_target() -> None:
    """A target that is not in the library raises `NotFoundError`."""
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.get.return_value = None

    with pytest.raises(NotFoundError, match="No target named"):
        service.send_to_phone("M 999")
