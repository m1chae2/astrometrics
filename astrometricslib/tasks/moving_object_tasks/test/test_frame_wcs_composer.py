"""Purpose: Unit tests for estimate_frame_wcs_from_mount_pointing.

Description: Verifies the stack-WCS + frame-header-RA/DEC based WCS
estimate against known synthetic headers, and its rejection paths
(missing RA/DEC, missing NAXIS1/NAXIS2, non-numeric RA/DEC).
"""

import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.tasks.moving_object_tasks.frame_wcs_composer import (
    estimate_frame_wcs_from_mount_pointing,
)


def _build_stack_wcs() -> WCS:
    """Build a synthetic TAN-projection stack WCS with a known CD matrix.

    Returns
    -------
    WCS
        A TAN-projection WCS centered at RA=150.0, Dec=30.0 degrees.
    """
    header = fits.Header()
    header["WCSAXES"] = 2
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 30.0
    header["CRPIX1"] = 512.0
    header["CRPIX2"] = 512.0
    header["CD1_1"] = -0.0005
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.0005
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"
    return WCS(header)


def _build_frame_header(right_ascension_deg, declination_deg, width_px=1024, height_px=1024) -> fits.Header:  # ruff: ignore[missing-type-function-argument]
    """Build a synthetic frame FITS header carrying mount-reported pointing.

    Returns
    -------
    fits.Header
        A header with NAXIS1/NAXIS2 and, when provided, RA/DEC keywords.
    """
    header = fits.Header()
    header["NAXIS1"] = width_px
    header["NAXIS2"] = height_px
    if right_ascension_deg is not None:
        header["RA"] = right_ascension_deg
    if declination_deg is not None:
        header["DEC"] = declination_deg
    return header


def test_estimate_frame_wcs_centers_on_frames_own_reported_pointing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the frame WCS's sky center matches the frame's own RA/DEC.

    At its own image center pixel, not the stack's.
    """
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(150.05, 30.02, width_px=2000, height_px=2000)

    frame_wcs = estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header)

    assert frame_wcs is not None
    sky_at_frame_center = frame_wcs.wcs_pix2world([[1000.0, 1000.0]], 1)[0]
    assert sky_at_frame_center == pytest.approx([150.05, 30.02])


def test_estimate_frame_wcs_reuses_stack_pixel_scale_matrix():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the frame WCS's pixel scale/rotation matches the stack's.

    Scale and rotation are assumed constant across one target's frames.
    """
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(150.05, 30.02)

    frame_wcs = estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header)

    assert frame_wcs is not None
    assert frame_wcs.pixel_scale_matrix == pytest.approx(stack_wcs.pixel_scale_matrix)


def test_estimate_frame_wcs_returns_none_when_ra_dec_missing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a header with no RA/DEC keywords is rejected, not raised."""
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(None, None)
    assert estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header) is None


def test_estimate_frame_wcs_returns_none_when_naxis_missing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a frame header with no NAXIS1/NAXIS2 keywords is rejected."""
    stack_wcs = _build_stack_wcs()
    frame_header = fits.Header()
    frame_header["RA"] = 150.05
    frame_header["DEC"] = 30.02
    assert estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header) is None


def test_estimate_frame_wcs_returns_none_for_non_numeric_ra_dec():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a header with non-numeric RA/DEC is rejected, not raised."""
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(0, 0, width_px=1024, height_px=1024)
    frame_header["RA"] = "not-a-number"
    assert estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header) is None
