"""Download Gaia DR3 stars in a circle of sky, from either of two services.

Plate solving names the stars it finds by matching them with Gaia DR3. The
same catalog is served in two places:

- **VizieR** (CDS, Strasbourg), catalog ``I/355/gaiadr3``. On 2026-10-10 it
  returned the 14,275 stars brighter than G = 18 within 1.13 degrees of M 13
  in 10 s.
- **The ESA Gaia archive** (table ``gaiadr3.gaia_source``). On the same day
  the same query failed with HTTP 408 after 64 s, and the archive's own
  message said it may be unstable while it prepares for Gaia DR4. Every
  remote lookup of 22 of 23 targets had failed this way.

Both functions here return one table shape, so the caller does not care
which service answered: columns ``source_id``, ``ra``, ``dec`` (degrees,
ICRS, epoch 2016.0), ``phot_g_mean_mag``, ``designation`` and ``pmra``,
``pmdec`` (milliarcseconds per year; ``pmra`` already includes the cos(dec)
factor, as in Gaia). Rows come brightest first, so a table cut at the row
limit loses the faintest stars. Neither function retries or waits for a
deadline; the caller decides the order of services and the time allowed.
"""

from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table

# The VizieR copy of Gaia DR3's main source table.
GAIA_DR3_VIZIER_CATALOG = "I/355/gaiadr3"

# The columns every result has, in this order.
GAIA_CONE_COLUMNS = ("source_id", "ra", "dec", "phot_g_mean_mag", "designation", "pmra", "pmdec")


def _as_float_column(column: Any) -> np.ndarray:
    """Turn a table column into floats, with NaN where a value is missing.

    Parameters
    ----------
    column : `Any`
        A table column, possibly masked.

    Returns
    -------
    values : `numpy.ndarray`
        The values as 64-bit floats.
    """
    return np.ma.filled(np.ma.asarray(column, dtype=float), np.nan)


def query_gaia_cone_vizier(
    ra_center: float,
    dec_center: float,
    radius_deg: float,
    max_magnitude: float,
    row_limit: int,
    timeout_seconds: float,
) -> Table:
    """Download Gaia DR3 stars in a circle from VizieR.

    Parameters
    ----------
    ra_center, dec_center, radius_deg : `float`
        The search circle, in degrees.
    max_magnitude : `float`
        Only stars brighter than this G magnitude are returned.
    row_limit : `int`
        The most rows to return, brightest first.
    timeout_seconds : `float`
        How long the HTTP request may take.

    Returns
    -------
    table : `astropy.table.Table`
        The stars, in the column layout of `GAIA_CONE_COLUMNS`. Empty when
        the circle holds no star brighter than `max_magnitude`.

    Network and service errors are not caught here; they reach the caller
    as astroquery raised them.
    """
    from astroquery.vizier import Vizier

    vizier = Vizier(
        columns=["Source", "RA_ICRS", "DE_ICRS", "+Gmag", "pmRA", "pmDE"],
        column_filters={"Gmag": f"<{max_magnitude}"},
        row_limit=int(row_limit),
        timeout=timeout_seconds,
    )
    results = vizier.query_region(
        SkyCoord(ra_center, dec_center, unit="deg"),
        radius=radius_deg * u.deg,
        catalog=GAIA_DR3_VIZIER_CATALOG,
    )
    if len(results) == 0:
        return Table(names=GAIA_CONE_COLUMNS, dtype=(str, float, float, float, str, float, float))
    found = results[0]
    source_ids = [str(value) for value in found["Source"]]
    return Table(
        [
            source_ids,
            _as_float_column(found["RA_ICRS"]),
            _as_float_column(found["DE_ICRS"]),
            _as_float_column(found["Gmag"]),
            [f"Gaia DR3 {source_id}" for source_id in source_ids],
            _as_float_column(found["pmRA"]),
            _as_float_column(found["pmDE"]),
        ],
        names=GAIA_CONE_COLUMNS,
    )


def query_gaia_cone_esa(
    ra_center: float,
    dec_center: float,
    radius_deg: float,
    max_magnitude: float,
    row_limit: int,
) -> Table:
    """Download Gaia DR3 stars in a circle from the ESA Gaia archive.

    The archive's client offers no timeout, so the caller must run this
    under a deadline of its own.

    Parameters
    ----------
    ra_center, dec_center, radius_deg : `float`
        The search circle, in degrees.
    max_magnitude : `float`
        Only stars brighter than this G magnitude are returned.
    row_limit : `int`
        The most rows to return, brightest first.

    Returns
    -------
    table : `astropy.table.Table`
        The stars, in the column layout of `GAIA_CONE_COLUMNS`.

    Network and service errors are not caught here; they reach the caller
    as astroquery raised them.
    """
    from astroquery.gaia import Gaia

    # The table is pinned (gaiadr3.gaia_source) rather than left to the
    # archive's current default release, so it always matches the
    # "Gaia DR3 ..." names and the local cache.
    # Every value is turned into a number before it joins the query, so no
    # text can reach it. The parts are joined, not formatted into one string.
    query = " ".join([
        "SELECT TOP",
        str(int(row_limit)),
        "source_id, ra, dec, phot_g_mean_mag, designation, pmra, pmdec",
        "FROM gaiadr3.gaia_source WHERE phot_g_mean_mag <",
        repr(float(max_magnitude)),
        "AND CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS',",
        f"{float(ra_center)!r}, {float(dec_center)!r}, {float(radius_deg)!r}))=1",
        "ORDER BY phot_g_mean_mag ASC",
    ])
    found = Gaia.launch_job_async(query, dump_to_file=False).get_results()
    return Table(
        [
            [str(value) for value in found["source_id"]],
            _as_float_column(found["ra"]),
            _as_float_column(found["dec"]),
            _as_float_column(found["phot_g_mean_mag"]),
            [str(value) for value in found["designation"]],
            _as_float_column(found["pmra"]),
            _as_float_column(found["pmdec"]),
        ],
        names=GAIA_CONE_COLUMNS,
    )
