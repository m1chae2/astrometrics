"""Gives the Gaia DR3 colors of the stars in a piece of sky.

A star's color is the Gaia ``BP - RP`` index: the star's magnitude in Gaia's
blue band minus its magnitude in the red band. The Sun's index is about 0.82.
A larger index is a redder star. The preview of a color stack uses these
colors to check its white balance against stars that look like the Sun (see
`astrometricslib.pipelines.stacking.post_processing.sun_like_balance`).

The tile table of the star cache (``catalog_store``) keeps position and
brightness only, and Siril's local Gaia file keeps no ``BP - RP`` at all
(only an effective temperature, which for a star behind dust describes the
star and not the light that reached the camera). So the colors come from the
ESA Gaia archive, one question for each sky tile, and are kept in the stored
catalog answers (see ``catalog_cache``). A field asked for once is read from
the disk afterward, with no network. The archive is asked for a tile's range
of source IDs, as the star cache does (``gaia_cone_driver``): on 2026-11-24 a
question for a circle of 1.6 degrees radius timed out after 61 seconds, while
a circle of 1.0 degree at the same place answered in 1.7 seconds. An
unreachable archive is a status: the caller gets `CatalogUnavailableError` and
carries on without the colors.

The question asks for stars from `BRIGHTEST_G_MAGNITUDE` to
`FAINTEST_G_MAGNITUDE`, with or without a color. Stars without a color are
kept because the white-balance check uses them to find stars that have a
close neighbor.
"""

import logging
from collections.abc import Sequence

import numpy as np
from astropy.table import Table, vstack

from astrometricslib._internal.drivers import outside_services
from astrometricslib._internal.drivers.catalog_cache import answer_through_cache
from astrometricslib._internal.drivers.sky_tiles import (
    build_pixel_query,
    pixel_source_id_range,
    tiles_for_footprint,
)
from astrometricslib.foundation.errors import ExternalServiceError

logger = logging.getLogger(__name__)

__all__ = [
    "COLOUR_COLUMNS",
    "gaia_star_colors_in_footprint",
]

# The name the stored answers are filed under.
_CATALOG_NAME = "Gaia DR3 colors"

# The columns of the table this module returns: the Gaia source ID, the
# position in degrees (epoch 2016.0), the G magnitude and the BP - RP color
# (NaN when Gaia has none).
COLOUR_COLUMNS = ("source_id", "ra", "dec", "g", "bp_rp")

# How long a stored answer is used, in days. Gaia DR3 is a frozen release, so
# its colors never change: the answer is kept for ten years, as the Gaia
# variability classes are (see ``catalog_cache``).
_ANSWER_LIFETIME_DAYS = 3650

# The HEALPix level of a stored answer. Level 6 tiles are about 0.9 degrees
# across, about 0.74 square degrees. A tile held 1,617 to 6,838 stars from
# G = 10.5 to 15 in the fields of NGC 6992 and NGC 7023 on 2026-11-24, far
# under the row limit below, and a typical DSLR field needs 10 to 20 tiles.
_ANSWER_TILE_LEVEL = 6

# The brightest and faintest G magnitudes asked for. Stars brighter than
# 10.5 may saturate the camera, so a color measured on them is not used, and
# there are few of them. Stars fainter than 15 are too faint to disturb a
# measurement of a G = 11 star 24 pixels away. These two numbers only bound
# the question; the check picks the stars it measures from this table (see
# `sun_like_balance`).
BRIGHTEST_G_MAGNITUDE = 10.5
FAINTEST_G_MAGNITUDE = 15.0

# The most rows one tile's circle may return. An answer that reaches it is
# refused, because the archive cuts the answer at the limit and rows could
# be missing.
_ROW_LIMIT = 100_000


def _ask_archive_tile(tile: int) -> Table:
    """Ask the Gaia archive live for the stars of one sky tile.

    Parameters
    ----------
    tile : `int`
        The tile's HEALPix nested number at the stored answers' level.

    Returns
    -------
    stars : `astropy.table.Table`
        The stars, with the columns of `COLOUR_COLUMNS`. A star with no
        ``BP - RP`` has NaN there.

    Raises
    ------
    ExternalServiceError
        If the answer reaches the row limit, so stars may be missing.

    Notes
    -----
    The question goes through the outside-service gateway (see
    ``outside_services``), which keeps the rate limit and pauses a service
    after repeated trouble. When this launch sends nothing off the computer,
    the gateway raises an error that the caller treats as an unreachable
    catalog.
    """
    low, high = pixel_source_id_range(_ANSWER_TILE_LEVEL, tile)
    # build_pixel_query writes the ADQL for the archive (not SQL for a local
    # database) from whole numbers and a magnitude limit. This adds the color
    # column, the row limit and the bright end of the magnitude range.
    query = build_pixel_query(low, high, FAINTEST_G_MAGNITUDE).replace(
        "SELECT source_id, ra, dec, phot_g_mean_mag",
        f"SELECT TOP {_ROW_LIMIT} source_id, ra, dec, phot_g_mean_mag, bp_rp",
        1,
    )
    query += f" AND phot_g_mean_mag >= {BRIGHTEST_G_MAGNITUDE}"
    found = outside_services.gaia_archive_query(query)
    if len(found) >= _ROW_LIMIT:
        raise ExternalServiceError(f"The Gaia archive gave {len(found)} stars, the most it is asked for.")

    def floats(name: str) -> np.ndarray:
        """Read one column as 64-bit floats, with NaN for a missing value.

        Returns
        -------
        values : `numpy.ndarray`
            The column.
        """
        return np.ma.filled(np.ma.asarray(found[name], dtype=float), np.nan)

    return Table(
        [
            np.asarray(found["source_id"], dtype=np.int64),
            floats("ra"),
            floats("dec"),
            floats("phot_g_mean_mag"),
            floats("bp_rp"),
        ],
        names=COLOUR_COLUMNS,
    )


def gaia_star_colors_in_footprint(
    corners_ra_dec: Sequence[tuple[float, float]], margin_degrees: float, *, allow_live: bool = True
) -> Table | None:
    """Give the Gaia stars under an image's footprint, with their colors.

    Every tile that touches the footprint (or lies within the margin of it)
    is answered from the stored answers first and the Gaia archive second
    (see ``catalog_cache.answer_through_cache``). The result holds the whole
    of every such tile, a little more than the footprint.

    Parameters
    ----------
    corners_ra_dec : `Sequence` [`tuple` [`float`, `float`]]
        The footprint corners as ``(ra, dec)`` in degrees, in order around
        the edge.
    margin_degrees : `float`
        How far past the edge to include, in degrees.
    allow_live : `bool`, optional
        When `False` only stored answers are used and the network is not
        touched.

    Returns
    -------
    stars : `astropy.table.Table` or `None`
        The stars with the columns of `COLOUR_COLUMNS`, or `None` when no tile
        holds a star.

    Notes
    -----
    `CatalogUnavailableError` (an `ExternalServiceError`) is raised when the
    archive cannot be reached and a tile is not stored. An unreachable
    archive is a status for the caller to report, not a failure.
    """
    pieces: list[Table] = []
    for tile in tiles_for_footprint(corners_ra_dec, margin_degrees, _ANSWER_TILE_LEVEL):
        piece = answer_through_cache(
            _CATALOG_NAME,
            f"esa|{BRIGHTEST_G_MAGNITUDE}-{FAINTEST_G_MAGNITUDE}|L{_ANSWER_TILE_LEVEL}|{tile}",
            _ANSWER_LIFETIME_DAYS,
            lambda tile=tile: _ask_archive_tile(tile),
            allow_live=allow_live,
        )
        if piece is not None and len(piece):
            pieces.append(piece)
    if not pieces:
        return None
    return vstack(pieces, metadata_conflicts="silent") if len(pieces) > 1 else pieces[0]
