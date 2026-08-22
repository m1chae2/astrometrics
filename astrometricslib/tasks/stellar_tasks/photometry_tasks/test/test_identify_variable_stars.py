"""Unit tests for VariabilityAnalyzer.identify_variable_stars.

Covers the adaptive ensemble-noise-floor cutoff (field scatter median +
sigma_threshold * MAD) and its interaction with airmass-detrended flux.
"""

import pytest

from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import VariabilityAnalyzer


def _make_star(star_id: str, fluxes_normalized: list[float], fluxes_detrended: list[float] | None = None):  # ruff: ignore[missing-return-type-private-function]
    star = StellarObject(id=star_id)
    star.light_curve = LightCurve(
        fluxes_normalized=fluxes_normalized,
        fluxes_detrended=fluxes_detrended or [],
    )
    return star


def test_identify_variable_stars_flags_only_field_outliers():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a star far outside the field scatter distribution is flagged.

    A quiet field of stars all near 1% scatter, plus one genuinely
    variable star at 15% scatter, should flag only the variable one.
    """
    analyzer = VariabilityAnalyzer()
    quiet_fluxes = [1.00, 1.01, 0.99, 1.00, 1.01, 0.99, 1.00]
    variable_fluxes = [1.00, 1.15, 0.85, 1.15, 0.85, 1.00, 1.15]
    analyzer.stellar_objects = [
        _make_star("quiet_1", quiet_fluxes),
        _make_star("quiet_2", quiet_fluxes),
        _make_star("quiet_3", quiet_fluxes),
        _make_star("variable_1", variable_fluxes),
    ]

    variable_candidates = analyzer.identify_variable_stars()

    assert [star.id for star in variable_candidates] == ["variable_1"]


def test_identify_variable_stars_uses_detrended_flux_when_present():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify CV is computed from fluxes_detrended, not fluxes_normalized.

    A star whose raw normalized flux carries an airmass-extinction
    slope (high scatter) but whose detrended flux is flat (low
    scatter) should be judged on the detrended values.
    """
    analyzer = VariabilityAnalyzer()
    trending_normalized = [1.00, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70]
    flat_detrended = [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00]
    star = _make_star("extinction_trend_star", trending_normalized, flat_detrended)
    other_quiet_stars = [_make_star(f"quiet_{i}", flat_detrended) for i in range(3)]
    analyzer.stellar_objects = [star, *other_quiet_stars]

    analyzer.identify_variable_stars()

    assert star.coefficient_of_variation == pytest.approx(0.0)


def test_identify_variable_stars_adaptive_cutoff_is_not_capped_at_ten_percent():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a noisy field's adaptive cutoff isn't overridden by a flat 10%.

    When the field's own scatter is already elevated (e.g. poor
    transparency), a star sitting just above that flat 10% line but
    within the field's normal noise should NOT be flagged -- otherwise
    the adaptive floor does nothing in exactly the conditions it exists
    for.
    """
    analyzer = VariabilityAnalyzer()
    # Every star in this noisy field scatters at ~12%, well above the
    # old flat 10% cutoff, but consistent with each other.
    noisy_fluxes = [1.00, 1.12, 0.88, 1.12, 0.88, 1.00, 1.12]
    analyzer.stellar_objects = [_make_star(f"noisy_{i}", noisy_fluxes) for i in range(5)]

    variable_candidates = analyzer.identify_variable_stars()

    assert variable_candidates == []
