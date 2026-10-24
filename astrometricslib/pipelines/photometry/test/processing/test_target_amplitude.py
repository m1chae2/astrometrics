"""Tests for the target's amplitude from a folded light curve."""

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.processing.target_amplitude import (
    MINIMUM_FILLED_BIN_FRACTION,
    PHASE_BIN_COUNT,
    folded_amplitude,
    magnitudes_from_flux,
)


def _sine(
    amplitude_mag: float, period: float, count: int = 600, noise: float = 0.002, seed: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    """Build a sinusoidal light curve with an exact peak-to-peak range.

    Returns
    -------
    time_days, flux : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        Times spread over twelve cycles and fluxes whose magnitudes swing
        by ``amplitude_mag`` peak to peak.
    """
    rng = np.random.default_rng(seed)
    time_days = np.sort(rng.uniform(0.0, 12.0 * period, count))
    magnitude = 0.5 * amplitude_mag * np.sin(2 * np.pi * time_days / period) + rng.normal(0.0, noise, count)
    return time_days, 10.0 ** (-0.4 * magnitude)


def test_a_covered_sine_gives_its_peak_to_peak_range_a_little_smoothed() -> None:
    """Ten bins smooth a sine's extremes, so the range reads slightly low."""
    time_days, flux = _sine(0.40, 2.87)
    result = folded_amplitude(time_days, flux, 2.87)
    assert result.reason == ""
    assert result.filled_fraction == pytest.approx(1.0)
    assert result.amplitude_mag == pytest.approx(0.40, rel=0.08)
    assert result.amplitude_mag < 0.40 + 0.01
    assert 0.0 < result.error_mag < 0.02
    assert result.point_count == 600


def test_the_wrong_period_smears_the_fold_to_a_small_amplitude() -> None:
    """Folding on a period that is not the star's flattens the curve."""
    time_days, flux = _sine(0.40, 2.87)
    wrong = folded_amplitude(time_days, flux, 2.87 * 1.37)
    assert wrong.amplitude_mag is not None
    assert wrong.amplitude_mag < 0.2


def test_a_light_curve_covering_part_of_the_cycle_gives_no_amplitude() -> None:
    """One short stretch of a long period fills too few bins."""
    rng = np.random.default_rng(1)
    period = 10.0
    time_days = np.sort(rng.uniform(0.0, 3.0, 300))
    flux = 10.0 ** (-0.4 * 0.2 * np.sin(2 * np.pi * time_days / period))
    result = folded_amplitude(time_days, flux, period)
    assert result.amplitude_mag is None
    assert result.filled_fraction < MINIMUM_FILLED_BIN_FRACTION
    assert "phase bins" in result.reason


def test_too_few_points_give_no_amplitude() -> None:
    """The fold needs at least three points in each of eight bins."""
    result = folded_amplitude(np.linspace(0, 5, 20), np.ones(20), 1.0)
    assert result.amplitude_mag is None
    assert "usable points" in result.reason


@pytest.mark.parametrize("period", [0.0, -1.0, float("nan")])
def test_no_period_gives_no_fold(period: float) -> None:
    """A period that is not a positive number cannot be folded on."""
    result = folded_amplitude(np.linspace(0, 5, 100), np.ones(100), period)
    assert result.amplitude_mag is None
    assert "no period" in result.reason


def test_magnitudes_are_relative_to_the_median_and_skip_non_positive_flux() -> None:
    """Twice the median flux is 0.753 mag brighter; zero flux has none."""
    magnitudes = magnitudes_from_flux(np.array([1.0, 2.0, 0.5, 0.0, -1.0]))
    assert magnitudes[0] == pytest.approx(0.0)
    assert magnitudes[1] == pytest.approx(-2.5 * np.log10(2.0))
    assert np.isnan(magnitudes[3]) and np.isnan(magnitudes[4])
    assert np.all(np.isnan(magnitudes_from_flux(np.array([0.0, -2.0]))))


def test_the_bin_settings_are_the_documented_ones() -> None:
    """Ten bins, eight needed: the values the metric declarations quote."""
    assert PHASE_BIN_COUNT == 10
    assert MINIMUM_FILLED_BIN_FRACTION == pytest.approx(0.8)
