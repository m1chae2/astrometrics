"""Purpose: Astronomical target visibility calculations.

Description: Evaluates real-time coordinate transformations, including
altitude/azimuth tracking and rise/set estimation, to determine whether
target objects are above the horizon.
"""

import logging
from typing import Any

import astropy.units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time

logger = logging.getLogger(__name__)


def get_target_status(observation, target_id: str) -> dict[str, Any] | None:  # ruff: ignore[missing-type-function-argument]
    """Calculate real-time Alt/Az coordinates and visibility for a target.

    Returns
    -------
    status : `dict` or `None`
        Formatted RA/Dec/Alt/Az, altitude in degrees, and visibility
        flag, or `None` if the target could not be resolved or the
        coordinate calculation failed.
    """
    from astrometricslib import Astrometrics, StellarCatalog, parse_coordinate_string

    astrometrics = Astrometrics(observation._config)
    target = astrometrics.targets.get(target_id)

    if not target:
        analysis_api = StellarCatalog(observation._config)
        target = analysis_api.get_object(target_id)

    if not target:
        return None

    try:
        ra_deg = parse_coordinate_string(str(target.ra), is_ra=True)
        dec_deg = parse_coordinate_string(str(target.dec), is_ra=False)

        coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)
        now = Time.now()

        altaz_frame = AltAz(obstime=now, location=observation.location)
        altaz = coord.transform_to(altaz_frame)

        return {
            "id": target_id,
            "ra": f"{ra_deg / 15.0:.4f}h",
            "dec": f"{dec_deg:.4f}d",
            "alt": f"{altaz.alt.deg:.1f}°",
            "az": f"{altaz.az.deg:.1f}°",
            "alt_deg": float(altaz.alt.deg),
            "visible": bool(altaz.alt.deg > 0),
            "rise_time": "N/A",
            "set_time": "N/A",
        }
    except Exception as e:
        logger.error(f"Error calculating astronomy status for {target_id}: {e}")
        return None


def get_visible_targets(observation) -> list[dict[str, Any]]:  # ruff: ignore[missing-type-function-argument]
    """Return targets above the horizon (Alt > 0), sorted by altitude.

    Returns
    -------
    visible_targets : `list` [`dict`]
        Status fields for each visible target, sorted by descending
        altitude.
    """
    from astrometricslib import Astrometrics

    visible_list = []
    astrometrics = Astrometrics(observation._config)
    targets = astrometrics.targets.list()

    for target in targets:
        status = observation.get_target_status(target.id)
        if status and status.get("visible"):
            visible_list.append(status)

    visible_list.sort(key=lambda x: x.get("alt_deg", 0), reverse=True)
    return visible_list
