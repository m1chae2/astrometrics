"""Purpose: Night Window Resolution.

Description: Brackets the astronomically usable portion of a calendar
night by sampling solar altitude at a configurable interval and
thresholding against a twilight definition -- coarse, sample-step
bracketing rather than exact rise/set bisection, adequate for the
scheduling granularity this subsystem needs
(`Wayfinding_Library_Architecture.md` §2.3.3). This is the *policy* of
which solar altitude brackets a usable night; the underlying solar
altitude calculation itself is Foundation-level, pure calculation in
`wayfindinglib.astronomy.solar_position`.
"""

from datetime import date, datetime, timedelta

from astropy.time import Time

from wayfindinglib.astronomy.coordinate_transforms import earth_location
from wayfindinglib.astronomy.solar_position import solar_altitude_deg
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.planning_config import PlanningConfig


def resolve_night_window(
    site_profile: SiteProfile,
    night_date: date,
    config: PlanningConfig | None = None,
) -> tuple[datetime, datetime]:
    """Bracket the astronomically usable window for one calendar night.

    Samples solar altitude across a 24-hour window centered on solar
    midnight for `night_date`, then walks outward from the point of
    minimum altitude to find where it first crosses the configured
    twilight threshold in each direction -- correctly handling the
    midnight-wrap case by construction, since the search starts from
    the night's actual low point rather than assuming it falls at any
    particular clock hour.

    Parameters
    ----------
    site_profile : `SiteProfile`
        The observing location.
    night_date : `date`
        The calendar date the observing night begins on.
    config : `PlanningConfig`, optional
        Supplies the time step and twilight threshold. Uses documented
        defaults if `None`.

    Returns
    -------
    dusk, dawn : `tuple` [`datetime`, `datetime`]
        The bracketed window, in UTC.
    """
    config = config or PlanningConfig()
    location = earth_location(site_profile.latitude_deg, site_profile.longitude_deg, site_profile.elevation_m)

    step = timedelta(minutes=config.night_window_time_step_min)
    window_start = datetime(night_date.year, night_date.month, night_date.day, 12, 0, 0)
    window_end = window_start + timedelta(hours=24)

    sample_times: list[datetime] = []
    sample_altitudes: list[float] = []
    t = window_start
    while t <= window_end:
        sample_times.append(t)
        sample_altitudes.append(solar_altitude_deg(location, Time(t)))
        t += step

    min_idx = min(range(len(sample_altitudes)), key=lambda i: sample_altitudes[i])
    threshold = config.twilight_sun_altitude_deg

    dusk_idx = 0
    for i in range(min_idx, -1, -1):
        if sample_altitudes[i] > threshold:
            dusk_idx = i + 1
            break

    dawn_idx = len(sample_times) - 1
    for i in range(min_idx, len(sample_altitudes)):
        if sample_altitudes[i] > threshold:
            dawn_idx = i
            break

    return sample_times[dusk_idx], sample_times[dawn_idx]
