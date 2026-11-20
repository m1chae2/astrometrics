"""Unit tests for the variability helpers of `VariabilityAnalyzer`.

Covers the ensemble and variability helpers that stay on
`VariabilityAnalyzer` and its module-level functions. Tests of the
per-frame helpers themselves (aperture flux, saturation flagging, centroid
re-location, frame offset) live in `test_frame_photometry.py`.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    _adaptive_cv_cutoff,
    _compute_star_coefficients_of_variation,
)


def test_adaptive_cv_cutoff_matches_a_hand_computed_mad() -> None:
    """Pins the cutoff, now computed via `scipy.stats.median_abs_deviation`."""
    cv_list = [0.01, 0.02, 0.02, 0.03, 0.08]

    result = _adaptive_cv_cutoff(cv_list, sigma_threshold=3.0)

    median_cv = float(np.median(cv_list))
    hand_rolled_mad = float(np.median(np.abs(np.array(cv_list) - median_cv)))
    assert result.cutoff == pytest.approx(max(0.02, median_cv + 3.0 * max(1e-4, hand_rolled_mad)))
    assert result.median_cv == pytest.approx(median_cv)
    assert result.mad_cv == pytest.approx(hand_rolled_mad)


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
