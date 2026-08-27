"""Tests for the shared "which HDU actually holds the data" rule.

Every test here writes a FITS file with the image split across HDU0
(bare, no data) and HDU1 (the real data), the exact shape that has
caused silent data loss before -- see the module docstring on
`image_type.py`. Each test proves the function under test reads HDU1's
header/data correctly rather than HDU0's near-empty one.
"""

import numpy as np
from astropy.io import fits

from astrometricslib.data_access import image_type


def _write_two_hdu_frame(path, width, height, bayer_pattern=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a FITS file with a bare HDU0 and the real image in HDU1.

    Returns
    -------
    path : `str`
        The path just written, as a string.
    """
    primary = fits.PrimaryHDU()
    image_header = fits.Header()
    if bayer_pattern is not None:
        image_header["BAYERPAT"] = bayer_pattern
    image_hdu = fits.ImageHDU(data=np.zeros((height, width), dtype=np.float32), header=image_header)
    fits.HDUList([primary, image_hdu]).writeto(path, overwrite=True)
    return str(path)


def _write_single_hdu_frame(path, width, height, bayer_pattern=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Write a plain, single-HDU FITS file.

    Returns
    -------
    path : `str`
        The path just written, as a string.
    """
    header = fits.Header()
    if bayer_pattern is not None:
        header["BAYERPAT"] = bayer_pattern
    fits.PrimaryHDU(data=np.zeros((height, width), dtype=np.float32), header=header).writeto(
        path, overwrite=True
    )
    return str(path)


def test_read_header_falls_back_to_hdu1_when_hdu0_is_bare(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the header comes from HDU1, not HDU0's near-empty one."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40, bayer_pattern="RGGB")

    header = image_type.read_header(path)

    assert header["NAXIS1"] == 60
    assert header["NAXIS2"] == 40
    assert header["BAYERPAT"] == "RGGB"


def test_read_header_uses_hdu0_when_it_has_data(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a normal single-HDU file is read from HDU0 as expected."""
    path = _write_single_hdu_frame(tmp_path / "frame.fits", 60, 40)

    header = image_type.read_header(path)

    assert header["NAXIS1"] == 60
    assert header["NAXIS2"] == 40


def test_read_data_falls_back_to_hdu1_when_hdu0_is_bare(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the pixel data comes from HDU1, not a missing HDU0 array."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    data = image_type.read_data(path)

    assert data is not None
    assert data.shape == (40, 60)


def test_frame_dimensions_falls_back_to_hdu1(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify dimensions are read from HDU1 rather than reported as unknown.

    Before this module existed, `select_dominant_frame_dimensions`
    read `fits.getheader(path)` directly (HDU0 only), so a frame stored
    this way had no NAXIS1/NAXIS2 to find and was silently treated as
    unreadable.
    """
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    assert image_type.frame_dimensions(path) == (60, 40)


def test_frame_dimensions_is_none_for_an_unreadable_file(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a file that cannot be read returns None, not an exception."""
    missing_path = str(tmp_path / "does_not_exist.fits")

    assert image_type.frame_dimensions(missing_path) is None


def test_frame_uses_color_filter_array_falls_back_to_hdu1(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify BAYERPAT is found in HDU1, not missed because HDU0 lacks it.

    This is the exact bug behind the real ZWO ASI 533MM Pro incident
    described in the CFA-detection docstring: a frame whose Bayer
    pattern lives in HDU1 must not be misread as monochrome.
    """
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40, bayer_pattern="RGGB")

    assert image_type.frame_uses_color_filter_array(path) is True


def test_frame_uses_color_filter_array_is_false_when_absent(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no BAYERPAT reads as a definitive, non-guessing "mono"."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    assert image_type.frame_uses_color_filter_array(path) is False


def test_frame_uses_color_filter_array_is_none_for_an_unreadable_file(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unreadable file is None, not misread as a definitive mono."""
    missing_path = str(tmp_path / "does_not_exist.fits")

    assert image_type.frame_uses_color_filter_array(missing_path) is None


def test_select_dominant_frame_dimensions_reads_hdu1_geometry(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the dominant-geometry filter itself sees HDU1-stored frames.

    Four majority-geometry frames stored the HDU0/HDU1 way against one
    stray also stored that way -- both must be read correctly for the
    filter to keep the right four and drop the stray, rather than
    treating all five as "unreadable" and keeping them all.
    """
    majority = [_write_two_hdu_frame(tmp_path / f"m{i}.fits", 60, 40) for i in range(4)]
    stray = _write_two_hdu_frame(tmp_path / "stray.fits", 30, 20)

    kept, dominant_dimensions = image_type.select_dominant_frame_dimensions([*majority, stray])

    assert kept == set(majority)
    assert dominant_dimensions == (60, 40)


def test_select_dominant_frame_dimensions_keeps_unreadable_frames(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a frame with no readable header is kept, not dropped."""
    good = [_write_single_hdu_frame(tmp_path / f"g{i}.fits", 60, 40) for i in range(3)]
    broken_path = str(tmp_path / "broken.fits")
    (tmp_path / "broken.fits").write_text("not a fits file")

    kept, dimensions = image_type.select_dominant_frame_dimensions([*good, broken_path])

    assert kept == {*good, broken_path}
    assert dimensions == (60, 40)


def test_select_dominant_frame_dimensions_of_an_empty_list():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the empty-input edge case returns an empty set, no dimensions."""
    assert image_type.select_dominant_frame_dimensions([]) == (set(), None)
