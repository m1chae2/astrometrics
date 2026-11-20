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

The tile functions at the end of the file fetch one HEALPix sky tile instead
of a circle, for the tile-based cache (see ``catalog_store``). A tile is one
of the equal-area pieces of sky that ``sky_tiles`` defines, and every Gaia
star's ID tells which tile it is in. `fetch_gaia_tile` tries the services in
the same order as the cone search and reports how deep the answer is
complete, so the cache can record it.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table

from astrometricslib._internal.drivers.sky_tiles import build_pixel_query, pixel_source_id_range
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.offline import require_online
from astrometricslib.utilities.exceptions import ONLINE_QUERY_ERRORS

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
    magnitude_filter: str | None = None,
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
    magnitude_filter : `str`, optional
        A VizieR filter for the Gmag column, such as ``"15.0..18.0"``. The
        default is ``<max_magnitude``.

    Returns
    -------
    table : `astropy.table.Table`
        The stars, in the column layout of `GAIA_CONE_COLUMNS`. Empty when
        the circle holds no star brighter than `max_magnitude`.

    Network and service errors are not caught here; they reach the caller
    as astroquery raised them.
    """
    require_online("VizieR")
    from astroquery.vizier import Vizier

    vizier = Vizier(
        columns=["Source", "RA_ICRS", "DE_ICRS", "+Gmag", "pmRA", "pmDE"],
        column_filters={"Gmag": magnitude_filter or f"<{max_magnitude}"},
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
    require_online("the ESA Gaia archive")
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


# HEALPix numbering of the 12 faces: how many rings down the face's top
# corner sits and how far around it starts. These are the standard values.
_FACE_RING_OFFSET = np.array([2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4])
_FACE_LONGITUDE_OFFSET = np.array([1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7])

# The circle that holds a tile is made a little larger than the farthest
# outline point, because the tile's edges curve slightly outward between the
# outline points.
_TILE_CIRCLE_PADDING = 1.05

# The tile outline is sampled at the nine points of a 3 x 3 grid across the
# pixel: the four corners, the four edge midpoints and the center (first).
_OUTLINE_OFFSETS = [(0.0, 0.0), (-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5),
                    (0.0, -0.5), (0.5, 0.0), (0.0, 0.5), (-0.5, 0.0)]  # fmt: skip


def _gather_even_bits(values: int) -> int:
    """Collect the even-numbered binary digits of a number into a new number.

    This undoes the weaving of two pixel coordinates into one number: bit 0
    stays bit 0, bit 2 becomes bit 1, bit 4 becomes bit 2, and so on.

    Parameters
    ----------
    values : `int`
        A non-negative whole number.

    Returns
    -------
    gathered : `int`
        The even-numbered digits packed together.
    """
    gathered = 0
    position = 0
    while values >> (2 * position):
        gathered |= ((values >> (2 * position)) & 1) << position
        position += 1
    return gathered


def tile_outline_points(healpix_level: int, tile: int) -> tuple[np.ndarray, np.ndarray]:
    """Find the center and the outline points of one sky tile.

    Parameters
    ----------
    healpix_level : `int`
        The level the tile number belongs to.
    tile : `int`
        The tile's HEALPix nested number.

    Returns
    -------
    ra_degrees, dec_degrees : `numpy.ndarray`
        Nine points in degrees: the center first, then the four corners and
        the four edge midpoints. This inverts the numbering that
        `sky_tiles.healpix_pixels_of_points` uses (the standard HEALPix
        nested scheme), so a test checks the two against each other.
    """
    side = 2**healpix_level
    face, within_face = divmod(int(tile), side * side)
    pixel_x = _gather_even_bits(within_face)
    pixel_y = _gather_even_bits(within_face >> 1)
    offsets = np.array(_OUTLINE_OFFSETS)
    x_values = pixel_x + offsets[:, 0]
    y_values = pixel_y + offsets[:, 1]
    ring = _FACE_RING_OFFSET[face] * side - x_values - y_values - 1
    north_cap = ring < side
    south_cap = ring > 3 * side
    ring_size = np.where(north_cap, ring, np.where(south_cap, 4 * side - ring, side))
    sin_dec = np.where(
        north_cap,
        1.0 - ring_size**2 / (3.0 * side**2),
        np.where(south_cap, ring_size**2 / (3.0 * side**2) - 1.0, (2.0 * side - ring) * 2.0 / (3.0 * side)),
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        longitude = (_FACE_LONGITUDE_OFFSET[face] * ring_size + x_values - y_values) / (2.0 * ring_size)
    longitude = np.where(ring_size > 0, longitude * (math.pi / 2.0), 0.0)
    ra_degrees = np.degrees(np.mod(longitude, 2.0 * math.pi))
    dec_degrees = np.degrees(np.arcsin(np.clip(sin_dec, -1.0, 1.0)))
    return ra_degrees, dec_degrees


def separations_degrees(
    ra_center: float, dec_center: float, ra_values: np.ndarray, dec_values: np.ndarray
) -> np.ndarray:
    """Measure the angle from one point to each of several points.

    Parameters
    ----------
    ra_center, dec_center : `float`
        The first point, in degrees.
    ra_values, dec_values : `numpy.ndarray`
        The other points, in degrees.

    Returns
    -------
    separations : `numpy.ndarray`
        The angles in degrees, from the haversine formula (accurate for
        small angles).
    """
    phi1 = math.radians(dec_center)
    phi2 = np.radians(dec_values)
    half_ra = np.radians(ra_values - ra_center) / 2.0
    half_dec = (phi2 - phi1) / 2.0
    haversine = np.sin(half_dec) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(half_ra) ** 2
    return np.degrees(2.0 * np.arcsin(np.minimum(1.0, np.sqrt(haversine))))


def tile_circle(healpix_level: int, tile: int) -> tuple[float, float, float]:
    """Find a circle that holds a whole sky tile.

    Parameters
    ----------
    healpix_level : `int`
        The level the tile number belongs to.
    tile : `int`
        The tile's HEALPix nested number.

    Returns
    -------
    circle : `tuple` [`float`, `float`, `float`]
        The center RA and Dec and the radius, in degrees. The radius
        reaches past the farthest corner and edge midpoint (about 0.33
        degrees at level 7).
    """
    ra_values, dec_values = tile_outline_points(healpix_level, tile)
    ra_center, dec_center = float(ra_values[0]), float(dec_values[0])
    farthest = float(np.max(separations_degrees(ra_center, dec_center, ra_values, dec_values)))
    return ra_center, dec_center, farthest * _TILE_CIRCLE_PADDING


def query_gaia_tile_esa(
    healpix_level: int, tile: int, have_magnitude: float, want_magnitude: float, row_limit: int
) -> Table:
    """Download the stars of one sky tile that the cache does not yet hold.

    The tile is asked for by its range of source IDs, which the archive can
    answer without a position search. Only stars with ``have_magnitude <= G
    < want_magnitude`` are asked for, so a tile already complete to
    ``have_magnitude`` fetches only its fainter stars.

    Parameters
    ----------
    healpix_level : `int`
        The tile level.
    tile : `int`
        The tile's HEALPix nested number.
    have_magnitude : `float`
        The depth the cache already holds for this tile (0 for none).
    want_magnitude : `float`
        The new depth wanted.
    row_limit : `int`
        The most rows to return, brightest first.

    Returns
    -------
    table : `astropy.table.Table`
        The stars, in the column layout of `GAIA_CONE_COLUMNS`.

    Network and service errors are not caught here.
    """
    require_online("the ESA Gaia archive")
    from astroquery.gaia import Gaia

    low, high = pixel_source_id_range(healpix_level, tile)
    query = build_pixel_query(low, high, float(want_magnitude))
    # build_pixel_query gives the four position columns; the cache also keeps
    # the proper motion and the name, and wants the brightest rows first.
    query = query.replace(
        "SELECT source_id, ra, dec, phot_g_mean_mag",
        f"SELECT TOP {int(row_limit)} source_id, ra, dec, phot_g_mean_mag, designation, pmra, pmdec",
        1,
    )
    query += f" AND phot_g_mean_mag >= {float(have_magnitude)!r} ORDER BY phot_g_mean_mag ASC"
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


def query_gaia_tile_vizier(
    healpix_level: int,
    tile: int,
    have_magnitude: float,
    want_magnitude: float,
    row_limit: int,
    timeout_seconds: float,
) -> Table:
    """Download the stars of one sky tile from VizieR.

    VizieR has no source-ID search, so this asks for the circle that holds
    the tile (see `tile_circle`) with the magnitude filter, then keeps only
    the stars whose source ID lies in the tile's range. The row limit
    applies to the circle, which is larger than the tile; a result that
    fills it is treated as cut short by the caller.

    Parameters
    ----------
    healpix_level : `int`
        The tile level.
    tile : `int`
        The tile's HEALPix nested number.
    have_magnitude : `float`
        The depth the cache already holds for this tile (0 for none).
    want_magnitude : `float`
        The new depth wanted.
    row_limit : `int`
        The most rows VizieR may return for the circle.
    timeout_seconds : `float`
        How long the HTTP request may take.

    Returns
    -------
    table : `astropy.table.Table`
        The tile's stars with ``have_magnitude <= G < want_magnitude``, in
        the column layout of `GAIA_CONE_COLUMNS`. ``meta["circle_rows"]`` is
        the number of rows VizieR returned before the tile filter, and
        ``meta["circle_faintest_g"]`` is the faintest G among them.
    """
    ra_center, dec_center, radius = tile_circle(healpix_level, tile)
    magnitude_filter = (
        f"{float(have_magnitude)!r}..{float(want_magnitude)!r}"
        if have_magnitude > 0
        else f"<{float(want_magnitude)!r}"
    )
    found = query_gaia_cone_vizier(
        ra_center,
        dec_center,
        radius,
        want_magnitude,
        row_limit,
        timeout_seconds,
        magnitude_filter=magnitude_filter,
    )
    low, high = pixel_source_id_range(healpix_level, tile)
    source_ids = np.array([int(value) for value in found["source_id"]], dtype=np.int64)
    magnitudes = np.asarray(found["phot_g_mean_mag"], dtype=float)
    keep = (
        (source_ids >= low)
        & (source_ids < high)
        & (magnitudes >= have_magnitude)
        & (magnitudes < want_magnitude)
    )
    result = found[keep]
    result.meta["circle_rows"] = len(found)
    result.meta["circle_faintest_g"] = float(np.nanmax(magnitudes)) if len(found) else float(want_magnitude)
    return result


@dataclass
class TileFetch:
    """One answer for one sky tile.

    Attributes
    ----------
    table : `astropy.table.Table`
        The stars returned, in the column layout of `GAIA_CONE_COLUMNS`.
    complete_to_g : `float`
        Every star of the tile with G below this is now in the cache. It is
        the depth asked for, or less if the answer was cut short.
    service : `str`
        The service that answered.
    row_limit_reached : `bool`
        True when the answer hit the row limit.
    """

    table: Table
    complete_to_g: float
    service: str
    row_limit_reached: bool


def _run_directly(query: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run a query with no deadline of its own.

    Parameters
    ----------
    query : `Callable`
        The query to run.
    timeout_seconds : `float`
        Ignored. A caller that needs a deadline passes its own runner.

    Returns
    -------
    result : `Any`
        What the query returned.
    """
    del timeout_seconds
    return query()


def fetch_gaia_tile(
    healpix_level: int,
    tile: int,
    have_magnitude: float,
    want_magnitude: float,
    row_limit: int,
    *,
    vizier_timeout_seconds: float,
    esa_timeout_seconds: float,
    depth_step_magnitudes: float,
    depth_steps_down: int,
    run_with_deadline: Callable[[Callable[[], Any], float], Any] = _run_directly,
    vizier_query: Callable[..., Table] = query_gaia_tile_vizier,
    esa_query: Callable[..., Table] = query_gaia_tile_esa,
    skip_service: Callable[[str], bool] | None = None,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> TileFetch:
    """Download the missing depth of one sky tile, trying each service in turn.

    The order is the one the cone search uses: VizieR, then the ESA Gaia
    archive, both at `want_magnitude`; then VizieR again at up to
    `depth_steps_down` brighter limits, each `depth_step_magnitudes` brighter
    than the last. A limit not fainter than `have_magnitude` is skipped. The
    first answer wins. When the answer fills the row limit, the tile is
    complete only to the faintest G in it, because the rows come brightest
    first.

    Parameters
    ----------
    healpix_level, tile : `int`
        The tile.
    have_magnitude : `float`
        The depth the cache already holds for this tile (0 for none).
    want_magnitude : `float`
        The depth wanted.
    row_limit : `int`
        The most rows one answer may have.
    vizier_timeout_seconds, esa_timeout_seconds : `float`
        The deadline for each service. VizieR also gets 5 s more for the
        runner, as the cone search does.
    depth_step_magnitudes : `float`
        How much brighter each repeat VizieR query is.
    depth_steps_down : `int`
        How many repeat queries may follow.
    run_with_deadline : `Callable`, optional
        Runs a query function under a deadline. The default has no deadline;
        the pipeline passes its daemon-thread runner. Tests pass a plain call.
    vizier_query, esa_query : `Callable`, optional
        The service queries, replaceable so tests need no network.
    skip_service : `Callable`, optional
        Given a service name, True if it failed earlier in this run and
        should be skipped.
    on_error : `Callable`, optional
        Called with the service name and the error for each failed attempt.

    Returns
    -------
    fetch : `TileFetch`
        The answer.

    Raises
    ------
    ExternalServiceError
        If every attempt failed.
    """
    attempts: list[tuple[str, float, Callable[[], Table], float]] = []
    for step in range(depth_steps_down + 1):
        magnitude = want_magnitude - step * depth_step_magnitudes
        if magnitude <= have_magnitude:
            break

        def vizier(magnitude: float = magnitude) -> Table:
            """Ask VizieR at this depth.

            Returns
            -------
            table : `astropy.table.Table`
                The tile's stars.
            """
            return vizier_query(
                healpix_level, tile, have_magnitude, magnitude, row_limit, vizier_timeout_seconds
            )

        attempts.append(("VizieR", magnitude, vizier, vizier_timeout_seconds + 5))
        if step == 0:

            def esa(magnitude: float = magnitude) -> Table:
                """Ask the ESA Gaia archive at this depth.

                Returns
                -------
                table : `astropy.table.Table`
                    The tile's stars.
                """
                return esa_query(healpix_level, tile, have_magnitude, magnitude, row_limit)

            attempts.append(("ESA Gaia archive", magnitude, esa, esa_timeout_seconds))

    last_error: BaseException | None = None
    for service, magnitude, query, deadline in attempts:
        if skip_service is not None and skip_service(service):
            continue
        try:
            table = run_with_deadline(query, deadline)
        except (ExternalServiceError, *ONLINE_QUERY_ERRORS) as error:
            last_error = error
            if on_error is not None:
                on_error(service, error)
            continue
        # VizieR's row limit applies to the circle around the tile, ESA's to
        # the tile itself.
        rows_returned = int(table.meta.get("circle_rows", len(table)))
        limit_reached = rows_returned >= row_limit
        reached = magnitude
        if limit_reached and "circle_faintest_g" in table.meta:
            reached = min(magnitude, float(table.meta["circle_faintest_g"]))
        elif limit_reached:
            reached = min(magnitude, float(np.nanmax(np.asarray(table["phot_g_mean_mag"], dtype=float))))
        return TileFetch(table, reached, service, limit_reached)
    raise ExternalServiceError(f"Gaia tile {tile} failed on every service: {last_error}")
