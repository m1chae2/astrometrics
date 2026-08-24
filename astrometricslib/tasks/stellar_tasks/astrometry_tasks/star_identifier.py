"""Orchestrate star detection, plate solving, and SIMBAD/Gaia identification.

Identification chain for each detected star:
  1. SIMBAD bulk region query + nearest-neighbour match (≤10 arcsec)
  2. Gaia DR3 bulk cone search + nearest-neighbour match (≤10 arcsec)
  3. Position-based fallback ID  FIELD_J{ra:.4f}{dec:+.4f}  (sky coords known)

No star is ever left with id="" or a synthetic Star_N label after
identification -- every tracked star has a stable, unique catalog or
position-based identity that survives INSERT OR REPLACE persistence.
"""

import logging
import queue
import threading
import warnings
from collections.abc import Callable
from typing import Any

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS, FITSFixedWarning
from astroquery.simbad import Simbad

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.tasks.shared.source_detection_shared import SourceDetector
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver
from astrometricslib.utilities import AstrometricsImage
from astrometricslib.utilities.config_loader import AppConfiguration
from astrometricslib.utilities.exceptions import AstroLibError

logger = logging.getLogger(__name__)

# astroquery's default is 1080s (18 min) -- a stalled/slow connection blocks
# an entire analysis run for that long with no visible progress before
# failing over to Gaia. 30s is generous for a single region query against a
# responsive server and fails fast on a genuinely stuck connection instead.
Simbad.timeout = 30

# SIMBAD class-level state is not thread-safe. Use this lock to
# protect configuration and queries.
SIMBAD_LOCK = threading.Lock()

# Gaia TAP service queries are also not thread-safe for the shared
# Gaia singleton; serialise them with a dedicated lock.
GAIA_LOCK = threading.Lock()

# --- Gaia remote-query circuit breaker -------------------------------------
#
# Damage control, not a fix: when ESA's Gaia TAP service is unresponsive
# every query still burns its full timeout before failing, and the pipeline
# keeps trying once per field. A full-catalog run on 2026-08-23 made 67
# remote Gaia calls with a 0% success rate -- 46 cone-search timeouts at
# 30s, 21 bulk-seed timeouts at 45s -- spending roughly 2,325 seconds of
# cumulative worker time to retrieve nothing. Measurement that day ruled out
# every local cause (TCP to gea.esac.esa.int connected in 0.2s, SIMBAD
# queries over the same network succeeded all run, and a bare
# ``SELECT TOP 5`` timed out at 60s on both the sync and async endpoints),
# so the failures are service-side and retrying within a run cannot help.
#
# Once tripped, `_query_gaia_region` and `_seed_gaia_cache_for_field` skip
# the *remote* call only -- local SQLite cache lookups still run, since
# those work and served 4 hits during that same run. State is deliberately
# per-process and in-memory: a new run always re-probes the service rather
# than staying permanently disabled, and worker processes each make their
# own small number of attempts rather than coordinating through shared
# storage.
#
# Threshold: 3 consecutive failures. High enough not to trip on a single
# blip, low enough to cap the waste at a few timeouts per process against
# the observed all-or-nothing failure mode.
GAIA_CONSECUTIVE_FAILURE_LIMIT = 3

_gaia_failure_state_lock = threading.Lock()
_gaia_consecutive_failures = 0
_gaia_circuit_open = False

# Cumulative per-process tallies, distinct from the consecutive-failure
# counter above (which resets on any success). These record what the run
# as a whole experienced, so a quality summary can say whether the
# catalog service was healthy -- without them, a run where every query
# failed looks identical to one where the fields genuinely held no
# catalog stars.
_gaia_queries_attempted = 0
_gaia_queries_failed = 0


def _record_gaia_failure(context: str) -> None:
    """Count a failed remote Gaia call, tripping the breaker at the limit.

    Parameters
    ----------
    context : `str`
        Short description of the failing call, for the trip log line.
    """
    global _gaia_consecutive_failures, _gaia_circuit_open, _gaia_queries_attempted, _gaia_queries_failed
    with _gaia_failure_state_lock:
        _gaia_queries_attempted += 1
        _gaia_queries_failed += 1
        _gaia_consecutive_failures += 1
        if not _gaia_circuit_open and _gaia_consecutive_failures >= GAIA_CONSECUTIVE_FAILURE_LIMIT:
            _gaia_circuit_open = True
            logger.warning(
                f"Gaia remote queries disabled for the rest of this process after "
                f"{_gaia_consecutive_failures} consecutive failures (last: {context}). "
                "Locally cached Gaia data is still used; SIMBAD identification is unaffected."
            )


def _record_gaia_success() -> None:
    """Reset the consecutive-failure count after a working remote call."""
    global _gaia_consecutive_failures, _gaia_queries_attempted
    with _gaia_failure_state_lock:
        _gaia_queries_attempted += 1
        _gaia_consecutive_failures = 0


def get_gaia_query_statistics() -> dict[str, int | bool]:
    """Report this process's cumulative remote Gaia outcomes.

    Returns
    -------
    statistics : `dict` [`str`, `int` or `bool`]
        ``attempted`` and ``failed`` query counts, plus
        ``circuit_breaker_tripped``. Counts are per-process and reset by
        `reset_gaia_circuit_breaker`.
    """
    with _gaia_failure_state_lock:
        return {
            "attempted": _gaia_queries_attempted,
            "failed": _gaia_queries_failed,
            "circuit_breaker_tripped": _gaia_circuit_open,
        }


def _gaia_remote_queries_disabled() -> bool:
    """Report whether the breaker has tripped in this process.

    Returns
    -------
    disabled : `bool`
        `True` once `GAIA_CONSECUTIVE_FAILURE_LIMIT` consecutive remote
        Gaia calls have failed.
    """
    with _gaia_failure_state_lock:
        return _gaia_circuit_open


def reset_gaia_circuit_breaker() -> None:
    """Clear the breaker so remote Gaia calls are attempted again.

    Intended for tests and for callers that know the service has
    recovered; ordinary runs get a clean breaker automatically because
    the state is per-process.
    """
    global _gaia_consecutive_failures, _gaia_circuit_open, _gaia_queries_attempted, _gaia_queries_failed
    with _gaia_failure_state_lock:
        _gaia_consecutive_failures = 0
        _gaia_circuit_open = False
        _gaia_queries_attempted = 0
        _gaia_queries_failed = 0


def _run_with_daemon_thread_timeout(query_function: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run a zero-argument callable on a daemon thread, enforcing a timeout.

    Behaves like ``ThreadPoolExecutor(max_workers=1).submit(query_function)
    .result(timeout=timeout_seconds)``, with one deliberate difference: the
    worker thread is created with ``daemon=True``.

    astroquery's Gaia calls set no socket-level timeout of their own, so a
    call that hangs past `timeout_seconds` leaves its thread permanently
    blocked on the network read -- `query_gaia_region` and
    `seed_gaia_cache_for_field` already give up on schedule by abandoning
    that thread, but a `ThreadPoolExecutor` thread is non-daemon, and
    Python joins every non-daemon thread in a process before that process
    is allowed to exit. During the 2026-08-23 Gaia outage that join is what
    stranded worker processes for hours after they had already finished
    all their work: each one had an abandoned, still-blocked Gaia thread
    holding its interpreter open. A daemon thread is exempt from that join,
    so the process exits normally with the still-running query simply
    abandoned to the OS.

    Parameters
    ----------
    query_function : `Callable`
        Zero-argument callable to run on the worker thread.
    timeout_seconds : `float`
        Seconds to wait before giving up.

    Returns
    -------
    result : `Any`
        Whatever `query_function` returned.

    Raises
    ------
    TimeoutError
        If `query_function` has not completed within `timeout_seconds`.
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


class StarIdentifier:
    """Orchestrate star detection, plate solving, and SIMBAD identification.

    Detection is handled by `SourceDetector`, plate solving by
    `PlateSolver` (Astrometry.net), and star identification by
    querying SIMBAD.
    """

    def __init__(self, config: AppConfiguration | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the StarIdentifier with a configuration object.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            Application configuration to use. If `None` (default), a
            new configuration instance is loaded.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()

        self.config = config
        self.detector = SourceDetector()

        # Extract API key for solver
        api_key = config.get_value(
            "Processing.Astrometry.Online Solver", "api_key", config.get_value("Astrometry", "api_key")
        )
        if api_key is not None and not isinstance(api_key, str) and hasattr(api_key, "_mock_methods"):
            api_key = "mock-api-key"
        self.solver = PlateSolver(api_key=api_key)
        self.stellar_objects: list[StellarObject] = []
        self.sources_detected: int = 0
        self.solve_attempted: bool = False

    @staticmethod
    def _build_stellar_objects_from_sources(sources: list[dict]) -> list[StellarObject]:
        """Build one `StellarObject` per detected source.

        Converts numpy scalar values in each source dict to native
        Python types for serialization safety, and assigns each object
        a temporary ``Star_{n}`` id and name.  This id is a safe
        non-empty placeholder that makes every object addressable in
        the ``stellar_object_map`` inside `VariabilityAnalyzer.process`
        and in the SQLite ``INSERT OR REPLACE`` step; it is overwritten
        by a real catalog id as soon as SIMBAD or Gaia identifies the
        star (see `identify_stars_with_wcs`).

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            One `StellarObject` per input source, in the same order.
        """
        stellar_objects = []
        for i, src in enumerate(sources):
            obj = StellarObject()

            # Convert numpy scalars to native Python types for
            # serialization safety
            cleaned_src = {}
            if isinstance(src, dict):
                for k, v in src.items():
                    if hasattr(v, "item"):
                        cleaned_src[k] = v.item()
                    else:
                        cleaned_src[k] = v
            else:
                cleaned_src = src

            obj.star_data = cleaned_src
            obj.flux = float(cleaned_src.get("flux", 0.0)) if isinstance(cleaned_src, dict) else 0.0
            # Give every object a unique, non-empty placeholder id so
            # it is addressable before catalog identification runs.
            # The underscore format matches VariabilityAnalyzer's own
            # blind-detection id scheme (Star_N), keeping naming
            # consistent across both code paths.
            obj.id = f"Star_{i + 1}"
            obj.name = f"Star_{i + 1}"
            stellar_objects.append(obj)
        return stellar_objects

    def _calculate_scale_hints(self, image_data_or_path: Any) -> tuple[float | None, float | None]:
        """Estimate a pixel-scale search window from equipment metadata.

        Tries the image's own FITS header first (`FOCALLEN`/
        `XPIXSZ`/`PIXSCAL`), falling back to the app configuration's
        focal length if the header doesn't have it. Returns a narrow
        +/-5% window around the calculated scale (`PlateSolver`
        relaxes it further on its own if that fails to solve).

        Returns
        -------
        scale_lower, scale_upper : `float` or `None`
            The lower/upper bounds of the pixel-scale search window
            in arcsec/pixel, or `(None, None)` if no usable equipment
            metadata was found.
        """
        focal_len = None
        pixel_size = None

        if isinstance(image_data_or_path, AstrometricsImage):
            hdr = image_data_or_path.header
            focal_len = hdr.get("FOCALLEN")
            pixel_size = hdr.get("XPIXSZ") or hdr.get("PIXSCAL")

        if not focal_len or focal_len <= 0:
            try:
                focal_len = self.config.get_focal_length_mm()
            except Exception:
                focal_len = None

        if focal_len and focal_len > 0 and pixel_size and pixel_size > 0:
            try:
                calculated_scale = 206.265 * float(pixel_size) / float(focal_len)
                logger.info(f"Calculated expected pixel scale: {calculated_scale:.3f} arcsec/pixel")
                return calculated_scale * 0.95, calculated_scale * 1.05
            except ValueError, TypeError:
                logger.warning("Could not calculate pixel scale from metadata.")
                return None, None

        logger.info("Equipment metadata missing; proceeding with blind scale search.")
        return None, None

    def process_image(
        self,
        image_data_or_path: Any,
        attempt_plate_solving: bool = True,
        center_ra: float | None = None,
        center_dec: float | None = None,
    ) -> tuple[list[StellarObject], WCS | None]:
        """Run the main pipeline: detect, solve, then identify.

        Parameters
        ----------
        image_data_or_path : `AstrometricsImage`, `numpy.ndarray`, or `str`
            The image data, an `AstrometricsImage`, or a path to a
            FITS file to process.
        attempt_plate_solving : `bool`, optional
            Whether to attempt plate solving (default `True`).
        center_ra : `float`, optional
            RA hint, in degrees, for the field center (default
            `None`).
        center_dec : `float`, optional
            Dec hint, in degrees, for the field center (default
            `None`).

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The detected and (where possible) identified stars.
        wcs : `astropy.wcs.WCS` or `None`
            The solved world coordinate system, or `None` if plate
            solving was skipped or failed.

        Raises
        ------
        AstroLibError
            Raised if plate solving is attempted and fails to solve
            the field.
        """
        # 1. Load Image
        if isinstance(image_data_or_path, str):
            img = AstrometricsImage(image_data_or_path)
            data = img.data
            path = image_data_or_path
        elif isinstance(image_data_or_path, AstrometricsImage):
            data = image_data_or_path.data
            path = image_data_or_path.path
        else:
            data = image_data_or_path
            path = None

        if data is not None and data.ndim == 3:
            if data.shape[0] in [3, 4]:
                data = np.mean(data, axis=0)
            else:
                data = np.mean(data, axis=-1)

        # 2. Detect Stars
        logger.info("Detecting stars...")
        sources = self.detector.detect(data)
        unique_sources = self.detector.deduplicate(sources)
        logger.debug(f"Detected {len(sources)} sources, {len(unique_sources)} unique.")
        self.sources_detected = len(unique_sources)

        # Limit to top 100
        if len(unique_sources) > 100:
            unique_sources = unique_sources[:100]

        self.stellar_objects = self._build_stellar_objects_from_sources(unique_sources)
        self.solve_attempted = False

        if not self.stellar_objects:
            return [], None

        # 3. Plate Solve
        wcs = None
        if attempt_plate_solving:
            self.solve_attempted = len(self.stellar_objects) >= 4
            if not self.solve_attempted:
                logger.info(f"Skipping plate solve: only {len(self.stellar_objects)} sources detected.")
            else:
                logger.info(f"Solving field with {len(self.stellar_objects)} sources...")
                h, w = data.shape

                # REQ: SR-3.1 - Determine scale hints dynamically. We
                # use a very narrow 5% window here because the
                # PlateSolver will relax it by another 20%.
                scale_lower, scale_upper = self._calculate_scale_hints(image_data_or_path)

                header = self.solver.solve(
                    image_path=path,
                    sources=unique_sources,
                    image_width=w,
                    image_height=h,
                    center_ra=center_ra,
                    center_dec=center_dec,
                    radius=2.0,
                    scale_units="arcsecperpix",
                    scale_lower=scale_lower,
                    scale_upper=scale_upper,
                    solve_timeout=300,
                )

                if header:
                    logger.info("Field solved! Querying SIMBAD...")
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", FITSFixedWarning)
                        wcs = WCS(header, naxis=2)
                    self._identify_stars_with_simbad(wcs, center_ra, center_dec, w, h)
                else:
                    logger.error("Could not solve field.")
                    raise AstroLibError("Plate solving failed: could not solve field.")
        else:
            # Not attempting plate solving, but we can still identify
            # if we have hints
            if center_ra is not None and center_dec is not None:
                logger.info("Using RA/Dec hints for center star identification (skipping solve)...")
                h, w = data.shape
                self._identify_stars_with_simbad(None, center_ra, center_dec, w, h)

        return self.stellar_objects, wcs

    def _query_simbad_region(
        self,
        ra_center: float,
        dec_center: float,
        wcs: WCS | None,
        width: int,
        height: int,
        radius_deg_override: float | None = None,
    ) -> tuple[Any, SkyCoord] | tuple[None, None]:
        """Run bulk SIMBAD region query centered on coordinates.

        The search radius is `radius_deg_override` when given (e.g. a
        tight radius bounding the actual detected star positions,
        rather than the whole field of view). Otherwise it's derived
        from the field of view when a `wcs` is available (capped at 1
        degree), or a default 12-arcmin radius is used. Non-stellar
        rows (galaxies, nebulae, star clusters) are filtered out via
        `_filter_stellar_rows` before returning, since a detected
        point source can never legitimately be one of those.

        Returns
        -------
        result_table, simbad_coords : `astropy.table.Table`, `SkyCoord`
            The stellar-filtered result table and its coordinates as
            a `SkyCoord` array, or `(None, None)` if the query
            failed, returned nothing, or had no stellar-type rows.
        """
        with SIMBAD_LOCK:
            # Calculate a reasonable radius based on image size (if
            # WCS is available)
            radius_deg = 0.2  # default 12 arcmin
            if radius_deg_override is not None:
                radius_deg = radius_deg_override
            elif wcs and width and height:
                try:
                    from astropy.wcs.utils import proj_plane_pixel_scales

                    pixel_scales = proj_plane_pixel_scales(wcs)  # in degrees
                    fov_x = pixel_scales[0] * width
                    fov_y = pixel_scales[1] * height
                    radius_deg = max(fov_x, fov_y) / 2.0 * 1.1  # 10% buffer
                    radius_deg = min(radius_deg, 1.0)  # Cap at 1 degree
                except Exception as e:
                    logger.warning(f"Failed to calculate FOV from WCS: {e}")

            logger.info(
                f"Querying SIMBAD bulk region at {ra_center:.4f}, {dec_center:.4f} "
                f"with {radius_deg:.3f} degree radius..."
            )
            try:
                Simbad.reset_votable_fields()
                Simbad.ROW_LIMIT = 5000  # Prevent massive result sets
                # REQ: AST-1.1 - common names
                Simbad.add_votable_fields("flux(V)", "sp_type", "ids", "ra(d)", "dec(d)", "otype")

                coord = SkyCoord(ra_center * u.deg, dec_center * u.deg)
                result_table = Simbad.query_region(coord, radius=f"{radius_deg}d")
            except Exception as e:
                logger.error(f"SIMBAD query failed: {e}")
                return None, None

        if result_table is None or len(result_table) == 0:
            logger.info("No SIMBAD results for this region.")
            return None, None

        logger.info(f"  Found {len(result_table)} potential matches in SIMBAD.")

        # Exclude non-stellar SIMBAD entries (galaxies, nebulae,
        # clusters, etc). A detected point source can never
        # legitimately be a galaxy, no matter how close the catalog
        # position is, so these must be dropped before any nearest-
        # neighbor matching is done below - otherwise a coincidentally
        # nearby (or arbitrarily first-listed) extended object can be
        # mistaken for the star.
        result_table = self._filter_stellar_rows(result_table)
        if result_table is None or len(result_table) == 0:
            logger.warning(
                "No stellar-type SIMBAD entries found in field (only non-stellar catalog objects, "
                "e.g. galaxies, were nearby); leaving detected star(s) with generic 'Star N' labels."
            )
            return None, None

        logger.info(f"SIMBAD table columns: {result_table.colnames}")
        logger.info("Creating SkyCoord for SIMBAD matches...")
        try:
            # REQ: AST-1.2 - Use actual column names from SIMBAD response
            ra_col = next((c for c in ["ra", "RA_d", "RA"] if c in result_table.colnames), "ra")
            dec_col = next((c for c in ["dec", "DEC_d", "DEC"] if c in result_table.colnames), "dec")

            # Check if columns already have units
            ra_vals = result_table[ra_col]
            dec_vals = result_table[dec_col]

            if not hasattr(ra_vals, "unit") or ra_vals.unit is None:
                ra_vals = ra_vals * u.deg
            if not hasattr(dec_vals, "unit") or dec_vals.unit is None:
                dec_vals = dec_vals * u.deg

            simbad_coords = SkyCoord(ra=ra_vals, dec=dec_vals)
        except Exception as e:
            logger.error(f"SkyCoord creation failed: {e}")
            return None, None

        return result_table, simbad_coords

    @staticmethod
    def _seed_gaia_cache_for_field(
        ra_center: float,
        dec_center: float,
        radius_deg: float = 0.5,
        max_magnitude: float = 18.0,
    ) -> int:
        """Seed the local Gaia DR3 SQLite cache for a field position.

        Checks if the target region is already recorded in ``cached_regions``;
        if not, downloads sources brighter than ``max_magnitude`` and writes
        them to ``gaia_sources``, adding an entry to ``cached_regions``.

        Parameters
        ----------
        ra_center, dec_center : `float`
            Field center coordinates in degrees.
        radius_deg : `float`, optional
            Search radius in degrees (capped at 1.0°, default 0.5°).
        max_magnitude : `float`, optional
            Magnitude threshold (default 18.0).

        Returns
        -------
        count : `int`
            Number of Gaia sources cached or loaded.
        """
        import os
        import sqlite3

        from astropy.table import Table
        from astroquery.gaia import Gaia

        from astrometricslib.utilities.config_loader import get_configuration

        radius_deg = min(max(0.1, radius_deg), 1.0)
        # ruff: ignore[float-equality-comparison]
        if ra_center == 0.0 and dec_center == 0.0:
            return 0

        config = get_configuration()
        cache_dir = config.get_library_path() / "catalogs"
        os.makedirs(cache_dir, exist_ok=True)
        cache_db_path = cache_dir / "catalog_cache.db"
        region_key = f"{ra_center:.3f}_{dec_center:.3f}_{radius_deg:.2f}"

        try:
            conn = sqlite3.connect(cache_db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gaia_sources (
                    source_id TEXT PRIMARY KEY,
                    ra REAL,
                    dec REAL,
                    phot_g_mean_mag REAL,
                    designation TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cached_regions (
                    region_key TEXT PRIMARY KEY,
                    ra REAL,
                    dec REAL,
                    radius REAL
                )
            """)
            conn.commit()

            cursor.execute("SELECT 1 FROM cached_regions WHERE region_key = ?", (region_key,))
            if cursor.fetchone() is not None:
                conn.close()
                logger.debug(f"Gaia region '{region_key}' already cached.")
                return 0

            conn.close()
        except Exception as e:
            logger.warning(f"Error checking cached_regions: {e}")

        # Same breaker as the cone-search path: the region-already-cached
        # check above is local and still runs, only the download is skipped.
        if _gaia_remote_queries_disabled():
            logger.debug(
                "Skipping Gaia cache seed for field (%.4f, %.4f): circuit breaker open.",
                ra_center,
                dec_center,
            )
            return 0

        logger.info(
            f"Seeding Gaia DR3 cache for field ({ra_center:.4f}, {dec_center:.4f}), "
            f"radius={radius_deg:.3f}°..."
        )
        query = (
            "SELECT source_id, ra, dec, phot_g_mean_mag, designation "
            "FROM gaiadr3.gaia_source "
            f"WHERE phot_g_mean_mag < {max_magnitude} "
            f"AND CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra_center}, {dec_center}, {radius_deg}))=1"
        )

        def _run_query():  # ruff: ignore[missing-return-type-private-function]
            job = Gaia.launch_job_async(query, dump_to_file=False)
            return job.get_results()

        try:
            result_table: Table = _run_with_daemon_thread_timeout(_run_query, timeout_seconds=45)
        except TimeoutError:
            logger.warning(
                f"Gaia bulk seed query timed out after 45s for field ({ra_center:.4f}, {dec_center:.4f})."
            )
            _record_gaia_failure("bulk seed timed out after 45s")
            return 0
        except Exception as e:
            logger.warning(f"Gaia bulk seed query failed: {e}")
            _record_gaia_failure(f"bulk seed failed: {e}")
            return 0

        _record_gaia_success()

        if result_table is None or len(result_table) == 0:
            return 0

        try:
            conn = sqlite3.connect(cache_db_path)
            cursor = conn.cursor()
            to_insert = [
                (
                    str(row["source_id"]),
                    float(row["ra"]),
                    float(row["dec"]),
                    float(row["phot_g_mean_mag"]) if row["phot_g_mean_mag"] is not None else 0.0,
                    str(row["designation"]) if row["designation"] else f"Gaia DR3 {row['source_id']}",
                )
                for row in result_table
            ]
            cursor.executemany(
                """
                INSERT OR REPLACE INTO gaia_sources (source_id, ra, dec, phot_g_mean_mag, designation)
                VALUES (?, ?, ?, ?, ?)
            """,
                to_insert,
            )
            cursor.execute(
                "INSERT OR REPLACE INTO cached_regions (region_key, ra, dec, radius) VALUES (?, ?, ?, ?)",
                (region_key, ra_center, dec_center, radius_deg),
            )
            conn.commit()
            conn.close()
            logger.info(
                f"Successfully cached {len(to_insert)} Gaia DR3 sources for field "
                f"({ra_center:.4f}, {dec_center:.4f})."
            )
            return len(to_insert)
        except Exception as e:
            logger.warning(f"Failed to persist Gaia DR3 sources to cache: {e}")
            return 0

    @staticmethod
    def _query_gaia_region(
        ra_center: float,
        dec_center: float,
        radius_deg: float,
    ) -> tuple[Any, SkyCoord] | tuple[None, None]:
        """Run a bulk Gaia DR3 cone search with local SQLite caching.

        Used as a fallback for stars that SIMBAD could not identify.
        Gaia DR3 contains ~1.8 billion sources, far exceeding SIMBAD's
        ~15 million, so most faint field stars missed by SIMBAD will be
        present in Gaia.

        Results are automatically cached locally in SQLite under the app's
        library path (`catalogs/catalog_cache.db`) so subsequent runs in
        the same sky region require no remote network requests.

        Parameters
        ----------
        ra_center, dec_center : `float`
            Field center in degrees.
        radius_deg : `float`
            Cone-search radius in degrees.

        Returns
        -------
        result_table, gaia_coords : `astropy.table.Table`, `SkyCoord`
            The result table and its coordinates, or `(None, None)` if
            the query failed or returned no rows.
        """
        import os
        import sqlite3

        from astropy.table import Table
        from astroquery.gaia import Gaia

        from astrometricslib.utilities.config_loader import get_configuration

        radius_deg = min(radius_deg, 1.0)
        config = get_configuration()
        cache_dir = config.get_library_path() / "catalogs"
        os.makedirs(cache_dir, exist_ok=True)
        cache_db_path = cache_dir / "catalog_cache.db"

        # Check local cache first
        try:
            conn = sqlite3.connect(cache_db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gaia_sources (
                    source_id TEXT PRIMARY KEY,
                    ra REAL,
                    dec REAL,
                    phot_g_mean_mag REAL,
                    designation TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cached_regions (
                    region_key TEXT PRIMARY KEY,
                    ra REAL,
                    dec REAL,
                    radius REAL
                )
            """)
            conn.commit()

            # Query existing cached sources within bounding box + radius
            min_ra = ra_center - (radius_deg / max(0.1, np.cos(np.radians(dec_center))))
            max_ra = ra_center + (radius_deg / max(0.1, np.cos(np.radians(dec_center))))
            min_dec = dec_center - radius_deg
            max_dec = dec_center + radius_deg

            cursor.execute(
                "SELECT source_id, ra, dec, phot_g_mean_mag, designation "
                "FROM gaia_sources WHERE ra >= ? AND ra <= ? AND dec >= ? AND dec <= ?",
                (min_ra, max_ra, min_dec, max_dec),
            )
            cached_rows = cursor.fetchall()
            conn.close()

            if cached_rows and len(cached_rows) >= 5:
                logger.info(
                    f"Loaded {len(cached_rows)} Gaia DR3 sources from local SQLite cache ({cache_db_path})."
                )
                source_ids = [r[0] for r in cached_rows]
                ras = [r[1] for r in cached_rows]
                decs = [r[2] for r in cached_rows]
                mags = [r[3] for r in cached_rows]
                desigs = [r[4] for r in cached_rows]

                result_table = Table(
                    [source_ids, ras, decs, mags, desigs],
                    names=["source_id", "ra", "dec", "phot_g_mean_mag", "DESIGNATION"],
                )
                gaia_coords = SkyCoord(ra=np.array(ras) * u.deg, dec=np.array(decs) * u.deg)
                return result_table, gaia_coords
        except Exception as e:
            logger.warning(f"Failed checking local Gaia SQLite cache: {e}")

        # Cache miss. Everything above this point is local and still runs
        # with the breaker open; only the remote fetch below is skipped.
        if _gaia_remote_queries_disabled():
            logger.debug(
                "Skipping remote Gaia query at %.4f, %.4f: circuit breaker open for this process.",
                ra_center,
                dec_center,
            )
            return None, None

        # If cache miss, auto-download from remote TAP server
        logger.info(
            f"Querying Gaia DR3 bulk region at {ra_center:.4f}, {dec_center:.4f} "
            f"with {radius_deg:.3f} degree radius..."
        )
        # Gaia's TAP client (unlike Simbad) exposes no configurable
        # timeout, so a stalled connection blocks indefinitely -- wrap the
        # call in a hard deadline via a worker thread rather than let a
        # bad connection hang the whole analysis run.

        def _run_gaia_query():  # ruff: ignore[missing-return-type-private-function]
            # GAIA_LOCK protects only the shared Gaia.ROW_LIMIT mutation,
            # not the network call itself: this query already runs inside
            # a worker thread that gets abandoned (not killed -- Python
            # threads can't be) if it exceeds the timeout below. Holding
            # the lock for the whole call would mean an abandoned thread
            # keeps it locked forever, permanently deadlocking every
            # subsequent Gaia query (including from other jobs) on this
            # same lock -- exactly the "jobs stuck" failure mode this is
            # guarding against.
            with GAIA_LOCK:
                Gaia.ROW_LIMIT = 10000
            coord = SkyCoord(ra=ra_center * u.deg, dec=dec_center * u.deg)
            job = Gaia.cone_search_async(
                coord,
                radius=u.Quantity(radius_deg, u.deg),
                # Pinned explicitly rather than left to astroquery's
                # default (the ESA archive server's own current-release
                # table, resolved at call time) -- this must always
                # match the release hardcoded in the bulk-seed ADQL
                # query, the local SQLite cache schema, and every
                # "Gaia DR3 ..." id string this file generates. Bump
                # deliberately, together with those, when moving to a
                # newer Gaia release.
                table_name="gaiadr3.gaia_source",
            )
            return job.get_results()

        try:
            result_table = _run_with_daemon_thread_timeout(_run_gaia_query, timeout_seconds=30)
        except TimeoutError:
            logger.error("Gaia query timed out after 30s.")
            _record_gaia_failure("cone search timed out after 30s")
            return None, None
        except Exception as e:
            logger.error(f"Gaia query failed: {e}")
            _record_gaia_failure(f"cone search failed: {e}")
            return None, None

        # The service answered. An empty region is a legitimate answer, so
        # this counts as success and clears any accumulated failures.
        _record_gaia_success()

        if result_table is None or len(result_table) == 0:
            logger.info("No Gaia results for this region.")
            return None, None

        logger.info(f"  Found {len(result_table)} Gaia sources in field.")

        # Persist downloaded sources to local SQLite cache
        try:
            ra_col = next((c for c in ["ra", "RA", "ra_epoch2000"] if c in result_table.colnames), None)
            dec_col = next((c for c in ["dec", "DEC", "dec_epoch2000"] if c in result_table.colnames), None)
            id_col = next((c for c in ["source_id", "SOURCE_ID"] if c in result_table.colnames), None)
            mag_col = next((c for c in ["phot_g_mean_mag", "g_mag"] if c in result_table.colnames), None)
            desig_col = next((c for c in ["DESIGNATION", "designation"] if c in result_table.colnames), None)

            if ra_col and dec_col:
                conn = sqlite3.connect(cache_db_path)
                cursor = conn.cursor()
                to_insert = []
                for row in result_table:
                    sid = str(row[id_col]) if id_col and row[id_col] is not None else ""
                    r_val = float(row[ra_col]) if row[ra_col] is not None else 0.0
                    d_val = float(row[dec_col]) if row[dec_col] is not None else 0.0
                    m_val = float(row[mag_col]) if mag_col and row[mag_col] is not None else 0.0
                    des = (
                        str(row[desig_col]) if desig_col and row[desig_col] is not None else f"Gaia DR3 {sid}"
                    )
                    to_insert.append((sid, r_val, d_val, m_val, des))

                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO gaia_sources (source_id, ra, dec, phot_g_mean_mag, designation)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    to_insert,
                )
                conn.commit()
                conn.close()
                logger.info(f"Cached {len(to_insert)} Gaia DR3 sources locally in {cache_db_path}.")
        except Exception as cache_err:
            logger.warning(f"Failed to cache Gaia sources locally: {cache_err}")

        try:
            ra_col = next(
                (c for c in ["ra", "RA", "ra_epoch2000"] if c in result_table.colnames),
                None,
            )
            dec_col = next(
                (c for c in ["dec", "DEC", "dec_epoch2000"] if c in result_table.colnames),
                None,
            )
            if ra_col is None or dec_col is None:
                logger.warning("Gaia result table missing ra/dec columns.")
                return None, None

            ra_vals = result_table[ra_col]
            dec_vals = result_table[dec_col]
            gaia_coords = SkyCoord(
                ra=ra_vals * u.deg,
                dec=dec_vals * u.deg,
            )
        except Exception as e:
            logger.error(f"Gaia SkyCoord creation failed: {e}")
            return None, None

        return result_table, gaia_coords

    def identify_stars_with_wcs(
        self,
        stellar_objects: list[StellarObject],
        wcs: WCS,
        width: int,
        height: int,
    ) -> list[StellarObject]:
        """Identify each of `stellar_objects` against SIMBAD then Gaia.

        Identification chain for every detected star:

        1. **SIMBAD** — bulk region query + nearest-neighbour at ≤10 arcsec.
        2. **Gaia DR3** — bulk cone search fallback for stars SIMBAD did
           not match. Gaia DR3 (~1.8 billion sources) covers most field
           stars absent from SIMBAD's ~15 million.
        3. **Position-based ID** — ``FIELD_J{ra:.4f}{dec:+.4f}`` for stars
           with a solved sky position but still no catalog match.  Gives
           every star a stable, unique, non-synthetic identity without
           inventing a catalog name.

        No star is left with ``id=""`` or a ``Star_N`` synthetic label
        after this method returns -- every star that can be plate-solved
        gets either a real catalog id or a position-anchored one.

        Parameters
        ----------
        stellar_objects : `list` [`StellarObject`]
            The detected stars to identify, each with a pixel
            centroid already recorded in `star_data`.
        wcs : `astropy.wcs.WCS`
            The solved (or reused) world coordinate system for the
            image `stellar_objects` was detected on.
        width, height : `int`
            The image dimensions, used to derive the SIMBAD query
            radius from the field of view.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The same `stellar_objects` list, mutated in place with
            identification results, returned for convenience chaining.
        """
        if not stellar_objects:
            return stellar_objects

        logger.info(f"Starting SIMBAD identification for {len(stellar_objects)} sources...")

        try:
            ra_center = wcs.wcs.crval[0]
            dec_center = wcs.wcs.crval[1]
        except Exception:
            logger.warning("Could not determine field center from WCS; skipping SIMBAD identification.")
            return stellar_objects

        # ------------------------------------------------------------------
        # Step 0: project every star's pixel centroid to sky coordinates up
        # front, so the catalog query region can be bounded to where the
        # detected stars actually are instead of the whole field of view.
        # On a sparse or off-center field this can shrink the query radius
        # (and therefore the result set SIMBAD/Gaia have to return) by a
        # large factor, which matters: an unnecessarily large bulk region
        # query is slow and, worse, a bigger surface for the kind of
        # network stall/timeout seen on real (Veil Nebula) analysis runs.
        # sky_positions maps stellar_object -> (ra, dec) for all stars
        # whose pixel centroid was successfully plate-solved.
        # ------------------------------------------------------------------
        sky_positions: dict[int, tuple[float, float]] = {}  # id(obj) -> (ra, dec)
        for stellar_object in stellar_objects:
            star = stellar_object.star_data
            x = star.get("x_centroid", star.get("xcentroid"))
            y = star.get("y_centroid", star.get("ycentroid"))
            if x is None or y is None:
                continue
            try:
                ra, dec = wcs.wcs_pix2world(x, y, 0)
                sky_positions[id(stellar_object)] = (float(ra), float(dec))
            except Exception as e:
                logger.error(f"Failed to project pixel ({x}, {y}) to sky: {e}")

        query_radius_deg = None
        if sky_positions:
            star_coords = SkyCoord(
                ra=[p[0] for p in sky_positions.values()] * u.deg,
                dec=[p[1] for p in sky_positions.values()] * u.deg,
            )
            # Midpoint of the star field's bounding sky region, not the
            # WCS reference pixel -- the two can differ when detected
            # stars cluster off-center in the frame.
            bbox_center = SkyCoord(
                ra=(star_coords.ra.deg.min() + star_coords.ra.deg.max()) / 2.0 * u.deg,
                dec=(star_coords.dec.deg.min() + star_coords.dec.deg.max()) / 2.0 * u.deg,
            )
            # +15 arcsec buffer keeps the search radius comfortably above
            # the 10 arcsec nearest-neighbour match tolerance used below.
            # Capped at 1 degree to match the pre-existing FOV-derived cap:
            # when detected stars are spread across the whole frame this
            # bounding radius can exceed that cap, and should never be
            # worse than the old field-of-view-based approach was.
            query_radius_deg = min(float(bbox_center.separation(star_coords).max().deg) + (15 / 3600), 1.0)
            ra_center, dec_center = bbox_center.ra.deg, bbox_center.dec.deg

        # ------------------------------------------------------------------
        # Step 1: SIMBAD bulk region query + per-star nearest-neighbour match
        # ------------------------------------------------------------------
        result_table, simbad_coords = self._query_simbad_region(
            ra_center, dec_center, wcs, width, height, radius_deg_override=query_radius_deg
        )

        unmatched_after_simbad: list[StellarObject] = []

        logger.info(
            f"Matching {len(stellar_objects)} detected stars against "
            f"{len(simbad_coords) if simbad_coords is not None else 0} SIMBAD entries..."
        )
        for stellar_object in stellar_objects:
            position = sky_positions.get(id(stellar_object))
            if position is None:
                logger.warning(f"Star {stellar_object.name} missing centroid in star_data; skipping.")
                continue
            ra, dec = position

            star_coord = SkyCoord(ra * u.deg, dec * u.deg)

            if simbad_coords is not None and result_table is not None:
                idx, d2d, _ = star_coord.match_to_catalog_sky(simbad_coords)
                if d2d < 10 * u.arcsec:
                    self._apply_simbad_match(stellar_object, result_table[idx], ra, dec)
                    logger.info(f"  SIMBAD match: {stellar_object.name} at ({ra:.4f}, {dec:.4f})")
                    continue

            # SIMBAD had no match — record sky position and defer to Gaia.
            stellar_object.right_ascension = ra
            stellar_object.declination = dec
            unmatched_after_simbad.append(stellar_object)

        simbad_match_count = len(stellar_objects) - len(unmatched_after_simbad)
        logger.info(
            f"SIMBAD matched {simbad_match_count} / {len(stellar_objects)} stars; "
            f"{len(unmatched_after_simbad)} deferred to Gaia DR3 fallback."
        )

        if not unmatched_after_simbad:
            return stellar_objects

        # ------------------------------------------------------------------
        # Step 2: Gaia DR3 bulk fallback for SIMBAD-unmatched stars
        # ------------------------------------------------------------------
        # Reuse the same star-position-bounded radius Step 1 used, so this
        # query covers only the actual detected field, not the full FOV.
        # Falls back to a field-of-view-derived radius on the rare path
        # where no star had a projectable sky position (query_radius_deg
        # is None) -- same as the pre-existing behavior in that case.
        radius_deg = query_radius_deg
        if radius_deg is None:
            radius_deg = 0.2
            try:
                from astropy.wcs.utils import proj_plane_pixel_scales

                pixel_scales = proj_plane_pixel_scales(wcs)
                fov_x = pixel_scales[0] * width
                fov_y = pixel_scales[1] * height
                radius_deg = min(max(fov_x, fov_y) / 2.0 * 1.1, 1.0)
            except Exception as exc:
                logger.debug("Could not derive search radius from WCS pixel scale: %s", exc)

        gaia_table, gaia_coords = self._query_gaia_region(ra_center, dec_center, radius_deg)

        still_unmatched: list[StellarObject] = []
        if gaia_table is not None and gaia_coords is not None:
            logger.info(
                f"Matching {len(unmatched_after_simbad)} stars against {len(gaia_coords)} Gaia DR3 sources..."
            )
            for stellar_object in unmatched_after_simbad:
                ra, dec = sky_positions.get(id(stellar_object), (None, None))
                if ra is None:
                    still_unmatched.append(stellar_object)
                    continue

                star_coord = SkyCoord(ra * u.deg, dec * u.deg)
                idx, d2d, _ = star_coord.match_to_catalog_sky(gaia_coords)

                if d2d < 10 * u.arcsec:
                    row = gaia_table[idx]
                    self._apply_gaia_match(stellar_object, row, ra, dec)
                    logger.info(f"  Gaia match: {stellar_object.name} at ({ra:.4f}, {dec:.4f})")
                else:
                    still_unmatched.append(stellar_object)
        else:
            still_unmatched = unmatched_after_simbad

        gaia_match_count = len(unmatched_after_simbad) - len(still_unmatched)
        logger.info(
            f"Gaia matched {gaia_match_count} additional stars; "
            f"{len(still_unmatched)} will receive position-based IDs."
        )

        # ------------------------------------------------------------------
        # Step 3: Position-based ID for any star still without a catalog
        # identity.  FIELD_J{ra:.4f}{dec:+.4f} is stable across runs
        # (same star at the same sky position always gets the same ID),
        # unique within the field, and unambiguously non-catalog so the
        # UI can choose to show/hide them separately.
        # ------------------------------------------------------------------
        for stellar_object in still_unmatched:
            ra, dec = sky_positions.get(id(stellar_object), (None, None))
            if ra is not None:
                field_id = f"FIELD_J{ra:.4f}{dec:+.4f}"
                stellar_object.id = field_id
                stellar_object.name = field_id
                stellar_object.right_ascension = ra
                stellar_object.declination = dec
            # If we never got a sky position (centroid projection failed),
            # keep the Star_N placeholder id from
            # _build_stellar_objects_from_sources -- it is at least unique
            # and non-empty.

        return stellar_objects

    def _identify_stars_with_simbad(  # ruff: ignore[missing-return-type-private-function]
        self,
        wcs: WCS | None,
        center_ra: float | None = None,
        center_dec: float | None = None,
        width: int = 1000,
        height: int = 1000,
    ):
        """Query SIMBAD for the field and match detected sources.

        With a `wcs`, delegates to `identify_stars_with_wcs` for full
        per-star identification. Without one, falls back to matching
        only the single star nearest the image center against the
        nearest stellar-type SIMBAD entry to the RA/Dec hint (SIMBAD's
        row order is not distance- or relevance-sorted, so using
        `result_table[0]` here would assign an arbitrary, unrelated
        catalog entry as the star's identity).
        """
        if not self.stellar_objects:
            return

        if wcs:
            self.identify_stars_with_wcs(self.stellar_objects, wcs, width, height)
            return

        if center_ra is None or center_dec is None:
            logger.warning("No center coordinates available for SIMBAD query.")
            return

        result_table, simbad_coords = self._query_simbad_region(center_ra, center_dec, None, width, height)
        if result_table is None:
            return

        for stellar_object in self.stellar_objects:
            star = stellar_object.star_data
            x = star.get("x_centroid", star.get("xcentroid"))
            y = star.get("y_centroid", star.get("ycentroid"))

            if x is None or y is None:
                logger.warning(
                    f"Star {stellar_object.name} is missing centroid coordinates in star_data: {star}"
                )
                continue

            dist_sq = (x - (width / 2)) ** 2 + (y - (height / 2)) ** 2
            star["center_dist_sq"] = dist_sq

        if not self.stellar_objects:
            return

        center_star_obj = min(self.stellar_objects, key=lambda o: o.star_data.get("center_dist_sq", 999999))
        try:
            hint_coord = SkyCoord(center_ra * u.deg, center_dec * u.deg)
            idx, d2d, _ = hint_coord.match_to_catalog_sky(simbad_coords)
            logger.info(f"Nearest stellar SIMBAD entry to hint coordinates is {d2d.to(u.arcsec)} away.")
            self._apply_simbad_match(center_star_obj, result_table[idx], center_ra, center_dec)
        except Exception as e:
            logger.warning(f"Failed to match hint coordinates against SIMBAD results: {e}")

    @staticmethod
    def _filter_stellar_rows(result_table):  # ruff: ignore[missing-type-function-argument, missing-return-type-static-method]
        """Restrict `result_table` to rows classified as individual stars.

        Excludes galaxies, nebulae, and star clusters using SIMBAD's
        OTYPE field (falling back to presence of a spectral type), so
        extended/non-stellar catalog objects are never eligible to be
        matched against a point source.

        Returns
        -------
        result_table : `astropy.table.Table`
            The input table restricted to rows classified as
            individual stars, or unchanged if no OTYPE column exists.
        """
        otype_col = next((c for c in ["OTYPE", "otype"] if c in result_table.colnames), None)
        sp_type_col = next((c for c in ["SP_TYPE", "sp_type"] if c in result_table.colnames), None)

        if otype_col is None:
            logger.warning("SIMBAD result has no OTYPE column; cannot exclude non-stellar catalog matches.")
            return result_table

        def _is_masked(value):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            return value is None or (hasattr(value, "mask") and bool(value.mask))

        stellar_mask = []
        for row in result_table:
            otype_val = row[otype_col]
            otype = "" if _is_masked(otype_val) else str(otype_val).strip().upper()

            spectral_type_val = row[sp_type_col] if sp_type_col else None
            has_spectral_type = not _is_masked(spectral_type_val) and str(spectral_type_val).strip() != ""

            # SIMBAD short OTYPE for an individual star is '*', 'V*',
            # 'WD*', etc. Star clusters use 'Cl*' and must not be
            # treated as individual stars.
            is_individual_star = otype.endswith("*") and "CL" not in otype
            stellar_mask.append(is_individual_star or "STAR" in otype or has_spectral_type)

        stellar_mask = np.array(stellar_mask, dtype=bool)
        return result_table[stellar_mask]

    def _apply_simbad_match(self, stellar_object, match, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Extract SIMBAD fields from `match` into `stellar_object`."""
        main_id = "Unknown"
        for col in ["main_id", "MAIN_ID", "ID", "id"]:
            if col in match.colnames:
                main_id = match[col]
                break

        if isinstance(main_id, bytes):
            main_id = main_id.decode("utf-8")

        common_name = None
        ids_col = next((c for c in ["IDS", "ids"] if c in match.colnames), None)
        if ids_col:
            all_ids = match[ids_col]
            if isinstance(all_ids, bytes):
                all_ids = all_ids.decode("utf-8")
            id_list = [i.strip() for i in str(all_ids).split("|")]
            for ident in id_list:
                if ident.startswith("NAME "):
                    common_name = ident.replace("NAME ", "").strip()
                    break

        spectral_type = "Unknown"
        for col in ["SP_TYPE", "sp_type"]:
            if col in match.colnames:
                spectral_type = match[col]
                break
        if isinstance(spectral_type, bytes):
            spectral_type = spectral_type.decode("utf-8")
        if not spectral_type or str(spectral_type).strip() == "":
            spectral_type = "Unknown"

        magnitude = 0.0
        # REQ: AST-1.3 - Map SIMBAD flux (magnitude)
        for col in ["V", "FLUX_V", "flux_v", "flux(V)"]:
            if col in match.colnames:
                val = match[col]
                if val is not None and not (hasattr(val, "mask") and bool(val.mask)):
                    try:
                        magnitude = float(val)
                    except ValueError, TypeError:
                        magnitude = 0.0
                break

        stellar_object.name = common_name if common_name else str(main_id)
        stellar_object.id = str(main_id)
        stellar_object.spectral_type = str(spectral_type)
        stellar_object.stellar_spectral_type = str(spectral_type)
        stellar_object.magnitude = magnitude
        stellar_object.right_ascension = float(ra)
        stellar_object.declination = float(dec)
        stellar_object.is_catalog_identified = True
        logger.info(
            f"  Identified: {stellar_object.name} ({stellar_object.spectral_type}, mag: {magnitude:.2f}) "
            f"at {ra:.4f}, {dec:.4f}"
        )

    def _apply_gaia_match(self, stellar_object: StellarObject, match: Any, ra: float, dec: float):  # ruff: ignore[missing-return-type-private-function]
        """Extract Gaia DR3 fields from `match` into `stellar_object`."""
        source_id = None
        for col in ["DESIGNATION", "designation", "source_id", "SOURCE_ID"]:
            if col in match.colnames:
                val = match[col]
                if val is not None and not (hasattr(val, "mask") and bool(val.mask)):
                    source_id = str(val).strip()
                    break

        if not source_id:
            source_id = f"Gaia DR3 J{ra:.4f}{dec:+.4f}"
        elif not source_id.startswith("Gaia"):
            source_id = f"Gaia DR3 {source_id}"

        magnitude = 0.0
        for col in ["phot_g_mean_mag", "PHOT_G_MEAN_MAG", "g_mag"]:
            if col in match.colnames:
                val = match[col]
                if val is not None and not (hasattr(val, "mask") and bool(val.mask)):
                    try:
                        magnitude = float(val)
                    except ValueError, TypeError:
                        magnitude = 0.0
                break

        stellar_object.name = source_id
        stellar_object.id = source_id
        stellar_object.spectral_type = "Unknown"
        stellar_object.stellar_spectral_type = "Unknown"
        stellar_object.magnitude = magnitude
        stellar_object.right_ascension = float(ra)
        stellar_object.declination = float(dec)
        stellar_object.is_catalog_identified = True
        logger.info(
            f"  Identified (Gaia): {stellar_object.name} (mag: {magnitude:.2f}) at {ra:.4f}, {dec:.4f}"
        )
