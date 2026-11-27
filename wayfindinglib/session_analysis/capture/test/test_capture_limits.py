"""Purpose: Test that the capture limits are read from the library.

Description: `read_capture_limits` takes the saturation limits from the
declaration of the saturated-pixel metric and the dark tolerance from the
library's tuned parameters. The test uses the library's real metric
declaration and parameter, so a change of a library limit shows up here.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.quality import QualityRecords
from astrometricslib.exceptions import ConfigurationError
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.parameters import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C, resolve_parameter
from wayfindinglib.session_analysis.capture.limits import read_capture_limits
from wayfindinglib.session_analysis.capture.test.conftest import CAPTURE_LIMITS


def _library(metrics: object = None) -> SimpleNamespace:
    """Build a library stand-in with the real metric declarations.

    Parameters
    ----------
    metrics : `Callable`, optional
        A stand-in for ``quality.metrics``. The real one when left out.

    Returns
    -------
    library : `types.SimpleNamespace`
        An object with ``quality.metrics``.
    """
    records = QualityRecords(AppConfiguration(), MagicMock())
    return SimpleNamespace(quality=SimpleNamespace(metrics=metrics or records.metrics))


def test_the_limits_come_from_the_librarys_own_rows() -> None:
    """The values equal the library's saturation constants and tolerance."""
    limits = read_capture_limits(_library())

    assert limits == CAPTURE_LIMITS
    assert (
        limits.dark_temperature_tolerance_c == resolve_parameter(DEFAULT_DARK_TEMPERATURE_TOLERANCE_C).value
    )


def test_a_library_without_the_saturation_limits_is_refused() -> None:
    """A missing limit is a fault of the library, not a quiet default."""
    declaration = SimpleNamespace(limits=[])
    with pytest.raises(ConfigurationError):
        read_capture_limits(_library(lambda **_: [declaration]))
