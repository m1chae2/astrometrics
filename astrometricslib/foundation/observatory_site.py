"""Where the observatory is on Earth, read from the config file.

Some calculations depend on the observer's position. The light from a star
reaches a telescope at a slightly different moment than it reaches Earth's
center, and the time scale used for light curves (BJD_TDB, defined in
`astrometricslib.pipelines.photometry.pre_processing.observation_times`)
needs the site to correct for that. The site is listed in the
``[Observatory.Location]`` section, which the planning tools also read::

    [Observatory.Location]
    latitude = 45.0
    longitude = -110.0
    elevation = 1500

This module only reads the section. It does not fall back to a default
site: a made-up site would put a wrong correction into every time stamp, so
the caller gets `None` and decides what to do.
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

LOCATION_SECTION = "Observatory.Location"


class ObservatorySite(BaseModel):
    """The observatory's position on Earth.

    Attributes
    ----------
    latitude_deg : `float`
        Degrees north of the equator. South is negative.
    longitude_deg : `float`
        Degrees east of the Greenwich meridian. West is negative.
    elevation_m : `float`
        Height above sea level, in meters. It is 0.0 when the config does
        not give one.
    """

    model_config = ConfigDict(frozen=True)

    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-360.0, le=360.0)
    elevation_m: float = Field(default=0.0, allow_inf_nan=False)


def load_observatory_site(app_config: Any) -> ObservatorySite | None:
    """Read the observatory site from the ``Observatory.Location`` section.

    Parameters
    ----------
    app_config : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.

    Returns
    -------
    site : `ObservatorySite` or `None`
        The site, or `None` when latitude or longitude is missing, or when
        a value is not a number in range. A bad value logs a warning.
    """
    latitude = app_config.get_value(LOCATION_SECTION, "latitude", None)
    longitude = app_config.get_value(LOCATION_SECTION, "longitude", None)
    if latitude is None or longitude is None:
        return None
    elevation = app_config.get_value(LOCATION_SECTION, "elevation", None)
    try:
        # The range checks on `ObservatorySite` also reject NaN and infinity.
        return ObservatorySite(
            latitude_deg=float(latitude),
            longitude_deg=float(longitude),
            elevation_m=float(elevation) if elevation is not None else 0.0,
        )
    except ValueError as error:
        logger.warning(
            "The [%s] section is not a valid site, so no site is used: %s", LOCATION_SECTION, error
        )
        return None
