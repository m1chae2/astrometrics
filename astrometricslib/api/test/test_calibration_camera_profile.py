"""Purpose: Test `CalibrationCatalog.camera_profile`.

Description: The call gives callers the stored camera profile for a camera
name written in any known spelling. These tests check that it passes the
catalog's own configuration to the profile lookup and that a real lookup
returns a `CameraProfile`, with the generic fallback for an unknown name.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib._internal.drivers.calibration_library import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
from astrometricslib.api import processing
from astrometricslib.api.processing import CalibrationCatalog
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.camera_profile import CameraProfile


def test_camera_profile_passes_its_own_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lookup reads the configuration the catalog was built with."""
    config = AppConfiguration()
    profile = MagicMock(spec=CameraProfile)
    lookup = MagicMock(return_value=profile)
    monkeypatch.setattr(processing, "resolve_camera_profile", lookup)

    catalog = CalibrationCatalog(config, MagicMock())

    assert catalog.camera_profile("ZWO ASI 533MM Pro") is profile
    lookup.assert_called_once_with("ZWO ASI 533MM Pro", config)


def test_camera_profile_gives_a_profile_and_the_generic_fallback() -> None:
    """A missing or unlisted name gives the generic profile."""
    catalog = CalibrationCatalog(AppConfiguration(), MagicMock())

    unnamed = catalog.camera_profile(None)

    assert isinstance(unnamed, CameraProfile)
    assert unnamed.is_generic_fallback is True
    assert catalog.camera_profile("No Such Camera 9000").is_generic_fallback is True


def test_calibration_query_states_the_dark_temperature_tolerance() -> None:
    """Every answer holds the tolerance the library matches darks with."""
    catalog = CalibrationCatalog(AppConfiguration(), MagicMock())
    catalog._library = MagicMock()
    catalog._library.get_stats.return_value = {}

    answer = catalog.query()

    assert answer.dark_temperature_tolerance_c == DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
