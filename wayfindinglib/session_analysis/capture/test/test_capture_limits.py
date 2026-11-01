"""Purpose: Test that the capture limits are read from the library's rows.

Description: `read_capture_limits` takes the saturation limits from the
declaration of the saturated-pixel metric and the dark tolerance from a
calibration query. The test uses the library's real metric declaration, so
a change of a library limit shows up here.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.quality import QualityRecords
from astrometricslib.exceptions import ConfigurationError
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.catalog_queries import CalibrationQueryResult
from wayfindinglib.session_analysis.capture.limits import read_capture_limits
from wayfindinglib.session_analysis.capture.test.conftest import CAPTURE_LIMITS


def _library(tolerance: float | None) -> SimpleNamespace:
    """Build a library stand-in with the real metric declarations.

    Parameters
    ----------
    tolerance : `float` or `None`
        The dark tolerance the calibration query reports.

    Returns
    -------
    library : `types.SimpleNamespace`
        An object with ``quality.metrics`` and the calibration query.
    """
    records = QualityRecords(AppConfiguration(), MagicMock())
    return SimpleNamespace(
        quality=SimpleNamespace(metrics=records.metrics),
        processing=SimpleNamespace(
            calibration=SimpleNamespace(
                query=lambda **_: CalibrationQueryResult(
                    detail="counts", dark_temperature_tolerance_c=tolerance
                )
            )
        ),
    )


def test_the_limits_come_from_the_librarys_own_rows() -> None:
    """The values equal the library's saturation constants and tolerance."""
    assert read_capture_limits(_library(3.0)) == CAPTURE_LIMITS


def test_a_library_without_a_tolerance_is_refused() -> None:
    """A missing tolerance is a fault of the library, not a quiet default."""
    with pytest.raises(ConfigurationError):
        read_capture_limits(_library(None))
