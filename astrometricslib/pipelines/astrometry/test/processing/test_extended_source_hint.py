"""Purpose: Tests for the extended-source hint when SIMBAD cannot answer.

Description: The Ring Nebula (M 57) is an extended target. Its hint (where it
sits in the image and how large it is) came only from SIMBAD, so a SIMBAD
that could not be reached lost the hint. The library's own record of the
target (type, position and size) now answers in that case. The hint is also
not attempted, and not reported as a failure, while an image has no plate
solution yet (a spectral stack). A fake SIMBAD and a fake plate solution are
used, so no network is needed.
"""

import logging
from typing import Any

import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib._internal.drivers.driver_set import Drivers
from astrometricslib._internal.drivers.interfaces import SimbadDriver
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline
from astrometricslib.pipelines.shared.analysis_context import KnownTarget
from astrometricslib.pipelines.shared.target_center_hint import resolve_known_target
from astrometricslib.test.synthetic.fake_simbad import FakeSimbad

M57_RA_DEG = 283.3962
M57_DEC_DEG = 33.0292
ARCSEC_PER_PIXEL = 1.9


def _pipeline(simbad: SimbadDriver) -> AstrometryPipeline:
    """Build a pipeline that uses the given SIMBAD client.

    Returns
    -------
    pipeline : `AstrometryPipeline`
        The pipeline.
    """
    from unittest.mock import MagicMock

    config = MagicMock()
    config.get_value.return_value = None
    return AstrometryPipeline(config, drivers=Drivers(simbad=simbad))


def _wcs_centered_on_m57() -> WCS:
    """Build a plate solution with M 57 at pixel (500, 500).

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A 1000 by 1000 pixel field at 1.9 arcsec per pixel.
    """
    header = fits.Header()
    header["CTYPE1"], header["CTYPE2"] = "RA---TAN", "DEC--TAN"
    header["CRPIX1"], header["CRPIX2"] = 501.0, 501.0
    header["CRVAL1"], header["CRVAL2"] = M57_RA_DEG, M57_DEC_DEG
    scale_deg = ARCSEC_PER_PIXEL / 3600.0
    header["CD1_1"], header["CD1_2"], header["CD2_1"], header["CD2_2"] = -scale_deg, 0.0, 0.0, scale_deg
    return WCS(header)


KNOWN_M57 = KnownTarget(object_type="PN", ra_deg=M57_RA_DEG, dec_deg=M57_DEC_DEG, major_axis_arcmin=1.4)


def _down_simbad() -> FakeSimbad:
    """Build a SIMBAD client that cannot be reached.

    Returns
    -------
    simbad : `FakeSimbad`
        A client whose every question raises `ExternalServiceError`.
    """
    return FakeSimbad(failure=ExternalServiceError("SIMBAD could not be reached."))


def test_down_simbad_uses_the_librarys_own_record() -> None:
    """With SIMBAD down, the record gives the object's pixel."""
    hint = _pipeline(_down_simbad())._build_extended_source_hint(
        "M 57", _wcs_centered_on_m57(), None, None, KNOWN_M57
    )
    assert hint is not None
    assert hint.otype == "PN"
    assert hint.extraction_center == pytest.approx((500.0, 500.0), abs=0.5)
    # Half of 1.4 arcmin is 42 arcsec, or 22 pixels at 1.9 arcsec per pixel.
    assert hint.extraction_radius_px == 22


def test_down_simbad_without_a_record_gives_no_hint_and_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreachable SIMBAD and no record is a status, not a warning."""
    with caplog.at_level(logging.INFO):
        hint = _pipeline(_down_simbad())._build_extended_source_hint(
            "M 57", _wcs_centered_on_m57(), None, None
        )
    assert hint is None
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_a_star_record_is_not_extended() -> None:
    """A record whose type is a star makes no extended-source hint."""
    star = KnownTarget(object_type="*", ra_deg=M57_RA_DEG, dec_deg=M57_DEC_DEG)
    hint = _pipeline(_down_simbad())._build_extended_source_hint(
        "Vega", _wcs_centered_on_m57(), None, None, star
    )
    assert hint is None


def test_no_plate_solution_is_not_a_failure(caplog: pytest.LogCaptureFixture) -> None:
    """A spectral stack has no solution yet: no SIMBAD query and no warning."""
    simbad = FakeSimbad()
    with caplog.at_level(logging.INFO):
        hint = _pipeline(simbad)._build_extended_source_hint("M 57", None, None, None, KNOWN_M57)
    assert hint is None
    assert simbad.call_count == 0
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


class _Record:
    """A stand-in for a target record."""

    def __init__(self, **fields: Any) -> None:
        """Set the fields."""
        self.ra = "18h 53m 35s"
        self.dec = "+33d 01m 45s"
        self.simbad_object_type = "PN"
        self.position_unverified = False
        self.major_axis_arcmin = 1.4
        self.__dict__.update(fields)


def test_a_record_with_a_verified_position_and_a_type_becomes_a_known_target() -> None:
    """The type, position and size are carried over."""
    known = resolve_known_target(_Record())
    assert known is not None
    assert known.object_type == "PN"
    assert known.ra_deg == pytest.approx(283.396, abs=0.01)
    assert known.major_axis_arcmin == pytest.approx(1.4)


@pytest.mark.parametrize(
    "fields",
    [
        {"simbad_object_type": None},
        {"position_unverified": True},
        {"ra": "0h 0m 0s", "dec": "0° 0′ 0′′"},
    ],
)
def test_a_record_that_cannot_stand_in_for_simbad_gives_none(fields: dict[str, Any]) -> None:
    """No type, an unverified or a placeholder position gives none."""
    assert resolve_known_target(_Record(**fields)) is None
