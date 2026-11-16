"""Tests for the typical streak width tuner (#175, Phase E3).

Each spectrum's median streak sigma becomes a FWHM, and the median is taken
over spectra; an imaging filter is skipped.
"""

import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy.trail_width_tuning import (
    median_trail_width_fwhm,
    tune_typical_trail_width,
)


def test_streak_sigmas_become_one_typical_fwhm() -> None:
    """The median over spectra of each median sigma, times 2.355."""
    assert median_trail_width_fwhm([[1.0, 2.0, 3.0], [2.0], [0.0, -1.0]]) == pytest.approx(2.355 * 2.0)
    assert median_trail_width_fwhm([[0.0]]) is None


def test_the_trail_tuner_skips_a_filter_that_is_not_the_grating() -> None:
    """An imaging filter has no streaks to read."""
    setup = SetupConfig(name="Mono on Refractor", camera_name="Mono Camera", optic_name="Refractor")
    with pytest.raises(TuningSkippedError):
        tune_typical_trail_width(TuningInputs(setup, "L", configuration=None))
