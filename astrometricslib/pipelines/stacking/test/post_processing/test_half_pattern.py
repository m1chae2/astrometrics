"""Tests for the fine-scale structure that two half stacks share.

The tests build two synthetic half stacks on one grid: independent random noise
in each, plus structure that both hold. The structure is either a pattern
smeared along a drift (what a sensor pattern becomes in a drifting stack) or
a smooth isotropic field (what faint sky structure looks like). The numbers
that come back are compared with what was put in.
"""

from datetime import UTC, datetime

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter, shift

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking import metrics as stacking_metrics
from astrometricslib.pipelines.stacking.post_processing.half_pattern import (
    MINIMUM_PIXELS,
    PATTERN_NOT_POSITIVE_REASON,
    PATTERN_ROW_NAMES,
    WALKING_LAG_PX,
    HalfPatternOutcome,
    HalfPatternResult,
    _tile_counts,
    half_pattern_measurements,
    measure_half_pattern,
)

CONTEXT = PipelineRunContext(
    "M 99", "stacking", "1.3.0", "job-1", "job-1", datetime(2026, 10, 23, tzinfo=UTC)
)
SIZE = 512
SMEAR_LENGTH = 16
BASE_LEVEL = 100.0


def _smeared(pattern: np.ndarray, axis_deg: float) -> np.ndarray:
    """Smear a pattern along a direction, as a drifting pointing does.

    Parameters
    ----------
    pattern : `numpy.ndarray`
        The pattern.
    axis_deg : `float`
        The direction from the column axis toward the row axis, in degrees.

    Returns
    -------
    smeared : `numpy.ndarray`
        The mean of the pattern moved by 0 to `SMEAR_LENGTH` - 1 pixels along
        the direction.
    """
    angle = np.radians(axis_deg)
    total = np.zeros_like(pattern)
    for step in range(SMEAR_LENGTH):
        total += shift(pattern, (step * np.sin(angle), step * np.cos(angle)), order=1, mode="wrap")
    return total / SMEAR_LENGTH


def _halves(
    seed: int,
    *,
    pattern_amplitude: float = 0.0,
    axis_deg: float = 0.0,
    smear: bool = True,
    sky_amplitude: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Make two half stacks with shared structure and their own noise.

    Parameters
    ----------
    seed : `int`
        The random seed.
    pattern_amplitude : `float`, optional
        The standard deviation of the shared pixel-level pattern before any
        smearing.
    axis_deg : `float`, optional
        The smear direction.
    smear : `bool`, optional
        Whether the pattern is smeared along the direction.
    sky_amplitude : `float`, optional
        The standard deviation of a smooth isotropic field that both halves
        hold (Gaussian-smoothed noise, 1.5 px).

    Returns
    -------
    odd, even : `numpy.ndarray`
        The two images, each with a level of `BASE_LEVEL` and independent
        noise of standard deviation 1.
    """
    generator = np.random.default_rng(seed)
    shared = np.zeros((SIZE, SIZE))
    if pattern_amplitude:
        pattern = generator.normal(0.0, pattern_amplitude, (SIZE, SIZE))
        shared += _smeared(pattern, axis_deg) if smear else pattern
    if sky_amplitude:
        field = gaussian_filter(generator.normal(0.0, 1.0, (SIZE, SIZE)), 1.5)
        shared += sky_amplitude * field / field.std()
    odd = BASE_LEVEL + shared + generator.normal(0.0, 1.0, (SIZE, SIZE))
    even = BASE_LEVEL + shared + generator.normal(0.0, 1.0, (SIZE, SIZE))
    return odd, even


def _measure(odd: np.ndarray, even: np.ndarray, **overrides: object) -> HalfPatternOutcome:
    """Run the measurement with plain weights.

    Parameters
    ----------
    odd, even : `numpy.ndarray`
        The two halves.
    **overrides : `object`
        Arguments that replace the defaults (no shared frame, a variance
        factor of 2 and one effective frame, so the random variance of the
        stack is the variance of one noise realization).

    Returns
    -------
    outcome : `HalfPatternOutcome`
        The measurement.
    """
    arguments: dict[str, object] = {
        "variance_factor": 2.0,
        "shared_fractions": (0.0, 0.0),
        "effective_count": 1.0,
        "drift_axis_deg": 0.0,
        "drift_extent_px": float(SMEAR_LENGTH),
    }
    arguments.update(overrides)
    return measure_half_pattern(odd, even, **arguments)


def test_noise_alone_shares_nothing() -> None:
    """Independent noise gives a shared variance of zero within its error."""
    outcome = _measure(*_halves(1))
    assert outcome.result is not None
    result = outcome.result
    assert abs(result.variance_ratio) < 3.0 * result.variance_ratio_error
    assert result.variance_ratio_error < 0.02
    assert result.pixel_count >= MINIMUM_PIXELS


def test_a_shared_pixel_pattern_is_found_with_its_size() -> None:
    """A shared pattern as large as the noise gives a ratio near 1."""
    outcome = _measure(*_halves(2, pattern_amplitude=1.0, smear=False))
    assert outcome.result is not None
    assert outcome.result.variance_ratio == pytest.approx(1.0, abs=0.1)
    assert outcome.result.rms == pytest.approx(1.0, abs=0.06)
    assert outcome.result.rms_error is not None


@pytest.mark.parametrize("axis_deg", [0.0, 90.0, 37.0])
def test_a_pattern_smeared_along_the_drift_correlates_more_along_it_than_across(axis_deg: float) -> None:
    """Along-drift correlation beats across-drift for any drift axis."""
    odd, even = _halves(3, pattern_amplitude=3.0, axis_deg=axis_deg)
    outcome = _measure(odd, even, drift_axis_deg=axis_deg)
    assert outcome.result is not None
    result = outcome.result
    assert result.excess is not None and result.excess_error is not None
    assert result.excess > 0.02
    assert result.excess > 5.0 * result.excess_error
    assert result.along_correlation is not None and result.across_correlation is not None
    assert result.along_correlation > result.across_correlation


def test_the_wrong_axis_does_not_show_the_smear() -> None:
    """Asking across the smear gives a negative excess."""
    odd, even = _halves(4, pattern_amplitude=3.0, axis_deg=0.0)
    outcome = _measure(odd, even, drift_axis_deg=90.0)
    assert outcome.result is not None
    assert outcome.result.excess is not None
    assert outcome.result.excess < -0.02


def test_smooth_sky_structure_is_shared_but_not_smeared() -> None:
    """A smooth field adds shared variance and leaves the excess at zero."""
    odd, even = _halves(5, sky_amplitude=2.0)
    outcome = _measure(odd, even, drift_axis_deg=30.0)
    assert outcome.result is not None
    result = outcome.result
    assert result.variance_ratio > 0.1
    assert result.excess is not None and result.excess_error is not None
    assert abs(result.excess) < 4.0 * result.excess_error + 0.005


def test_the_shared_frames_random_noise_is_taken_out() -> None:
    """A shared frame's weight alone gives a shared variance below zero."""
    odd, even = _halves(6)
    outcome = _measure(odd, even, shared_fractions=(0.5, 0.5))
    assert outcome.result is not None
    # One frame's variance is the difference variance (about 2) over the
    # factor 2. The shared frame would have added 0.25 of it, which is taken
    # out.
    assert outcome.result.shared_variance == pytest.approx(-0.25, abs=0.03)


def test_unknown_weights_are_not_measured() -> None:
    """Without the variance factor or the shares, nothing is measured."""
    odd, even = _halves(7)
    for overrides in (
        {"variance_factor": None},
        {"shared_fractions": (None, 0.1)},
        {"effective_count": None},
    ):
        outcome = _measure(odd, even, **overrides)
        assert outcome.result is None
        assert "weights are unknown" in outcome.reason


def test_a_small_clear_area_is_not_measured() -> None:
    """Fewer than the minimum number of clear pixels gives a reason."""
    odd, even = _halves(8)
    odd = odd[:300, :300]
    even = even[:300, :300]
    outcome = _measure(odd, even)
    assert outcome.result is None
    assert "pixels are left" in outcome.reason


def test_halves_on_different_grids_are_not_measured() -> None:
    """Two images of different shapes cannot be differenced."""
    odd, even = _halves(9)
    outcome = _measure(odd, even[:-1])
    assert outcome.result is None
    assert "different pixel grids" in outcome.reason


def test_an_empty_edge_is_left_out() -> None:
    """Pixels that are exactly zero in either half are not used."""
    odd, even = _halves(10)
    odd[:, :100] = 0.0
    outcome = _measure(odd, even)
    assert outcome.result is not None
    assert outcome.result.pixel_count < 0.8 * SIZE * SIZE


def test_the_lagged_correlations_need_an_axis_and_a_long_enough_drift() -> None:
    """Without an axis or a long drift, the walking rows give a reason."""
    odd, even = _halves(11, pattern_amplitude=2.0)
    unknown = _measure(odd, even, drift_axis_deg=None, drift_extent_px=None, drift_reason="no night drifts")
    assert unknown.result is not None
    assert unknown.result.excess is None
    assert unknown.result.walking_reason == "no night drifts"
    short = _measure(odd, even, drift_extent_px=2.0 * WALKING_LAG_PX - 1.0)
    assert short.result is not None
    assert short.result.excess is None
    assert "cannot reach the lag" in short.result.walking_reason
    assert short.result.rms is not None


def test_the_rows_carry_values_errors_and_reasons() -> None:
    """A measured result gives eight measured rows with errors."""
    odd, even = _halves(12, pattern_amplitude=3.0)
    outcome = _measure(odd, even)
    subject = CONTEXT.stack_subject("M99_Stacked.fits")
    rows = half_pattern_measurements(CONTEXT, subject, outcome, "", "The note.")
    assert [row.metric for row in rows] == [
        stacking_metrics.STACKING_HALF_SHARED_FINE_SCALE_RMS_STACK_UNIT,
        stacking_metrics.STACKING_HALF_SHARED_FINE_SCALE_VARIANCE_RATIO,
        stacking_metrics.STACKING_HALF_SHARED_FINE_SCALE_COVARIANCE_STACK_UNIT_SQUARED,
        stacking_metrics.STACKING_HALF_FINE_SCALE_DIFFERENCE_VARIANCE_STACK_UNIT_SQUARED,
        stacking_metrics.STACKING_HALF_SHARED_FINE_SCALE_REFERENCE_CORRECTION_STACK_UNIT_SQUARED,
        stacking_metrics.STACKING_WALKING_NOISE_ALONG_CORRELATION,
        stacking_metrics.STACKING_WALKING_NOISE_ACROSS_CORRELATION,
        stacking_metrics.STACKING_WALKING_NOISE_CORRELATION_EXCESS,
    ]
    assert tuple(row.metric for row in rows) == PATTERN_ROW_NAMES
    assert all(row.status == MeasurementStatus.MEASURED for row in rows)
    assert all(row.uncertainty is not None for row in rows)
    assert all("The note." in (row.reason or "") for row in rows)
    ratio = rows[1]
    assert "leaves out the uncertainty of F and N" in (ratio.reason or "")


def test_the_stored_parts_rebuild_the_ratio() -> None:
    """The ratio can be rebuilt from the stored parts."""
    odd, even = _halves(13, pattern_amplitude=1.0, smear=False)
    factor, shares, effective = 0.5, (0.1, 0.12), 20.0
    outcome = _measure(odd, even, variance_factor=factor, shared_fractions=shares, effective_count=effective)
    assert outcome.result is not None
    result = outcome.result
    correction = result.difference_variance / factor * shares[0] * shares[1]
    assert result.reference_correction == pytest.approx(correction)
    assert result.covariance - result.reference_correction == pytest.approx(result.shared_variance)
    denominator = result.difference_variance / (factor * effective)
    assert result.random_variance_full_stack == pytest.approx(denominator)
    assert result.variance_ratio == pytest.approx((result.covariance - correction) / denominator)
    assert result.covariance_error > 0.0
    assert result.difference_variance_error > 0.0
    assert result.reference_correction_error > 0.0


def test_pixels_are_counted_from_the_mask_in_tiles() -> None:
    """Tile counts equal the tile sums of ones, without an array of ones."""
    generator = np.random.default_rng(14)
    mask = generator.random((200, 150)) > 0.3
    from astrometricslib.pipelines.stacking.post_processing.half_pattern import _tile_sums

    expected = _tile_sums(np.ones(mask.shape, dtype=np.float32), mask)
    assert np.array_equal(_tile_counts(mask), expected)


def test_a_shared_variance_below_zero_has_no_root_and_keeps_its_ratio() -> None:
    """The size row says why it is missing; the ratio row keeps the sign."""
    result = HalfPatternResult(
        pixel_count=200_000,
        tile_count=50,
        shared_variance=-1.0e-9,
        shared_variance_error=2.0e-9,
        covariance=1.0e-9,
        covariance_error=1.0e-9,
        difference_variance=4.0e-9,
        difference_variance_error=1.0e-9,
        reference_correction=2.0e-9,
        reference_correction_error=1.0e-9,
        random_variance_full_stack=1.0e-9,
        variance_ratio=-1.0,
        variance_ratio_error=2.0,
        rms=None,
        rms_error=None,
        walking_reason="the drift is unknown",
    )
    rows = half_pattern_measurements(
        CONTEXT, CONTEXT.stack_subject("M99_Stacked.fits"), HalfPatternOutcome(result), ""
    )
    by_metric = {row.metric: row for row in rows}
    size = by_metric[stacking_metrics.STACKING_HALF_SHARED_FINE_SCALE_RMS_STACK_UNIT]
    ratio = by_metric[stacking_metrics.STACKING_HALF_SHARED_FINE_SCALE_VARIANCE_RATIO]
    excess = by_metric[stacking_metrics.STACKING_WALKING_NOISE_CORRELATION_EXCESS]
    assert size.status == MeasurementStatus.NOT_MEASURED
    assert size.reason == PATTERN_NOT_POSITIVE_REASON
    assert ratio.value == pytest.approx(-1.0)
    assert excess.status == MeasurementStatus.NOT_MEASURED
    assert excess.reason == "the drift is unknown"


def test_no_outcome_gives_eight_not_measured_rows_with_the_reason() -> None:
    """When the difference was not made, every pattern row says why."""
    rows = half_pattern_measurements(
        CONTEXT, CONTEXT.stack_subject("M99_Stacked.fits"), None, "the stacker failed on the odd half"
    )
    assert len(rows) == 8
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all(row.reason == "the stacker failed on the odd half" for row in rows)
