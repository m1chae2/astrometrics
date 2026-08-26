"""Tests for the tool that figures out where each photo is pointing.

We test if it correctly combines the general location of the target with
the specific coordinates saved by the telescope when it took each photo.
We also test if it correctly handles broken or missing data.
"""

import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.tasks.moving_object_tasks.frame_wcs_composer import (
    estimate_frame_wcs_from_mount_pointing,
)


def _build_stack_wcs() -> WCS:
    """Create a fake set of map coordinates for the center of the target.

    Returns
    -------
    WCS
        A fake coordinate system centered on a specific spot in the sky.
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
    """Create a fake set of telescope settings (like what's saved in a photo).

    Returns
    -------
    fits.Header
        A fake list of settings containing the image size and where the
        telescope was pointing.
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
    """Test that it uses the photo's own coordinates, not the target's."""
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(150.05, 30.02, width_px=2000, height_px=2000)

    frame_wcs = estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header)

    assert frame_wcs is not None
    sky_at_frame_center = frame_wcs.wcs_pix2world([[1000.0, 1000.0]], 1)[0]
    assert sky_at_frame_center == pytest.approx([150.05, 30.02])


def test_estimate_frame_wcs_reuses_stack_pixel_scale_matrix():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that it copies rotation and scale from the main target image."""
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(150.05, 30.02)

    frame_wcs = estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header)

    assert frame_wcs is not None
    assert frame_wcs.pixel_scale_matrix == pytest.approx(stack_wcs.pixel_scale_matrix)


def test_estimate_frame_wcs_returns_none_when_ra_dec_missing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that it safely fails if the photo is missing its coordinates."""
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(None, None)
    assert estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header) is None


def test_estimate_frame_wcs_returns_none_when_naxis_missing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that it safely fails if the photo is missing its size."""
    stack_wcs = _build_stack_wcs()
    frame_header = fits.Header()
    frame_header["RA"] = 150.05
    frame_header["DEC"] = 30.02
    assert estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header) is None


def test_estimate_frame_wcs_returns_none_for_non_numeric_ra_dec():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test it safely fails if the coordinates are letters, not numbers."""
    stack_wcs = _build_stack_wcs()
    frame_header = _build_frame_header(0, 0, width_px=1024, height_px=1024)
    frame_header["RA"] = "not-a-number"
    assert estimate_frame_wcs_from_mount_pointing(stack_wcs, frame_header) is None
