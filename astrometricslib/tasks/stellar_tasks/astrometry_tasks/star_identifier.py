"""Detect stars, map them to the sky, and identify their names.

This follows a step-by-step process to give every detected star a permanent
name:
  1. Search the SIMBAD database (matching stars within 10 arcseconds).
  2. If SIMBAD fails, search the Gaia DR3 database (matching within 10
  arcseconds).
  3. If both fail, name the star based on its coordinates (e.g.,
  ``FIELD_J{ra:.4f}{dec:+.4f}``).

This makes sure every star gets a stable, consistent name before it is saved to
the database, replacing temporary labels so we don't save duplicates.
"""

import logging
import math
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

from astrometricslib.data_access.image_quality_metrics import measure_fwhm_from_data
from astrometricslib.data_access.image_type import collapse_to_2d
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.tasks.shared.source_detection_shared import SourceDetector
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver
from astrometricslib.utilities import AstrometricsImage
from astrometricslib.utilities.config_loader import AppConfiguration
from astrometricslib.utilities.exceptions import AstroLibError

logger = logging.getLogger(__name__)

# --- Preparing the Image for Star Detection ---------------------------------
#
# The star-finding algorithm assumes background noise is random for
# every pixel. Color images break this rule because the debayering
# process interpolates colors, linking neighboring pixels together.
# This tricks the algorithm into thinking the noise clumps are
# actually stars. Tests showed this causes thousands of false
# detections and makes it nearly impossible to match real stars to
# the catalog.
#
# To fix this, we shrink the image to half its size (2x2 averaging)
# before looking for stars. Tests prove this successfully breaks up
# the noise clumps without missing any real stars, dropping false
# detections significantly.
_COLOR_DETECTION_BIN_FACTOR = 2

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

# --- Gaia Connection Safety Switch ------------------------------------------
#
# This acts like a circuit breaker to protect the program if the Gaia database
# servers go offline. Normally, waiting for a dead server to time out over and
# over causes massive delays.
#
# If the server fails 3 times in a row, the breaker trips and we stop trying
# to connect for the rest of the run. We still check our local offline cache,
# but we skip the internet request to save time. Setting the limit to 3 allows
# for a few normal internet hiccups before giving up.
GAIA_CONSECUTIVE_FAILURE_LIMIT = 3

# The maximum number of stars to send to the plate solver (which figures out
# where the telescope is pointing). The solver only needs the brightest stars
# to work. Sending every faint star makes the math take much longer without
# helping the solve succeed. This limit only applies to solving the image;
# we still measure the brightness of every star later.
MAXIMUM_PLATE_SOLVE_SOURCES = 100

# The maximum allowed distance between two star positions to consider them
# the same physical star. This 10-arcsecond limit is used when matching our
# detected stars to the SIMBAD/Gaia catalogs, and when merging duplicates.
CATALOG_MATCH_RADIUS_ARCSEC = 10.0

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
    """Log a failure to connect to Gaia, possibly triggering the safety switch.

    Parameters
    ----------
    context : `str`
        What we were trying to do when it failed.
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
    """Reset the failure counter back to zero after a successful connection."""
    global _gaia_consecutive_failures, _gaia_queries_attempted
    with _gaia_failure_state_lock:
        _gaia_queries_attempted += 1
        _gaia_consecutive_failures = 0


def get_gaia_query_statistics() -> dict[str, int | bool]:
    """Check how reliable the Gaia connection has been so far.

    Returns
    -------
    statistics : `dict` [`str`, `int` or `bool`]
        How many times we tried to connect, how many times it failed,
        and whether the safety switch has flipped.
    """
    with _gaia_failure_state_lock:
        return {
            "attempted": _gaia_queries_attempted,
            "failed": _gaia_queries_failed,
            "circuit_breaker_tripped": _gaia_circuit_open,
        }


def _gaia_remote_queries_disabled() -> bool:
    """Check if the Gaia safety switch has tripped.

    Returns
    -------
    disabled : `bool`
        True if we've failed to connect too many times in a row.
    """
    with _gaia_failure_state_lock:
        return _gaia_circuit_open


def reset_gaia_circuit_breaker() -> None:
    """Reset the safety switch so we can try connecting to Gaia again."""
    global _gaia_consecutive_failures, _gaia_circuit_open, _gaia_queries_attempted, _gaia_queries_failed
    with _gaia_failure_state_lock:
        _gaia_consecutive_failures = 0
        _gaia_circuit_open = False
        _gaia_queries_attempted = 0
        _gaia_queries_failed = 0


def reset_gaia_query_statistics() -> None:
    """Reset the tracking stats back to zero for a new image.

    This does NOT reset the safety switch. If Gaia was offline for the
    last image, we assume it's still offline and don't bother trying again.
    """
    global _gaia_queries_attempted, _gaia_queries_failed
    with _gaia_failure_state_lock:
        _gaia_queries_attempted = 0
        _gaia_queries_failed = 0


def _run_with_daemon_thread_timeout(query_function: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run a background task and strictly enforce a time limit.

    Normally, if a network connection stalls forever, the program won't
    be allowed to close until that connection finishes. By running it this
    way, we can abandon a stuck connection and still shut down cleanly.

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


def _block_average(data: np.ndarray, factor: int) -> np.ndarray:
    """Shrink an image by averaging groups of pixels together.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image data.
    factor : `int`
        How many pixels to group together (e.g., 2 means 2x2 blocks).

    Returns
    -------
    binned : `numpy.ndarray`
        The shrunk image.
    """
    height, width = data.shape
    cropped_height = height - height % factor
    cropped_width = width - width % factor
    cropped = data[:cropped_height, :cropped_width]
    return cropped.reshape(cropped_height // factor, factor, cropped_width // factor, factor).mean(
        axis=(1, 3)
    )


def _rescale_source_centroids(sources: list[dict], factor: int) -> None:
    """Convert star locations found in a shrunk image back to normal size.

    Parameters
    ----------
    sources : `list` [`dict`]
        The list of stars found.
    factor : `int`
        The same shrink factor used in `_block_average`.
    """
    offset = (factor - 1) / 2.0
    for source in sources:
        for x_key, y_key in (("x_centroid", "y_centroid"), ("xcentroid", "ycentroid")):
            if x_key in source and source[x_key] is not None:
                source[x_key] = source[x_key] * factor + offset
            if y_key in source and source[y_key] is not None:
                source[y_key] = source[y_key] * factor + offset


class StarIdentifier:
    """The main tool for finding stars, mapping the image, and naming them."""

    def __init__(self, config: AppConfiguration | None = None):  # ruff: ignore[missing-return-type-special-method]
        """Set up the tools.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The system settings.
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
        # Angular separations between each catalog-matched star's solved
        # position and its catalog position, in arcsec. Their RMS is the
        # astrometric residual -- the direct measure of solution quality
        # that `AstrometryPipelineQualityMetrics` declared but nothing
        # ever produced, leaving it None on all 28 solved targets of the
        # 2026-08-24 run.
        self.catalog_match_separations_arcsec: list[float] = []

    @staticmethod
    def _build_stellar_objects_from_sources(sources: list[dict]) -> list[StellarObject]:
        """Create a `StellarObject` for each dot of light we found.

        We give every star a temporary name like "Star_1", "Star_2".
        Later, we'll try to replace these with real database names.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The list of star objects ready for identification.
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

    def get_astrometric_residual_rms_arcsec(self) -> float | None:
        """Check how accurate our map is.

        This measures the average distance between where our map says a
        star should be, and where the official database says it actually is.
        A small number (less than 1) is good.

        Returns
        -------
        residual_rms_arcsec : `float` or `None`
            The average error distance, or None if we didn't match any stars.
        """
        separations = self.catalog_match_separations_arcsec
        if not separations:
            return None
        return round(math.sqrt(sum(value**2 for value in separations) / len(separations)), 4)

    def detect_stars(self, data: np.ndarray, is_color_frame: bool = False) -> tuple[list[dict], list[dict]]:
        """Find all the dots of light in the image and remove duplicates.

        This uses different tricks depending on whether it's a color image
        or a black-and-white (monochrome) image.

        Parameters
        ----------
        data : `numpy.ndarray`
            The image to search.
        is_color_frame : `bool`, optional
            True if this started as a color image. We handle color images
            differently to avoid false detections from debayering noise.

        Returns
        -------
        sources : `list` [`dict`]
            Everything we found that looks like a star.
        unique_sources : `list` [`dict`]
            The same list, but with duplicates removed.
        """
        if is_color_frame and data is not None:
            binned = _block_average(data, _COLOR_DETECTION_BIN_FACTOR)
            sources = self.detector.detect(binned)
            unique_sources = self.detector.deduplicate(sources)
            _rescale_source_centroids(sources, _COLOR_DETECTION_BIN_FACTOR)
            _rescale_source_centroids(unique_sources, _COLOR_DETECTION_BIN_FACTOR)
            return sources, unique_sources

        try:
            measured_fwhm = measure_fwhm_from_data(data) if data is not None else None
        except Exception as fwhm_error:
            logger.debug("Could not measure detection FWHM, keeping the configured default: %s", fwhm_error)
            measured_fwhm = None
        if measured_fwhm is not None:
            logger.debug(f"Matching detection kernel to measured FWHM: {measured_fwhm:.2f}px")
            self.detector.fwhm = measured_fwhm

        sources = self.detector.detect(data)
        unique_sources = self.detector.deduplicate(sources)
        return sources, unique_sources

    def _calculate_scale_hints(self, image_data_or_path: Any) -> tuple[float | None, float | None]:
        """Guess how zoomed in the image is based on the telescope settings.

        This helps the math solver run much faster because it doesn't have
        to guess the zoom level.

        Returns
        -------
        scale_lower, scale_upper : `float` or `None`
            A rough guess of the zoom scale, or None if the settings are
            missing.
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
        maximum_identified_stars: int | None = None,
    ) -> tuple[list[StellarObject], WCS | None]:
        """Run the full process: find the stars, map the image, and name them.

        Parameters
        ----------
        image_data_or_path : `AstrometricsImage`, `numpy.ndarray`, or `str`
            The image we are analyzing.
        attempt_plate_solving : `bool`, optional
            If True, figure out exactly where the telescope was pointing.
        center_ra, center_dec : `float`, optional
            Hints about where the telescope was pointing.
        maximum_identified_stars : `int`, optional
            A cap on how many stars we look up in the database.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The final list of named stars.
        wcs : `astropy.wcs.WCS` or `None`
            The map data, or None if the mapping process failed.

        Raises
        ------
        AstroLibError
            If plate solving is attempted and the field cannot be solved.
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

        is_color_frame = data is not None and data.ndim == 3
        if is_color_frame:
            data = collapse_to_2d(data)

        # 2. Detect Stars
        logger.info("Detecting stars...")
        sources, unique_sources = self.detect_stars(data, is_color_frame=is_color_frame)
        logger.debug(f"Detected {len(sources)} sources, {len(unique_sources)} unique.")
        self.sources_detected = len(unique_sources)
        self.catalog_match_separations_arcsec = []

        # Only the plate solver gets a capped list. astrometry.net
        # matches quads built from the brightest sources, so a couple
        # hundred is ample and thousands of faint detections mostly add
        # solve time. Everything downstream wants the opposite: the
        # comparison ensemble, catalog cross-matching and variable-star
        # detection all get better with more stars, and capping the
        # shared list at 100 starved them on every deep target. On the
        # 2026-08-25 catalog run the median frame detected 4,235
        # sources and kept 100.
        #
        # detect() returns sources by descending flux and deduplicate()
        # preserves that order, so this is the brightest N rather than
        # an arbitrary slice.
        solver_sources = unique_sources[:MAXIMUM_PLATE_SOLVE_SOURCES]

        # An explicit argument wins over configuration, and an explicit
        # 0 means "no limit" so a caller can override a configured cap
        # without having to read the configuration first.
        identification_limit = maximum_identified_stars
        if identification_limit is None:
            try:
                identification_limit = self.config.get_maximum_identified_stars()
            except Exception:
                # A configuration stub without this getter must not stop
                # a solve; no limit is the documented default anyway.
                identification_limit = None
        if isinstance(identification_limit, int) and identification_limit > 0:
            if len(unique_sources) > identification_limit:
                logger.info(
                    f"Identifying the brightest {identification_limit} of "
                    f"{len(unique_sources)} detected sources, per the configured limit."
                )
            unique_sources = unique_sources[:identification_limit]

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
                logger.info(f"Solving field with {len(solver_sources)} of {len(unique_sources)} sources...")
                h, w = data.shape

                # Determine scale hints dynamically. We
                # use a very narrow 5% window here because the
                # PlateSolver will relax it by another 20%.
                scale_lower, scale_upper = self._calculate_scale_hints(image_data_or_path)

                header = self.solver.solve(
                    image_path=path,
                    sources=solver_sources,
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
        """Download a chunk of the SIMBAD database for our specific image area.

        We filter out galaxies and nebulae because our algorithm only
        cares about stars.

        Returns
        -------
        result_table, simbad_coords : `astropy.table.Table`, `SkyCoord`
            The list of known stars and their coordinates, or None if the
            download failed.
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
                # common names
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
            # Use actual column names from SIMBAD response
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
        """Download and save Gaia DR3 stars for a specific area of the sky.

        This checks if we've already downloaded stars for this area and saved
        them in our local database (`cached_regions`). If we haven't, it
        downloads stars brighter than the `max_magnitude` limit and saves them
        so we don't have to download them again later.

        Parameters
        ----------
        ra_center, dec_center : `float`
            The center of the image area in degrees.
        radius_deg : `float`, optional
            How wide of an area to search.
        max_magnitude : `float`, optional
            How faint of a star to care about.

        Returns
        -------
        count : `int`
            How many stars we downloaded or found in the cache.
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

        # Use the same safety switch as the main search. We still check the
        # local database above, we just skip the internet download part if the
        # connection has failed too many times.
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
        """Search the Gaia DR3 database for stars in a circular area.

        We check our local cache first. If it's not there, we download from
        the internet and save it for next time.

        Parameters
        ----------
        ra_center, dec_center : `float`
            The center of the image area in degrees.
        radius_deg : `float`
            How wide of an area to search.

        Returns
        -------
        result_table, gaia_coords : `astropy.table.Table`, `SkyCoord`
            The list of stars and their coordinates, or None if the search
            failed.
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

        # We didn't find the stars in our local database. Like before, we
        # only skip this next part (the internet download) if the connection
        # safety switch has been tripped.
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
        """Name every detected star using SIMBAD, then Gaia, then position.

        Here is the order we follow to name a star:

        1. **SIMBAD**: We check SIMBAD first because it has the most famous
        stars.
        2. **Gaia DR3**: If SIMBAD doesn't know the star, we check Gaia, which
           has over a billion faint stars.
        3. **Position**: If neither database knows the star, we name it based
           on its coordinates (like `FIELD_J123.4+45.6`). This gives the star
           a permanent name without making up a fake one.

        Parameters
        ----------
        stellar_objects : `list` [`StellarObject`]
            The stars we want to name.
        wcs : `astropy.wcs.WCS`
            The map data that tells us where in the sky we are looking.
        width, height : `int`
            The size of the image, which helps us know how big an area to
            search.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The updated list of named stars.
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
        # Step 0: Convert every star's position from pixels (X/Y) to sky
        # coordinates (RA/Dec) right at the beginning. This allows us to
        # ask SIMBAD/Gaia only for the specific area where our stars are,
        # rather than the entire picture. If the stars are only in one
        # corner of the image, this makes the download much smaller and
        # faster, and prevents the internet connection from timing out.
        # `sky_positions` stores the RA/Dec for every star we successfully
        # located.
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
                if d2d < CATALOG_MATCH_RADIUS_ARCSEC * u.arcsec:
                    self._apply_simbad_match(stellar_object, result_table[idx], ra, dec)
                    # The separation between where the solved WCS put this
                    # star and where the catalog says it is, which is the
                    # only direct measure of how good the solution is.
                    # match_to_catalog_sky returns an array-like even for
                    # a single coordinate, so ravel before taking a scalar.
                    self.catalog_match_separations_arcsec.append(float(np.ravel(d2d.arcsec)[0]))
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

                if d2d < CATALOG_MATCH_RADIUS_ARCSEC * u.arcsec:
                    row = gaia_table[idx]
                    self._apply_gaia_match(stellar_object, row, ra, dec)
                    # Same reason as the SIMBAD match above: the residual
                    # RMS must cover every catalog match, not just
                    # whichever catalog happened to resolve a star first.
                    self.catalog_match_separations_arcsec.append(float(np.ravel(d2d.arcsec)[0]))
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
        # Step 3: Give a coordinate-based name to any star we couldn't find
        # in the catalogs. Using the format FIELD_J{ra:.4f}{dec:+.4f} ensures
        # that the same star will get the exact same name every time we run
        # the program. It also makes it obvious that this isn't a famous star,
        # so the user interface can easily hide them if desired.
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
        """Ask SIMBAD for stars in the image area and match them up.

        If we successfully mapped the image, we name every star. If the map
        failed, we just try to name the star closest to the center of the image
        using our best guess of where the telescope was pointing.
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
        """Filter the SIMBAD results to only include individual stars.

        This removes galaxies, nebulae, and star clusters. Our algorithm only
        looks for small dots of light, so it won't find a whole galaxy anyway.

        Returns
        -------
        result_table : `astropy.table.Table`
            The list with only single stars included.
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

            # In SIMBAD, codes for individual stars end in '*' (like 'V*' or
            # 'WD*').
            # The code 'Cl*' stands for a star cluster, which is a group of
            # stars,
            # so we want to filter those out.
            is_individual_star = otype.endswith("*") and "CL" not in otype
            stellar_mask.append(is_individual_star or "STAR" in otype or has_spectral_type)

        stellar_mask = np.array(stellar_mask, dtype=bool)
        return result_table[stellar_mask]

    def _apply_simbad_match(self, stellar_object, match, ra, dec):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Copy the star details from SIMBAD into our star object."""
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
        # Map SIMBAD flux (magnitude)
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
        """Copy the star details from Gaia into our star object."""
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
