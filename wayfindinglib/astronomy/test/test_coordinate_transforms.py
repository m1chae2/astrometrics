"""Purpose: Unit tests for coordinate transform primitives.

Description: Verifies compute_altaz against a known zenith case and
hour_angle_deg's wraparound normalization, using real Astropy
computation rather than mocks.
"""

import pytest
from astropy.time import Time

from wayfindinglib.astronomy.coordinate_transforms import (
    compute_altaz,
    earth_location,
    hour_angle_deg,
)


def test_compute_altaz_object_at_zenith():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a target at the observer's zenith reports ~90 degrees altitude.

    At local sidereal time equal to the target's RA, and dec equal to
    the observer's latitude, the target sits directly overhead.
    """
    latitude_deg = 39.7392
    location = earth_location(latitude_deg=latitude_deg, longitude_deg=-104.9903, elevation_m=1600.0)
    obstime = Time("2026-08-10T06:00:00")
    lst_hours = obstime.sidereal_time("apparent", longitude=location.lon).hour
    ra_deg = lst_hours * 15.0

    altitude_deg, _azimuth_deg = compute_altaz(ra_deg, latitude_deg, location, obstime)
    # Astropy's apparent-sidereal-time transform includes aberration/nutation
    # corrections a naive LST==RA zenith approximation does not capture
    # exactly; a wider tolerance still confirms "near zenith" without
    # asserting exact agreement with the approximation.
    assert altitude_deg == pytest.approx(90.0, abs=1.0)


def test_hour_angle_zero_at_transit():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify hour angle is ~0 when RA equals the local sidereal time."""
    location = earth_location(latitude_deg=39.7392, longitude_deg=-104.9903, elevation_m=1600.0)
    obstime = Time("2026-08-10T06:00:00")
    lst_hours = obstime.sidereal_time("apparent", longitude=location.lon).hour
    ra_deg = lst_hours * 15.0

    assert hour_angle_deg(ra_deg, location, obstime) == pytest.approx(0.0, abs=0.1)


def test_hour_angle_normalizes_to_plus_minus_180():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify hour_angle_deg never returns a value outside [-180, 180)."""
    location = earth_location(latitude_deg=39.7392, longitude_deg=-104.9903, elevation_m=1600.0)
    obstime = Time("2026-08-10T06:00:00")
    for ra_deg in (0.0, 90.0, 180.0, 270.0, 359.0):
        ha = hour_angle_deg(ra_deg, location, obstime)
        assert -180.0 <= ha < 180.0
