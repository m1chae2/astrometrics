"""Purpose: Unit tests for CalibrationLibrary and its compatibility checks.

Description: Verifies addition of dark, bias, and flat calibration frames,
their recording behavior, and the gain/exposure compatibility checks
used to flag a mismatched calibration master.
"""

import numpy as np
from astropy.io import fits

from astrometricslib.drivers.calibration_library import (
    CalibrationLibrary,
    is_calibration_gain_compatible,
    is_dark_calibration_metadata_compatible,
)


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


def test_calibration_gain_compatible_matches():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify matching gain values are compatible."""
    assert is_calibration_gain_compatible(light_gain="100", master_gain="100")


def test_calibration_gain_incompatible_on_mismatch():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify any gain mismatch is flagged, independent of calibration type."""
    assert not is_calibration_gain_compatible(light_gain="100", master_gain="200")


def test_dark_calibration_metadata_compatible_matches_within_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a small exposure difference at matching gain is accepted."""
    assert is_dark_calibration_metadata_compatible(
        light_exposure=120.0, light_gain="100", master_exposure=120.5, master_gain="100"
    )


def test_dark_calibration_metadata_incompatible_on_gain_mismatch():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify any gain mismatch is flagged regardless of exposure match."""
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=120.0, light_gain="100", master_exposure=120.0, master_gain="200"
    )


def test_dark_calibration_metadata_incompatible_beyond_exposure_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a dark's exposure difference beyond tolerance is flagged.

    Even at otherwise matching gain.
    """
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=120.0,
        light_gain="100",
        master_exposure=125.0,
        master_gain="100",
        exposure_tolerance_seconds=1.0,
    )


def test_dark_calibration_metadata_compatible_ignores_bias_like_short_exposure_only_via_gain_check():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify bias/flat-style short exposures flag incompatible for darks.

    This documents why bias/flat masters must use
    is_calibration_gain_compatible instead -- a bias master's
    near-zero exposure would always fail the dark-specific exposure
    tolerance against a real light exposure, which is expected and
    correct for darks but would be a false positive if applied to
    bias/flat masters.
    """
    assert not is_dark_calibration_metadata_compatible(
        light_exposure=30.0, light_gain="0", master_exposure=3.2e-05, master_gain="0"
    )
