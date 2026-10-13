"""Tests for the pointing correction when a field straddles RA = 0 h.

Right Ascension (RA) wraps from 360 deg back to 0 deg. The pointing
correction pairs each detection with nearby reference stars and votes on
the offset between them. These tests build a field that crosses the
RA = 0 line and check that the correction still finds the known pointing
error, to a tenth of an arcsecond.
"""

import math

import numpy as np
import pytest

from astrometricslib.pipelines.asteroid_detection.pipeline import (
    _build_reference_star_tree,
    _estimate_bulk_pointing_correction_deg,
)

_DECLINATION_CENTRE_DEG = 20.0
_STAR_COUNT = 40
_COS_DECLINATION = math.cos(math.radians(_DECLINATION_CENTRE_DEG))
_TOLERANCE_ARCSEC = 0.1


def _make_reference_stars(ra_min_deg: float, ra_max_deg: float, seed: int) -> list[tuple[float, float]]:
    """Scatter fake reference stars over a range of RA and Dec.

    Parameters
    ----------
    ra_min_deg, ra_max_deg : `float`
        The RA range to fill, in degrees. The range may be negative
        (for example -0.02 to 0.02) to put stars on both sides of the
        RA = 0 line. Stored RA values are wrapped into [0, 360).
    seed : `int`
        Seed for the random generator, so the field is the same on
        every run.

    Returns
    -------
    positions_deg : `list` [`tuple` [`float`, `float`]]
        `(ra_deg, dec_deg)` for each star.
    """
    rng = np.random.default_rng(seed)
    ras_deg = np.linspace(ra_min_deg, ra_max_deg, _STAR_COUNT)
    decs_deg = _DECLINATION_CENTRE_DEG + rng.uniform(-0.02, 0.02, _STAR_COUNT)
    return [(float(ra % 360.0), float(dec)) for ra, dec in zip(ras_deg, decs_deg, strict=True)]


@pytest.mark.parametrize(
    ("ra_min_deg", "ra_max_deg", "ra_error_arcsec", "dec_error_arcsec"),
    [
        pytest.param(-0.02, 0.02, 6.0, -4.0, id="field-straddles-ra-zero"),
        pytest.param(0.0005, 0.006, 40.0, 25.0, id="narrow-strip-every-pair-crosses-ra-zero"),
        pytest.param(179.98, 180.02, 6.0, -4.0, id="control-far-from-ra-zero"),
    ],
)
def test_pointing_correction_recovers_known_offset_across_ra_zero(
    ra_min_deg: float, ra_max_deg: float, ra_error_arcsec: float, dec_error_arcsec: float
) -> None:
    """Recover a known mount pointing error near and far from RA = 0.

    The detections are the reference stars shifted by a pointing error
    of a few arcseconds (or tens of arcseconds, for the narrow strip).
    The correction must undo that shift to 0.1 arcsec on both axes.

    Parameters
    ----------
    ra_min_deg, ra_max_deg : `float`
        The RA range the reference stars fill, in degrees.
    ra_error_arcsec : `float`
        The mount's pointing error along RA, in arcseconds of true
        angle on the sky.
    dec_error_arcsec : `float`
        The mount's pointing error along Dec, in arcseconds.
    """
    reference_positions = _make_reference_stars(ra_min_deg, ra_max_deg, seed=7)
    detections = [
        ((ra - ra_error_arcsec / 3600.0 / _COS_DECLINATION) % 360.0, dec - dec_error_arcsec / 3600.0)
        for ra, dec in reference_positions
    ]
    if ra_min_deg < 1.0:
        # The test only means something if the RA = 0 line is crossed
        # by the field itself or by the pointing error.
        assert any(ra > 359.0 for ra, _ in detections)

    tree, reference_ra, reference_dec, reference_cos_dec = _build_reference_star_tree(reference_positions)
    ra_offset_deg, dec_offset_deg, _position_error = _estimate_bulk_pointing_correction_deg(
        detections, tree, reference_ra, reference_dec, reference_cos_dec
    )

    assert ra_offset_deg * 3600.0 * _COS_DECLINATION == pytest.approx(ra_error_arcsec, abs=_TOLERANCE_ARCSEC)
    assert dec_offset_deg * 3600.0 == pytest.approx(dec_error_arcsec, abs=_TOLERANCE_ARCSEC)
