"""GAIA DR3 online catalog query driver.

Queries the ESA Gaia Data Release 3 catalog via astroquery.gaia using ADQL.
Applies a G-band magnitude limit and a hard radius cap to prevent runaway
queries against the ~2 billion row catalog.

Stars already saved in the local catalog cache are used instead of asking
ESA again, and every region that is downloaded is saved for next time, so a
patch of sky is only ever slow the first time it is looked at.

REQ: PLN-3.2
"""

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, Protocol

from astrometricslib import StellarObject
from wayfindinglib.drivers.catalog.base_catalog_driver import CatalogDriver

logger = logging.getLogger(__name__)

# The most stars one query may return, brightest first.
#
# Derivation: in the fields this library has imaged, the local Gaia cache
# holds about 1,470 stars per square degree down to G = 18, and about a third
# of those (roughly 490) are brighter than G = 16. The sky map asks for a
# circle 1.5 times the field of view, about 2.4 degrees in radius (18 square
# degrees) for the ASI533MM's 1.6 degree field, so a typical query returns
# about 9,000 stars. 20,000 is more than double that, leaving room for
# denser fields nearer the galactic plane, while keeping one response to a
# few megabytes of JSON.
# Not validated on the galactic plane. If a query hits this limit, the
# brightest stars still arrive first and only the faintest are dropped.
_GAIA_ROW_LIMIT: int = 20000

# Faintest Gaia G magnitude this driver returns.
#
# Derivation: for a 30 s exposure with the 75 mm Apertura 75Q and the
# ASI533MM Pro (1.92 arcsec per pixel), a back-of-envelope signal-to-noise
# estimate gives a 5-sigma detection at about G = 17.5 and photometry good to
# about 5 percent (signal-to-noise 20) at about G = 15.7. It assumes a sky of
# about 21 mag/arcsec^2, so it is uncertain by about half a magnitude.
# Validation: the M 13 field has photometry down to G = 17-18, but only from
# stacked frames, which reach deeper than one 30 s frame; and the star
# cache limit of G = 18 (see DEFAULT_MAGNITUDE_LIMIT in
# astrometricslib/pipelines/astrometry/catalog_seeding.py) is already
# described there as beyond what the stacks can detect. G = 16 keeps the
# stars whose photometry can be trusted while being about a third of the
# data of G = 18. Must match DEEP_STAR_MAX_MAGNITUDE in
# ui/planetariumDisplay/layers/StarOverlay.ts.
_GAIA_MAGNITUDE_LIMIT: float = 16.0

# How long to wait for one Gaia download before giving up, in seconds.
#
# The identification pipeline waits 45 s for its bulk downloads, which use the
# same asynchronous query. This is a little longer because these downloads
# may carry more rows (see _GAIA_ROW_LIMIT). Chosen by judgement, not
# measured.
_GAIA_QUERY_TIMEOUT_SECONDS: float = 60.0

# ADQL's CIRCLE function rejects radii >= 90° (a hemisphere is its largest
# representable cap), so this is also the threshold at which query_region()
# switches to an unbounded, magnitude-only "whole sky" query.
_GAIA_MAX_QUERY_RADIUS_DEGREES: float = 90.0


def _run_with_timeout(query_function: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run a function in the background and stop waiting after a time limit.

    A Gaia job can sit in the server's queue for a long time. Running it on
    a daemon thread means giving up on it does not stop the program from
    closing later.

    Parameters
    ----------
    query_function : `Callable`
        The function to run.
    timeout_seconds : `float`
        How many seconds to wait before giving up.

    Returns
    -------
    result : `Any`
        Whatever the function returned.

    Raises
    ------
    TimeoutError
        If the function has not finished within `timeout_seconds`.
    """
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def _run_and_report() -> None:
        try:
            result_queue.put((True, query_function()))
        except BaseException as query_error:
            # Caught broadly and re-raised on the caller's thread below via
            # `raise payload` -- nothing here is swallowed.
            result_queue.put((False, query_error))

    threading.Thread(target=_run_and_report, daemon=True).start()

    try:
        succeeded, payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty:
        raise TimeoutError(f"Timed out after {timeout_seconds}s") from None

    if succeeded:
        return payload
    raise payload


class GaiaStarCache(Protocol):
    """Where the driver looks for, and saves, Gaia stars on the local disk.

    `astrometricslib.api.stars.StellarCatalog` provides this, so the driver
    reaches the local cache through the `Astrometrics` facade.
    """

    def find_saved_gaia_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float
    ) -> list[tuple[str, float, float, float, str]] | None:
        """Return a circle's saved stars, or `None` if not fully saved."""

    def save_downloaded_gaia_stars(
        self,
        ra: float,
        dec: float,
        radius: float,
        magnitude_limit: float,
        rows: list[tuple[str, float, float, float, str]],
    ) -> None:
        """Save the stars downloaded for a circle."""


class GaiaCatalogDriver(CatalogDriver):
    """Query driver for the ESA GAIA DR3 online catalog.

    Issues ADQL queries against the Gaia TAP service via astroquery.gaia,
    applying a G-band magnitude cutoff (never fainter than
    _GAIA_MAGNITUDE_LIMIT) and a _GAIA_ROW_LIMIT row cap (brightest-first) to
    keep queries bounded.

    Before asking ESA, the driver looks in the local star cache it was given;
    a region it has to download is saved there for next time. Without a
    cache it always downloads.

    Parameters
    ----------
    star_cache : `GaiaStarCache`, optional
        Where to look for, and save, Gaia stars on the local disk.

    REQ: PLN-3.2
    """

    def __init__(self, star_cache: GaiaStarCache | None = None) -> None:
        self._star_cache = star_cache

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
        magnitude_limit: float | None = None,
    ) -> list[StellarObject]:
        """Query GAIA DR3 for bright stars in a circular sky region.

        Only returns stars brighter than G = `magnitude_limit`, and never
        fainter than _GAIA_MAGNITUDE_LIMIT, to limit result volume. When many
        candidates exist, only the _GAIA_ROW_LIMIT brightest are returned.

        Parameters
        ----------
        ra_degrees : float
            Center Right Ascension in degrees (ICRS).
        dec_degrees : float
            Center Declination in degrees (ICRS).
        radius_degrees : float
            Search radius in degrees. Clamped to maximum_query_radius_degrees.
        magnitude_limit : float, optional
            Faintest Gaia G magnitude wanted. Defaults to, and is clamped
            to, _GAIA_MAGNITUDE_LIMIT.

        Returns
        -------
        List[StellarObject]
            Transient StellarObject instances. Never recorded to the database.
        """
        effective_radius = min(radius_degrees, self.maximum_query_radius_degrees)
        effective_limit = (
            _GAIA_MAGNITUDE_LIMIT if magnitude_limit is None else min(magnitude_limit, _GAIA_MAGNITUDE_LIMIT)
        )
        # A whole-sky request is not a circle ADQL can express (see
        # _GAIA_MAX_QUERY_RADIUS_DEGREES), so it is neither looked up in nor
        # saved to the circle-based local cache.
        is_whole_sky = effective_radius >= _GAIA_MAX_QUERY_RADIUS_DEGREES

        if not is_whole_sky:
            cached_rows = self._read_cache(ra_degrees, dec_degrees, effective_radius, effective_limit)
            if cached_rows is not None:
                return [self._stellar_object_from_row(row) for row in cached_rows]

        downloaded_rows = self._download(
            ra_degrees, dec_degrees, effective_radius, effective_limit, is_whole_sky
        )
        if downloaded_rows is None:
            return []

        if not is_whole_sky:
            self._write_cache(ra_degrees, dec_degrees, effective_radius, effective_limit, downloaded_rows)
        return [self._stellar_object_from_row(row) for row in downloaded_rows]

    @staticmethod
    def _stellar_object_from_row(row: tuple[str, float, float, float | None, str]) -> StellarObject:
        """Turn one saved or downloaded star into a `StellarObject`.

        Parameters
        ----------
        row : `tuple`
            ``(source_id, ra, dec, phot_g_mean_mag, designation)``.

        Returns
        -------
        stellar_object : `StellarObject`
            The star, identified by its Gaia source ID.
        """
        source_id, ra_value, dec_value, magnitude_g, _designation = row
        return StellarObject(
            id=f"GAIA_{source_id}",
            name=f"Gaia DR3 {source_id}",
            ra=ra_value,
            dec=dec_value,
            magnitude=magnitude_g,
            spectralType="",
        )

    def _read_cache(
        self, ra_degrees: float, dec_degrees: float, radius_degrees: float, magnitude_limit: float
    ) -> list[tuple[str, float, float, float, str]] | None:
        """Look for the whole circle in the local catalog cache.

        Parameters
        ----------
        ra_degrees, dec_degrees : float
            Center of the circle, in degrees.
        radius_degrees : float
            Radius of the circle, in degrees.
        magnitude_limit : float
            Faintest Gaia G magnitude wanted.

        Returns
        -------
        rows : `list` [`tuple`] or `None`
            The saved stars, or `None` if the circle is not fully saved (or
            the cache could not be read).
        """
        if self._star_cache is None:
            return None
        try:
            return self._star_cache.find_saved_gaia_stars(
                ra_degrees, dec_degrees, radius_degrees, magnitude_limit
            )
        except Exception as cache_error:
            # The cache only makes things faster. If it cannot be read, fall
            # back to asking ESA rather than failing the sky map.
            logger.warning("Could not read the local Gaia cache: %s", cache_error)
            return None

    def _write_cache(
        self,
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
        magnitude_limit: float,
        rows: list[tuple[str, float, float, float | None, str]],
    ) -> None:
        """Save a downloaded circle so it is not downloaded again.

        Parameters
        ----------
        ra_degrees, dec_degrees : float
            Center of the circle, in degrees.
        radius_degrees : float
            Radius of the circle, in degrees.
        magnitude_limit : float
            Faintest Gaia G magnitude that was asked for.
        rows : `list` [`tuple`]
            The downloaded stars, brightest first.
        """
        # If the download stopped at the row limit, the stars beyond it were
        # never fetched, so the circle is only complete as far down as the
        # faintest star that did arrive.
        if self._star_cache is None:
            return
        reached_row_limit = len(rows) >= _GAIA_ROW_LIMIT
        completeness_limit = rows[-1][3] if reached_row_limit and rows[-1][3] is not None else magnitude_limit
        try:
            self._star_cache.save_downloaded_gaia_stars(
                ra_degrees,
                dec_degrees,
                radius_degrees,
                completeness_limit,
                [row for row in rows if row[3] is not None],
            )
        except Exception as cache_error:
            # Saving is only an optimization; the stars are still returned.
            logger.warning("Could not save Gaia stars to the local cache: %s", cache_error)

    @staticmethod
    def _download(
        ra_degrees: float,
        dec_degrees: float,
        radius_degrees: float,
        magnitude_limit: float,
        is_whole_sky: bool,
    ) -> list[tuple[str, float, float, float | None, str]] | None:
        """Ask ESA's Gaia archive for the brightest stars in a circle.

        Parameters
        ----------
        ra_degrees, dec_degrees : float
            Center of the circle, in degrees.
        radius_degrees : float
            Radius of the circle, in degrees.
        magnitude_limit : float
            Faintest Gaia G magnitude wanted.
        is_whole_sky : bool
            Whether to skip the circle and search the whole sky.

        Returns
        -------
        rows : `list` [`tuple`] or `None`
            ``(source_id, ra, dec, phot_g_mean_mag, designation)`` for each
            star, brightest first, or `None` if the download failed.
        """
        try:
            from astroquery.gaia import Gaia
        except ImportError:
            logger.error("astroquery is not installed; cannot query GAIA catalog.")
            return None

        # A "whole sky" request can't be expressed as an ADQL CIRCLE — the
        # widest circle ADQL accepts only covers one hemisphere — so it is
        # expressed as a magnitude-only query with no spatial filter instead.
        if is_whole_sky:
            spatial_filter = ""
        else:
            spatial_filter = (
                f"CONTAINS("
                f"  POINT('ICRS', ra, dec), "
                f"  CIRCLE('ICRS', {ra_degrees}, {dec_degrees}, {radius_degrees})"
                f")=1 "
                f"AND "
            )

        adql_query = (
            f"SELECT TOP {_GAIA_ROW_LIMIT} "
            f"source_id, ra, dec, phot_g_mean_mag "
            f"FROM gaiadr3.gaia_source "
            f"WHERE {spatial_filter}phot_g_mean_mag < {magnitude_limit} "
            f"ORDER BY phot_g_mean_mag ASC"
        )

        def _run_query() -> Any:
            # Asynchronous, because a synchronous Gaia query is limited to
            # 2000 rows and this asks for up to _GAIA_ROW_LIMIT.
            job = Gaia.launch_job_async(adql_query, dump_to_file=False, verbose=False)
            return job.get_results()

        try:
            table = _run_with_timeout(_run_query, _GAIA_QUERY_TIMEOUT_SECONDS)
        except Exception as query_error:
            logger.warning("GAIA DR3 catalog query failed or timed out: %s", query_error)
            return None

        # No table at all is a failed download. Only a real, empty table means
        # "no stars here", which is safe to save as such.
        if table is None:
            logger.warning("GAIA DR3 catalog query returned no result table.")
            return None

        rows: list[tuple[str, float, float, float | None, str]] = []
        if len(table) == 0:
            return rows

        for table_row in table:
            try:
                source_id = str(table_row["source_id"])
                magnitude_g = None
                try:
                    raw_magnitude = table_row["phot_g_mean_mag"]
                    if not getattr(raw_magnitude, "mask", False):
                        magnitude_g = float(raw_magnitude)
                except ValueError, TypeError:
                    pass
                rows.append((
                    source_id,
                    float(table_row["ra"]),
                    float(table_row["dec"]),
                    magnitude_g,
                    f"Gaia DR3 {source_id}",
                ))
            except Exception as row_error:
                logger.debug("Skipping GAIA row: %s", row_error)
        return rows
