"""Purpose: Unit tests for per-frame FITS background/saturation reads.

Description: Verifies measure_frame_background_level's and
measure_frame_saturated_pixel_fraction's FITS measurement against
synthetic frames with known constant/saturated pixel values.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.quality.background_measurement import (
    measure_frame_background_level,
    measure_frame_saturated_pixel_fraction,
)


def test_measure_frame_background_level_reads_known_constant_background(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify measured background matches a known constant-background frame."""
    data = np.full((100, 100), 500.0, dtype=np.float32)
    path = tmp_path / "flat_background.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_frame_background_level(str(path)) == pytest.approx(500.0)


def test_measure_frame_saturated_pixel_fraction_reads_known_saturated_frame(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify measured saturated fraction matches a known partial frame."""
    data = np.full((100, 100), 500.0, dtype=np.float32)
    data[:10, :] = 65535.0  # 10% of pixels saturated
    path = tmp_path / "partially_saturated.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_frame_saturated_pixel_fraction(str(path), 65000.0) == pytest.approx(0.1)


def test_measure_frame_saturated_pixel_fraction_reads_known_clean_frame(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unsaturated synthetic frame reports zero saturation."""
    data = np.full((100, 100), 500.0, dtype=np.float32)
    path = tmp_path / "clean.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_frame_saturated_pixel_fraction(str(path), 65000.0) == pytest.approx(0.0)


def test_measure_frame_saturated_pixel_fraction_uses_the_threshold_it_is_given(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a low threshold flags the frame and a high one does not."""
    data = np.full((100, 100), 500.0, dtype=np.float32)
    data[:10, :] = 16000.0  # a 14-bit camera's clipped pixels
    path = tmp_path / "fourteen_bit.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_frame_saturated_pixel_fraction(str(path), 15000.0) == pytest.approx(0.1)
    assert measure_frame_saturated_pixel_fraction(str(path), 65000.0) == pytest.approx(0.0)
