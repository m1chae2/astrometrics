"""Purpose: Unit tests for CalibrationLibrary.

Description: Verifies addition of dark, bias, and flat calibration frames,
and their persistence behavior.
"""

import numpy as np
from astropy.io import fits

from astrometricslib.utilities.calibration_library import CalibrationLibrary


def _make_small_fits(path, shape=(20, 20)):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Generate a small dummy FITS calibration frame."""
    arr = np.zeros(shape, dtype=np.float32)
    hdu = fits.PrimaryHDU(arr)
    # Calibration code checks INSTRUME header for specific camera strings;
    # set it to a commonly-checked value so the code path executes.
    hdu.header["INSTRUME"] = "Nikon D5300"
    hdu.writeto(path)


def test_add_dark_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify dark frames can be registered into the library and saved."""
    calibration_frames = CalibrationLibrary()
    dark_files = [str(tmp_path / f"dark_{i}.fits") for i in (1, 2, 3)]
    for p in dark_files:
        _make_small_fits(p)
        calibration_frames.add_dark_frame(p)
    calibration_frames.save_library()


def test_add_bias_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify bias frames can be registered into the library and saved."""
    calibration_frames = CalibrationLibrary()
    bias_files = [str(tmp_path / f"bias_{i}.fits") for i in (1, 2)]
    for p in bias_files:
        _make_small_fits(p)
        calibration_frames.add_bias_frame(p)
    calibration_frames.save_library()


def test_add_flat_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify flat frames can be registered into the library and saved."""
    calibration_frames = CalibrationLibrary()
    flat_files = [str(tmp_path / f"flat_{i}.fits") for i in (1, 2)]
    for p in flat_files:
        _make_small_fits(p)
        calibration_frames.add_flat_frame(p)
    calibration_frames.save_library()
