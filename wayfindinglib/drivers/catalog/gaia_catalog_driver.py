"""GAIA DR3 online catalog query driver.

Queries the ESA Gaia Data Release 3 catalog via astroquery.gaia using ADQL.
Applies a G-band magnitude limit and a hard radius cap to prevent runaway
queries against the ~2 billion row catalog.

REQ: PLN-3.2
"""

import logging

from astrometricslib import StellarObject
from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver

logger = logging.getLogger(__name__)

_GAIA_ROW_LIMIT: int = 1000
_GAIA_MAGNITUDE_LIMIT: float = 22.0
# ADQL's CIRCLE function rejects radii >= 90° (a hemisphere is its largest
# representable cap), so this is also the threshold at which query_region()
# switches to an unbounded, magnitude-only "whole sky" query.
_GAIA_MAX_QUERY_RADIUS_DEGREES: float = 90.0


class GaiaCatalogDriver(CatalogDriver):
    """Query driver for the ESA GAIA DR3 online catalog.

    Issues ADQL queries against the Gaia TAP service via astroquery.gaia,
    applying a flat G < _GAIA_MAGNITUDE_LIMIT cutoff and a _GAIA_ROW_LIMIT
    row cap (brightest-first) to keep queries bounded.

    REQ: PLN-3.2
    """

    @property
    def driver_name(self) -> str:
        """The registry key for this driver ("gaia")."""
        return "gaia"

    @property
    def display_name(self) -> str:
        """The human-readable catalog name ("GAIA DR3")."""
        return "GAIA DR3"

    @property
    def maximum_query_radius_degrees(self) -> float:
        """The largest radius, in degrees, this driver accepts."""
        return _GAIA_MAX_QUERY_RADIUS_DEGREES

    def query_region(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
    ) -> list[StellarObject]:
        """Query GAIA DR3 for bright stars in a circular sky region.

        Only returns stars brighter than G = _GAIA_MAGNITUDE_LIMIT to
        limit result volume. A TOP clause further restricts results to
        the _GAIA_ROW_LIMIT brightest objects when many candidates
        exist.

        Parameters
        ----------
        ra_degrees : float
            Center Right Ascension in degrees (ICRS).
        dec_degrees : float
            Center Declination in degrees (ICRS).
        radius_degrees : float
            Search radius in degrees. Clamped to maximum_query_radius_degrees.

        Returns
        -------
        List[StellarObject]
            Transient StellarObject instances. Never persisted to the database.
        """
        results: list[StellarObject] = []

        try:
            from astroquery.gaia import Gaia
        except ImportError:
            logger.error("astroquery is not installed; cannot query GAIA catalog.")
            return results

        effective_radius = min(radius_degrees, self.maximum_query_radius_degrees)
        magnitude_limit = _GAIA_MAGNITUDE_LIMIT

        # A "whole sky" request (radius at the 90° cap) can't be
        # expressed as an ADQL CIRCLE — the widest circle ADQL accepts
        # only covers one hemisphere — so it's expressed as a
        # magnitude-only query with no spatial filter instead.
        if effective_radius >= _GAIA_MAX_QUERY_RADIUS_DEGREES:
            spatial_filter = ""
        else:
            spatial_filter = (
                f"WHERE CONTAINS("
                f"  POINT('ICRS', ra, dec), "
                f"  CIRCLE('ICRS', {ra_degrees}, {dec_degrees}, {effective_radius})"
                f")=1 "
                f"AND "
            )

        adql_query = (
            f"SELECT TOP {_GAIA_ROW_LIMIT} "
            f"source_id, ra, dec, phot_g_mean_mag "
            f"FROM gaiadr3.gaia_source "
            f"{spatial_filter}"
            f"{'WHERE ' if not spatial_filter else ''}phot_g_mean_mag < {magnitude_limit} "
            f"ORDER BY phot_g_mean_mag ASC"
        )

        try:
            job = Gaia.launch_job(adql_query, dump_to_file=False, verbose=False)
            table = job.get_results()

            if table is None or len(table) == 0:
                return results

            for row in table:
                try:
                    source_id = str(row["source_id"])
                    ra_value = float(row["ra"])
                    dec_value = float(row["dec"])
                    magnitude_g = None
                    try:
                        raw_magnitude = row["phot_g_mean_mag"]
                        if not getattr(raw_magnitude, "mask", False):
                            magnitude_g = float(raw_magnitude)
                    except ValueError, TypeError:
                        pass

                    results.append(
                        StellarObject(
                            id=f"GAIA_{source_id}",
                            name=f"Gaia DR3 {source_id}",
                            ra=ra_value,
                            dec=dec_value,
                            magnitude=magnitude_g if magnitude_g is not None else "",
                            spectralType="",
                        )
                    )
                except Exception as row_error:
                    logger.debug("Skipping GAIA row: %s", row_error)
                    continue

        except Exception as query_error:
            logger.warning("GAIA DR3 catalog query failed or timed out: %s", query_error)

        return results
