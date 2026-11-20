"""Tests for the tuner of the dark current allowance.

The fit gets dark levels that rise with exposure at a known rate, with and
without a step in the level and a group cut off at zero.
"""

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.stacking.pre_processing.dark_current_tuning import (
    DARK_CURRENT_TUNER,
    dark_current_rate,
    theil_sen_slope,
)

SETUP = SetupConfig(name="Setup", camera_name="Camera", optic_name="Optic")
EXPOSURES = [2.0, 10.0, 30.0, 60.0, 120.0]


def _levels(rate: float, bias: float = 400.0) -> dict[float, list[float]]:
    """Build ten frames per exposure whose level follows a known dark current.

    Returns
    -------
    levels : `dict` [`float`, `list` [`float`]]
        The frame levels by exposure.
    """
    return {exposure: [bias + rate * exposure] * 10 for exposure in EXPOSURES}


def _no_zeros() -> dict[float, list[float]]:
    """Build zero shares of 0 for every exposure.

    Returns
    -------
    zeros : `dict` [`float`, `list` [`float`]]
        Ten zero shares of 0.0 per exposure.
    """
    return {exposure: [0.0] * 10 for exposure in EXPOSURES}


def test_slope_of_a_straight_line() -> None:
    """Points on a line of slope 0.002 give that slope."""
    levels = [400.0 + 0.002 * exposure for exposure in EXPOSURES]
    assert theil_sen_slope(EXPOSURES, levels) == pytest.approx(0.002)


def test_slope_ignores_one_exposure_that_is_off() -> None:
    """A step at one exposure does not bend the median slope."""
    levels = [400.0 + 0.002 * exposure for exposure in EXPOSURES]
    levels[0] += 5.0
    assert theil_sen_slope(EXPOSURES, levels) == pytest.approx(0.002, abs=0.0005)


def test_dark_current_rate_recovers_the_rate() -> None:
    """The fit finds the known rate and the number of exposures it used."""
    fit = dark_current_rate(
        _levels(0.002), _no_zeros(), minimum_frames=10, minimum_exposures=4, zero_limit=0.001
    )
    assert fit is not None
    assert fit[0] == pytest.approx(0.002)
    assert fit[1] == len(EXPOSURES)


def test_exposures_with_too_few_frames_are_left_out() -> None:
    """Two thin exposures drop out, and three left are too few."""
    levels = _levels(0.002)
    levels[2.0] = levels[2.0][:3]
    levels[10.0] = levels[10.0][:3]
    assert dark_current_rate(levels, _no_zeros(), 10, 4, 0.001) is None


def test_a_group_cut_off_at_zero_is_left_out() -> None:
    """Frames cut off at zero are not used."""
    zeros = _no_zeros()
    zeros[120.0] = [0.5] * 10
    fit = dark_current_rate(_levels(0.002), zeros, 10, 4, 0.001)
    assert fit is not None
    assert fit[1] == len(EXPOSURES) - 1


def test_tuner_skips_without_the_library() -> None:
    """With no library service given, the tuner raises the skip error."""
    with pytest.raises(TuningSkippedError):
        DARK_CURRENT_TUNER.tune(TuningInputs(SETUP, None, None, {}))
