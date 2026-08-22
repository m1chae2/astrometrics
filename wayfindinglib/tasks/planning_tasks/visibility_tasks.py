"""Purpose: Visibility Policy.

Description: What counts as visible given a telescope's pointing
envelope, a site's avoidance zones, and a package's own visibility
floor -- the *policy* built on top of `wayfindinglib.astronomy`'s pure
altitude/azimuth calculation (`Wayfinding_Library_Architecture.md` §2.1).

Deliberately altitude- and avoidance-zone-only: hour-angle limits and
meridian-flip handling are captured as `Telescope` configuration but not
yet enforced as a placement constraint (`Wayfinding_Library_Architecture.md`
§4), which is why `InfeasibilityReasonCode` has no hour-angle-specific
value -- this module's visibility check must not silently start
enforcing a constraint the diagnosis vocabulary cannot yet explain.
"""

from datetime import datetime, timedelta

from astropy.time import Time

from wayfindinglib.astronomy.coordinate_transforms import compute_altaz, earth_location
from wayfindinglib.models.equipment_and_site.equipment import Telescope
from wayfindinglib.models.equipment_and_site.site_profile import AvoidanceZone, SiteProfile


def effective_altitude_floor(telescope: Telescope, package_minimum_altitude_deg: float | None) -> float:
    """Return the stricter of the telescope and package altitude floors.

    Returns
    -------
    floor_deg : `float`
        The stricter (higher) of the two altitude floors.
    """
    telescope_floor = telescope.min_altitude_deg if telescope.altitude_limits_enabled else -90.0
    if package_minimum_altitude_deg is None:
        return telescope_floor
    return max(telescope_floor, package_minimum_altitude_deg)


def _blocking_zone(azimuth_deg: float, avoidance_zones: list[AvoidanceZone]) -> AvoidanceZone | None:
    """Return the first avoidance zone containing `azimuth_deg`, or `None`.

    Returns
    -------
    zone : `AvoidanceZone` or `None`
        The first matching zone, or `None` if none contains `azimuth_deg`.
    """
    for zone in avoidance_zones:
        if zone.contains_azimuth(azimuth_deg):
            return zone
    return None


def visibility_state_at(
    ra_deg: float,
    dec_deg: float,
    site_profile: SiteProfile,
    telescope: Telescope,
    obstime: datetime,
    effective_floor_deg: float,
) -> tuple[bool, AvoidanceZone | None]:
    """Return visibility at one instant, plus the blocking zone if any.

    Visible means: altitude at or above `effective_floor_deg`, at or
    below the telescope's `max_altitude_deg` when enabled, and outside
    every configured avoidance zone (an avoidance zone's own
    `min_clear_altitude_deg`, when stricter than `effective_floor_deg`,
    additionally raises the floor while inside that zone's azimuth
    range).

    Returns
    -------
    visible : `bool`
        Whether the target clears every constraint at this instant.
    blocking_zone : `AvoidanceZone` or `None`
        The avoidance zone responsible for blocking visibility, if any
        -- `None` when the target is blocked by altitude alone or is
        visible.
    """
    location = earth_location(site_profile.latitude_deg, site_profile.longitude_deg, site_profile.elevation_m)
    altitude_deg, azimuth_deg = compute_altaz(ra_deg, dec_deg, location, Time(obstime))

    if altitude_deg < effective_floor_deg:
        return False, None
    if telescope.altitude_limits_enabled and altitude_deg > telescope.max_altitude_deg:
        return False, None

    zone = _blocking_zone(azimuth_deg, site_profile.avoidance_zones)
    if zone is not None and altitude_deg < zone.min_clear_altitude_deg:
        return False, zone

    return True, None


def find_earliest_visible_window(
    ra_deg: float,
    dec_deg: float,
    site_profile: SiteProfile,
    telescope: Telescope,
    window_start: datetime,
    window_end: datetime,
    effective_floor_deg: float,
    duration_sec: float,
    step: timedelta,
) -> tuple[datetime, datetime] | None:
    """Find the earliest continuous visible span of at least `duration_sec`.

    Samples at `step` intervals across `[window_start, window_end]`
    rather than solving continuously, matching this subsystem's coarse
    sampling posture (`night_window.resolve_night_window`).

    Returns
    -------
    span : `tuple` [`datetime`, `datetime`] or `None`
        The earliest `(start, end)` continuous visible span at least
        `duration_sec` long, or `None` if no such span exists in the
        window.
    """
    run_start: datetime | None = None
    t = window_start
    while t <= window_end:
        visible, _zone = visibility_state_at(ra_deg, dec_deg, site_profile, telescope, t, effective_floor_deg)
        if visible:
            if run_start is None:
                run_start = t
            if (t - run_start).total_seconds() >= duration_sec:
                return run_start, run_start + timedelta(seconds=duration_sec)
        else:
            run_start = None
        t += step
    return None


def ever_clears_floor(
    ra_deg: float,
    dec_deg: float,
    site_profile: SiteProfile,
    telescope: Telescope,
    window_start: datetime,
    window_end: datetime,
    effective_floor_deg: float,
    step: timedelta,
) -> bool:
    """Return whether the target clears the floor at any sampled instant.

    Used to distinguish `NEVER_CLEARS_ALTITUDE` from
    `BLOCKED_BY_AVOIDANCE_ZONE`: a target that clears the floor but
    only within an avoidance zone's
    azimuth range fails visibility for a different reason than one that
    never reaches the floor at all.

    Returns
    -------
    clears : `bool`
        Whether the target clears the altitude floor at any sampled
        instant.
    """
    location = earth_location(site_profile.latitude_deg, site_profile.longitude_deg, site_profile.elevation_m)
    t = window_start
    while t <= window_end:
        altitude_deg, _azimuth_deg = compute_altaz(ra_deg, dec_deg, location, Time(t))
        if altitude_deg >= effective_floor_deg and (
            not telescope.altitude_limits_enabled or altitude_deg <= telescope.max_altitude_deg
        ):
            return True
        t += step
    return False


def ever_visible(
    ra_deg: float,
    dec_deg: float,
    site_profile: SiteProfile,
    telescope: Telescope,
    window_start: datetime,
    window_end: datetime,
    effective_floor_deg: float,
    step: timedelta,
) -> bool:
    """Return whether the target is visible at any single sampled instant.

    Distinct from `find_earliest_visible_window`, which requires a
    *continuous* run of at least `duration_sec` -- that function cannot
    detect a single isolated clear instant, since its first sample of a
    run always has zero elapsed time from itself. This checks each
    sample independently, which is what distinguishing `WINDOW_TOO_SHORT`
    (visible somewhere, just too briefly) from
    `BLOCKED_BY_AVOIDANCE_ZONE` (never visible with both constraints
    applied) actually requires.

    Returns
    -------
    visible : `bool`
        Whether the target is visible at any single sampled instant.
    """
    t = window_start
    while t <= window_end:
        visible, _zone = visibility_state_at(ra_deg, dec_deg, site_profile, telescope, t, effective_floor_deg)
        if visible:
            return True
        t += step
    return False
