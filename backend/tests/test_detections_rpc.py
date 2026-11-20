"""Tests of the ``images:measure_detections`` call.

The call is declared, wired to the image service, refuses an image outside
the library folders, sends plain data, and keeps its answer until the file
changes.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import Astrometrics
from astrometricslib.exceptions import InvalidArgumentError
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.data.image_service import ImageService


def _service(root: Path, astrometrics: object) -> ImageService:
    """Build an image service whose library folder is ``root``.

    Returns
    -------
    service : `ImageService`
        The service.
    """
    config = SimpleNamespace(get_library_path=lambda: str(root))
    return ImageService(target_service=SimpleNamespace(config=config, astrometrics=astrometrics))


def _write_stars(path: Path) -> None:
    """Write a small image with a few bright stars."""
    rows, columns = np.mgrid[0:150, 0:150]
    image = np.random.default_rng(1).normal(100.0, 2.0, size=(150, 150))
    for x, y in ((30, 40), (100, 50), (60, 110)):
        image += 3000.0 * np.exp(-((columns - x) ** 2 + (rows - y) ** 2) / (2 * 1.7**2))
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path, overwrite=True)


def test_call_is_declared_and_wired() -> None:
    """The call is in the public interface and the registry."""
    assert "images:measure_detections" in RPC_METHODS
    assert rpc_registry._handlers["images:measure_detections"] == ("image_service", "measure_detections")


def test_outside_path_is_refused(tmp_path: Path) -> None:
    """An image outside the library folders is not read."""
    library = tmp_path / "library"
    library.mkdir()
    with pytest.raises(InvalidArgumentError):
        _service(library, MagicMock()).measure_detections("/etc/hostname")


def test_detections_are_plain_data_and_cached(tmp_path: Path) -> None:
    """The stars come back as plain data; a second call reuses the answer."""
    library = tmp_path / "library"
    library.mkdir()
    path = library / "stars.fits"
    _write_stars(path)
    astrometrics = Astrometrics()
    service = _service(library, astrometrics)
    first = service.measure_detections(str(path))
    assert first["detection_count"] >= 3
    assert type(first["detections"][0]["x"]) is float
    assert service.measure_detections(str(path)) is first


def test_changed_file_is_searched_again(tmp_path: Path) -> None:
    """A file with a new modified time is measured again."""
    library = tmp_path / "library"
    library.mkdir()
    path = library / "stars.fits"
    _write_stars(path)
    service = _service(library, Astrometrics())
    first = service.measure_detections(str(path))
    _write_stars(path)
    stat = path.stat()

    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert service.measure_detections(str(path)) is not first


def test_processed_picture_is_searched_on_its_linear_stack(tmp_path: Path) -> None:
    """A stack's ``_processed`` picture is searched on the stack beside it."""
    library = tmp_path / "library"
    library.mkdir()
    stack = library / "M_31_L_Stacked.fits"
    _write_stars(stack)
    picture = library / "M_31_L_Stacked_processed.fits"
    fits.PrimaryHDU(np.zeros((150, 150), dtype=np.float32)).writeto(picture)
    result = _service(library, Astrometrics()).measure_detections(str(picture))
    assert os.path.realpath(result["path"]) == os.path.realpath(stack)
    assert result["detection_count"] >= 3


def test_processed_picture_of_another_size_is_searched_itself(tmp_path: Path) -> None:
    """A picture whose size differs from the stack is searched as it is."""
    library = tmp_path / "library"
    library.mkdir()
    stack = library / "M_31_L_Stacked.fits"
    fits.PrimaryHDU(np.zeros((120, 150), dtype=np.float32)).writeto(stack)
    picture = library / "M_31_L_Stacked_processed.fits"
    _write_stars(picture)
    result = _service(library, Astrometrics()).measure_detections(str(picture))
    assert os.path.realpath(result["path"]) == os.path.realpath(picture)
