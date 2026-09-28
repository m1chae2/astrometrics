"""Unit tests for saturation flagging in forced-aperture photometry.

Verifies _measure_flux_numpy's saturation detection, which flags
individual flux measurements as untrustworthy when the star's own
aperture (not the surrounding background/annulus) is significantly
saturated, plus the ensemble/variability helpers that stay on
`VariabilityAnalyzer` and its module-level functions. Tests of the
per-frame helpers themselves (aperture flux, centroid re-location,
frame offset) live in `test_frame_photometry.py`.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.variability_analyzer import (
    VariabilityAnalyzer,
    _adaptive_cv_cutoff,
    _compute_star_coefficients_of_variation,
)


def test_adaptive_cv_cutoff_matches_a_hand_computed_mad():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Pins the cutoff, now computed via `scipy.stats.median_abs_deviation`."""
    cv_list = [0.01, 0.02, 0.02, 0.03, 0.08]

    cutoff = _adaptive_cv_cutoff(cv_list, sigma_threshold=3.0)

    median_cv = float(np.median(cv_list))
    hand_rolled_mad = float(np.median(np.abs(np.array(cv_list) - median_cv)))
    assert cutoff == pytest.approx(max(0.02, median_cv + 3.0 * max(1e-4, hand_rolled_mad)))


def test_measure_flux_numpy_unsaturated_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a normal, unsaturated star aperture is not flagged."""
    data = np.full((100, 100), 500.0)
    data[45:55, 45:55] += 3000.0  # bright but unsaturated star

    analyzer = VariabilityAnalyzer()
    flux, is_saturated = analyzer._measure_flux_numpy(data, 50, 50, saturation_threshold_adu=65000.0)
    assert flux > 0
    assert is_saturated is False


def test_measure_flux_numpy_saturated_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a star aperture mostly at the saturation ADU is flagged."""
    data = np.full((100, 100), 500.0)
    data[46:54, 46:54] = 65535.0  # saturated core within the 4px-radius aperture

    analyzer = VariabilityAnalyzer()
    _flux, is_saturated = analyzer._measure_flux_numpy(data, 50, 50, saturation_threshold_adu=65000.0)
    assert is_saturated is True


def test_measure_flux_numpy_uses_the_threshold_it_is_given():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a low threshold flags the star and a high one does not."""
    data = np.full((100, 100), 500.0)
    data[46:54, 46:54] = 16000.0  # a 14-bit camera's clipped core

    analyzer = VariabilityAnalyzer()
    _flux, saturated_for_low = analyzer._measure_flux_numpy(data, 50, 50, saturation_threshold_adu=15000.0)
    _flux, saturated_for_high = analyzer._measure_flux_numpy(data, 50, 50, saturation_threshold_adu=65000.0)
    assert saturated_for_low is True
    assert saturated_for_high is False


def test_measure_flux_numpy_out_of_bounds_returns_unsaturated_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an out-of-bounds star returns (0.0, False), not raises."""
    data = np.full((20, 20), 500.0)
    analyzer = VariabilityAnalyzer()
    flux, is_saturated = analyzer._measure_flux_numpy(data, 1, 1, saturation_threshold_adu=65000.0)
    assert flux == pytest.approx(0.0)
    assert is_saturated is False


def test_the_variability_step_keeps_the_catalog_magnitude() -> None:
    """An instrument magnitude in `star_data` must not replace the catalog one.

    Regression: the step copied the star finder's instrument magnitude over
    `magnitude`, so BD+33 3249 showed -14.5 instead of its catalog value.
    """
    catalog_star = StellarObject(id="cataloged", name="cataloged")
    catalog_star.magnitude = 9.0
    catalog_star.star_data = {"mag": -14.5}
    catalog_star.photometry = PhotometryResult(fluxes_normalized=[1.0, 1.1, 0.9, 1.0])
    unknown_star = StellarObject(id="unknown", name="unknown")
    unknown_star.star_data = {"mag": -11.6}
    unknown_star.photometry = PhotometryResult(fluxes_normalized=[1.0, 1.1, 0.9, 1.0])

    _compute_star_coefficients_of_variation([catalog_star, unknown_star])

    assert catalog_star.magnitude == pytest.approx(9.0)
    assert unknown_star.magnitude is None
