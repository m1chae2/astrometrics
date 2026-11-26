"""Purpose: Tests of reading the sky map (WCS) from a FITS header.

Description: Checks that a plate-solved header gives its two sky axes with
the SIP distortion terms, that a header with no sky map or a broken one gives
`None` instead of raising, that a plain dictionary of cards works, and that
the image corners on the sky follow the SIP distortion.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.header_wcs import celestial_wcs, image_corners_on_sky


def _solved_header(sip: bool = False) -> fits.Header:
    """Build the header of a small plate-solved image.

    Parameters
    ----------
    sip : `bool`, optional
        Add a strong second-order SIP distortion term.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The header.
    """
    header = fits.Header()
    header["NAXIS"] = 2
    header["NAXIS1"] = 400
    header["NAXIS2"] = 300
    header["CTYPE1"] = "RA---TAN-SIP" if sip else "RA---TAN"
    header["CTYPE2"] = "DEC--TAN-SIP" if sip else "DEC--TAN"
    header["CRVAL1"] = 150.0
    header["CRVAL2"] = 30.0
    header["CRPIX1"] = 200.5
    header["CRPIX2"] = 150.5
    header["CD1_1"] = -0.001
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.001
    if sip:
        header["A_ORDER"] = 2
        header["B_ORDER"] = 2
        header["A_2_0"] = 1e-4
        header["B_0_2"] = 1e-4
    return header


def test_a_solved_header_gives_its_sky_map() -> None:
    """A plate-solved header gives two celestial axes."""
    wcs = celestial_wcs(_solved_header())

    assert wcs is not None
    assert wcs.naxis == 2
    ra, dec = wcs.all_pix2world([199.5], [149.5], 0)
    assert float(ra[0]) == pytest.approx(150.0)
    assert float(dec[0]) == pytest.approx(30.0)


def test_the_sip_terms_are_kept() -> None:
    """The distortion terms of the header survive the reading."""
    wcs = celestial_wcs(_solved_header(sip=True))

    assert wcs is not None
    assert wcs.sip is not None


def test_a_plain_dictionary_of_cards_is_read() -> None:
    """A header passed as a dictionary is read like a header."""
    assert celestial_wcs(dict(_solved_header())) is not None


@pytest.mark.parametrize("header", [None, fits.Header(), fits.Header({"CTYPE1": "PIXEL", "CTYPE2": "PIXEL"})])
def test_a_header_with_no_sky_map_gives_none(header: fits.Header | None) -> None:
    """No header, an empty one, or one with pixel axes give `None`."""
    assert celestial_wcs(header) is None


def test_a_broken_sky_map_gives_none() -> None:
    """A header whose projection cannot be read gives `None`, not an error."""
    header = _solved_header()
    header["CTYPE1"] = "RA---XYZ"
    header["CTYPE2"] = "DEC--XYZ"

    assert celestial_wcs(header) is None


def test_the_corners_follow_the_distortion() -> None:
    """The corners differ from the plain projection when SIP terms are set."""
    wcs = celestial_wcs(_solved_header(sip=True))
    assert wcs is not None
    corners = image_corners_on_sky(wcs, 400, 300)
    plain_ra, plain_dec = wcs.wcs_pix2world([-0.5], [-0.5], 0)

    assert len(corners) == 4
    assert corners[0] != pytest.approx((float(plain_ra[0]), float(plain_dec[0])), abs=1e-6)
    expected = wcs.all_pix2world(np.array([399.5]), np.array([299.5]), 0)
    assert corners[2] == pytest.approx((float(expected[0][0]), float(expected[1][0])))
