"""Tests that the saved planetarium reference file still matches the library.

The UI tests compare the planetarium's TypeScript math with
``ui/tests/fixtures/projection_reference.json``. These tests recompute that
file with `wayfindinglib.astronomy.coordinate_transforms` and check that
nothing has drifted, so the UI tests always check against what the library
really computes. They also check the two frame conversions undo each other.
"""

import json
from pathlib import Path

import pytest
from astropy.time import Time

from wayfindinglib.astronomy.coordinate_transforms import current_epoch_to_icrs, icrs_to_current_epoch
from wayfindinglib.scripts import generate_projection_reference as script

ANGLE_TOLERANCE_DEG = 1e-3
"""Allowed drift in any stored angle, in degrees (3.6 arcseconds). Updated
Earth-rotation tables in a new Astropy release can move the sidereal time by
a few tenths of an arcsecond; anything larger means the library changed."""


def _angle_difference_deg(a: float, b: float) -> float:
    """Return the size of the gap between two angles, wrapped at 360 degrees.

    Parameters
    ----------
    a, b : `float`
        Angles in degrees.

    Returns
    -------
    difference_deg : `float`
        ``|a - b|``, taking the short way around the circle.
    """
    return abs((a - b + 540.0) % 360.0 - 180.0)


def test_saved_fixture_matches_the_library() -> None:
    """Recomputing the reference gives the saved file's values."""
    saved = json.loads(Path(script.DEFAULT_OUTPUT).read_text(encoding="utf-8"))
    fresh = script.build_reference()

    assert saved["site"] == fresh["site"]
    assert [case["utc"] for case in saved["cases"]] == [case["utc"] for case in fresh["cases"]]
    for saved_case, fresh_case in zip(saved["cases"], fresh["cases"], strict=True):
        assert saved_case["unixMs"] == fresh_case["unixMs"]
        assert (
            _angle_difference_deg(saved_case["localSiderealTimeDeg"], fresh_case["localSiderealTimeDeg"])
            < ANGLE_TOLERANCE_DEG
        )
        for saved_star, fresh_star in zip(saved_case["stars"], fresh_case["stars"], strict=True):
            assert saved_star["name"] == fresh_star["name"]
            for key in (
                "raJ2000Deg",
                "decJ2000Deg",
                "raCurrentEpochDeg",
                "decCurrentEpochDeg",
                "altDeg",
                "azDeg",
            ):
                assert _angle_difference_deg(saved_star[key], fresh_star[key]) < ANGLE_TOLERANCE_DEG, (
                    f"{saved_case['utc']} {saved_star['name']} {key}"
                )


def test_script_writes_the_reference_file(tmp_path: Path) -> None:
    """Running the script writes a file with every reference time."""
    output = tmp_path / "reference.json"

    assert script.main(["--output", str(output)]) == 0

    written = json.loads(output.read_text(encoding="utf-8"))
    assert [case["utc"] for case in written["cases"]] == list(script.REFERENCE_TIMES_UTC)


def test_current_epoch_round_trip() -> None:
    """Converting to the current epoch and back returns the start position."""
    obstime = Time("2026-10-07T02:15:00", scale="utc")

    current_ra_deg, current_dec_deg = icrs_to_current_epoch(88.79294, 7.40706, obstime)
    ra_deg, dec_deg = current_epoch_to_icrs(current_ra_deg, current_dec_deg, obstime)

    assert ra_deg == pytest.approx(88.79294, abs=1e-7)
    assert dec_deg == pytest.approx(7.40706, abs=1e-7)


def test_current_epoch_differs_from_j2000_by_precession_in_2026() -> None:
    """In 2026 the current-epoch frame sits about 0.36 degrees from J2000."""
    obstime = Time("2026-10-07T02:15:00", scale="utc")

    # A star on the celestial equator near RA 0 moves almost purely in RA,
    # by the accumulated precession (about 50 arcseconds a year).
    current_ra_deg, _current_dec_deg = icrs_to_current_epoch(0.0, 0.0, obstime)

    assert 0.33 < current_ra_deg < 0.40
