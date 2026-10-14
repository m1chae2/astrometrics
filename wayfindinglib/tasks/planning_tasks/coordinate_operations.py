"""Purpose: Coordinate sums for the planning code.

Description: The local mean sidereal time at the site a `SkyEngine`
holds. The RA/Dec to Alt/Az conversion that planning and the INDI drivers
share is `compute_altaz` in `wayfindinglib/astronomy/coordinate_transforms.py`,
in the Foundation, so the drivers never import planning code.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from astropy.time import Time

if TYPE_CHECKING:
    from wayfindinglib.tasks.planning_tasks.sky_engine import SkyEngine


def get_local_sidereal_time(sky: SkyEngine, time_input: datetime | Time) -> float:
    """Calculate the local mean sidereal time (LST) in hours.

    Parameters
    ----------
    sky : `SkyEngine`
        Supplies the observer location.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    float
        Local mean sidereal time in hours (0.0 to 24.0).
    """
    observation_time = time_input if isinstance(time_input, Time) else Time(time_input)
    lst_angle = observation_time.sidereal_time("mean", longitude=sky.location.lon)
    return lst_angle.hour
