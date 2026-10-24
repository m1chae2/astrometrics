"""Tests for the expected position residual.

The parts are centroid noise, pixel phase and the Gaia epoch gap.

The centroid-noise bound is checked against hand numbers and against the
star finder itself on noisy injected stars (the bound is a floor the finder
may not beat, and it should be near it). The pixel-phase table is checked by
interpolation and against the finder on noise-free stars. Missing inputs must
give a reason and no number.
"""

import math
from datetime import UTC, datetime

import numpy as np
import pytest
from scipy.special import erf

from astrometricslib.models.quality.measurement import MeasurementStatus, SubjectLevel
from astrometricslib.pipelines.astrometry.metrics import (
    ASTROMETRY_EXPECTED_CENTROID_NOISE_RMS_ARCSEC,
    ASTROMETRY_EXPECTED_GAIA_EPOCH_ERROR_RMS_ARCSEC,
    ASTROMETRY_EXPECTED_PIXEL_PHASE_RMS_ARCSEC,
    ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT,
)
from astrometricslib.pipelines.astrometry.post_processing.expected_residual import (
    CRAMER_RAO_PEAK_FACTOR,
    GAIA_EPOCH_ERROR_REASON,
    PIXEL_PHASE_RMS_PX_BY_FWHM_PX,
    centroid_error_per_axis_px,
    expected_residual,
    expected_residual_measurements,
    pixel_phase_rms_per_axis_px,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.record_measurements import check_measurement_against_registry
from astrometricslib.pipelines.shared.star_finding import find_sources_above_background

CONTEXT = PipelineRunContext(
    "M 99", "astrometry", "1.4.0", "job-1", "job-1", datetime(2026, 10, 23, tzinfo=UTC)
)


def test_the_centroid_bound_is_sqrt_two_over_pi_times_noise_over_peak() -> None:
    """A star of peak over noise 10 is placed no better than 0.0798 px."""
    assert CRAMER_RAO_PEAK_FACTOR == pytest.approx(math.sqrt(2 / math.pi))
    assert centroid_error_per_axis_px([10.0, 20.0]) == pytest.approx([0.07979, 0.039894], rel=1e-3)


def test_the_bound_equals_sqrt_two_fwhm_over_2355_snr_for_the_optimal_aperture() -> None:
    """The bound is sqrt(2) FWHM / (2.355 SNR) at any width."""
    for fwhm in (2.0, 3.5):
        sigma = fwhm / 2.3548
        ratio = 12.0
        snr = ratio * math.sqrt(math.pi) * sigma
        assert math.sqrt(2.0) * (fwhm / 2.3548) / snr == pytest.approx(
            centroid_error_per_axis_px([ratio])[0], rel=1e-9
        )


def test_the_expected_centroid_noise_is_two_axes_times_the_pixel_scale() -> None:
    """Four equal stars with peak over noise 20 at 2 arcsec per pixel."""
    result = expected_residual([20.0] * 4, "", 3.0, 2.0)
    assert result.centroid_noise_rms_arcsec == pytest.approx(
        math.sqrt(2.0) * CRAMER_RAO_PEAK_FACTOR / 20.0 * 2.0
    )
    assert result.star_count == 4
    assert result.centroid_noise_reason == ""


def test_faint_stars_dominate_the_root_mean_square() -> None:
    """One faint star among bright ones raises the RMS far above theirs."""
    bright_only = expected_residual([50.0] * 9, "", 3.0, 2.0).centroid_noise_rms_arcsec
    with_faint = expected_residual([50.0] * 9 + [5.0], "", 3.0, 2.0).centroid_noise_rms_arcsec
    assert with_faint > 2.0 * bright_only


def test_the_pixel_phase_table_is_read_by_interpolation_and_stops_at_its_ends() -> None:
    """Table widths give their entry; others are interpolated."""
    for width, error in PIXEL_PHASE_RMS_PX_BY_FWHM_PX:
        assert pixel_phase_rms_per_axis_px(width) == pytest.approx(error)
    low, high = PIXEL_PHASE_RMS_PX_BY_FWHM_PX[1], PIXEL_PHASE_RMS_PX_BY_FWHM_PX[2]
    assert pixel_phase_rms_per_axis_px(0.5 * (low[0] + high[0])) == pytest.approx(0.5 * (low[1] + high[1]))
    assert pixel_phase_rms_per_axis_px(1.0) is None
    assert pixel_phase_rms_per_axis_px(6.0) is None
    assert pixel_phase_rms_per_axis_px(None) is None
    assert pixel_phase_rms_per_axis_px(float("nan")) is None


def test_the_pixel_phase_error_shrinks_as_the_star_widens() -> None:
    """A wider star spreads over more pixels, so the table falls with width."""
    errors = [error for _, error in PIXEL_PHASE_RMS_PX_BY_FWHM_PX]
    assert errors == sorted(errors, reverse=True)


def test_the_expected_pixel_phase_is_two_axes_times_the_pixel_scale() -> None:
    """At a width of 3 px: sqrt(2) times the table entry times the scale."""
    result = expected_residual([20.0], "", 3.0, 1.915)
    assert result.pixel_phase_rms_arcsec == pytest.approx(math.sqrt(2.0) * 0.0216 * 1.915)


@pytest.mark.parametrize("scale", [None, 0.0, -1.0])
def test_without_a_pixel_scale_neither_part_is_found(scale: float | None) -> None:
    """An unsolved stack has no scale, so no arcsecond value exists."""
    result = expected_residual([20.0], "", 3.0, scale)
    assert result.centroid_noise_rms_arcsec is None
    assert result.pixel_phase_rms_arcsec is None
    assert "pixel scale" in result.centroid_noise_reason


def test_with_no_fit_stars_the_signal_reason_is_passed_on() -> None:
    """With no fit stars the reason is passed on and the phase part stands."""
    result = expected_residual([], "no Gaia fit was made", 3.0, 1.9)
    assert result.centroid_noise_rms_arcsec is None
    assert result.centroid_noise_reason == "no Gaia fit was made"
    assert result.pixel_phase_rms_arcsec is not None
    assert result.star_count == 0


def test_a_width_outside_the_table_leaves_the_phase_part_out() -> None:
    """A star width of 1 px was never injected, so no phase value is given."""
    result = expected_residual([20.0], "", 1.0, 1.9)
    assert result.pixel_phase_rms_arcsec is None
    assert "1.5 to 5" in result.pixel_phase_reason
    assert result.centroid_noise_rms_arcsec is not None


def test_the_measurements_cover_every_declared_metric_and_the_epoch_part_is_never_measured() -> None:
    """Four stack rows: two parts, the epoch gap with its reason, the count."""
    rows = expected_residual_measurements(
        expected_residual([20.0] * 5, "", 3.0, 1.9), CONTEXT, "M99_Stacked.fits"
    )
    by_metric = {row.metric: row for row in rows}
    assert set(by_metric) == {
        ASTROMETRY_EXPECTED_CENTROID_NOISE_RMS_ARCSEC,
        ASTROMETRY_EXPECTED_PIXEL_PHASE_RMS_ARCSEC,
        ASTROMETRY_EXPECTED_GAIA_EPOCH_ERROR_RMS_ARCSEC,
        ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT,
    }
    epoch = by_metric[ASTROMETRY_EXPECTED_GAIA_EPOCH_ERROR_RMS_ARCSEC]
    assert epoch.status is MeasurementStatus.NOT_MEASURED
    assert epoch.reason == GAIA_EPOCH_ERROR_REASON
    assert by_metric[ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT].value == pytest.approx(5.0)
    assert by_metric[ASTROMETRY_EXPECTED_CENTROID_NOISE_RMS_ARCSEC].sample_count == 5
    assert all(row.subject.level is SubjectLevel.STACK for row in rows)
    for row in rows:
        check_measurement_against_registry(row)


def test_with_no_stars_the_count_and_centroid_rows_are_not_measured() -> None:
    """The count row carries the same reason as the centroid part."""
    rows = expected_residual_measurements(
        expected_residual([], "no Gaia fit was made", 3.0, 1.9), CONTEXT, "s.fits"
    )
    by_metric = {row.metric: row for row in rows}
    assert by_metric[ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT].value is None
    assert by_metric[ASTROMETRY_EXPECTED_RESIDUAL_STAR_COUNT].reason == "no Gaia fit was made"


def _inject(fwhm: float, peak_over_noise: float, seed: int, noise: float) -> tuple[np.ndarray, np.ndarray]:
    """Find noisy injected stars with the project's star finder.

    Parameters
    ----------
    fwhm : `float`
        The star width, in pixels.
    peak_over_noise : `float`
        The stars' peak over the noise.
    seed : `int`
        The random seed.
    noise : `float`
        The noise per pixel (0 for a noise-free frame).

    Returns
    -------
    errors : `numpy.ndarray`
        N by 3 array: centroid error in x and in y, and the found peak over
        the noise (or the found peak when there is no noise).
    truth_count : `numpy.ndarray`
        A one-element array with the number of injected stars.
    """
    rng = np.random.default_rng(seed)
    shape = (240, 240)
    columns, rows = np.arange(shape[1]), np.arange(shape[0])
    sigma = fwhm / 2.3548
    scale = sigma * math.sqrt(2.0)
    data = rng.normal(0.0, noise, shape) if noise else np.zeros(shape)
    truth = []
    for grid_y in range(10, 230, 30):
        for grid_x in range(10, 230, 30):
            x0, y0 = grid_x + rng.uniform(0, 1), grid_y + rng.uniform(0, 1)
            fx = 0.5 * (erf((columns + 0.5 - x0) / scale) - erf((columns - 0.5 - x0) / scale))
            fy = 0.5 * (erf((rows + 0.5 - y0) / scale) - erf((rows - 0.5 - y0) / scale))
            data += peak_over_noise * (noise or 1.0) * 2 * math.pi * sigma**2 * np.outer(fy, fx)
            truth.append((x0, y0))
    truth_array = np.array(truth)
    found = find_sources_above_background(
        data, noise=noise or 1.0, expected_fwhm_px=fwhm, threshold_sigma=5.0, roundness_range=(-0.6, 0.6)
    )
    rows_out = []
    for source in found:
        cx = float(source.get("x_centroid", source.get("xcentroid")))
        cy = float(source.get("y_centroid", source.get("ycentroid")))
        distance = np.hypot(truth_array[:, 0] - cx, truth_array[:, 1] - cy)
        best = int(np.argmin(distance))
        if distance[best] < 1.5:
            rows_out.append((
                cx - truth_array[best, 0],
                cy - truth_array[best, 1],
                float(source["peak"]) / (noise or 1.0),
            ))
    return np.array(rows_out), np.array([len(truth)])


def test_the_star_finder_scatters_a_little_above_the_bound_and_never_below_it() -> None:
    """On noisy stars the finder scatters 1.0 to 1.5 times the bound."""
    errors, _ = _inject(3.0, 20.0, seed=11, noise=1.0)
    errors2, _ = _inject(3.0, 20.0, seed=12, noise=1.0)
    errors = np.vstack([errors, errors2])
    assert errors.shape[0] > 100
    measured = math.sqrt(np.mean(errors[:, :2] ** 2))
    phase = pixel_phase_rms_per_axis_px(3.0)
    noise_only = math.sqrt(max(measured**2 - phase**2, 0.0))
    bound = math.sqrt(np.mean(centroid_error_per_axis_px(errors[:, 2]) ** 2))
    assert 0.95 * bound < noise_only < 1.5 * bound


def test_the_pixel_phase_table_matches_the_star_finder_on_noise_free_stars() -> None:
    """Noise-free scatter at widths 2 and 4 is within 40% of the table."""
    for fwhm in (2.0, 4.0):
        errors, _ = _inject(fwhm, 1000.0, seed=int(fwhm), noise=0.0)
        measured = math.sqrt(np.mean(errors[:, :2] ** 2))
        assert measured == pytest.approx(pixel_phase_rms_per_axis_px(fwhm), rel=0.4)
