"""Purpose: Unit tests for AstrometricsImage.

Description: Verifies FITS header/data lazy-loading and the auto-repair
path for deprecated headers.
"""

import os

import numpy as np
from astropy.io import fits
from astropy.time import Time

from astrometricslib.utilities.image import AstrometricsImage


def _make_deprecated_header_fits(path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a FITS file with a deprecated RADECSYS header and no MJD-OBS."""
    arr = np.zeros((5, 5), dtype=np.float32)
    hdu = fits.PrimaryHDU(arr)
    hdu.header["RADECSYS"] = "FK5"
    hdu.header["DATE-OBS"] = "2024-01-01T00:00:00"
    hdu.writeto(path, overwrite=True)


def _make_clean_header_fits(path):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a FITS file with no deprecated headers."""
    arr = np.zeros((5, 5), dtype=np.float32)
    hdu = fits.PrimaryHDU(arr)
    hdu.header["DATE-OBS"] = "2024-01-01T00:00:00"
    hdu.header["MJD-OBS"] = Time(hdu.header["DATE-OBS"]).mjd
    hdu.writeto(path, overwrite=True)


def test_auto_repair_fixes_deprecated_headers_and_preserves_dtype(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify repair rewrites RADECSYS/MJD-OBS without changing pixel dtype."""
    path = str(tmp_path / "deprecated.fits")
    _make_deprecated_header_fits(path)
    original_dtype = fits.getdata(path).dtype

    image = AstrometricsImage(path)
    header = image.header

    assert "RADECSYS" not in header
    assert header["RADESYS"] == "FK5"
    assert "MJD-OBS" in header
    assert fits.getdata(path).dtype == original_dtype


def test_auto_repair_leaves_clean_file_untouched(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verifies a file with no deprecated headers is never rewritten."""
    path = str(tmp_path / "clean.fits")
    _make_clean_header_fits(path)

    before_mtime = os.path.getmtime(path)
    image = AstrometricsImage(path)
    _ = image.header
    after_mtime = os.path.getmtime(path)

    assert before_mtime == after_mtime


def test_auto_repair_reduces_fits_open_calls_for_clean_file(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a header access on a clean file only opens the file once."""
    path = str(tmp_path / "clean_spy.fits")
    _make_clean_header_fits(path)

    spy = mocker.spy(fits, "open")
    image = AstrometricsImage(path)
    _ = image.header

    assert spy.call_count == 1


def test_auto_repair_reduces_fits_open_calls_for_repaired_file(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a header access on a file needing repair opens the file twice."""
    path = str(tmp_path / "deprecated_spy.fits")
    _make_deprecated_header_fits(path)

    spy = mocker.spy(fits, "open")
    image = AstrometricsImage(path)
    _ = image.header

    assert spy.call_count == 2
