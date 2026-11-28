"""Tests that a colour stack's header with a SIP plate solution can be read.

A colour stack has three axes (channel, row, column), and its plate solution
carries SIP distortion terms. Astropy refuses to build a WCS from such a
header unless it is told to use only two axes. These tests build
that header and check that the stacking code which reads it gets a sky
solution back instead of an error.
"""

from pathlib import Path

import numpy as np
from astropy.io import fits

from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.stacking.post_processing.background_gradient import _sky_position_angle
from astrometricslib.pipelines.stacking.post_processing.field_rotation import (
    PARITY_UNKNOWN,
    image_parity_text,
)


def _colour_sip_header() -> fits.Header:
    """Build a three-axis colour stack header with a SIP plate solution.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A header like the one the plate solver writes into a colour stack.
    """
    header = fits.Header()
    header["NAXIS"] = 3
    header["NAXIS1"] = 200
    header["NAXIS2"] = 100
    header["NAXIS3"] = 3
    header["CTYPE1"] = "RA---TAN-SIP"
    header["CTYPE2"] = "DEC--TAN-SIP"
    header["CRVAL1"] = 297.7
    header["CRVAL2"] = 8.87
    header["CRPIX1"] = 100.0
    header["CRPIX2"] = 50.0
    header["CD1_1"] = -5.4e-4
    header["CD1_2"] = 9.4e-5
    header["CD2_1"] = 9.4e-5
    header["CD2_2"] = 5.4e-4
    header["A_ORDER"] = 2
    header["B_ORDER"] = 2
    header["A_2_0"] = 1e-7
    header["B_0_2"] = 1e-7
    return header


def test_celestial_wcs_reads_a_colour_stack_with_sip_terms() -> None:
    """A colour stack's SIP header gives a celestial WCS, not None."""
    wcs = celestial_wcs(_colour_sip_header())
    assert wcs is not None
    assert wcs.has_celestial


def test_sky_position_angle_reads_a_colour_stack_with_sip_terms() -> None:
    """The background tilt's position angle is found on a colour SIP header."""
    angle, reason = _sky_position_angle(_colour_sip_header(), 1.0, 0.0, (100, 200))
    assert angle is not None, reason
    assert 0.0 <= angle < 360.0


def test_image_parity_reads_a_colour_stack_with_sip_terms(tmp_path: Path) -> None:
    """The stack's mirror state is known for a colour stack with SIP terms."""
    path = tmp_path / "stack.fits"
    fits.PrimaryHDU(np.zeros((3, 100, 200), dtype=np.float32), header=_colour_sip_header()).writeto(path)
    assert image_parity_text(str(path)) != PARITY_UNKNOWN
