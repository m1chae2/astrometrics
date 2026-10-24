"""Tests for the noise the master flat adds to a calibrated stack.

A white-noise flat error is blurred and summed over circles the way the stack's
apertures sum the sky. The tests check the blur factor against its formula and
the aperture figure against a simulation, and check that a missing input gives
a reason and never a default.
"""

import math

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution import (
    flat_noise_contribution,
    smoothed_noise_factor,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import signed_aperture_sum

# The simulated spread of about 1500 sums has a relative error of 3%. The
# sums include the sky ring's median, which the model leaves out and which adds
# 7% to 11% here, so the allowed excess over the model is 15%.
SIMULATION_TOLERANCE = 0.10


def test_a_wide_blur_lowers_white_noise_by_the_gaussian_formula() -> None:
    """The factor is 1 / (2 sigma sqrt(pi)) for a wide kernel."""
    for sigma in (2.0, 4.0, 8.0):
        assert smoothed_noise_factor(sigma) == pytest.approx(
            1.0 / (2.0 * sigma * math.sqrt(math.pi)), rel=0.01
        )
    assert smoothed_noise_factor(None) == pytest.approx(1.0)
    assert smoothed_noise_factor(0.0) == pytest.approx(1.0)


def test_the_blur_factor_matches_a_blurred_noise_field() -> None:
    """A blurred noise field has the factor times the input spread."""
    field = gaussian_filter(np.random.default_rng(3).normal(0.0, 1.0, (1500, 1500)), 2.0)
    assert float(field.std()) == pytest.approx(smoothed_noise_factor(2.0), rel=0.02)


@pytest.mark.parametrize("sigma", [None, 1.5])
def test_the_aperture_figure_matches_a_simulated_flat_error(sigma: float | None) -> None:
    """A flat error field summed over circles matches the model."""
    relative_noise = 0.02
    sky = 0.006
    generator = np.random.default_rng(5)
    error = generator.normal(0.0, relative_noise, (900, 900))
    if sigma:
        error = gaussian_filter(error, sigma)
    # The sky seen through the flat: no star, no other noise. The sum over the
    # circle, without the ring's median, isolates the pattern modeled.
    image = sky * (1.0 - error)
    sums = []
    for column in range(14, 900 - 14, 24):
        for row in range(14, 900 - 14, 24):
            value = signed_aperture_sum(image, float(column), float(row), ring_inner=7.0, ring_outer=12.0)
            if value is not None:
                sums.append(value)
    simulated = float(np.std(sums))
    outcome = flat_noise_contribution(relative_noise, sigma, sky)
    assert outcome.result is not None
    # The simulated sums include the sky ring, which the model leaves out. The
    # ring adds its own noise (about 11% for white noise, 7% for a 1.5 px
    # blur in this simulation), so the sums come out a little above the model.
    ratio = simulated / outcome.result.aperture_noise
    assert 1.0 <= ratio <= 1.0 + SIMULATION_TOLERANCE + 0.05


def test_the_result_holds_the_fraction_after_the_blur_and_the_sky_level() -> None:
    """The parts are the master's noise after the blur and the level."""
    outcome = flat_noise_contribution(0.04, 4.0, 0.006)
    assert outcome.result is not None
    assert outcome.result.noise_after_smoothing_fraction == pytest.approx(0.04 * smoothed_noise_factor(4.0))
    assert outcome.result.sky_level == pytest.approx(0.006)
    assert outcome.result.aperture_noise > 0


def test_a_flat_that_is_not_blurred_keeps_its_noise() -> None:
    """With no blur the noise after the blur is the noise before it."""
    outcome = flat_noise_contribution(0.003, None, 0.01)
    assert outcome.result is not None
    assert outcome.result.noise_after_smoothing_fraction == pytest.approx(0.003)


def test_missing_inputs_give_a_reason_and_no_number() -> None:
    """No flats, a bad noise or no sky level are reported."""
    assert flat_noise_contribution(None, None, 0.01).result is None
    assert "no flat" in flat_noise_contribution(None, None, 0.01).reason
    assert flat_noise_contribution(float("nan"), None, 0.01).result is None
    assert "signal level" in flat_noise_contribution(0.01, None, None).reason
    assert flat_noise_contribution(0.01, None, 0.0).result is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_a_non_finite_input_gives_a_reason_instead_of_raising(bad: float) -> None:
    """A NaN or infinite input gives a reason; NaN used to raise."""
    assert flat_noise_contribution(0.01, bad, 0.01).result is None
    assert "blur width" in flat_noise_contribution(0.01, bad, 0.01).reason
    assert flat_noise_contribution(bad, 1.5, 0.01).result is None
    assert flat_noise_contribution(0.01, 1.5, bad).result is None
