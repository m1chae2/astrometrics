"""Purpose: Unit tests for solar altitude calculation.

Description: Verifies solar_altitude_deg reports a plausible daytime
altitude and a plausible nighttime (below-horizon) altitude for a known
location/time pair, using real Astropy computation.
"""

from astropy.time import Time

from wayfindinglib.astronomy.coordinate_transforms import earth_location
from wayfindinglib.astronomy.solar_position import solar_altitude_deg


def test_solar_altitude_positive_at_local_solar_noon():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the sun is well above the horizon near local solar noon."""
    location = earth_location(latitude_deg=39.7392, longitude_deg=-104.9903, elevation_m=1600.0)
    # Denver (UTC-6/7); ~19:00 UTC is near local solar noon in August.
    obstime = Time("2026-08-10T19:00:00")
    assert solar_altitude_deg(location, obstime) > 30.0


def test_solar_altitude_negative_at_local_midnight():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the sun is well below the horizon near local midnight."""
    location = earth_location(latitude_deg=39.7392, longitude_deg=-104.9903, elevation_m=1600.0)
    # ~07:00 UTC is near local solar midnight in Denver during August.
    obstime = Time("2026-08-10T07:00:00")
    assert solar_altitude_deg(location, obstime) < -30.0
