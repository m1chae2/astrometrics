"""Tests for the shared "which HDU actually holds the data" rule.

Every test here writes a FITS file with the image split across HDU0
(bare, no data) and HDU1 (the real data), the exact shape that has
caused silent data loss before -- see the module docstring on
`fits_access.py`. Each test proves the function under test reads HDU1's
header/data correctly rather than HDU0's near-empty one.

The last tests cover `header_is_plate_solved`, the one test of whether a
header's sky map came from a plate solve.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib._internal.drivers import fits_access


def _write_two_hdu_frame(path, width, height, bayer_pattern=None) -> str:  # ruff: ignore[missing-type-function-argument]
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


def _write_single_hdu_frame(path, width, height, bayer_pattern=None) -> str:  # ruff: ignore[missing-type-function-argument]
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


def test_read_header_falls_back_to_hdu1_when_hdu0_is_bare(tmp_path: Path) -> None:
    """Verify the header comes from HDU1, not HDU0's near-empty one."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40, bayer_pattern="RGGB")

    header = fits_access.read_header(path)

    assert header["NAXIS1"] == 60
    assert header["NAXIS2"] == 40
    assert header["BAYERPAT"] == "RGGB"


def test_read_header_uses_hdu0_when_it_has_data(tmp_path: Path) -> None:
    """Verify a normal single-HDU file is read from HDU0 as expected."""
    path = _write_single_hdu_frame(tmp_path / "frame.fits", 60, 40)

    header = fits_access.read_header(path)

    assert header["NAXIS1"] == 60
    assert header["NAXIS2"] == 40


def test_read_data_falls_back_to_hdu1_when_hdu0_is_bare(tmp_path: Path) -> None:
    """Verify the pixel data comes from HDU1, not a missing HDU0 array."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    data = fits_access.read_data(path)

    assert data is not None
    assert data.shape == (40, 60)


def test_frame_dimensions_falls_back_to_hdu1(tmp_path: Path) -> None:
    """Verify dimensions are read from HDU1 rather than reported as unknown.

    Before this module existed, `select_dominant_frame_dimensions`
    read `fits.getheader(path)` directly (HDU0 only), so a frame stored
    this way had no NAXIS1/NAXIS2 to find and was silently treated as
    unreadable.
    """
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    assert fits_access.frame_dimensions(path) == (60, 40)


def test_frame_dimensions_is_none_for_an_unreadable_file(tmp_path: Path) -> None:
    """Verify a file that cannot be read returns None, not an exception."""
    missing_path = str(tmp_path / "does_not_exist.fits")

    assert fits_access.frame_dimensions(missing_path) is None


def test_frame_uses_color_filter_array_falls_back_to_hdu1(tmp_path: Path) -> None:
    """Verify BAYERPAT is found in HDU1, not missed because HDU0 lacks it.

    This is the exact bug behind the real ZWO ASI 533MM Pro incident
    described in the CFA-detection docstring: a frame whose Bayer
    pattern lives in HDU1 must not be misread as monochrome.
    """
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40, bayer_pattern="RGGB")

    assert fits_access.frame_uses_color_filter_array(path) is True


def test_frame_uses_color_filter_array_is_false_when_absent(tmp_path: Path) -> None:
    """Verify no BAYERPAT reads as a definitive, non-guessing "mono"."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    assert fits_access.frame_uses_color_filter_array(path) is False


def test_frame_uses_color_filter_array_is_none_for_an_unreadable_file(tmp_path: Path) -> None:
    """Verify an unreadable file is None, not misread as a definitive mono."""
    missing_path = str(tmp_path / "does_not_exist.fits")

    assert fits_access.frame_uses_color_filter_array(missing_path) is None


def test_select_dominant_frame_dimensions_reads_hdu1_geometry(tmp_path: Path) -> None:
    """Verify the dominant-geometry filter itself sees HDU1-stored frames.

    Four majority-geometry frames stored the HDU0/HDU1 way against one
    stray also stored that way -- both must be read correctly for the
    filter to keep the right four and drop the stray, rather than
    treating all five as "unreadable" and keeping them all.
    """
    majority = [_write_two_hdu_frame(tmp_path / f"m{i}.fits", 60, 40) for i in range(4)]
    stray = _write_two_hdu_frame(tmp_path / "stray.fits", 30, 20)

    kept, dominant_dimensions = fits_access.select_dominant_frame_dimensions([*majority, stray])

    assert kept == set(majority)
    assert dominant_dimensions == (60, 40)


def test_select_dominant_frame_dimensions_keeps_unreadable_frames(tmp_path: Path) -> None:
    """Verify a frame with no readable header is kept, not dropped."""
    good = [_write_single_hdu_frame(tmp_path / f"g{i}.fits", 60, 40) for i in range(3)]
    broken_path = str(tmp_path / "broken.fits")
    (tmp_path / "broken.fits").write_text("not a fits file")

    kept, dimensions = fits_access.select_dominant_frame_dimensions([*good, broken_path])

    assert kept == {*good, broken_path}
    assert dimensions == (60, 40)


def test_select_dominant_frame_dimensions_of_an_empty_list() -> None:
    """Verify the empty-input edge case returns an empty set, no dimensions."""
    assert fits_access.select_dominant_frame_dimensions([]) == (set(), None)


def test_collapse_to_2d_leaves_a_2d_array_unchanged() -> None:
    """Verify a plain mono frame passes through untouched."""
    data = np.arange(12, dtype=float).reshape(3, 4)

    result = fits_access.collapse_to_2d(data)

    assert result is data


def test_collapse_to_2d_averages_a_leading_rgb_channel_axis() -> None:
    """Verify a (3, H, W) debayered stack collapses across its channel axis."""
    data = np.stack([np.full((5, 6), value, dtype=float) for value in (10.0, 20.0, 30.0)])

    result = fits_access.collapse_to_2d(data)

    assert result.shape == (5, 6)
    assert np.allclose(result, 20.0)


def test_collapse_to_2d_averages_a_trailing_channel_axis() -> None:
    """Verify an (H, W, 3) array collapses across its trailing axis."""
    data = np.stack([np.full((5, 6), value, dtype=float) for value in (10.0, 20.0, 30.0)], axis=-1)

    result = fits_access.collapse_to_2d(data)

    assert result.shape == (5, 6)
    assert np.allclose(result, 20.0)


def test_collapse_to_2d_handles_a_degenerate_single_channel_cube() -> None:
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

    result = fits_access.collapse_to_2d(data)

    assert result.shape == (5, 6)
    assert np.array_equal(result, data[0])


def test_first_hdu_only_reads_the_bare_hdu0(tmp_path: Path) -> None:
    """Verify `first_hdu_only` reads the bare HDU 0 of a two-HDU file."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    header, data = fits_access.read_header_and_data(path, first_hdu_only=True)

    assert data is None
    assert header["NAXIS"] == 0
    assert fits_access.read_data(path, first_hdu_only=True) is None
    assert fits_access.read_header(path, first_hdu_only=True)["NAXIS"] == 0


def test_read_header_and_data_falls_back_to_hdu1(tmp_path: Path) -> None:
    """Verify the header and pixels both come from HDU1 when HDU0 is bare."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 60, 40)

    header, data = fits_access.read_header_and_data(path)

    assert header["NAXIS1"] == 60
    assert data is not None
    assert data.shape == (40, 60)


def test_update_first_header_saves_a_change(tmp_path: Path) -> None:
    """Verify a changed header is written back to the file."""
    path = _write_single_hdu_frame(tmp_path / "frame.fits", 20, 10)

    def _add_keyword(header: fits.Header) -> bool:
        """Add one keyword and report the change.

        Returns
        -------
        changed : `bool`
            Always `True`.
        """
        header["TESTKEY"] = 7
        return True

    assert fits_access.update_first_header(str(path), _add_keyword) is True
    assert fits_access.read_header(str(path))["TESTKEY"] == 7


def test_update_first_header_leaves_the_file_alone_when_nothing_changed(tmp_path: Path) -> None:
    """Verify a callback that changes nothing leaves the file as it was."""
    path = _write_single_hdu_frame(tmp_path / "frame.fits", 20, 10)
    size_before = Path(path).stat().st_size

    def _decline(header: fits.Header) -> bool:
        """Report no change.

        Returns
        -------
        changed : `bool`
            Always `False`.
        """
        return False

    assert fits_access.update_first_header(str(path), _decline) is False
    assert Path(path).stat().st_size == size_before
    assert "TESTKEY" not in fits_access.read_header(str(path))


def _sky_axes_header() -> fits.Header:
    """Build a header with both sky axes and nothing that marks a solve.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The ``CTYPE``, ``CRVAL`` and ``CRPIX`` cards of axes 1 and 2, as the
        capture program writes them from the mount's pointing.
    """
    header = fits.Header()
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 10.0
    header["CRVAL2"] = 41.0
    header["CRPIX1"] = 100.0
    header["CRPIX2"] = 100.0
    return header


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        (True, True),
        (False, False),
        ("T", True),
        ("true", True),
        (" 1 ", True),
        ("F", False),
        ("FALSE", False),
        ("0", False),
        ("", False),
        (1, True),
        (0, False),
    ],
)
def test_the_plate_solved_card_is_read_as_a_fits_logical(flag: object, expected: bool) -> None:
    """A text "F" is false, unlike plain `bool`; "T", "TRUE" and "1" are true.

    Parameters
    ----------
    flag : `object`
        The ``PLTSOLVD`` value.
    expected : `bool`
        Whether the header counts as plate solved.
    """
    header = _sky_axes_header()
    header[fits_access.PLATE_SOLVED_KEYWORD] = flag
    assert fits_access.header_is_plate_solved(header) is expected


def test_a_pointing_only_header_is_not_plate_solved() -> None:
    """Sky axes alone, with no card and no SIP terms, are only a pointing."""
    assert not fits_access.header_is_plate_solved(_sky_axes_header())


def test_sip_terms_mark_a_header_solved_before_the_card_existed() -> None:
    """A header with SIP terms and no card was written by a solver."""
    header = _sky_axes_header()
    header["A_ORDER"] = 2
    assert fits_access.header_is_plate_solved(header)


@pytest.mark.parametrize("missing", ["CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2"])
def test_a_solved_card_without_both_sky_axes_is_not_a_solution(missing: str) -> None:
    """A true card is not enough when a sky axis card is missing.

    Parameters
    ----------
    missing : `str`
        The sky-axis card to leave out.
    """
    header = _sky_axes_header()
    header[fits_access.PLATE_SOLVED_KEYWORD] = True
    header["A_ORDER"] = 2
    del header[missing]
    assert not fits_access.header_is_plate_solved(header)


def test_the_public_name_is_the_same_function() -> None:
    """Other apps reach the one test through `astrometricslib.drivers`."""
    from astrometricslib import drivers

    assert drivers.header_is_plate_solved is fits_access.header_is_plate_solved


def _count_pixel_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count every time astropy reads an image's pixels from disk.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The pytest fixture used to wrap astropy's pixel reader.

    Returns
    -------
    reads : `list` of `int`
        A list that gains one entry per pixel read.
    """
    from astropy.io.fits.hdu.image import _ImageBaseHDU

    reads: list[int] = []
    original_reader = _ImageBaseHDU._get_scaled_image_data

    def _counting_reader(self: _ImageBaseHDU, offset: int, shape: tuple[int, ...]) -> np.ndarray:
        """Record one pixel read, then read the pixels as astropy would.

        Returns
        -------
        pixels : `numpy.ndarray`
            The pixels astropy's own reader gives.
        """
        reads.append(1)
        return original_reader(self, offset, shape)

    monkeypatch.setattr(_ImageBaseHDU, "_get_scaled_image_data", _counting_reader)
    return reads


def test_choose_pixel_hdu_picks_hdu0_for_a_plain_image(tmp_path: Path) -> None:
    """Verify a single-HDU image gives HDU 0 without reading its pixels."""
    path = _write_single_hdu_frame(tmp_path / "frame.fits", 20, 10)

    with fits.open(path, memmap=False) as hdul:
        assert fits_access.choose_pixel_hdu(hdul) is hdul[0]
        assert "data" not in hdul[0].__dict__


def test_choose_pixel_hdu_picks_hdu1_after_a_bare_hdu0(tmp_path: Path) -> None:
    """Verify a bare HDU 0 followed by an image gives HDU 1."""
    path = _write_two_hdu_frame(tmp_path / "frame.fits", 20, 10)

    with fits.open(path, memmap=False) as hdul:
        assert fits_access.choose_pixel_hdu(hdul) is hdul[1]
        assert fits_access.choose_pixel_hdu(hdul, first_hdu_only=True) is hdul[0]


def test_choose_pixel_hdu_picks_the_compressed_image(tmp_path: Path) -> None:
    """Verify a compressed file (bare HDU 0, compressed image) gives HDU 1."""
    path = tmp_path / "frame.fits.fz"
    image = np.arange(200, dtype=np.int16).reshape(10, 20)
    fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(image)]).writeto(path)

    with fits.open(path, memmap=False) as hdul:
        chosen = fits_access.choose_pixel_hdu(hdul)
        assert chosen is hdul[1]
        assert chosen.header["NAXIS1"] == 20


def test_read_header_does_not_read_the_pixels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify reading a header leaves the pixel data on disk.

    The spy is checked first on a real data read, so a zero count proves
    the header read skipped the pixels rather than that the spy missed.
    """
    single_path = _write_single_hdu_frame(tmp_path / "single.fits", 200, 100)
    two_hdu_path = _write_two_hdu_frame(tmp_path / "two.fits", 200, 100)
    reads = _count_pixel_reads(monkeypatch)

    assert fits_access.read_data(single_path) is not None
    assert len(reads) == 1
    reads.clear()

    assert fits_access.read_header(single_path)["NAXIS1"] == 200
    assert fits_access.read_header(two_hdu_path)["NAXIS1"] == 200
    assert reads == []
