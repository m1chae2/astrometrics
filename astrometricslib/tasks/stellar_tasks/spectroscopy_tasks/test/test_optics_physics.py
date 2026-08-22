"""Purpose: Unit Tests for Spectroscopy Optics Physics Utilities.

Description: Provides test cases to mathematically audit first-order
transmission grating dispersion modeling, verifying round-trip
transformations and correct behavior for physical constants.
"""

import numpy as np
import pytest

from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.optics_physics import (
    calculate_pixel_offset,
    calculate_wavelength,
)


def test_wavelength_round_trip():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify pixel-offset-to-wavelength-and-back round trips exactly.

    Converting from pixel offset to physical wavelength and then back
    to pixel offset must match exactly within floating-point tolerance.
    """
    # Standard hardware constants (ZWO ASI533MM Pro and standard grating
    # spacer)
    grating_distance_mm = 16.5
    lines_per_mm = 100.0
    pixel_size_um = 3.76

    pixel_offsets = [0.0, 120.0, 450.0, 750.0]

    for offset in pixel_offsets:
        # 1. Forward calculation: pixel -> nm
        wl_nm = calculate_wavelength(
            pixel_offset_px=offset,
            grating_distance_mm=grating_distance_mm,
            lines_per_mm=lines_per_mm,
            pixel_size_um=pixel_size_um,
        )

        # 2. Reverse calculation: nm -> pixel
        reconstructed_offset = calculate_pixel_offset(
            wavelength_nm=wl_nm,
            grating_distance_mm=grating_distance_mm,
            lines_per_mm=lines_per_mm,
            pixel_size_um=pixel_size_um,
        )

        # Verify they match within 1e-9 absolute difference
        assert pytest.approx(offset, abs=1e-9) == reconstructed_offset


def test_numpy_vectorization():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify calculate_wavelength/calculate_pixel_offset vectorize.

    Both functions must behave correctly when processing vectorized
    NumPy arrays instead of single scalars.
    """
    grating_distance_mm = 18.2
    lines_per_mm = 100.0
    pixel_size_um = 3.76

    offsets = np.array([0.0, 100.0, 300.0, 500.0])

    wls = calculate_wavelength(
        pixel_offset_px=offsets,
        grating_distance_mm=grating_distance_mm,
        lines_per_mm=lines_per_mm,
        pixel_size_um=pixel_size_um,
    )

    assert isinstance(wls, np.ndarray)
    assert len(wls) == 4

    reconstructed = calculate_pixel_offset(
        wavelength_nm=wls,
        grating_distance_mm=grating_distance_mm,
        lines_per_mm=lines_per_mm,
        pixel_size_um=pixel_size_um,
    )

    np.testing.assert_allclose(offsets, reconstructed, atol=1e-9)


def test_optics_wavelength_scaling():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies that wavelength increases monotonically with pixel offset,.

    confirming correct geometry under larger diffraction angles.
    """
    grating_distance_mm = 16.5
    lines_per_mm = 100.0
    pixel_size_um = 3.76

    wl_small = calculate_wavelength(100.0, grating_distance_mm, lines_per_mm, pixel_size_um)
    wl_large = calculate_wavelength(500.0, grating_distance_mm, lines_per_mm, pixel_size_um)

    assert wl_large > wl_small
    assert wl_small > 0.0
