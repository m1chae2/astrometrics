"""Purpose: Unit tests for site profile domain models.

Description: Verifies AvoidanceZone's azimuth containment, including
the due-north wraparound case, and SiteProfile's duplicate-id rejection.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.models.equipment_and_site.site_profile import AvoidanceZone, SiteProfile


def test_avoidance_zone_contains_azimuth_normal_range():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify containment within a non-wrapping azimuth range."""
    zone = AvoidanceZone(
        id="z1", name="Oak Tree", azimuth_start_deg=80.0, azimuth_end_deg=110.0, min_clear_altitude_deg=25.0
    )
    assert zone.contains_azimuth(95.0) is True
    assert zone.contains_azimuth(70.0) is False


def test_avoidance_zone_contains_azimuth_wraps_north():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify containment correctly wraps past due north (360 -> 0)."""
    zone = AvoidanceZone(
        id="z1", name="Roofline", azimuth_start_deg=350.0, azimuth_end_deg=10.0, min_clear_altitude_deg=30.0
    )
    assert zone.contains_azimuth(355.0) is True
    assert zone.contains_azimuth(5.0) is True
    assert zone.contains_azimuth(180.0) is False


def test_site_profile_rejects_duplicate_avoidance_zone_ids():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify duplicate avoidance zone identifiers are rejected."""
    zone_a = AvoidanceZone(
        id="z1", name="A", azimuth_start_deg=0.0, azimuth_end_deg=10.0, min_clear_altitude_deg=20.0
    )
    zone_b = AvoidanceZone(
        id="z1", name="B", azimuth_start_deg=20.0, azimuth_end_deg=30.0, min_clear_altitude_deg=20.0
    )
    with pytest.raises(ValidationError):
        SiteProfile(
            id="site1",
            name="Backyard",
            latitude_deg=39.7392,
            longitude_deg=-104.9903,
            avoidance_zones=[zone_a, zone_b],
        )


def test_site_profile_constructs_with_no_obstructions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a SiteProfile constructs with an empty avoidance_zones list."""
    profile = SiteProfile(id="site1", name="Backyard", latitude_deg=39.7392, longitude_deg=-104.9903)
    assert profile.avoidance_zones == []
    assert profile.elevation_m == pytest.approx(0.0)
