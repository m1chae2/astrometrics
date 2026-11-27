"""Purpose: Tests for the picture replies the app's viewer reads.

Description: The library draws a picture as bytes (`Visualization.render`).
`ImageService` turns them into the reply the viewer has always read: a data
URL under ``imageData`` and ``image_data``, the brightness range as ``min``
and ``max``, the file's FITS header cards and the automatic stretch. These
tests draw a small real FITS file through the library and check that shape.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import Astrometrics
from backend.services.data.image_service import ImageService


def _service(library: Path) -> ImageService:
    """Build an image service whose library allows ``library``.

    Parameters
    ----------
    library : `Path`
        The library folder.

    Returns
    -------
    service : `ImageService`
        The service, over a real `Astrometrics`.
    """
    config = MagicMock()
    config.get_library_path.return_value = str(library)
    config.get_frames_path.return_value = str(library / "frames")
    target_service = SimpleNamespace(astrometrics=Astrometrics(), config=config)
    return ImageService(target_service)


def test_a_drawn_file_has_the_viewers_reply_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply holds a PNG data URL, the range, the header, the stretch."""
    from backend.services.data import image_service

    frame = tmp_path / "frame.fits"
    data = np.random.default_rng(1).normal(1000.0, 20.0, (60, 80)).astype(np.float32)
    header = fits.Header()
    header["OBJECT"] = "M 13"
    fits.PrimaryHDU(data, header=header).writeto(frame)
    monkeypatch.setattr(image_service, "resolve_allowed_image", lambda path, config: path)

    reply = _service(tmp_path).convert_fits(str(frame), maxdim=200)

    assert reply["imageData"].startswith("data:image/png;base64,")
    assert reply["image_data"] == reply["imageData"]
    assert reply["id"] == ""
    assert reply["min"] < reply["max"]
    assert reply["path"] == str(frame)
    assert {"key": "OBJECT", "value": "M 13", "comment": ""} in reply["headers"]
    assert set(reply["stretchParameters"]) == {"blackPoint", "whitePoint", "midtones"}


def test_the_newest_frame_is_named_after_its_folder(tmp_path: Path) -> None:
    """The last-image panel names the picture after the frame's folder."""
    astrometrics = MagicMock()
    astrometrics.visualization.render_newest.return_value = None
    service = ImageService(SimpleNamespace(astrometrics=astrometrics, config=MagicMock()))
    assert service.get_last_image() is None

    real = Astrometrics()
    frame = tmp_path / "M_13" / "light_001.fits"
    frame.parent.mkdir()
    fits.PrimaryHDU(np.ones((20, 30), dtype=np.float32) * np.arange(30)).writeto(frame)
    astrometrics.visualization.render_newest.return_value = real.visualization.render(str(frame))
    astrometrics.targets.frames.get_header = real.targets.frames.get_header
    reply = service.get_last_image()
    assert reply["id"] == "M_13"
    assert reply["image_data"].startswith("data:image/png;base64,")
