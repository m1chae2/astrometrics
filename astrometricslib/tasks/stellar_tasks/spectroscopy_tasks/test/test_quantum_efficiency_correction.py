"""Purpose: Unit tests for sensor quantum-efficiency correction.

Description: Verifies quantum-efficiency curve interpolation (exact
knots, mid-point linear interpolation, edge-hold extrapolation),
correction division math, divide-by-zero floor protection, and the
per-camera curve lookup's None-handling for unregistered cameras.
"""

import numpy as np

from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.quantum_efficiency_correction import (
    apply_quantum_efficiency_correction,
    interpolate_quantum_efficiency,
)
from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.quantum_efficiency_curves import (
    QuantumEfficiencyCurve,
    get_quantum_efficiency_curve,
)


def _make_test_curve() -> QuantumEfficiencyCurve:
    """Build a small synthetic curve for isolated interpolation tests.

    Returns
    -------
    QuantumEfficiencyCurve
        A 3-knot curve spanning 400-600 nm.
    """
    return QuantumEfficiencyCurve(
        wavelength_nm=np.array([400.0, 500.0, 600.0]),
        quantum_efficiency_fraction=np.array([0.80, 0.90, 0.40]),
    )


def test_interpolate_quantum_efficiency_at_exact_knots():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify interpolation at exact knots returns the stored value."""
    curve = _make_test_curve()
    result = interpolate_quantum_efficiency(np.array([400.0, 500.0, 600.0]), curve)
    np.testing.assert_allclose(result, [0.80, 0.90, 0.40])


def test_interpolate_quantum_efficiency_midpoint():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies linear interpolation between two digitized knots."""
    curve = _make_test_curve()
    # Halfway between 400nm (0.80) and 500nm (0.90) should be 0.85.
    result = interpolate_quantum_efficiency(np.array([450.0]), curve)
    np.testing.assert_allclose(result, [0.85])


def test_interpolate_quantum_efficiency_edge_hold_extrapolation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify out-of-range wavelengths edge-hold, not extrapolate."""
    curve = _make_test_curve()
    result = interpolate_quantum_efficiency(np.array([300.0, 700.0]), curve)
    np.testing.assert_allclose(result, [0.80, 0.40])


def test_apply_quantum_efficiency_correction_known_division():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a known QE value produces the expected corrected intensity."""
    curve = _make_test_curve()
    # QE = 0.90 at 500nm, so raw intensity of 100 should correct to ~111.11.
    corrected = apply_quantum_efficiency_correction(
        wavelength_nm=np.array([500.0]), intensity=np.array([100.0]), curve=curve
    )
    np.testing.assert_allclose(corrected, [100.0 / 0.90])


def test_apply_quantum_efficiency_correction_floors_near_zero_quantum_efficiency():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the minimum-QE floor prevents divide-by-zero/blow-up."""
    curve = QuantumEfficiencyCurve(
        wavelength_nm=np.array([400.0, 500.0]),
        quantum_efficiency_fraction=np.array([0.0, 0.0]),
    )
    corrected = apply_quantum_efficiency_correction(
        wavelength_nm=np.array([450.0]),
        intensity=np.array([100.0]),
        curve=curve,
        minimum_quantum_efficiency_fraction=0.01,
    )
    assert np.all(np.isfinite(corrected))
    np.testing.assert_allclose(corrected, [100.0 / 0.01])


def test_get_quantum_efficiency_curve_known_camera():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the ZWO ASI533MM Pro curve is registered with real data."""
    curve = get_quantum_efficiency_curve("ZWO ASI533MM Pro")
    assert curve is not None
    assert curve.wavelength_nm.size == curve.quantum_efficiency_fraction.size
    assert curve.wavelength_nm.size > 0


def test_get_quantum_efficiency_curve_unregistered_camera_returns_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies cameras without a digitized curve return None, not an error."""
    assert get_quantum_efficiency_curve("Nikon D5300") is None
    assert get_quantum_efficiency_curve("Some Unknown Camera") is None
