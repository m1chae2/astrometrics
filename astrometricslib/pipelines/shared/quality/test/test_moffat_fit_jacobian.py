"""Tests of the analytic Jacobian and the fit of the Moffat star model.

The Jacobian is checked against finite differences. The fit is checked on a
synthetic elongated star with known width, elongation and angle.
"""

import math

import numpy as np
import pytest

from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
    _fit_moffat,
    _moffat_fwhm_px,
    _moffat_jacobian,
    _moffat_model,
    _subpixel_grid,
)

BOX_SIZE = 21


@pytest.mark.parametrize("fixed_beta", [None, 2.7])
def test_analytic_jacobian_matches_finite_differences(fixed_beta: float | None) -> None:
    """Every partial derivative agrees with a central finite difference."""
    grid = _subpixel_grid(BOX_SIZE)
    generator = np.random.default_rng(7)
    for _ in range(5):
        values = [
            generator.uniform(50, 500),
            generator.uniform(8, 12),
            generator.uniform(8, 12),
            generator.uniform(1.0, 4.0),
            generator.uniform(1.0, 4.0),
            generator.uniform(-3.0, 3.0),
        ]
        if fixed_beta is None:
            values.append(generator.uniform(1.5, 5.0))
        values.append(generator.uniform(-20, 100))
        parameters = np.array(values)
        analytic = _moffat_jacobian(parameters, grid, fixed_beta)
        assert analytic.shape == (BOX_SIZE * BOX_SIZE, len(parameters))
        for column in range(len(parameters)):
            step = 1e-6 * max(1.0, abs(parameters[column]))
            shift = np.zeros_like(parameters)
            shift[column] = step
            numeric = (
                _moffat_model(parameters + shift, grid, fixed_beta)
                - _moffat_model(parameters - shift, grid, fixed_beta)
            ).ravel() / (2.0 * step)
            scale = max(1.0, float(np.abs(numeric).max()))
            assert np.abs(analytic[:, column] - numeric).max() < 1e-6 * scale


def test_fit_recovers_an_elongated_star() -> None:
    """A noisy elongated Moffat star is fitted back to its shape."""
    grid = _subpixel_grid(BOX_SIZE)
    beta, alpha_major, alpha_minor, angle = 3.0, 3.2, 2.2, 0.5
    truth = np.array([800.0, 10.2, 9.8, alpha_major, alpha_minor, angle, 100.0])
    generator = np.random.default_rng(3)
    cutout = _moffat_model(truth, grid, beta) + generator.normal(0.0, 2.0, (BOX_SIZE, BOX_SIZE))
    fit = _fit_moffat(cutout, grid, fixed_beta=beta)
    assert fit is not None
    expected_major = _moffat_fwhm_px(alpha_major, beta)
    expected_minor = _moffat_fwhm_px(alpha_minor, beta)
    assert fit.fwhm_major_px == pytest.approx(expected_major, rel=0.01)
    assert fit.fwhm_minor_px == pytest.approx(expected_minor, rel=0.01)
    assert fit.fwhm_major_px / fit.fwhm_minor_px == pytest.approx(expected_major / expected_minor, rel=0.01)
    assert math.isclose(fit.angle_rad % math.pi, angle, abs_tol=0.03)
    assert fit.center_x == pytest.approx(10.2, abs=0.05)
