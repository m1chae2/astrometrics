"""Purpose: Read the observatory site from the configuration.

Description: The site is the latitude, longitude and elevation of the
observatory. It decides which stars are up, when they rise and set, and
where the mount points. The ``[Observatory.Location]`` section of the
configuration holds it::

    ["Observatory.Location"]
    latitude = "45.76"     # degrees, north is positive
    longitude = "-110.74"  # degrees, east is positive (west is negative)
    elevation = "1450"     # metres above sea level; 0 if unknown

Every part of wayfindinglib reads the site through this module, so the
planning code, the live-status tools and the stored default
`SiteProfile` cannot disagree. `configured_observer_location` returns
`None` when latitude or longitude is missing. `resolve_site_location`
then falls back to a default site (Denver) and logs a warning, because a
wrong site makes every altitude, rise time and set time wrong.
`get_or_seed_default_site_profile` stores that resolved site as the
default `SiteProfile` the first time it is asked for
(`Wayfinding_Library_Architecture.md`).
"""

import logging
from typing import Any

from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile

logger = logging.getLogger(__name__)

LOCATION_SECTION = "Observatory.Location"
"""The configuration section that holds the site."""

_DEFAULT_SITE_PROFILE_ID = "default"

FALLBACK_SITE: dict[str, float] = {"latitude": 39.7392, "longitude": -104.9903, "elevation": 1600.0}
"""Denver: the site used when the configuration does not name one."""


def configured_observer_location(config: Any) -> dict[str, float] | None:
    """Read the observatory site from the ``Observatory.Location`` settings.

    Parameters
    ----------
    config : `AppConfiguration` or `None`
        The application configuration.

    Returns
    -------
    location : `dict` [`str`, `float`] or `None`
        The ``"latitude"``, ``"longitude"`` and ``"elevation"`` (metres,
        0.0 if unset), or `None` if latitude or longitude is not set.
    """
    app_config = getattr(config, "app_config", None)
    if app_config is None:
        return None
    latitude = app_config.get(LOCATION_SECTION, "latitude", fallback=None)
    longitude = app_config.get(LOCATION_SECTION, "longitude", fallback=None)
    if latitude is None or longitude is None:
        return None
    elevation = app_config.get(LOCATION_SECTION, "elevation", fallback=0.0)
    return {"latitude": float(latitude), "longitude": float(longitude), "elevation": float(elevation)}


def resolve_site_location(config: Any) -> dict[str, float]:
    """Return the configured site, or the Denver fallback with a warning.

    Parameters
    ----------
    config : `AppConfiguration` or `None`
        The application configuration.

    Returns
    -------
    location : `dict` [`str`, `float`]
        The ``"latitude"``, ``"longitude"`` and ``"elevation"`` to use.
    """
    site = configured_observer_location(config)
    if site is not None:
        return site
    logger.warning(
        "No Observatory.Location latitude/longitude in the configuration; using the default "
        "site (Denver). Altitudes, rise and set times will be wrong for any other observatory."
    )
    return dict(FALLBACK_SITE)


def get_or_seed_default_site_profile(butler: Any, config: Any = None) -> SiteProfile:
    """Return the recorded default `SiteProfile`, seeding one if none exists.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The storage layer to read from and, if seeding, write to.
    config : `AppConfiguration`, optional
        Application configuration to seed the default from. If `None`,
        the Denver fallback is used.

    Returns
    -------
    site_profile : `SiteProfile`
        The recorded or newly seeded default site profile.
    """
    existing = butler.get("site_profile", {"id": _DEFAULT_SITE_PROFILE_ID})
    if existing is not None:
        return existing
    site = resolve_site_location(config)
    seeded = SiteProfile(
        id=_DEFAULT_SITE_PROFILE_ID,
        name="Default Site",
        latitude_deg=site["latitude"],
        longitude_deg=site["longitude"],
        elevation_m=site["elevation"],
    )
    butler.put(seeded, "site_profile", {"id": _DEFAULT_SITE_PROFILE_ID})
    return seeded
