"""Purpose: Test `calibration.get_profile`.

Description: The call gives the stored noise profile of a `Camera`. These
tests check that it reads the catalog's own configuration, that it matches
the camera's aliases when its name has no profile, and that no camera, or a
camera with no profile, gives the generic fallback.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib import Camera
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.pipelines.stacking import calibration_catalog


def _camera(name: str, *aliases: str) -> Camera:
    """Make a camera with the given name and other spellings.

    Returns
    -------
    camera : `Camera`
        A small camera.
    """
    return Camera(
        id=name, name=name, pixel_size_um=3.76, sensor_width_px=3008, sensor_height_px=3008, aliases=aliases
    )


def test_get_profile_passes_its_own_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lookup reads the configuration the catalog was built with."""
    config = AppConfiguration()
    profile = MagicMock(spec=CameraProfile, is_generic_fallback=False)
    lookup = MagicMock(return_value=profile)
    monkeypatch.setattr(calibration_catalog, "resolve_camera_profile", lookup)

    catalog = CalibrationCatalog(config, MagicMock())

    assert catalog.get_profile(_camera("ZWO ASI 533MM Pro")) is profile
    lookup.assert_called_once_with("ZWO ASI 533MM Pro", config)


def test_get_profile_tries_the_aliases_after_the_name() -> None:
    """A camera whose name has no profile is found by an alias."""
    catalog = CalibrationCatalog(AppConfiguration(), MagicMock())

    profile = catalog.get_profile(_camera("My Mono Camera", "ZWO ASI 533MM Pro"))

    assert profile.is_generic_fallback is False
    assert profile == catalog.get_profile(_camera("ZWO ASI 533MM Pro"))


def test_no_camera_or_an_unknown_camera_gives_the_generic_fallback() -> None:
    """`None` or a camera with no profile gives the generic profile."""
    catalog = CalibrationCatalog(AppConfiguration(), MagicMock())

    unnamed = catalog.get_profile()

    assert isinstance(unnamed, CameraProfile)
    assert unnamed.is_generic_fallback is True
    assert catalog.get_profile(_camera("No Such Camera 9000")).is_generic_fallback is True
