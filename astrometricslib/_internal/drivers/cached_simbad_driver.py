"""Purpose: A SIMBAD driver that stores every answer and reuses it.

Description: `CachedSimbadDriver` wraps another `SimbadDriver` (the built-in
one asks SIMBAD through astroquery) and applies the library's one catalog rule
(see ``catalog_cache``): a fresh stored answer is used with no network call;
otherwise SIMBAD is asked, preferred server first, and the answer is stored;
when SIMBAD cannot be reached the stored answer is used, even an old one, with
a warning.

The three kinds of question are stored differently:

- ``query_region`` (the stars and objects around a point) is cut into sky
  tiles, so two images of the same field share one stored answer. Each tile is
  asked for as the circle that holds it, and the rows inside the wanted circle
  are returned.
- ``query_object`` (one name) is stored under the name, in lower case, with the
  columns asked for.
- ``query_tap`` (a free ADQL question, as the Enhance object list asks) is
  stored under the query text.

Nothing here is specific to a star: the wrapper stores whatever table the
inner driver returned, so each caller reads the same columns as before.
"""

import logging
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import Angle, SkyCoord
from astropy.table import Table

from astrometricslib._internal.drivers.astroquery_simbad_driver import AstroquerySimbadDriver
from astrometricslib._internal.drivers.catalog_cache import (
    SIMBAD_ANSWER_LIFETIME_DAYS,
    SIMBAD_ANSWER_TILE_LEVEL,
    answer_for_cone,
    answer_through_cache,
)
from astrometricslib._internal.drivers.interfaces.simbad_driver import SimbadDriver

logger = logging.getLogger(__name__)

SIMBAD_CATALOG_NAME = "SIMBAD"

# The columns that tell two rows of a region answer apart. The name column
# differs by astroquery version, so each is tried; a row with none of them is
# kept whole.
_ROW_KEY_COLUMNS = ("main_id", "MAIN_ID")


class CachedSimbadDriver(AstroquerySimbadDriver):
    """Answer SIMBAD questions from the library's stored answers when it can.

    Parameters
    ----------
    inner : `SimbadDriver`
        The driver that asks SIMBAD live (the built-in astroquery driver).
        It is a subclass of that driver so code that checks for the built-in
        driver still accepts this one.
    """

    def __init__(self, inner: SimbadDriver) -> None:
        """Keep the driver that asks SIMBAD live.

        Parameters
        ----------
        inner : `SimbadDriver`
            The driver that asks SIMBAD live.
        """
        self.inner = inner

    @staticmethod
    def _region_variant(votable_fields: tuple[str, ...], row_limit: int | None) -> str:
        """Name what a region question asked for, as part of its stored key.

        Returns
        -------
        variant : `str`
            The sorted columns and the row limit.
        """
        return f"region|{','.join(sorted(votable_fields))}|{row_limit}"

    @staticmethod
    def _radius_degrees(radius: str) -> float:
        """Read a radius such as ``"0.2d"``.

        Returns
        -------
        degrees : `float`
            The radius in degrees.
        """
        return float(Angle(radius).deg) if not isinstance(radius, int | float) else float(radius)

    def query_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
        allow_live: bool = True,
    ) -> Any:
        """Give the SIMBAD objects within `radius` of a point.

        Parameters
        ----------
        coordinates : `astropy.coordinates.SkyCoord`
            Center of the search cone.
        radius : `str`
            Cone radius in a form astroquery accepts, such as ``"0.5d"``.
        votable_fields : `tuple` [`str`], optional
            Extra columns to request.
        row_limit : `int`, optional
            The most rows one stored tile may hold.
        allow_live : `bool`, optional
            When `False` only stored tiles are read and no network call is
            made.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The rows inside the cone, or `None` when there are none.

        """
        center = SkyCoord(coordinates).icrs
        radius_degrees = self._radius_degrees(radius)

        def fetch_circle(ra: float, dec: float, circle_radius: float) -> Table | None:
            """Ask SIMBAD live for the circle that holds one tile.

            Returns
            -------
            table : `astropy.table.Table` or `None`
                The rows.
            """
            return self.inner.query_region(
                SkyCoord(ra * u.deg, dec * u.deg),
                f"{circle_radius:.6f}d",
                votable_fields=votable_fields,
                row_limit=row_limit,
            )

        tiles = answer_for_cone(
            SIMBAD_CATALOG_NAME,
            self._region_variant(votable_fields, row_limit),
            float(center.ra.deg),
            float(center.dec.deg),
            radius_degrees,
            healpix_level=SIMBAD_ANSWER_TILE_LEVEL,
            lifetime_days=SIMBAD_ANSWER_LIFETIME_DAYS,
            fetch_circle=fetch_circle,
            key_columns=_ROW_KEY_COLUMNS,
            allow_live=allow_live,
        )
        if tiles is None:
            return None
        return self._inside_cone(tiles, center, radius_degrees)

    @staticmethod
    def _inside_cone(table: Table, center: SkyCoord, radius_degrees: float) -> Table | None:
        """Keep the rows of a joined tile answer that lie inside the cone.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            The rows inside, or `None` when none are (or the table has no
            position columns, in which case it is returned whole).
        """
        ra_column = next((name for name in ("ra", "RA_d", "RA", "ra_d") if name in table.colnames), None)
        dec_column = next((name for name in ("dec", "DEC_d", "DEC", "dec_d") if name in table.colnames), None)
        if ra_column is None or dec_column is None:
            return table
        ra = np.ma.filled(np.ma.asarray(table[ra_column], dtype=float), np.nan)
        dec = np.ma.filled(np.ma.asarray(table[dec_column], dtype=float), np.nan)
        usable = np.isfinite(ra) & np.isfinite(dec)
        inside = np.zeros(len(table), dtype=bool)
        if usable.any():
            separation = SkyCoord(ra[usable] * u.deg, dec[usable] * u.deg).separation(center).deg
            inside[usable] = separation <= radius_degrees
        kept = table[inside]
        return kept if len(kept) else None

    def stored_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
    ) -> Any:
        """Read a region only from the stored tiles, with no network call.

        Parameters
        ----------
        coordinates, radius, votable_fields, row_limit
            As for `query_region`.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The stored rows inside the cone.

        """
        return self.query_region(
            coordinates, radius, votable_fields=votable_fields, row_limit=row_limit, allow_live=False
        )

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Give SIMBAD's record of one named object.

        Parameters
        ----------
        object_name : `str`
            Catalog identifier to resolve, such as ``"M 13"``.
        votable_fields : `tuple` [`str`], optional
            Extra columns to request.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The matching row, or `None` if the name did not resolve.

        """
        key = f"object|{' '.join(object_name.casefold().split())}|{','.join(sorted(votable_fields))}"
        return answer_through_cache(
            SIMBAD_CATALOG_NAME,
            key,
            SIMBAD_ANSWER_LIFETIME_DAYS,
            lambda: self.inner.query_object(object_name, votable_fields=votable_fields),
        )

    def query_tap(self, adql_query: str) -> Any:
        """Run an ADQL query against SIMBAD, or give its stored answer.

        Parameters
        ----------
        adql_query : `str`
            The query to run.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The rows, or `None` when there are none.

        """
        return answer_through_cache(
            SIMBAD_CATALOG_NAME,
            f"tap|{' '.join(adql_query.split())}",
            SIMBAD_ANSWER_LIFETIME_DAYS,
            lambda: self.inner.query_tap(adql_query),
        )


def get_simbad_driver() -> SimbadDriver:
    """Give the SIMBAD driver that keeps and reuses its answers.

    Every outside catalog is asked the same way: a fresh stored answer is
    used with no network call, and when SIMBAD cannot be reached the last
    stored answer stands in with a warning that gives its age.

    Returns
    -------
    simbad : `SimbadDriver`
        A driver with `query_object` and `query_region` methods.
    """
    from astrometricslib._internal.drivers.driver_set import Drivers

    return Drivers().simbad_or_default()
