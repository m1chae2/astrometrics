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


def test_collapse_to_2d_leaves_a_2d_array_unchanged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a plain mono frame passes through untouched."""
    data = np.arange(12, dtype=float).reshape(3, 4)

    result = image_type.collapse_to_2d(data)

    assert result is data


def test_collapse_to_2d_averages_a_leading_rgb_channel_axis():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a (3, H, W) debayered stack collapses across its channel axis."""
    data = np.stack([np.full((5, 6), value, dtype=float) for value in (10.0, 20.0, 30.0)])

    result = image_type.collapse_to_2d(data)

    assert result.shape == (5, 6)
    assert np.allclose(result, 20.0)


def test_collapse_to_2d_averages_a_trailing_channel_axis():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an (H, W, 3) array collapses across its trailing axis."""
    data = np.stack([np.full((5, 6), value, dtype=float) for value in (10.0, 20.0, 30.0)], axis=-1)

    result = image_type.collapse_to_2d(data)

    assert result.shape == (5, 6)
    assert np.allclose(result, 20.0)


def test_collapse_to_2d_handles_a_degenerate_single_channel_cube():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Regression test for the (1, H, W) bug this function replaces.

    Every call site this function consolidates checked `shape[0] in (3,
    4)` before this fix -- not `(1, 3, 4)`. On a `(1, height, width)`
    array that check is False, so the old code fell through to
    `np.mean(data, axis=-1)`, averaging away the image's own *width*
    instead of the degenerate channel axis, and produced a garbage
    `(1, height)` result. This must produce a real `(height, width)`
    image instead.
    """
    data = np.arange(30, dtype=float).reshape(1, 5, 6)

    result = image_type.collapse_to_2d(data)

    assert result.shape == (5, 6)
    assert np.array_equal(result, data[0])
