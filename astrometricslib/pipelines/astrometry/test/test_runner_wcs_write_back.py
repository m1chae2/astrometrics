"""Tests that the astrometry runner keeps SIP distortion when it saves a WCS.

SIP (Simple Imaging Polynomial) terms bend the flat sky map to match lens
distortion. The runner writes the solved map into the FITS header. These
tests re-read that header and check that the map still points where the
solved map pointed, most of all at the image corners.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.utils.exceptions import AstropyWarning
from astropy.wcs import WCS

from astrometricslib.pipelines.astrometry import runner

# The image size used by every test here, in pixels.
WIDTH = 1000
HEIGHT = 800

# How closely the saved and the in-memory maps must agree, in arcseconds.
TOLERANCE_ARCSEC = 0.01


def _build_wcs(with_sip: bool) -> WCS:
    """Build a solved-looking sky map, with or without SIP terms.

    Parameters
    ----------
    with_sip : `bool`
        When true, add second-order SIP terms large enough to move the
        corners by several arcseconds.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The sky map.
    """
    header = fits.Header()
    header["NAXIS"] = 2
    header["NAXIS1"] = WIDTH
    header["NAXIS2"] = HEIGHT
    suffix = "-SIP" if with_sip else ""
    header["CTYPE1"] = "RA---TAN" + suffix
    header["CTYPE2"] = "DEC--TAN" + suffix
    header["CRVAL1"] = 250.4
    header["CRVAL2"] = 36.46
    header["CRPIX1"] = WIDTH / 2
    header["CRPIX2"] = HEIGHT / 2
    header["CD1_1"] = -2.0e-4
    header["CD1_2"] = 1.0e-6
    header["CD2_1"] = 1.0e-6
    header["CD2_2"] = 2.0e-4
    if with_sip:
        header["A_ORDER"] = 2
        header["B_ORDER"] = 2
        header["A_2_0"] = 1.0e-6
        header["A_1_1"] = -2.0e-6
        header["A_0_2"] = 3.0e-7
        header["B_2_0"] = -4.0e-7
        header["B_1_1"] = 2.0e-6
        header["B_0_2"] = 1.0e-6
    return WCS(header)


def _write_blank_fits(path: Path, header: fits.Header | None = None) -> None:
    """Write a small FITS file with a zero-filled image.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    header : `astropy.io.fits.Header`, optional
        Extra keywords to store with the image.
    """
    fits.PrimaryHDU(data=np.zeros((HEIGHT, WIDTH), dtype=np.float32), header=header).writeto(path)


def _read_wcs_back(path: Path) -> tuple[fits.Header, WCS]:
    """Re-open a FITS file and build a sky map from its header.

    Parameters
    ----------
    path : `pathlib.Path`
        The file to read.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The primary header, copied so the file can close.
    wcs : `astropy.wcs.WCS`
        The sky map the header describes.
    """
    with fits.open(path, memmap=False) as hdul:
        header = hdul[0].header.copy()
    return header, WCS(header)


def _corner_sky_positions(wcs: WCS) -> SkyCoord:
    """Convert the four image corners from pixels to sky positions.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The sky map.

    Returns
    -------
    corners : `astropy.coordinates.SkyCoord`
        The sky positions of the four corners.
    """
    x_pixels = np.array([0, WIDTH - 1, 0, WIDTH - 1], dtype=float)
    y_pixels = np.array([0, 0, HEIGHT - 1, HEIGHT - 1], dtype=float)
    return wcs.pixel_to_world(x_pixels, y_pixels)


def _assert_corners_match(expected: WCS, actual: WCS) -> None:
    """Assert that two sky maps agree at the four image corners.

    Parameters
    ----------
    expected : `astropy.wcs.WCS`
        The in-memory sky map.
    actual : `astropy.wcs.WCS`
        The sky map read back from the file.
    """
    separation = _corner_sky_positions(expected).separation(_corner_sky_positions(actual))
    assert separation.arcsec.max() < TOLERANCE_ARCSEC


def test_write_back_keeps_sip_distortion(tmp_path: Path) -> None:
    """A WCS with SIP terms reads back with the same corner positions."""
    wcs = _build_wcs(with_sip=True)
    path = tmp_path / "solved.fits"
    _write_blank_fits(path)

    runner._write_solved_wcs_to_fits_header(str(path), SimpleNamespace(wcs=wcs))

    header, reread = _read_wcs_back(path)
    assert "A_ORDER" in header
    assert "B_ORDER" in header
    assert header["CTYPE1"].endswith("-SIP")
    assert reread.sip is not None
    _assert_corners_match(wcs, reread)


def test_dropping_relax_loses_sip_terms() -> None:
    """Plain `to_header()` drops SIP, so the runner must pass `relax=True`."""
    wcs = _build_wcs(with_sip=True)

    with pytest.warns(AstropyWarning, match="non-standard WCS keywords"):
        plain_header = wcs.to_header()
    assert "A_ORDER" not in plain_header
    assert "A_ORDER" in wcs.to_header(relax=True)


def test_write_back_without_sip_still_works(tmp_path: Path) -> None:
    """A WCS with no SIP terms is saved and read back without error."""
    wcs = _build_wcs(with_sip=False)
    path = tmp_path / "solved.fits"
    _write_blank_fits(path)

    runner._write_solved_wcs_to_fits_header(str(path), SimpleNamespace(wcs=wcs))

    header, reread = _read_wcs_back(path)
    assert "A_ORDER" not in header
    assert not header["CTYPE1"].endswith("-SIP")
    assert reread.sip is None
    _assert_corners_match(wcs, reread)


def test_resolve_replaces_cards_from_an_earlier_solve(tmp_path: Path) -> None:
    """A re-solve leaves no old CD matrix or higher-order SIP terms behind."""
    old_header = _build_wcs(with_sip=True).to_header(relax=True)
    old_header["A_ORDER"] = 4
    old_header["A_4_0"] = 5.0e-9
    old_header["CD1_1"] = -9.0e-4
    old_header["PV1_1"] = 0.5
    path = tmp_path / "solved.fits"
    _write_blank_fits(path, old_header)
    wcs = _build_wcs(with_sip=True)

    runner._write_solved_wcs_to_fits_header(str(path), SimpleNamespace(wcs=wcs))

    header, reread = _read_wcs_back(path)
    assert header["A_ORDER"] == 2
    for stale_keyword in ("A_4_0", "CD1_1", "PV1_1"):
        assert stale_keyword not in header
    _assert_corners_match(wcs, reread)


@pytest.mark.parametrize("path", [None, ""])
def test_write_back_ignores_missing_path(path: str | None) -> None:
    """A missing path or a missing solution does nothing and does not raise."""
    runner._write_solved_wcs_to_fits_header(path, SimpleNamespace(wcs=_build_wcs(with_sip=True)))
    runner._write_solved_wcs_to_fits_header("any.fits", SimpleNamespace(wcs=None))
