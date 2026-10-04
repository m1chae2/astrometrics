"""Read the observatory's site from the application configuration.

The site is the latitude, longitude and elevation of the observatory. It
decides which stars are up, when they rise and set, and where the mount
points. The ``[Observatory.Location]`` section of the configuration holds
it::

    ["Observatory.Location"]
    latitude = "45.76"     # degrees, north is positive
    longitude = "-110.74"  # degrees, east is positive (west is negative)
    elevation = "1450"     # metres above sea level; 0 if unknown

Both the planning code (`wayfindinglib.sky.Sky`) and the live-status tools
read the site through `configured_observer_location`, so they cannot
disagree. When latitude or longitude is missing, the function returns
`None` and the caller decides what to do. The planning code then falls back
to a default site and logs a warning, because a wrong site makes every
altitude, rise time and set time wrong.
"""

from typing import Any

LOCATION_SECTION = "Observatory.Location"
"""The configuration section that holds the site."""


def configured_observer_location(config: Any) -> dict[str, float] | None:
    """Read the observatory site from the ``Observatory.Location`` settings.

    Used when the telescope is not connected, and by the planning code, so
    that every part of the app answers from the same site.

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
