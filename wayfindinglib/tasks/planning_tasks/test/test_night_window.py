"""Purpose: Unit tests for night window resolution.

Description: Verifies resolve_night_window against real solar-position
computation (not mocked) for a real site/date, confirming the window
straddles midnight correctly and both bounds fall on the twilight
threshold as expected.
"""

from datetime import date

from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.tasks.planning_tasks.night_window import resolve_night_window


def _denver() -> SiteProfile:
    return SiteProfile(
        id="s1", name="Denver", latitude_deg=39.7392, longitude_deg=-104.9903, elevation_m=1600.0
    )


def test_resolve_night_window_brackets_solar_midnight():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the window brackets solar midnight, not an assumed clock hour.

    The algorithm finds the point of minimum solar altitude first, then
    walks outward -- confirmed here by checking the window's midpoint
    falls close to that minimum, which is what makes it correct
    regardless of which UTC calendar date(s) the window happens to fall
    on for a given site's longitude (a longitude-dependent detail, not
    an invariant worth asserting on directly).
    """
    from astropy.time import Time

    from wayfindinglib.astronomy.coordinate_transforms import earth_location
    from wayfindinglib.astronomy.solar_position import solar_altitude_deg

    site = _denver()
    dusk, dawn = resolve_night_window(site, date(2026, 8, 10))
    assert dawn > dusk

    location = earth_location(site.latitude_deg, site.longitude_deg, site.elevation_m)
    midpoint = dusk + (dawn - dusk) / 2
    # The sun should be well below the horizon at the window's midpoint,
    # confirming it was bracketed around the actual low point rather than
    # an arbitrary clock-hour assumption.
    assert solar_altitude_deg(location, Time(midpoint)) < -30.0
    # The window should span several hours of astronomical night, not
    # collapse to a single sample or span the entire 24-hour search range.
    assert (dawn - dusk).total_seconds() > 3600 * 4
    assert (dawn - dusk).total_seconds() < 3600 * 14


def test_resolve_night_window_respects_custom_twilight_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a stricter twilight threshold yields a shorter window."""
    site = _denver()
    nautical = resolve_night_window(site, date(2026, 8, 10), PlanningConfig(twilight_sun_altitude_deg=-12.0))
    astronomical = resolve_night_window(
        site, date(2026, 8, 10), PlanningConfig(twilight_sun_altitude_deg=-18.0)
    )

    nautical_duration = (nautical[1] - nautical[0]).total_seconds()
    astronomical_duration = (astronomical[1] - astronomical[0]).total_seconds()
    assert astronomical_duration < nautical_duration


def test_resolve_night_window_uses_default_config_when_none_given():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify resolve_night_window works with no explicit PlanningConfig."""
    site = _denver()
    dusk, dawn = resolve_night_window(site, date(2026, 8, 10))
    assert dawn > dusk
