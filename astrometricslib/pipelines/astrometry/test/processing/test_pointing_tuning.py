"""Tests for the tuner of the pointing error limits.

The limits get the four pointing errors of the 2026-10-04 spectral stacks
(22, 24, 24 and 138 arcseconds), which the register entry AST-09 lists.
"""

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.astrometry.processing.pointing_tuning import (
    POINTING_TUNER,
    pointing_error_arcsec,
    pointing_limits,
)

SETUP = SetupConfig(name="Setup", camera_name="Camera", optic_name="Optic")
ERRORS = [22.0, 24.0, 24.0, 138.0]


def test_pointing_error_of_one_arcminute_in_declination() -> None:
    """Positions 1/60 degree apart in declination are 60 arcseconds apart."""
    assert pointing_error_arcsec(100.0, 10.0, 100.0, 10.0 + 1 / 60) == pytest.approx(60.0, abs=1e-6)


def test_search_radius_is_a_margin_above_the_largest_error() -> None:
    """A margin of 2 on a 138 arcsecond error gives 276 arcseconds."""
    limits = pointing_limits(ERRORS, [1.92] * 4, [3008.0] * 4, 2.0, 2.0, 5.0)
    assert limits["astrometry.pointing_error_search_radius_arcsec"] == pytest.approx(276.0)


def test_centre_fraction_is_the_radius_in_pixels_over_the_short_side() -> None:
    """The radius in pixels, doubled, over the shorter side."""
    limits = pointing_limits(ERRORS, [1.92] * 4, [3008.0] * 4, 2.0, 2.0, 5.0)
    assert limits["astrometry.centre_star_search_radius_fraction"] == pytest.approx(
        2.0 * (276.0 / 1.92) / 3008.0
    )


def test_warning_ignores_the_one_large_error() -> None:
    """The warning sits just above the typical 24, not near the 138."""
    limits = pointing_limits(ERRORS, [1.92] * 4, [3008.0] * 4, 2.0, 2.0, 5.0)
    assert 24.0 < limits["astrometry.hint_match_warning_arcsec"] < 40.0


def test_warning_spread_is_at_least_one_pixel() -> None:
    """Identical errors still leave a warning a few pixels above."""
    limits = pointing_limits([10.0, 10.0, 10.0], [2.0] * 3, [3000.0] * 3, 2.0, 2.0, 5.0)
    assert limits["astrometry.hint_match_warning_arcsec"] == pytest.approx(10.0 + 5.0 * 2.0)


def test_tuner_skips_without_the_library() -> None:
    """With no library service given, the tuner raises the skip error."""
    with pytest.raises(TuningSkippedError):
        POINTING_TUNER.tune(TuningInputs(SETUP, None, None, {}))
