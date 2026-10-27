"""Tests for the noise of empty apertures at several sizes (decision D20)."""

import math
from datetime import UTC, datetime

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.metrics import STACKING_EMPTY_APERTURE_NOISE_SIZE_EXPONENT
from astrometricslib.pipelines.stacking.post_processing.aperture_size_noise import (
    SIZE_RADIUS_MULTIPLES,
    ApertureSizeNoiseOutcome,
    measure_aperture_size_noise,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import effective_aperture_area_px
from astrometricslib.pipelines.stacking.post_processing.record_quality import size_exponent_measurements
from astrometricslib.pipelines.stacking.post_processing.stack_image import APERTURE_RADIUS_PX

SIZE = 900
RUN_TIME = datetime(2026, 10, 26, tzinfo=UTC)
POSITIONS = np.array(
    [(column, row) for row in range(60, 860, 40) for column in range(60, 860, 40)], dtype=float
)
EVERYWHERE_CLEAR = np.full((SIZE, SIZE), np.inf)


def _white(seed: int, sigma: float = 2.0) -> np.ndarray:
    """Build a sky of white noise.

    Parameters
    ----------
    seed : `int`
        Seed of the noise.
    sigma : `float`, optional
        The per-pixel standard deviation.

    Returns
    -------
    image : `numpy.ndarray`
        The image, with a sky level of 100.
    """
    return 100.0 + np.random.default_rng(seed).normal(0.0, sigma, (SIZE, SIZE))


def test_white_noise_grows_with_the_square_root_of_the_area() -> None:
    """White noise gives the exponent the aperture and ring predict."""
    outcome = measure_aperture_size_noise(_white(1), POSITIONS, EVERYWHERE_CLEAR)

    assert outcome.result is not None
    result = outcome.result
    assert result.radii_px == tuple(multiple * APERTURE_RADIUS_PX for multiple in SIZE_RADIUS_MULTIPLES)
    assert result.aperture_count >= 300
    # Independent pixels: variance = sigma^2 x effective area.
    expected_noises = [
        2.0 * math.sqrt(effective_aperture_area_px(radius, radius + 3.0, radius + 8.0))
        for radius in result.radii_px
    ]
    expected_slope = np.polyfit(np.log(result.pixel_counts), np.log(expected_noises), 1)[0]
    assert 0.5 <= expected_slope < 0.62
    assert result.exponent == pytest.approx(expected_slope, abs=max(3 * result.exponent_error, 0.03))
    assert result.exponent_error > 0
    assert list(result.noises) == sorted(result.noises)


def test_correlated_noise_gives_a_larger_exponent_than_white_noise() -> None:
    """Correlated noise grows faster than white noise."""
    white = measure_aperture_size_noise(_white(2), POSITIONS, EVERYWHERE_CLEAR)
    smooth = 100.0 + gaussian_filter(_white(3) - 100.0, 2.0)
    correlated = measure_aperture_size_noise(smooth, POSITIONS, EVERYWHERE_CLEAR)

    assert white.result is not None and correlated.result is not None
    assert correlated.result.exponent > white.result.exponent + 0.15


def test_apertures_near_the_mask_are_left_out_for_every_radius() -> None:
    """Apertures near a flagged pixel are left out at every radius."""
    distance = np.full((SIZE, SIZE), np.inf)
    distance[:, :450] = 0.0  # the left half is next to a source
    outcome = measure_aperture_size_noise(_white(4), POSITIONS, distance)

    assert outcome.result is not None
    assert outcome.result.aperture_count == int(np.count_nonzero(POSITIONS[:, 0] >= 450))
    # A distance of 17 px or less is too near; 18 px is clear.
    edge = np.full((SIZE, SIZE), 17.0)
    assert measure_aperture_size_noise(_white(4), POSITIONS, edge).result is None
    clear = np.full((SIZE, SIZE), 17.5)
    assert measure_aperture_size_noise(_white(4), POSITIONS, clear).result is not None


def test_too_few_clear_apertures_is_a_reason_not_a_number() -> None:
    """With under 20 usable apertures nothing is fitted."""
    outcome = measure_aperture_size_noise(_white(5), POSITIONS[:15], EVERYWHERE_CLEAR)

    assert outcome.result is None
    assert "only 15 apertures" in outcome.reason


def test_apertures_off_the_stack_or_not_finite_are_skipped() -> None:
    """A position outside the image or not finite never reaches the sums."""
    positions = np.vstack([POSITIONS, [[np.nan, 100.0], [5000.0, 5000.0], [2.0, 2.0]]])
    outcome = measure_aperture_size_noise(_white(6), positions, EVERYWHERE_CLEAR)

    assert outcome.result is not None
    assert outcome.result.aperture_count == len(POSITIONS)


def test_the_exponent_row_carries_its_error_and_the_noise_at_each_radius() -> None:
    """The row holds the exponent, its error and the noise per radius."""
    context = PipelineRunContext("M 99", "stacking", "1.3.0", "run-1", "run-1", RUN_TIME)
    subject = context.stack_subject("M99_Stacked.fits")
    outcome = measure_aperture_size_noise(_white(7), POSITIONS, EVERYWHERE_CLEAR)

    (row,) = size_exponent_measurements(outcome, context, subject)

    assert row.metric == STACKING_EMPTY_APERTURE_NOISE_SIZE_EXPONENT
    assert row.status == MeasurementStatus.MEASURED
    assert row.value == pytest.approx(outcome.result.exponent)
    assert row.uncertainty == pytest.approx(outcome.result.exponent_error)
    assert row.sample_count == outcome.result.aperture_count
    assert "radius 2 px" in row.reason and "8 px" in row.reason


def test_a_failed_or_untried_measurement_gives_a_reason_or_no_row() -> None:
    """Not tried makes no row; a failure is stored as not measured."""
    context = PipelineRunContext("M 99", "stacking", "1.3.0", "run-1", "run-1", RUN_TIME)
    subject = context.stack_subject("M99_Stacked.fits")

    assert size_exponent_measurements(None, context, subject) == []
    (row,) = size_exponent_measurements(ApertureSizeNoiseOutcome(None, "only 3 apertures"), context, subject)
    assert row.status == MeasurementStatus.NOT_MEASURED
    assert row.reason == "only 3 apertures"
