"""Unit tests for monochrome/CFA sensor detection in the Siril driver.

Regression coverage for a real defect: the Siril calibration script
hardcoded ``-cfa -equalize_cfa -debayer``, so monochrome frames from a
ZWO ASI 533MM Pro were demosaiced against a Bayer pattern Siril had
merely guessed. Every stacked product came out 3-channel RGB instead of
2D mono.
"""

import numpy as np
from astropy.io import fits

from astrometricslib.drivers.siril_interface import _frames_use_color_filter_array


def _write_frame(directory, name, bayer_pattern=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a small FITS frame, optionally declaring a Bayer pattern.

    Returns
    -------
    path : `pathlib.Path`
        Location of the frame just written.
    """
    header = fits.Header()
    if bayer_pattern is not None:
        header["BAYERPAT"] = bayer_pattern
    path = directory / name
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16), header=header).writeto(path)
    return path


def test_monochrome_frames_without_bayer_keyword_are_not_cfa(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A mono frame declares no BAYERPAT, so no CFA flags may be applied."""
    _write_frame(tmp_path, "light_00001.fits")

    assert _frames_use_color_filter_array(str(tmp_path)) is False


def test_frames_declaring_a_bayer_pattern_are_cfa(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A color sensor writes BAYERPAT; debayering it is correct."""
    _write_frame(tmp_path, "light_00001.fits", bayer_pattern="RGGB")

    assert _frames_use_color_filter_array(str(tmp_path)) is True


def test_blank_bayer_keyword_is_treated_as_monochrome(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An empty BAYERPAT names no pattern, so no debayering is applied."""
    _write_frame(tmp_path, "light_00001.fits", bayer_pattern="   ")

    assert _frames_use_color_filter_array(str(tmp_path)) is False


def test_unreadable_frame_does_not_decide_the_stack(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A corrupt first frame is skipped in favour of a readable one."""
    (tmp_path / "aaa_broken.fits").write_bytes(b"not a FITS file")
    _write_frame(tmp_path, "bbb_light.fits", bayer_pattern="RGGB")

    assert _frames_use_color_filter_array(str(tmp_path)) is True


def test_missing_directory_defaults_to_monochrome(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Absent frames must not cause a guessed debayer; mono is the default."""
    assert _frames_use_color_filter_array(str(tmp_path / "does_not_exist")) is False


def test_empty_directory_defaults_to_monochrome(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """No frames means no evidence of a CFA sensor."""
    assert _frames_use_color_filter_array(str(tmp_path)) is False


def test_subdirectories_are_ignored(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Only files are inspected; a stray subdirectory cannot break this."""
    (tmp_path / "aaa_subdir").mkdir()
    _write_frame(tmp_path, "bbb_light.fits", bayer_pattern="GRBG")

    assert _frames_use_color_filter_array(str(tmp_path)) is True


def test_a_bayerpat_stored_in_hdu1_is_still_found(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Regression test for the second live HDU0/HDU1 bug this function had.

    Before this was routed through `image_type.frame_uses_color_filter_array`,
    this function read `fits.getheader(frame_path)` directly -- HDU0 only.
    A frame whose real header (and BAYERPAT) lives in HDU1, because HDU0
    is a bare primary HDU with no data, would read back with no BAYERPAT
    at all and be misclassified as monochrome, silently skipping the
    debayer step a CFA sensor's frames actually need.
    """
    primary = fits.PrimaryHDU()
    image_header = fits.Header()
    image_header["BAYERPAT"] = "RGGB"
    image_hdu = fits.ImageHDU(data=np.zeros((4, 4), dtype=np.uint16), header=image_header)
    fits.HDUList([primary, image_hdu]).writeto(tmp_path / "light_00001.fits")

    assert _frames_use_color_filter_array(str(tmp_path)) is True
