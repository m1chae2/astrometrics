"""Tests of the ``images:measure_star_profile`` call.

The call is declared, wired to the image service, passes the library
arguments, and refuses an image outside the library folders.
"""

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
    target_service = SimpleNamespace(config=config, astrometrics=astrometrics)
    return ImageService(target_service=target_service)


def test_call_is_declared_and_wired() -> None:
    """The call is in the public interface and the registry."""
    assert "images:measure_star_profile" in RPC_METHODS
    assert rpc_registry._handlers["images:measure_star_profile"] == ("image_service", "measure_star_profile")


def test_outside_path_is_refused(tmp_path: Path) -> None:
    """An image outside the library folders is not read."""
    library = tmp_path / "library"
    library.mkdir()
    service = _service(library, MagicMock())
    with pytest.raises(InvalidArgumentError):
        service.measure_star_profile("/etc/hostname", 10.0, 10.0)


def test_measures_a_star_in_the_library(tmp_path: Path) -> None:
    """A star in a library file is measured and sent as plain data."""
    library = tmp_path / "library"
    library.mkdir()
    rows, columns = np.mgrid[0:61, 0:61]
    image = 100.0 + 2000.0 * np.exp(-((columns - 30) ** 2 + (rows - 30) ** 2) / (2 * 1.8**2))
    path = library / "star.fits"
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    service = _service(library, Astrometrics())
    result = service.measure_star_profile(str(path), 31.0, 31.0, radius_px=10.0, cut_angle_degrees=45.0)
    assert result["gaussian"]["fwhm_px"] == pytest.approx(2.3548 * 1.8, rel=0.05)
    assert isinstance(result["cut_values"], list)
    assert type(result["center_x"]) is float
