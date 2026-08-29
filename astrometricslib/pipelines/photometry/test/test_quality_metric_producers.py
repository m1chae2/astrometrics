"""Tests for the two headline quality metrics.

Verifies that `astrometric_residual_rms_arcsec` and
`light_curve_scatter_rms_mag` are correctly calculated and assigned.
These metrics are essential for reporting the quality of the
astrometry solve and the photometry light curves.
"""

import math

import pytest

from astrometricslib.pipelines.photometry.variability_analyzer import (
    median_light_curve_scatter_mag,
)


class _LightCurve:
    """A light-curve stand-in exposing the flux series used."""

    def __init__(self, fluxes, normalized=None, detrended=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.fluxes = fluxes
        self.fluxes_normalized = normalized or []
        self.fluxes_detrended = detrended or []


class _Star:
    """A stellar-object stand-in carrying only a light curve."""

    def __init__(self, light_curve):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.light_curve = light_curve


def test_a_perfectly_flat_star_scatters_at_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """No variation means no scatter."""
    stars = [_Star(_LightCurve([100.0] * 8))]

    assert median_light_curve_scatter_mag(stars) == pytest.approx(0.0)


def test_scatter_is_reported_in_magnitudes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A known fractional scatter must convert to the right magnitude.

    A series alternating +/-10% about its mean has a fractional standard
    deviation whose magnitude equivalent is 2.5*log10(1 + sigma/mean).
    """
    fluxes = [110.0, 90.0] * 6
    stars = [_Star(_LightCurve(fluxes))]

    expected_fraction = pytest.approx(0.1, rel=0.05)
    result = median_light_curve_scatter_mag(stars)

    assert result is not None
    recovered_fraction = 10 ** (result / 2.5) - 1.0
    assert recovered_fraction == expected_fraction


def test_the_median_resists_a_few_real_variables():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """One genuine variable must not stand in for the field's precision."""
    steady = [_Star(_LightCurve([100.0, 101.0] * 5)) for _ in range(9)]
    variable = _Star(_LightCurve([50.0, 150.0] * 5))

    assert median_light_curve_scatter_mag([*steady, variable]) < 0.1


def test_detrended_fluxes_are_preferred():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Detrending is the last correction, so it is the truest series."""
    star = _Star(
        _LightCurve(
            fluxes=[50.0, 150.0] * 5,
            normalized=[80.0, 120.0] * 5,
            detrended=[100.0, 100.0] * 5,
        )
    )

    assert median_light_curve_scatter_mag([star]) == pytest.approx(0.0)


def test_a_star_with_too_few_points_is_skipped():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Two points cannot describe a scatter."""
    assert median_light_curve_scatter_mag([_Star(_LightCurve([100.0, 101.0]))]) is None


def test_non_positive_fluxes_are_ignored():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A failed measurement must not be treated as a faint one."""
    stars = [_Star(_LightCurve([100.0, 0.0, -5.0, 100.0, 100.0, 100.0]))]

    assert median_light_curve_scatter_mag(stars) == pytest.approx(0.0)


def test_no_stars_yields_no_scatter():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An empty field is not an error."""
    assert median_light_curve_scatter_mag([]) is None


def _identifier_with_separations(separations):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a StarIdentifier carrying known match separations.

    Returns
    -------
    identifier : `StarIdentifier`
        Instance with `catalog_match_separations_arcsec` populated and
        nothing else initialised.
    """
    from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier

    identifier = object.__new__(StarIdentifier)
    identifier.catalog_match_separations_arcsec = list(separations)
    return identifier


def test_residual_rms_is_the_quadratic_mean():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """RMS, not a plain average: large misses must dominate."""
    identifier = _identifier_with_separations([3.0, 4.0])

    assert identifier.get_astrometric_residual_rms_arcsec() == pytest.approx(math.sqrt(12.5), rel=1e-3)


def test_a_tight_solution_reports_a_small_residual():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Sub-arcsecond separations mean a good WCS."""
    identifier = _identifier_with_separations([0.2, 0.25, 0.3, 0.18])

    residual = identifier.get_astrometric_residual_rms_arcsec()

    assert residual is not None
    assert residual < 0.5


def test_no_catalog_matches_yields_no_residual():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A solve with nothing matched cannot report a residual."""
    assert _identifier_with_separations([]).get_astrometric_residual_rms_arcsec() is None
