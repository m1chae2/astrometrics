"""Tests for the point-source depth and surface-brightness limit of a stack."""

import math
from datetime import UTC, datetime

import pytest

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_POINT_SOURCE_DEPTH_5SIGMA_MAG,
    STACKING_SURFACE_BRIGHTNESS_LIMIT_3SIGMA_10ARCSEC_MAG_PER_ARCSEC2,
)
from astrometricslib.pipelines.stacking.post_processing.aperture_size_noise import (
    ApertureSizeNoise,
    ApertureSizeNoiseOutcome,
)
from astrometricslib.pipelines.stacking.post_processing.depth_limits import (
    gaia_zero_point,
    measure_depth_limits,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import depth_limit_measurements
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    StackNoiseOutcome,
    StackNoiseResult,
)

TRUE_ZERO_POINT = 25.0
PIXEL_SCALE = 2.0
NOISE = 10.0
RUN_TIME = datetime(2026, 10, 26, tzinfo=UTC)
CONTEXT = PipelineRunContext("M 99", "stacking", "1.3.0", "run-1", "run-1", RUN_TIME)
SUBJECT = CONTEXT.stack_subject("M99_Stacked.fits")
# (right ascension, declination, Gaia G) of four separated stars.
GAIA_STARS = [(150.0, 2.0, 10.0), (150.1, 2.1, 11.0), (150.2, 2.2, 12.0), (150.3, 2.3, 9.5)]


def _gaia_lookup(
    right_ascension: float, declination: float, radius_deg: float
) -> list[tuple[float, float, float]]:
    """Give the fake Gaia stars inside a circle (a box is enough here).

    Parameters
    ----------
    right_ascension, declination : `float`
        The center, in degrees.
    radius_deg : `float`
        The radius, in degrees.

    Returns
    -------
    rows : `list`
        The fake stars within the radius.
    """
    return [
        row
        for row in GAIA_STARS
        if abs(row[0] - right_ascension) < radius_deg and abs(row[1] - declination) < radius_deg
    ]


def _fluxes(offset: float = 0.0) -> tuple[tuple[float, float, float], ...]:
    """Give each fake star's flux for a known zero point.

    Parameters
    ----------
    offset : `float`, optional
        Added to every star's zero point, in magnitudes.

    Returns
    -------
    fluxes : `tuple`
        (right ascension, declination, flux) of each star.
    """
    return tuple((ra, dec, 10.0 ** (-0.4 * (g - (TRUE_ZERO_POINT + offset)))) for ra, dec, g in GAIA_STARS)


def _result(
    size_noise: ApertureSizeNoiseOutcome | None = None,
    fluxes: tuple[tuple[float, float, float], ...] | None = None,
    scale: float = PIXEL_SCALE,
) -> StackNoiseResult:
    """Build a noise result with known noise and reference stars.

    Parameters
    ----------
    size_noise : `ApertureSizeNoiseOutcome`, optional
        The noise at several sizes, if measured.
    fluxes : `tuple`, optional
        The reference stars' fluxes; the four fake stars by default.
    scale : `float`, optional
        The pixel scale, in arcseconds per pixel.

    Returns
    -------
    result : `StackNoiseResult`
        A result with noise 10 in a 4 px aperture.
    """
    return StackNoiseResult(
        noise_adu=NOISE,
        noise_uncertainty_adu=0.0,
        aperture_count=100,
        reference_flux_adu=1000.0,
        reference_uncertainty_adu=0.0,
        reference_star_count=4,
        normalized_noise=0.01,
        normalized_uncertainty=0.0,
        wcs_source="stack header",
        apertures_created=False,
        reference_aperture_radius_px=8.0,
        size_noise=size_noise,
        pixel_scale_arcsec=scale,
        reference_star_fluxes=_fluxes() if fluxes is None else fluxes,
    )


def _sizes(exponent: float) -> ApertureSizeNoiseOutcome:
    """Build a size measurement that follows an exact power law.

    Parameters
    ----------
    exponent : `float`
        The noise exponent.

    Returns
    -------
    outcome : `ApertureSizeNoiseOutcome`
        Noise NOISE at the 4 px aperture and a power law elsewhere.
    """
    radii = (2.0, 4.0, 6.0, 8.0)
    areas = tuple(math.pi * radius**2 for radius in radii)
    noises = tuple(NOISE * (area / (math.pi * 16.0)) ** exponent for area in areas)
    return ApertureSizeNoiseOutcome(ApertureSizeNoise(radii, areas, noises, exponent, 0.0, 100))


def test_the_zero_point_is_the_median_of_gaia_g_plus_the_instrumental_magnitude() -> None:
    """Stars of known G and flux give back their zero point."""
    zero_point, reason = gaia_zero_point(_fluxes(), _gaia_lookup, 8.0, PIXEL_SCALE)

    assert zero_point is not None, reason
    assert zero_point.zero_point_mag == pytest.approx(TRUE_ZERO_POINT)
    assert zero_point.star_count == 4


def test_a_crowded_star_is_left_out_of_the_zero_point() -> None:
    """A bright Gaia neighbor in the aperture removes the star."""

    def crowded(
        right_ascension: float, declination: float, radius_deg: float
    ) -> list[tuple[float, float, float]]:
        """Add a bright neighbor to the first star.

        Parameters
        ----------
        right_ascension, declination, radius_deg : `float`
            The search circle, in degrees.

        Returns
        -------
        rows : `list`
            The stars, with a neighbor beside the first.
        """
        rows = list(_gaia_lookup(right_ascension, declination, radius_deg))
        if abs(right_ascension - 150.0) < 1e-6:
            rows.append((150.0 + 1.0 / 3600.0, 2.0, 11.0))
        return rows

    zero_point, _ = gaia_zero_point(_fluxes(), crowded, 8.0, PIXEL_SCALE)

    assert zero_point is not None and zero_point.star_count == 3


def test_the_depths_follow_the_formulas_with_a_measured_exponent() -> None:
    """The point-source and surface depths equal the hand-worked values."""
    exponent = 0.7
    fwhm = 3.0
    limits = measure_depth_limits(_result(_sizes(exponent)), fwhm, _gaia_lookup)

    point_area = math.pi * fwhm**2
    point_sigma = NOISE * (point_area / (math.pi * 16.0)) ** exponent
    assert limits.point_source.value_mag == pytest.approx(TRUE_ZERO_POINT - 2.5 * math.log10(5 * point_sigma))
    box_area = (10.0 / PIXEL_SCALE) ** 2
    box_sigma = NOISE * (box_area / (math.pi * 16.0)) ** exponent
    assert limits.surface_brightness.value_mag == pytest.approx(
        TRUE_ZERO_POINT - 2.5 * math.log10(3 * box_sigma / 100.0)
    )
    assert not limits.point_source.assumed
    assert "between the measured apertures" in limits.point_source.note
    assert limits.surface_brightness.sample_count == 4


def test_an_unmeasured_exponent_uses_one_half_and_says_so() -> None:
    """Without a size measurement, independent pixels are assumed."""
    limits = measure_depth_limits(_result(None), 3.0, _gaia_lookup)

    point_sigma = NOISE * math.sqrt(math.pi * 9.0 / (math.pi * 16.0))
    assert limits.point_source.value_mag == pytest.approx(TRUE_ZERO_POINT - 2.5 * math.log10(5 * point_sigma))
    assert limits.point_source.assumed
    assert "assumes independent pixels" in limits.point_source.note
    assert limits.surface_brightness.assumed


def test_a_box_beyond_the_measured_apertures_is_called_an_extrapolation() -> None:
    """A coarse pixel scale makes the box larger than the 8 px aperture."""
    limits = measure_depth_limits(_result(_sizes(0.6), scale=0.5), 3.0, _gaia_lookup)

    assert "extrapolation" in limits.surface_brightness.note
    assert "between the measured apertures" in limits.point_source.note


def test_missing_inputs_give_not_measured_rows_with_reasons() -> None:
    """No Gaia stars, no noise or no star width each say why."""
    no_gaia = measure_depth_limits(_result(None), 3.0, None)
    no_noise = measure_depth_limits(None, 3.0, _gaia_lookup, "the stack has no solution")
    no_width = measure_depth_limits(_result(None), None, _gaia_lookup)

    assert no_gaia.point_source.value_mag is None and "no zero point" in no_gaia.point_source.note
    assert no_noise.surface_brightness.value_mag is None
    assert "no solution" in no_noise.surface_brightness.note
    assert no_width.point_source.value_mag is None and "star width" in no_width.point_source.note
    assert no_width.surface_brightness.value_mag is not None


def test_the_rows_carry_the_value_the_uncertainty_and_the_assumed_status() -> None:
    """Stored rows have the right metric, status and reasons."""
    rows = depth_limit_measurements(StackNoiseOutcome(_result(None)), 3.0, _gaia_lookup, CONTEXT, SUBJECT)
    names = [row.metric for row in rows]
    assert names == [
        STACKING_POINT_SOURCE_DEPTH_5SIGMA_MAG,
        STACKING_SURFACE_BRIGHTNESS_LIMIT_3SIGMA_10ARCSEC_MAG_PER_ARCSEC2,
    ]
    assert all(row.status == MeasurementStatus.ASSUMED for row in rows)
    assert all(row.uncertainty is not None and row.sample_count == 4 for row in rows)

    missing = depth_limit_measurements(StackNoiseOutcome(None, "no noise"), 3.0, None, CONTEXT, SUBJECT)
    assert all(row.status == MeasurementStatus.NOT_MEASURED and "no noise" in row.reason for row in missing)
