"""Purpose: Divide the sky into the parts the analysis compares.

Description: A measurement's pointing is placed in one part of the sky along
each of three dimensions:

* Altitude, in 15 degree bands. Light from low in the sky crosses more air
  (airmass, the path length through the atmosphere, is 2.0 at 30 degrees and
  1.4 at 45 degrees), so seeing and refraction worsen toward the horizon.
* Azimuth, in eight 45 degree sectors named for the compass points. Nearby
  buildings, trees and ground heat can make one direction worse.
* Pier side. A German equatorial mount tracks from one side of its pier, then
  flips to the other. The two sides can behave differently (balance, backlash
  direction, cable drag).

Each dimension is analysed on its own, so a part of the sky is one band, one
sector or one pier side, never a combination. The data here is too sparse to
split further.
"""

from itertools import pairwise

ALTITUDE_BAND_EDGES = (0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0)
"""Band edges in degrees. 15 degree steps give about equal steps in airmass
above 30 degrees (2.0, 1.4, 1.15, 1.04), where most observing happens."""

AZIMUTH_SECTORS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
"""Sector names, clockwise from north. Each covers 45 degrees centred on its
compass point."""

PIER_SIDES = ("East", "West")
"""The two sides of the pier."""

_AZIMUTH_SECTOR_WIDTH = 45.0


def altitude_label(altitude_degrees: float) -> str:
    """Name the altitude band a pointing falls in.

    Returns
    -------
    label : `str`
        For example ``"30-45 deg"``. An altitude below the first edge or
        at or above the last edge falls in the nearest band.
    """
    bands = list(pairwise(ALTITUDE_BAND_EDGES))
    lower, upper = bands[-1]
    for band_lower, band_upper in bands:
        if altitude_degrees < band_upper:
            lower, upper = band_lower, band_upper
            break
    return f"{lower:g}-{upper:g} deg"


def altitude_band_limits(label: str) -> tuple[float, float]:
    """Read the edges of an altitude band from its label.

    Returns
    -------
    limits : `tuple` [`float`, `float`]
        Lower and upper edge, in degrees.
    """
    lower, upper = label.removesuffix(" deg").split("-")
    return float(lower), float(upper)


def azimuth_label(azimuth_degrees: float) -> str:
    """Name the azimuth sector a pointing falls in.

    Returns
    -------
    label : `str`
        ``"N"``, ``"NE"`` and so on.
    """
    index = int(((azimuth_degrees % 360.0) + _AZIMUTH_SECTOR_WIDTH / 2.0) // _AZIMUTH_SECTOR_WIDTH) % len(
        AZIMUTH_SECTORS
    )
    return AZIMUTH_SECTORS[index]


def pier_label(pier_side: str | None) -> str | None:
    """Normalise a pier side to ``"East"`` or ``"West"``.

    Returns
    -------
    label : `str` or `None`
        The normalised side, or `None` if the pier side is unknown. Frame
        headers write it in capitals and guide logs do not.
    """
    if pier_side is None:
        return None
    normalised = pier_side.strip().capitalize()
    return normalised if normalised in PIER_SIDES else None


def bins_of(
    altitude_degrees: float | None, azimuth_degrees: float | None, pier_side: str | None
) -> list[tuple[str, str]]:
    """List the parts of the sky a pointing belongs to.

    Returns
    -------
    bins : `list` [`tuple` [`str`, `str`]]
        A (dimension, label) pair for each dimension with a known value.
    """
    found = []
    if altitude_degrees is not None:
        found.append(("altitude", altitude_label(altitude_degrees)))
    if azimuth_degrees is not None:
        found.append(("azimuth", azimuth_label(azimuth_degrees)))
    pier = pier_label(pier_side)
    if pier is not None:
        found.append(("pier_side", pier))
    return found


def reachable_bins(minimum_altitude_degrees: float, maximum_altitude_degrees: float) -> list[tuple[str, str]]:
    """List every part of the sky the telescope is configured to reach.

    Parameters
    ----------
    minimum_altitude_degrees : `float`
        The lowest altitude the telescope may observe at.
    maximum_altitude_degrees : `float`
        The highest.

    Returns
    -------
    bins : `list` [`tuple` [`str`, `str`]]
        Altitude bands that overlap the configured range, every azimuth
        sector, and both pier sides.
    """
    altitude_bands = [
        ("altitude", f"{lower:g}-{upper:g} deg")
        for lower, upper in pairwise(ALTITUDE_BAND_EDGES)
        if upper > minimum_altitude_degrees and lower < maximum_altitude_degrees
    ]
    return [
        *altitude_bands,
        *(("azimuth", sector) for sector in AZIMUTH_SECTORS),
        *(("pier_side", side) for side in PIER_SIDES),
    ]
