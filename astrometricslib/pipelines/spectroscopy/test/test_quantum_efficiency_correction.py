"""Purpose: Unit tests for sensor quantum-efficiency correction.

Description: Verifies quantum-efficiency curve interpolation (exact
knots, mid-point linear interpolation, edge-hold extrapolation),
correction division math, divide-by-zero floor protection, and the
conversion of a camera profile's stored curve, and the None handling for
cameras without one.
"""

import numpy as np

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_correction import (
    QuantumEfficiencyCurve,
    apply_quantum_efficiency_correction,
    curve_from_profile_record,
    interpolate_quantum_efficiency,
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


def test_the_asi533_profile_curve_becomes_matching_arrays():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the ASI533's stored curve converts to arrays of equal length."""
    record = resolve_camera_profile("ZWO ASI533MM Pro").quantum_efficiency
    assert record is not None
    curve = curve_from_profile_record(record)
    assert isinstance(curve.wavelength_nm, np.ndarray)
    assert curve.wavelength_nm.size == curve.quantum_efficiency_fraction.size
    assert curve.wavelength_nm.size > 0
    # Peak sensitivity (92%) is near 480 nm; it falls to 6% at 1000 nm.
    np.testing.assert_allclose(interpolate_quantum_efficiency(np.array([480.0, 1000.0]), curve), [0.92, 0.06])


def test_cameras_without_a_stored_curve_have_none_not_an_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies cameras without a digitized curve give None."""
    assert resolve_camera_profile("Nikon D5300").quantum_efficiency is None
    assert resolve_camera_profile("Some Unknown Camera").quantum_efficiency is None
