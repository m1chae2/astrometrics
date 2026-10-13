"""Purpose: Set up astropy to work offline with its own Earth-rotation table.

Description: Turning a sky position into altitude and azimuth needs a table of
how the Earth's rotation drifts against atomic time (UT1 minus UTC). This
table comes from the International Earth Rotation and Reference Systems
Service (IERS). By default astropy downloads the newest copy the first time
it needs it. That download stalled the first altitude conversion of a program
for 6 to 14 seconds, and it quietly needed the internet.

This module keeps the setting in one place. `configure_offline_iers` tells
astropy to use the copy that ships with it and to accept it at any age. Both
libraries call it when they are imported, so every program that uses them
works offline. `warm_earth_orientation_data` loads the table ahead of time,
so a program can pay the half-second load cost during startup instead of on
the user's first click.

Measured with astropy 8.0.1 for a star at RA 315.7 deg, Dec 68.7 deg seen
from Bozeman: the first conversion took 5.8 to 13.8 s with the download and
0.5 s without. Altitude and azimuth differed by at most 0.5 arcseconds for
dates from two years ago to ten years ahead. That is far below what a sky map
or a telescope slew can resolve, and no date raised an error.
"""

import time

from astropy.utils import iers


def configure_offline_iers() -> None:
    """Make astropy use its bundled Earth-rotation table, never a download.

    Sets ``iers.conf.auto_download = False`` and ``iers.conf.auto_max_age =
    None``. Without the second setting, astropy refuses to use a table more
    than 30 days old. Calling this more than once is harmless.
    """
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None


def warm_earth_orientation_data() -> float:
    """Load the Earth-rotation table now by doing one altitude conversion.

    The first altitude and azimuth conversion in a program reads the table,
    which takes about half a second. A program that calls this during startup
    moves that cost off the first user action. It applies the offline settings
    first, so it never downloads anything.

    Returns
    -------
    float
        How long the conversion took, in seconds.
    """
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time

    configure_offline_iers()
    started_at = time.monotonic()
    SkyCoord(0.0 * u.deg, 0.0 * u.deg).transform_to(
        AltAz(obstime=Time.now(), location=EarthLocation(lat=0.0 * u.deg, lon=0.0 * u.deg))
    )
    return time.monotonic() - started_at
