"""Tests of the ``images:measure_star_profile`` call and the image folder rule.

The call is declared, wired to the image service, passes the library
arguments, and refuses an image outside the library folders. Drawing a FITS
file (``images:convert_fits``) follows the same folder rule.
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


def test_drawing_a_file_follows_the_same_folder_rule(tmp_path: Path) -> None:
    """``convert_fits`` draws a library file and refuses one from elsewhere."""
    library = tmp_path / "library"
    library.mkdir()
    astrometrics = MagicMock()
    astrometrics.visualization.render_fits.return_value = None
    service = _service(library, astrometrics)
    with pytest.raises(InvalidArgumentError):
        service.convert_fits("/etc/hostname")
    astrometrics.visualization.render_fits.assert_not_called()
    assert service.convert_fits(str(library / "frame.fits")) is None
    drawn = astrometrics.visualization.render_fits.call_args.args[0]
    assert drawn == str((library / "frame.fits").resolve())


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


def test_ring_fields_are_sent_as_plain_data(tmp_path: Path) -> None:
    """The ring analysis comes back with the profile as plain data."""
    library = tmp_path / "library"
    library.mkdir()
    rows, columns = np.mgrid[0:121, 0:121]
    image = 100.0 + 2000.0 * np.exp(-((columns - 60) ** 2 + (rows - 60) ** 2) / (2 * 1.8**2))
    image += np.random.default_rng(2).normal(0.0, 2.0, image.shape)
    path = library / "ring.fits"
    fits.PrimaryHDU(image.astype(np.float32)).writeto(path)
    result = _service(library, Astrometrics()).measure_star_profile(str(path), 61.0, 61.0)
    assert 0.0 < result["ring_inner_px"] < result["ring_outer_px"]
    assert [term["order"] for term in result["ring_harmonics"]] == list(range(1, 9))
    assert len(result["ring_bin_angle_degrees"]) == len(result["ring_bin_values"])
    assert type(result["ring_masked_fraction"]) is float
    assert isinstance(result["ring_neighbors"], list)
