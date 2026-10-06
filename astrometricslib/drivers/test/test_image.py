"""Purpose: Unit tests for AstrometricsImage.

Description: Verifies FITS header/data lazy-loading and the in-memory fix for
deprecated headers. Reading a file must never change it.
"""

import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError


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


def test_deprecated_headers_are_fixed_in_memory_only(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the header comes back fixed while the file stays as it was."""
    path = str(tmp_path / "deprecated.fits")
    _make_deprecated_header_fits(path)
    with open(path, "rb") as handle:
        original_bytes = handle.read()
    before_mtime = os.path.getmtime(path)

    image = AstrometricsImage(path)
    header = image.header

    assert "RADECSYS" not in header
    assert header["RADESYS"] == "FK5"
    assert "MJD-OBS" in header
    with open(path, "rb") as handle:
        assert handle.read() == original_bytes
    assert os.path.getmtime(path) == before_mtime
    assert "RADECSYS" in fits.getheader(path)


def test_clean_file_is_untouched(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verifies a file with no deprecated headers is never rewritten."""
    path = str(tmp_path / "clean.fits")
    _make_clean_header_fits(path)

    before_mtime = os.path.getmtime(path)
    image = AstrometricsImage(path)
    _ = image.header
    after_mtime = os.path.getmtime(path)

    assert before_mtime == after_mtime


def test_header_access_opens_clean_file_once(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a header access on a clean file only opens the file once."""
    path = str(tmp_path / "clean_spy.fits")
    _make_clean_header_fits(path)

    spy = mocker.spy(fits, "open")
    image = AstrometricsImage(path)
    _ = image.header

    assert spy.call_count == 1


def test_header_access_opens_deprecated_file_once(tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify fixing a header in memory needs no second open of the file."""
    path = str(tmp_path / "deprecated_spy.fits")
    _make_deprecated_header_fits(path)

    spy = mocker.spy(fits, "open")
    image = AstrometricsImage(path)
    _ = image.header

    assert spy.call_count == 1


def test_a_missing_file_raises_not_found(tmp_path: Path) -> None:
    """Reading the header of a file that is not there raises NotFoundError."""
    with pytest.raises(NotFoundError, match="Image not found"):
        _ = AstrometricsImage(str(tmp_path / "missing.fits")).header
