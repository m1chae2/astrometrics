"""Detect stars, map them to the sky, and identify their names.

This follows a step-by-step process to give every detected star a permanent
name:
  1. Search the SIMBAD database (matching stars within 10 arcseconds).
  2. If SIMBAD fails, search the Gaia DR3 database (matching within 10
  arcseconds).
  3. If both fail, name the star based on its coordinates (e.g.,
  ``FIELD_J{ra:.4f}{dec:+.4f}``).

This makes sure every star gets a stable, consistent name before it is saved to
the database, replacing temporary labels so duplicates are not saved.
"""

import logging
import math
import queue
import sqlite3
import threading
import warnings
from collections.abc import Callable
from typing import Any

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.nddata import block_reduce
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning
from erfa import ErfaWarning

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.drivers.interfaces.plate_solve_driver import PlateSolveDriver, read_fit_statistics
from astrometricslib.drivers.interfaces.simbad_driver import SimbadDriver
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.post_processing.assess_match_quality import assess_match_quality
from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_blob_width_from_data
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.shared.simbad_object_types import read_simbad_object_types
from astrometricslib.pipelines.shared.solar_system_targets import is_solar_system_target
from astrometricslib.utilities.exceptions import DATA_ERRORS, ONLINE_QUERY_ERRORS, PlateSolveFailedError

logger = logging.getLogger(__name__)

# A centre star matched to a catalog star farther than this from the position
# hint is reported as a probable mislabel. Only a warning, nothing is changed
# by it. Unvalidated: 30 arcsec is about 16 pixels at this rig's 1.92 arcsec
# per pixel. The correct Vega match is 25 arcsec from the solved stack's
# centre, and the wrong one (TYC 3105-827-1) was 145 arcsec from the mount's
# reported position, so 30 lies between the two cases seen.
HINT_MATCH_WARNING_ARCSEC = 30.0

# How far from the position hint the star at the frame centre may really be,
# when the star is picked by name or by brightness instead of by distance. The
# hint is the mount's report, so it is only as good as the mount's pointing.
# Measured on the 2026-10-04 spectral stacks: Aldebaran 24, Sirius 24, Mars
# 22 and Alhena 138 arcseconds. The wrong Vega match in an earlier session
# was 145 arcseconds from the mount's position. 300 arcseconds (5 arcminutes)
# covers every case seen with about twice the largest error, and stays well
# inside the 12 arcminute SIMBAD region searched on the hint path.
POINTING_ERROR_SEARCH_RADIUS_ARCSEC = 300.0

# When the centre star is named by the target's name or by brightness, the
# name goes to the brightest detection within this fraction of the frame's
# shorter side from the centre, not to whatever is nearest the centre. The
# mount's error (5 arcminutes, about 157 pixels at 1.9 arcseconds per pixel,
# so 5 percent of a 3008 pixel frame) plus a re-centring or drift sets the
# size: on 2026-10-04 the Sirius zero order sat 151 pixels from the centre.
# 10 percent (about 300 pixels) covers that with margin.
CENTRE_STAR_SEARCH_RADIUS_FRACTION = 0.10

# The detection that gets the target's name must reach at least this fraction
# of the frame's brightest pixel. The target of a spectral frame makes the
# brightest compact source in it, because the exposure is chosen to fill the
# zero order. Measured on the 2026-10-04 stacks: the zero orders reached 0.64
# to 1.0 of the brightest pixel; the noise detections near the centre of the
# Sirius stack (whose zero order was not detected) reached 0.00003. 10 percent
# lies between the two cases.
CENTRE_STAR_MINIMUM_PEAK_FRACTION = 0.10

# SIMBAD columns that may hold a star's V magnitude, tried in order.
_SIMBAD_V_MAGNITUDE_COLUMNS = ["V", "FLUX_V", "flux_v", "flux(V)"]

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
# To fix this, shrink the image to half its size (2x2 averaging)
# before looking for stars. Tests prove this successfully breaks up
# the noise clumps without missing any real stars, dropping false
# detections significantly.
_COLOR_DETECTION_BIN_FACTOR = 2

# SIMBAD's client configuration, request timeout and thread lock live in
# drivers/astroquery_simbad_driver.py, alongside every other external-service
# driver, rather than here: they describe how to talk to astroquery, not
# how this pipeline identifies a star.

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

# Catalog entries closer together than this cannot be told apart in one of our
# frames, so the light comes from all of them and the brightest dominates. The
# scale is 1.9 arcseconds per pixel and a star's blur spans a few pixels; 3
# arcseconds is about one and a half pixels. Albireo's two stars (beta1 Cyg A,
# a K3II star, and its B9.5V companion, 0.4 arcseconds apart) were the case
# that showed the problem: the spectrum was K-type, but the star was named
# after the companion because it happened to be nearer by a fraction of a
# pixel. A judgement call, checked on that one pair.
UNRESOLVED_COMPANION_RADIUS_ARCSEC = 3.0

# --- Gaia Search Depth and Epoch --------------------------------------------
#
# The Gaia cone search asks for stars brighter than a magnitude limit and
# returns at most `GAIA_ROW_LIMIT` of them, brightest first. Without a limit
# the search ranked stars by distance from the field centre and stopped at
# 10,000 rows, so a dense field lost its edge stars and those stars went
# unmatched.
#
# The limit comes from the frame when it can: the faintest detected star's
# estimated G magnitude plus `GAIA_MAGNITUDE_LIMIT_MARGIN`. The estimate fits
# a zero point to the detected stars that SIMBAD matched (their catalog V
# magnitude against their measured flux). V and G differ by a few tenths of a
# magnitude for most stars and by more than one for the reddest, so the
# margin and the clamps below are design estimates, not measurements.
# Without enough SIMBAD-matched stars to fit, the limit is
# `GAIA_DEFAULT_MAGNITUDE_LIMIT`, which is also the depth the bulk cache
# seed uses.
GAIA_DEFAULT_MAGNITUDE_LIMIT = 18.0
GAIA_MAGNITUDE_LIMIT_MARGIN = 1.0
GAIA_MINIMUM_MAGNITUDE_LIMIT = 14.0
GAIA_MAXIMUM_MAGNITUDE_LIMIT = 20.0
MINIMUM_STARS_FOR_DEPTH_ESTIMATE = 5

# The most rows one Gaia cone search may return. The old limit was 10,000.
# A one-degree-radius field at G < 18 near the galactic plane holds tens of
# thousands of stars, so the limit is raised to 50,000. A result with exactly
# this many rows is treated as cut short and recorded in a flag.
GAIA_ROW_LIMIT = 50_000

# Gaia DR3 positions are for this epoch (the year 2016.0, in Julian years).
# A star's position at the time of the image is its Gaia position moved along
# its proper motion for the years in between.
GAIA_DR3_REFERENCE_EPOCH = "J2016.0"

# Flags recorded on the quality summary (`astrometry_flags` of the
# astrometry metrics). Each is a stable key; tests and readers match on it.
FLAG_GAIA_ROW_LIMIT_REACHED = "gaia_row_limit_reached"
FLAG_GAIA_PROPER_MOTION_UNKNOWN = "gaia_proper_motion_unknown"
FLAG_GAIA_EPOCH_UNKNOWN = "gaia_epoch_unknown"
FLAG_SCALE_HINT_BINNING_MISMATCH = "scale_hint_binning_mismatch"

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
        What was being attempted when it failed.
    """
    global _gaia_consecutive_failures, _gaia_circuit_open, _gaia_queries_attempted, _gaia_queries_failed
    with _gaia_failure_state_lock:
        _gaia_queries_attempted += 1
        _gaia_queries_failed += 1
        _gaia_consecutive_failures += 1
        if not _gaia_circuit_open and _gaia_consecutive_failures >= GAIA_CONSECUTIVE_FAILURE_LIMIT:
            _gaia_circuit_open = True
            logger.warning(
                "Gaia remote queries disabled for the rest of this process after %s consecutive "
                "failures (last: %s). Locally cached Gaia data is still used; SIMBAD "
                "identification is unaffected.",
                _gaia_consecutive_failures,
                context,
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
        How many times the connection was attempted, how many times it failed,
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
        True if the connection failed too many times in a row.
    """
    with _gaia_failure_state_lock:
        return _gaia_circuit_open


def reset_gaia_circuit_breaker() -> None:
    """Reset the safety switch to allow trying to connect to Gaia again."""
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
    way, a stuck connection can be abandoned while still shutting down cleanly.

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
    ExternalServiceError
        If the function has not finished within `timeout_seconds`.
    """
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def _run_and_report() -> None:
        try:
            result_queue.put((True, query_function()))
        except BaseException as query_error:
            # Caught broadly and re-raised on the caller's thread below via
            # `raise payload` -- nothing here is swallowed. The traceback is
            # also kept at debug level, in case the caller has given up
            # waiting and never raises it.
            logger.debug("The background query raised an error.", exc_info=True)
            result_queue.put((False, query_error))

    threading.Thread(target=_run_and_report, daemon=True).start()

    try:
        succeeded, payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty:
        raise ExternalServiceError(f"Timed out after {timeout_seconds}s") from None

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
    return block_reduce(data, factor, func=np.mean)


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
        if source.get("radius_px") is not None:
            source["radius_px"] = source["radius_px"] * factor


def _brightest_pixel(data: Any) -> float | None:
    """Find the brightest pixel of an image, if it has a usable one.

    Parameters
    ----------
    data : `numpy.ndarray` or `None`
        The image pixels.

    Returns
    -------
    peak : `float` or `None`
        The largest finite pixel value, or `None` when the image is missing,
        empty or holds no finite value.
    """
    try:
        with warnings.catch_warnings():
            # An all-NaN image is expected here and handled below.
            warnings.simplefilter("ignore", RuntimeWarning)
            peak = float(np.nanmax(data))
    except TypeError, ValueError:
        return None
    return peak if math.isfinite(peak) else None


def _optional_float(value: Any) -> float | None:
    """Turn a catalog table value into a float, or `None` if it has no value.

    Parameters
    ----------
    value : `Any`
        A table cell: a number, `None`, or an astropy masked value.

    Returns
    -------
    number : `float` or `None`
        The value, or `None` when it is missing, masked, not a number, or
        not finite.
    """
    if value is None or (hasattr(value, "mask") and bool(np.all(value.mask))):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _read_catalog_magnitude(match: Any, column_names: list[str]) -> float | None:
    """Read a star's brightness from a catalog row, if the catalog has one.

    A catalog often has no brightness for a star (the value is masked, or
    the column is missing). That case must stay `None`, meaning "not yet
    known" (see `StellarObject.magnitude`). Returning ``0.0`` instead
    would look like a real measurement, since magnitude 0 is about as
    bright as Vega, and it would also stop a real magnitude from being
    filled in later.

    Parameters
    ----------
    match : `astropy.table.Row`
        One row of a SIMBAD or Gaia result table.
    column_names : `list` [`str`]
        Names of the columns that can hold this catalog's magnitude, in
        the order to try them. Only the first one that exists is used.

    Returns
    -------
    magnitude : `float` or `None`
        The magnitude, or `None` if the catalog has no usable value.
    """
    for column_name in column_names:
        if column_name not in match.colnames:
            continue
        value = match[column_name]
        if value is None or (hasattr(value, "mask") and bool(value.mask)):
            return None
        try:
            magnitude = float(value)
        except ValueError, TypeError:
            return None
        return magnitude if math.isfinite(magnitude) else None
    return None


def brightest_unresolved_entry_index(
    star_coord: SkyCoord, nearest_index: int, simbad_coords: SkyCoord, result_table: Any
) -> int:
    """Pick the brightest catalog entry that a star cannot be told from.

    Parameters
    ----------
    star_coord : `astropy.coordinates.SkyCoord`
        Where the detected star is on the sky.
    nearest_index : `int`
        The index of the nearest catalog entry.
    simbad_coords : `astropy.coordinates.SkyCoord`
        The positions of every catalog entry, lined up with `result_table`.
    result_table : `astropy.table.Table`
        The catalog rows.

    Returns
    -------
    index : `int`
        The index of the brightest entry (lowest V magnitude) within
        `UNRESOLVED_COMPANION_RADIUS_ARCSEC` of the star, or
        `nearest_index` when there is only one, or none has a magnitude.
        An entry with no magnitude is treated as the faintest.
    """
    separations = star_coord.separation(simbad_coords).arcsec
    candidates = np.flatnonzero(separations <= UNRESOLVED_COMPANION_RADIUS_ARCSEC)
    if candidates.size < 2:
        return nearest_index
    magnitudes = [
        _read_catalog_magnitude(result_table[int(index)], ["V", "FLUX_V", "flux_v", "flux(V)"])
        for index in candidates
    ]
    if all(magnitude is None for magnitude in magnitudes):
        return nearest_index
    brightest = min(
        range(len(candidates)),
        key=lambda position: math.inf if magnitudes[position] is None else magnitudes[position],
    )
    return int(candidates[brightest])


def _is_unresolved_match(star_coord: SkyCoord, simbad_coords: SkyCoord) -> bool:
    """Check whether a star's SIMBAD match was ambiguous.

    Shares `UNRESOLVED_COMPANION_RADIUS_ARCSEC` with
    `brightest_unresolved_entry_index`, which is the function that
    actually resolves the ambiguity by picking the brightest candidate;
    this only reports whether that resolution happened, so a caller can
    record it on `CatalogMatchQuality.is_ambiguous`.

    Returns
    -------
    is_ambiguous : `bool`
        True if two or more catalog entries sat within
        `UNRESOLVED_COMPANION_RADIUS_ARCSEC` of the star.
    """
    separations = star_coord.separation(simbad_coords).arcsec
    return int(np.count_nonzero(separations <= UNRESOLVED_COMPANION_RADIUS_ARCSEC)) >= 2


def _catalog_ra_ranges(ra_center: float, dec_center: float, radius_deg: float) -> list[tuple[float, float]]:
    """Build the Right Ascension (RA) ranges of a catalog search box.

    RA is an angle that wraps from 360 deg back to 0 deg. A box centred
    near that line has edges below 0 or above 360, and the catalog stores
    RA only inside [0, 360], so a single range would miss the rows on the
    far side of the line. This function splits such a box into two
    ranges, one on each side. The box is wider in RA than in declination
    by ``1 / cos(dec)``, with the cosine held at 0.1 or more so the box
    stays finite near a pole.

    Parameters
    ----------
    ra_center : `float`
        RA of the search centre, in degrees.
    dec_center : `float`
        Declination of the search centre, in degrees.
    radius_deg : `float`
        Search radius on the sky, in degrees.

    Returns
    -------
    ranges : `list` [`tuple` [`float`, `float`]]
        One or two ``(low, high)`` RA ranges in degrees, each inside
        [0, 360]. Two ranges are returned when the box crosses the wrap
        line, and they do not overlap. A single range ``(0, 360)`` is
        returned when the box is at least half the circle wide.
    """
    half_width_deg = float(radius_deg / max(0.1, np.cos(np.radians(dec_center))))
    if half_width_deg >= 180.0:
        return [(0.0, 360.0)]
    centre_deg = float(ra_center) % 360.0
    low_deg = centre_deg - half_width_deg
    high_deg = centre_deg + half_width_deg
    if low_deg < 0.0:
        return [(0.0, high_deg), (low_deg + 360.0, 360.0)]
    if high_deg > 360.0:
        return [(low_deg, 360.0), (0.0, high_deg - 360.0)]
    return [(low_deg, high_deg)]


def propagate_gaia_positions(
    ra_deg: Any,
    dec_deg: Any,
    pmra_mas_per_year: Any,
    pmdec_mas_per_year: Any,
    observation_time: Time,
) -> tuple[np.ndarray, np.ndarray]:
    """Move Gaia DR3 star positions from epoch 2016.0 to the observation time.

    A star's position changes over the years because the star moves across the
    sky (its proper motion). Gaia reports each position for the year 2016.0.
    A fast star such as Barnard's Star has moved by several arcseconds by the
    time of an image taken years later, which is enough to miss a match.

    Parameters
    ----------
    ra_deg, dec_deg : array-like
        The Gaia positions at epoch 2016.0, in degrees.
    pmra_mas_per_year, pmdec_mas_per_year : array-like
        The proper motion in milliarcseconds per year. ``pmra`` is Gaia's
        ``pmra`` column, which already includes the cosine of the
        declination. A value that is not finite (Gaia has no motion for the
        star) leaves that star where it is.
    observation_time : `astropy.time.Time`
        When the image was taken.

    Returns
    -------
    ra_deg, dec_deg : `numpy.ndarray`
        The positions at `observation_time`, in degrees.
    """
    ra = np.atleast_1d(np.asarray(ra_deg, dtype=float))
    dec = np.atleast_1d(np.asarray(dec_deg, dtype=float))
    pm_ra = np.nan_to_num(np.atleast_1d(np.asarray(pmra_mas_per_year, dtype=float)), nan=0.0)
    pm_dec = np.nan_to_num(np.atleast_1d(np.asarray(pmdec_mas_per_year, dtype=float)), nan=0.0)
    catalog_coords = SkyCoord(
        ra=ra * u.deg,
        dec=dec * u.deg,
        pm_ra_cosdec=pm_ra * u.mas / u.yr,
        pm_dec=pm_dec * u.mas / u.yr,
        obstime=Time(GAIA_DR3_REFERENCE_EPOCH),
    )
    with warnings.catch_warnings():
        # Gaia gives no distance here. astropy then assumes one and says so.
        # The shift over a few years does not depend on it.
        warnings.simplefilter("ignore", ErfaWarning)
        moved = catalog_coords.apply_space_motion(new_obstime=observation_time)
    return np.asarray(moved.ra.deg, dtype=float), np.asarray(moved.dec.deg, dtype=float)


def read_observation_time(header: Any) -> Time | None:
    """Read when an image was taken from its ``DATE-OBS`` header card.

    Parameters
    ----------
    header : `astropy.io.fits.Header` or `None`
        The image header.

    Returns
    -------
    observation_time : `astropy.time.Time` or `None`
        The time in UTC, or `None` if the header is missing, has no
        ``DATE-OBS``, or the value cannot be read as a date.
    """
    if header is None:
        return None
    value = header.get("DATE-OBS")
    if not value:
        return None
    try:
        return Time(str(value), scale="utc")
    except ValueError:
        logger.warning("Could not read DATE-OBS %r as a date.", value)
        return None


def estimate_gaia_magnitude_limit(stellar_objects: list[StellarObject]) -> float | None:
    """Work out how faint a Gaia search needs to go for one frame.

    The detected stars that SIMBAD identified have both a measured flux and a
    catalog V magnitude. Magnitude is ``zero_point - 2.5 * log10(flux)``, so
    each such star gives a zero point, and the median is the frame's. The
    faintest detected star's magnitude follows from its flux. The limit is
    that magnitude plus `GAIA_MAGNITUDE_LIMIT_MARGIN`, kept between
    `GAIA_MINIMUM_MAGNITUDE_LIMIT` and `GAIA_MAXIMUM_MAGNITUDE_LIMIT`.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        Every detected star of the frame, after the SIMBAD pass.

    Returns
    -------
    magnitude_limit : `float` or `None`
        The limit, or `None` when fewer than
        `MINIMUM_STARS_FOR_DEPTH_ESTIMATE` stars have both a positive flux
        and a catalog magnitude. The caller then uses
        `GAIA_DEFAULT_MAGNITUDE_LIMIT`.
    """
    fluxes: list[float] = []
    zero_points: list[float] = []
    for star in stellar_objects:
        flux = _optional_float(star.flux)
        if flux is None or flux <= 0:
            continue
        fluxes.append(flux)
        magnitude = _optional_float(star.magnitude)
        if star.is_catalog_identified and magnitude is not None:
            zero_points.append(magnitude + 2.5 * math.log10(flux))
    if len(zero_points) < MINIMUM_STARS_FOR_DEPTH_ESTIMATE:
        return None
    faintest_magnitude = float(np.median(zero_points)) - 2.5 * math.log10(min(fluxes))
    limit = faintest_magnitude + GAIA_MAGNITUDE_LIMIT_MARGIN
    return float(min(max(limit, GAIA_MINIMUM_MAGNITUDE_LIMIT), GAIA_MAXIMUM_MAGNITUDE_LIMIT))


class StarIdentifier:
    """The main tool for finding stars, mapping the image, and naming them."""

    def __init__(self, config: AppConfiguration | None = None, *, drivers: Drivers | None = None) -> None:
        """Set up the tools.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            The system settings.
        drivers : `Drivers`, optional
            The plate solver and SIMBAD driver to use. Any left out is the
            built-in one (Astrometry.net, astroquery).
        """
        if config is None:
            from astrometricslib.foundation.config import get_configuration

            config = get_configuration()

        self.config = config
        self.detector = SourceDetector()

        # Extract API key for solver
        api_key = config.get_value(
            "Processing.Astrometry.Online Solver", "api_key", config.get_value("Astrometry", "api_key")
        )
        if api_key is not None and not isinstance(api_key, str) and hasattr(api_key, "_mock_methods"):
            api_key = "mock-api-key"
        drivers = drivers or Drivers()
        self.solver: PlateSolveDriver = drivers.plate_solve_or_default(api_key)
        self.simbad: SimbadDriver = drivers.simbad_or_default()
        self.stellar_objects: list[StellarObject] = []
        self.sources_detected: int = 0
        self.solve_attempted: bool = False
        # The brightest pixel of the last image processed, used to tell a real
        # star from a noise detection (see `_select_target_star`).
        self.frame_peak: float | None = None
        # The distance between where we calculated a star is and where the
        # database says it should be (measured in arcseconds). We use this
        # to figure out how accurate our image alignment is.
        self.catalog_match_separations_arcsec: list[float] = []
        # The plate solver's own fit residual (RMS, arcseconds) and matched
        # star count for the last solve. `None` when the solver gave none.
        self.plate_solve_fit_residual_rms_arcsec: float | None = None
        self.plate_solve_matched_star_count: int | None = None
        # When the last image was taken (from DATE-OBS), or `None`. Gaia
        # positions are moved to this time.
        self.observation_time: Time | None = None
        # Stable keys for conditions that limit the last run's results; see
        # the ``FLAG_*`` constants.
        self.astrometry_flags: list[str] = []

    def _add_flag(self, flag: str) -> None:
        """Record a limiting condition once for the current image.

        Parameters
        ----------
        flag : `str`
            One of the ``FLAG_*`` constants.
        """
        if flag not in self.astrometry_flags:
            self.astrometry_flags.append(flag)

    @staticmethod
    def _build_stellar_objects_from_sources(sources: list[dict]) -> list[StellarObject]:
        """Create a `StellarObject` for each dot of light found.

        Every star is given a temporary name like "Star_1", "Star_2".
        Later, an attempt is made to replace these with real database names.

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
            radius_px = cleaned_src.get("radius_px") if isinstance(cleaned_src, dict) else None
            obj.radius_px = float(radius_px) if radius_px is not None else None
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

    def get_catalog_match_separation_rms_arcsec(self) -> float | None:
        """Measure how far detected stars sit from their catalog matches.

        This is the root mean square (RMS) of the distance from each detected
        star to its nearest SIMBAD or Gaia star. It is not the plate-solve fit
        residual. Only stars within `CATALOG_MATCH_RADIUS_ARCSEC` of a catalog
        star count, so the number cannot exceed that radius. A wrong match,
        and SIMBAD's uneven position precision, make it larger.

        Returns
        -------
        separation_rms_arcsec : `float` or `None`
            The RMS separation in arcseconds, or `None` if no star was
            matched.
        """
        separations = self.catalog_match_separations_arcsec
        if not separations:
            return None
        return round(math.sqrt(sum(value**2 for value in separations) / len(separations)), 4)

    def get_astrometric_residual_rms_arcsec(self) -> float | None:
        """Return the catalog match separation RMS under its older name.

        Returns
        -------
        separation_rms_arcsec : `float` or `None`
            The same value as `get_catalog_match_separation_rms_arcsec`. It is
            the catalog proxy, not the plate-solve fit residual (see
            `plate_solve_fit_residual_rms_arcsec` for that).
        """
        return self.get_catalog_match_separation_rms_arcsec()

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
            blob_width = measure_blob_width_from_data(data) if data is not None else None
        except DATA_ERRORS as width_error:
            logger.debug(
                "Could not measure the detection kernel width, keeping the configured default: %s",
                width_error,
            )
            blob_width = None
        if blob_width is not None:
            logger.debug("Sizing the detection kernel from the measured blob width: %.2fpx", blob_width)
            self.detector.fwhm = blob_width

        sources = self.detector.detect(data)
        unique_sources = self.detector.deduplicate(sources)
        return sources, unique_sources

    def _read_axis_mean(self, header: Any, x_keyword: str, y_keyword: str) -> float | None:
        """Read a per-axis header value, averaging the X and Y cards.

        If both cards hold usable values and they differ, the mean is used
        and the run is flagged, because the scale hint then fits neither
        axis exactly.

        Parameters
        ----------
        header : `astropy.io.fits.Header`
            The image header.
        x_keyword, y_keyword : `str`
            The cards for the X and Y axes, such as ``XPIXSZ`` and ``YPIXSZ``.

        Returns
        -------
        value : `float` or `None`
            The common value when the axes agree, their mean when they
            differ, the one usable value when only one card is usable, or
            `None` when neither is a positive, finite number.
        """
        values: list[float] = []
        for keyword in (x_keyword, y_keyword):
            try:
                value = float(header.get(keyword))
            except TypeError, ValueError:
                continue
            if math.isfinite(value) and value > 0:
                values.append(value)
        if not values:
            return None
        if len(values) == 2 and not math.isclose(values[0], values[1]):
            logger.warning(
                "%s (%s) and %s (%s) differ; using their mean for the scale hint.",
                x_keyword,
                values[0],
                y_keyword,
                values[1],
            )
            self._add_flag(FLAG_SCALE_HINT_BINNING_MISMATCH)
        return sum(values) / len(values)

    def _read_binned_pixel_size(self, header: Any) -> float | None:
        """Read the size of one image pixel, in micrometers, from the header.

        Capture programs that write through INDI/Ekos (libindi), NINA and
        Siril store ``XPIXSZ`` and ``YPIXSZ`` as the binned pixel size (the
        camera's pixel size times the binning factor), so those cards are
        used as they are. Only when they are absent does the function use
        ``PIXSIZE1`` and ``PIXSIZE2``, which hold the camera's own
        (unbinned) pixel size, and multiply by the binning factor from
        ``XBINNING`` and ``YBINNING`` (1 when absent).

        Parameters
        ----------
        header : `astropy.io.fits.Header`
            The image header.

        Returns
        -------
        pixel_size : `float` or `None`
            The binned pixel size in micrometers, or `None` if the header
            names none.
        """
        binned_size = self._read_axis_mean(header, "XPIXSZ", "YPIXSZ")
        if binned_size is not None:
            return binned_size
        unbinned_size = self._read_axis_mean(header, "PIXSIZE1", "PIXSIZE2")
        if unbinned_size is None:
            return None
        binning = self._read_axis_mean(header, "XBINNING", "YBINNING")
        return unbinned_size * (1.0 if binning is None else binning)

    def _calculate_scale_hints(self, image_data_or_path: Any) -> tuple[float | None, float | None]:
        """Guess how zoomed in the image is based on the telescope settings.

        This helps the math solver run much faster because it doesn't have
        to guess the zoom level. The guess is the pixel scale in arcseconds
        per pixel: ``206.265 * pixel size (micrometers) / focal length
        (millimeters)``. The pixel size is the header's ``XPIXSZ``, which
        already includes binning. Without ``XPIXSZ`` it is ``PIXSIZE1``
        times ``XBINNING`` (see `_read_binned_pixel_size`). When the header
        has neither but has ``PIXSCAL`` (the scale in arcseconds per
        pixel), that value is the scale and binning is not applied to it.

        Returns
        -------
        scale_lower, scale_upper : `float` or `None`
            A rough guess of the zoom scale, or None if the settings are
            missing.
        """
        focal_len = None
        pixel_size = None
        header_scale = None

        if isinstance(image_data_or_path, AstrometricsImage):
            hdr = image_data_or_path.header
            focal_len = hdr.get("FOCALLEN")
            pixel_size = self._read_binned_pixel_size(hdr)
            if pixel_size is None:
                header_scale = hdr.get("PIXSCAL")

        try:
            if header_scale and float(header_scale) > 0:
                logger.info("Using the header's pixel scale: %.3f arcsec/pixel", float(header_scale))
                return float(header_scale) * 0.95, float(header_scale) * 1.05
        except TypeError, ValueError:
            logger.warning("Could not read PIXSCAL from the image header.")

        if not focal_len or focal_len <= 0:
            try:
                focal_len = self.config.get_focal_length_mm()
            except ValueError:
                focal_len = None

        if focal_len and focal_len > 0 and pixel_size and pixel_size > 0:
            try:
                calculated_scale = 206.265 * float(pixel_size) / float(focal_len)
                logger.info("Calculated expected pixel scale: %.3f arcsec/pixel", calculated_scale)
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
        target_name: str | None = None,
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
        target_name : `str`, optional
            The name of the target being imaged. Used only when the field is
            not solved: it names the star at the frame centre (see
            `_identify_stars_with_simbad`) and stops a planet from being
            given a star's name.

        Returns
        -------
        stellar_objects : `list` [`StellarObject`]
            The final list of named stars.
        wcs : `astropy.wcs.WCS` or `None`
            The map data, or None if the mapping process failed.

        Raises
        ------
        PlateSolveFailedError
            If plate solving is attempted and the field cannot be solved.
        """
        # 1. Load Image
        header = None
        if isinstance(image_data_or_path, str):
            img = AstrometricsImage(image_data_or_path)
            data = img.data
            path = image_data_or_path
            header = img.header
        elif isinstance(image_data_or_path, AstrometricsImage):
            data = image_data_or_path.data
            path = image_data_or_path.path
            header = image_data_or_path.header
        else:
            data = image_data_or_path
            path = None
        self.observation_time = read_observation_time(header)

        is_color_frame = data is not None and data.ndim == 3
        if is_color_frame:
            data = collapse_to_2d(data)
        self.frame_peak = _brightest_pixel(data)

        # 2. Detect Stars
        logger.info("Detecting stars...")
        sources, unique_sources = self.detect_stars(data, is_color_frame=is_color_frame)
        logger.debug("Detected %s sources, %s unique.", len(sources), len(unique_sources))
        self.sources_detected = len(unique_sources)
        self.catalog_match_separations_arcsec = []
        self.plate_solve_fit_residual_rms_arcsec = None
        self.plate_solve_matched_star_count = None
        self.astrometry_flags = []

        # We limit how many stars we send to the plate solver because it
        # only needs the brightest ones to figure out where the image is
        # pointing. Sending too many faint stars just slows it down.
        # However, the rest of the program wants as many stars as possible
        # for analysis, so we keep the full list for them.
        #
        # We take the stars from the beginning of the list because they
        # are already sorted from brightest to dimmest.
        solver_sources = unique_sources[:MAXIMUM_PLATE_SOLVE_SOURCES]

        # An explicit argument wins over configuration, and an explicit
        # 0 means "no limit" so a caller can override a configured cap
        # without having to read the configuration first.
        identification_limit = maximum_identified_stars
        if identification_limit is None:
            identification_limit = self.config.get_maximum_identified_stars()
        if isinstance(identification_limit, int) and identification_limit > 0:
            if len(unique_sources) > identification_limit:
                logger.info(
                    "Identifying the brightest %s of %s detected sources, per the configured limit.",
                    identification_limit,
                    len(unique_sources),
                )
            unique_sources = unique_sources[:identification_limit]

        self.stellar_objects = self._build_stellar_objects_from_sources(unique_sources)
        self.solve_attempted = False

        if not self.stellar_objects:
            return [], None

        # 3. Plate Solve
        wcs = None
        if attempt_plate_solving:
            # Gated on solver_sources (built from unique_sources before the
            # maximum_identified_stars truncation), not on self.stellar_objects
            # (built after it) -- maximum_identified_stars is documented as
            # only capping database lookups, so a low value must not also
            # silently disable a solve that would otherwise succeed.
            self.solve_attempted = len(solver_sources) >= 4
            if not self.solve_attempted:
                logger.info("Skipping plate solve: only %s sources detected.", len(solver_sources))
            else:
                logger.info(
                    "Solving field with %s of %s sources...", len(solver_sources), len(unique_sources)
                )
                h, w = data.shape

                # Determine scale hints dynamically. We
                # use a very narrow 5% window here because the
                # plate solve driver will relax it by another 20%.
                scale_lower, scale_upper = self._calculate_scale_hints(image_data_or_path)

                solved_header = self.solver.solve(
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

                if solved_header:
                    logger.info("Field solved! Querying SIMBAD...")
                    # The solver's own fit statistics, when it gave any. They
                    # are the residual the quality gate judges; the catalog
                    # match separation stays a separate, weaker number.
                    (
                        self.plate_solve_fit_residual_rms_arcsec,
                        self.plate_solve_matched_star_count,
                    ) = read_fit_statistics(solved_header)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", FITSFixedWarning)
                        wcs = WCS(solved_header, naxis=2)
                    self._identify_stars_with_simbad(wcs, center_ra, center_dec, w, h)
                else:
                    logger.error("Could not solve field.")
                    raise PlateSolveFailedError("Plate solving failed: could not solve field.")
        else:
            # Not attempting plate solving, but we can still identify
            # if we have hints
            if center_ra is not None and center_dec is not None:
                logger.info("Using RA/Dec hints for center star identification (skipping solve)...")
                h, w = data.shape
                self._identify_stars_with_simbad(None, center_ra, center_dec, w, h, target_name=target_name)

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
            except DATA_ERRORS as e:
                logger.warning("Failed to calculate FOV from WCS: %s", e)

        logger.info(
            "Querying SIMBAD bulk region at %.4f, %.4f with %.3f degree radius...",
            ra_center,
            dec_center,
            radius_deg,
        )
        try:
            coord = SkyCoord(ra_center * u.deg, dec_center * u.deg)
            result_table = self.simbad.query_region(
                coord,
                radius=f"{radius_deg}d",
                # "ids" carries the common names used to label a star.
                # "alltypes" lists every object type, not just the main one:
                # the main type of the eclipsing binary Algol is "SB*".
                votable_fields=(
                    "flux(V)",
                    "flux(B)",
                    "sp_type",
                    "ids",
                    "ra(d)",
                    "dec(d)",
                    "otype",
                    "alltypes",
                ),
                row_limit=5000,  # Prevent massive result sets
            )
        except ExternalServiceError, ValueError:
            logger.exception("SIMBAD query failed")
            return None, None

        if result_table is None or len(result_table) == 0:
            logger.info("No SIMBAD results for this region.")
            return None, None

        logger.info("  Found %s potential matches in SIMBAD.", len(result_table))

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

        logger.info("SIMBAD table columns: %s", result_table.colnames)
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
        except DATA_ERRORS:
            logger.exception("SkyCoord creation failed")
            return None, None

        return result_table, simbad_coords

    @staticmethod
    def _seed_gaia_cache_for_field(
        ra_center: float,
        dec_center: float,
        radius_deg: float = 0.5,
        max_magnitude: float = GAIA_DEFAULT_MAGNITUDE_LIMIT,
    ) -> int:
        """Download and save Gaia DR3 stars for a specific area of the sky.

        This checks if the local database already holds a complete download
        that covers this area to this depth (see
        `catalog_store.is_gaia_region_complete`). If not, it downloads stars
        brighter than the `max_magnitude` limit, saves them, and records
        the region's centre, radius and magnitude limit so a later search can
        reuse them. The bulk query sets no row limit, so the record always
        says the download was not cut short.

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
        from astropy.table import Table
        from astroquery.gaia import Gaia

        from astrometricslib.drivers import catalog_store
        from astrometricslib.foundation.config import get_configuration

        radius_deg = min(max(0.1, radius_deg), 1.0)
        if math.isclose(ra_center, 0.0, abs_tol=1e-9) and math.isclose(dec_center, 0.0, abs_tol=1e-9):
            return 0

        config = get_configuration()
        region_key = f"{ra_center:.3f}_{dec_center:.3f}_{radius_deg:.2f}"

        try:
            if catalog_store.is_gaia_region_complete(
                config, ra_center, dec_center, radius_deg, max_magnitude
            ):
                logger.debug("Gaia region '%s' already cached.", region_key)
                return 0
        except (sqlite3.Error, OSError) as e:
            logger.warning("Error checking gaia_cache_regions: %s", e)

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
            "Seeding Gaia DR3 cache for field (%.4f, %.4f), radius=%.3f°...",
            ra_center,
            dec_center,
            radius_deg,
        )
        query = (
            "SELECT source_id, ra, dec, phot_g_mean_mag, designation, pmra, pmdec "
            "FROM gaiadr3.gaia_source "
            f"WHERE phot_g_mean_mag < {max_magnitude} "
            f"AND CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra_center}, {dec_center}, {radius_deg}))=1"
        )

        def _run_query() -> Table:
            """Run the bulk seed query and wait for its table.

            Returns
            -------
            result_table : `astropy.table.Table`
                The rows the Gaia server returned.
            """
            job = Gaia.launch_job_async(query, dump_to_file=False)
            return job.get_results()

        try:
            result_table: Table = _run_with_daemon_thread_timeout(_run_query, timeout_seconds=45)
        except ExternalServiceError:
            logger.warning(
                "Gaia bulk seed query timed out after 45s for field (%.4f, %.4f).", ra_center, dec_center
            )
            _record_gaia_failure("bulk seed timed out after 45s")
            return 0
        except ONLINE_QUERY_ERRORS as e:
            logger.warning("Gaia bulk seed query failed: %s", e)
            _record_gaia_failure(f"bulk seed failed: {e}")
            return 0

        _record_gaia_success()

        if result_table is None or len(result_table) == 0:
            return 0

        try:
            to_insert = [
                (
                    str(row["source_id"]),
                    float(row["ra"]),
                    float(row["dec"]),
                    float(row["phot_g_mean_mag"]) if row["phot_g_mean_mag"] is not None else 0.0,
                    str(row["designation"]) if row["designation"] else f"Gaia DR3 {row['source_id']}",
                    _optional_float(row["pmra"]) if "pmra" in result_table.colnames else None,
                    _optional_float(row["pmdec"]) if "pmdec" in result_table.colnames else None,
                )
                for row in result_table
            ]
            catalog_store.insert_gaia_sources(config, to_insert)
            catalog_store.mark_region_cached(config, region_key, ra_center, dec_center, radius_deg)
            catalog_store.record_gaia_region(
                config, ra_center, dec_center, radius_deg, max_magnitude, row_limit_hit=False
            )
            logger.info(
                "Successfully cached %s Gaia DR3 sources for field (%.4f, %.4f).",
                len(to_insert),
                ra_center,
                dec_center,
            )
            return len(to_insert)
        except (*DATA_ERRORS, sqlite3.Error, OSError) as e:
            logger.warning("Failed to record Gaia DR3 sources to cache: %s", e)
            return 0

    @staticmethod
    def _query_gaia_region(
        ra_center: float,
        dec_center: float,
        radius_deg: float,
        max_magnitude: float = GAIA_DEFAULT_MAGNITUDE_LIMIT,
    ) -> tuple[Any, SkyCoord] | tuple[None, None]:
        """Search the Gaia DR3 database for stars in a circular area.

        We check our local cache first. The cache is used only when a recorded
        download covers this area, is at least as deep as `max_magnitude` and
        was not cut short by the row limit. Otherwise we download from the
        internet and save it, with a record of how it was fetched, for next
        time.

        Parameters
        ----------
        ra_center, dec_center : `float`
            The center of the image area in degrees.
        radius_deg : `float`
            How wide of an area to search.
        max_magnitude : `float`, optional
            Only stars brighter than this Gaia G magnitude are returned,
            whether they come from the cache or the download.

        Returns
        -------
        result_table, gaia_coords : `astropy.table.Table`, `SkyCoord`
            The list of stars and their coordinates, or None if the search
            failed. The table's ``meta`` dictionary can hold two keys:
            ``row_limit_reached`` (the download returned `GAIA_ROW_LIMIT`
            rows, so it may be cut short) and ``proper_motion_known``
            (whether any row has a proper motion).
        """
        from astrometricslib.foundation.config import get_configuration

        radius_deg = min(radius_deg, 1.0)
        config = get_configuration()

        cached = StarIdentifier._query_gaia_region_from_cache(
            config, ra_center, dec_center, radius_deg, max_magnitude
        )
        if cached is not None:
            return cached

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

        result_table = StarIdentifier._download_gaia_region(ra_center, dec_center, radius_deg, max_magnitude)

        if result_table is None or len(result_table) == 0:
            logger.info("No Gaia results for this region.")
            return None, None

        logger.info("  Found %s Gaia sources in field.", len(result_table))
        result_table.meta["proper_motion_known"] = StarIdentifier._table_has_proper_motion(result_table)

        StarIdentifier._cache_gaia_results(
            config, result_table, region=(ra_center, dec_center, radius_deg, max_magnitude)
        )

        gaia_coords = StarIdentifier._gaia_coords_from_table(result_table)
        if gaia_coords is None:
            return None, None

        return result_table, gaia_coords

    @staticmethod
    def _table_has_proper_motion(result_table: Any) -> bool:
        """Check whether any star of a Gaia table has a proper motion.

        Parameters
        ----------
        result_table : `astropy.table.Table`
            A Gaia result or cache table.

        Returns
        -------
        known : `bool`
            True if the table has ``pmra`` and ``pmdec`` columns and at least
            one row has a finite value in both. Some Gaia stars have none
            (their solution has no motion), so a few missing values are normal.
            A table where every value is missing came from a cache written
            before proper motions were stored.
        """
        if "pmra" not in result_table.colnames or "pmdec" not in result_table.colnames:
            return False
        pm_ra = np.ma.filled(np.ma.asarray(result_table["pmra"], dtype=float), np.nan)
        pm_dec = np.ma.filled(np.ma.asarray(result_table["pmdec"], dtype=float), np.nan)
        return bool(np.any(np.isfinite(pm_ra) & np.isfinite(pm_dec)))

    @staticmethod
    def _query_gaia_region_from_cache(
        config: Any,
        ra_center: float,
        dec_center: float,
        radius_deg: float,
        max_magnitude: float = GAIA_DEFAULT_MAGNITUDE_LIMIT,
    ) -> tuple[Any, SkyCoord] | None:
        """Look for cached Gaia DR3 sources covering this region.

        Parameters
        ----------
        config : `AppConfiguration`
            The application settings.
        ra_center, dec_center, radius_deg : `float`
            The search centre and radius in degrees.
        max_magnitude : `float`, optional
            Cached stars fainter than this Gaia G magnitude are left out. A
            star stored without a magnitude (stored as 0) is kept. The cache
            is also used only if a recorded download is at least this deep.

        Returns
        -------
        result : `tuple` or `None`
            `(result_table, gaia_coords)` if a recorded download covers the
            search circle, reaches `max_magnitude` and was not cut short, and
            the cache holds at least one star in the search bounding box.
            Otherwise `None`, and the caller downloads. A cache file with no
            region records (one written before they existed) always gives
            `None`, so its regions are downloaded again once. The table has
            ``pmra`` and ``pmdec`` columns (NaN where the cache has no
            proper motion for the star) and its ``meta["proper_motion_known"]``
            says whether any star has one.
        """
        from astropy.table import Table

        from astrometricslib.drivers import catalog_store

        cache_db_path = catalog_store.get_catalog_cache_path(config)
        try:
            # A star count says nothing about completeness: a region cut off
            # by the old 10,000-row limit, or downloaded to a brighter limit,
            # holds plenty of rows. Only a recorded download counts.
            if not catalog_store.is_gaia_region_complete(
                config, ra_center, dec_center, radius_deg, max_magnitude
            ):
                logger.debug(
                    "No complete cached Gaia download covers (%.4f, %.4f) r=%.3f G<%.1f.",
                    ra_center,
                    dec_center,
                    radius_deg,
                    max_magnitude,
                )
                return None

            # Query existing cached sources within bounding box + radius.
            # A box that crosses RA = 0 deg splits in two, one query per side.
            min_dec = dec_center - radius_deg
            max_dec = dec_center + radius_deg

            cached_rows = []
            for min_ra, max_ra in _catalog_ra_ranges(ra_center, dec_center, radius_deg):
                cached_rows.extend(
                    catalog_store.query_gaia_sources_in_bounds(
                        config, min_ra, max_ra, min_dec, max_dec, include_proper_motion=True
                    )
                )
            # The recorded download may be deeper than this request, so the
            # same magnitude limit applies here as to a download.
            cached_rows = [row for row in cached_rows if not row[3] or row[3] < max_magnitude]

            if cached_rows:
                logger.info(
                    "Loaded %s Gaia DR3 sources from local SQLite cache (%s).",
                    len(cached_rows),
                    cache_db_path,
                )
                source_ids = [r[0] for r in cached_rows]
                ras = [r[1] for r in cached_rows]
                decs = [r[2] for r in cached_rows]
                mags = [r[3] for r in cached_rows]
                desigs = [r[4] for r in cached_rows]
                pm_ras = [np.nan if r[5] is None else r[5] for r in cached_rows]
                pm_decs = [np.nan if r[6] is None else r[6] for r in cached_rows]

                result_table = Table(
                    [source_ids, ras, decs, mags, desigs, pm_ras, pm_decs],
                    names=["source_id", "ra", "dec", "phot_g_mean_mag", "DESIGNATION", "pmra", "pmdec"],
                )
                result_table.meta["proper_motion_known"] = StarIdentifier._table_has_proper_motion(
                    result_table
                )
                gaia_coords = SkyCoord(ra=np.array(ras) * u.deg, dec=np.array(decs) * u.deg)
                return result_table, gaia_coords
        except (*DATA_ERRORS, sqlite3.Error, OSError) as e:
            logger.warning("Failed checking local Gaia SQLite cache: %s", e)

        return None

    @staticmethod
    def _download_gaia_region(
        ra_center: float,
        dec_center: float,
        radius_deg: float,
        max_magnitude: float = GAIA_DEFAULT_MAGNITUDE_LIMIT,
        row_limit: int = GAIA_ROW_LIMIT,
    ) -> Any | None:
        """Download Gaia DR3 sources for a region from the remote TAP server.

        The query asks for stars brighter than `max_magnitude` inside the
        circle and returns the brightest first, so if the result is cut at
        `row_limit` rows, the faintest stars are the ones lost. Each row has
        the position (epoch 2016.0), the G magnitude, the designation and the
        proper motion.

        Parameters
        ----------
        ra_center, dec_center, radius_deg : `float`
            The search circle in degrees.
        max_magnitude : `float`, optional
            Only stars brighter than this Gaia G magnitude are returned.
        row_limit : `int`, optional
            The most rows to return.

        Returns
        -------
        result_table : `astropy.table.Table` or `None`
            The downloaded sources, or `None` if the query timed out
            or failed. When the table holds exactly `row_limit` rows,
            ``result_table.meta["row_limit_reached"]`` is True.
        """
        from astroquery.gaia import Gaia

        # If cache miss, auto-download from remote TAP server
        logger.info(
            "Querying Gaia DR3 bulk region at %.4f, %.4f with %.3f degree radius (G < %.1f)...",
            ra_center,
            dec_center,
            radius_deg,
            max_magnitude,
        )
        # The table is pinned explicitly (gaiadr3.gaia_source) rather than
        # left to astroquery's default, the ESA archive server's own current
        # release. It must always match the release hardcoded in the bulk-seed
        # ADQL query, the local SQLite cache schema, and every "Gaia DR3 ..."
        # id string this file generates. Bump deliberately, together with
        # those, when moving to a newer Gaia release.
        query = (
            f"SELECT TOP {int(row_limit)} source_id, ra, dec, phot_g_mean_mag, designation, pmra, pmdec "
            "FROM gaiadr3.gaia_source "
            f"WHERE phot_g_mean_mag < {max_magnitude} "
            "AND CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra_center}, {dec_center}, {radius_deg}))=1 "
            "ORDER BY phot_g_mean_mag ASC"
        )

        # Gaia's TAP client (unlike Simbad) exposes no configurable
        # timeout, so a stalled connection blocks indefinitely -- wrap the
        # call in a hard deadline via a worker thread rather than let a
        # bad connection hang the whole analysis run. The thread is
        # abandoned, not killed, if it exceeds the deadline.
        def _run_gaia_query():  # ruff: ignore[missing-return-type-private-function]
            job = Gaia.launch_job_async(query, dump_to_file=False)
            return job.get_results()

        try:
            result_table = _run_with_daemon_thread_timeout(_run_gaia_query, timeout_seconds=60)
        except ExternalServiceError:
            logger.exception("Gaia query timed out after 60s.")
            _record_gaia_failure("cone search timed out after 60s")
            return None
        except ONLINE_QUERY_ERRORS as e:
            logger.exception("Gaia query failed")
            _record_gaia_failure(f"cone search failed: {e}")
            return None

        # The service answered. An empty region is a legitimate answer, so
        # this counts as success and clears any accumulated failures.
        _record_gaia_success()
        if result_table is not None and len(result_table) >= row_limit:
            logger.warning(
                "The Gaia search returned its limit of %s rows (G < %.1f), so faint stars in the "
                "field may be missing.",
                row_limit,
                max_magnitude,
            )
            result_table.meta["row_limit_reached"] = True
        return result_table

    @staticmethod
    def _cache_gaia_results(
        config: Any,
        result_table: Any,
        *,
        region: tuple[float, float, float, float] | None = None,
    ) -> None:
        """Save downloaded Gaia DR3 sources to the local SQLite cache.

        Parameters
        ----------
        config : `AppConfiguration`
            The application settings.
        result_table : `astropy.table.Table`
            The downloaded sources.
        region : `tuple` of `float`, optional
            ``(ra_center, dec_center, radius_deg, magnitude_limit)`` of the
            download, in degrees and Gaia G magnitude. When given, the region
            is recorded after the stars are saved, together with whether
            ``result_table.meta["row_limit_reached"]`` says the download was
            cut short. Without it the stars are saved but no region is
            recorded, so they are never reused as a complete download.
        """
        from astrometricslib.drivers import catalog_store

        cache_db_path = catalog_store.get_catalog_cache_path(config)
        try:
            ra_col = next((c for c in ["ra", "RA", "ra_epoch2000"] if c in result_table.colnames), None)
            dec_col = next((c for c in ["dec", "DEC", "dec_epoch2000"] if c in result_table.colnames), None)
            id_col = next((c for c in ["source_id", "SOURCE_ID"] if c in result_table.colnames), None)
            mag_col = next((c for c in ["phot_g_mean_mag", "g_mag"] if c in result_table.colnames), None)
            pm_ra_col = "pmra" if "pmra" in result_table.colnames else None
            pm_dec_col = "pmdec" if "pmdec" in result_table.colnames else None
            desig_col = next((c for c in ["DESIGNATION", "designation"] if c in result_table.colnames), None)

            if ra_col and dec_col:
                to_insert = []
                for row in result_table:
                    sid = str(row[id_col]) if id_col and row[id_col] is not None else ""
                    r_val = float(row[ra_col]) if row[ra_col] is not None else 0.0
                    d_val = float(row[dec_col]) if row[dec_col] is not None else 0.0
                    m_val = float(row[mag_col]) if mag_col and row[mag_col] is not None else 0.0
                    des = (
                        str(row[desig_col]) if desig_col and row[desig_col] is not None else f"Gaia DR3 {sid}"
                    )
                    to_insert.append((
                        sid,
                        r_val,
                        d_val,
                        m_val,
                        des,
                        _optional_float(row[pm_ra_col]) if pm_ra_col else None,
                        _optional_float(row[pm_dec_col]) if pm_dec_col else None,
                    ))

                catalog_store.insert_gaia_sources(config, to_insert)
                if region is not None:
                    region_ra, region_dec, region_radius, region_magnitude_limit = region
                    catalog_store.record_gaia_region(
                        config,
                        region_ra,
                        region_dec,
                        region_radius,
                        region_magnitude_limit,
                        row_limit_hit=bool(result_table.meta.get("row_limit_reached")),
                    )
                logger.info("Cached %s Gaia DR3 sources locally in %s.", len(to_insert), cache_db_path)
        except (*DATA_ERRORS, sqlite3.Error, OSError) as cache_err:
            logger.warning("Failed to cache Gaia sources locally: %s", cache_err)

    @staticmethod
    def _gaia_coords_from_table(result_table: Any) -> SkyCoord | None:
        """Build a `SkyCoord` from a Gaia result table's RA/Dec columns.

        Returns
        -------
        gaia_coords : `SkyCoord` or `None`
            `None` if the table has no recognisable RA/Dec columns, or
            coordinate construction failed.
        """
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
                return None

            ra_vals = result_table[ra_col]
            dec_vals = result_table[dec_col]
            return SkyCoord(
                ra=ra_vals * u.deg,
                dec=dec_vals * u.deg,
            )
        except DATA_ERRORS:
            logger.exception("Gaia SkyCoord creation failed")
            return None

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

        logger.info("Starting SIMBAD identification for %s sources...", len(stellar_objects))

        try:
            ra_center = wcs.wcs.crval[0]
            dec_center = wcs.wcs.crval[1]
        except DATA_ERRORS:
            logger.warning("Could not determine field center from WCS; skipping SIMBAD identification.")
            return stellar_objects

        sky_positions, ra_center, dec_center, query_radius_deg = self._project_stars_to_sky(
            stellar_objects, wcs, ra_center, dec_center
        )

        unmatched_after_simbad = self._match_stars_against_simbad(
            stellar_objects, sky_positions, ra_center, dec_center, wcs, width, height, query_radius_deg
        )

        if not unmatched_after_simbad:
            return stellar_objects

        still_unmatched = self._match_stars_against_gaia(
            unmatched_after_simbad,
            sky_positions,
            ra_center,
            dec_center,
            wcs,
            width,
            height,
            query_radius_deg,
            magnitude_limit=estimate_gaia_magnitude_limit(stellar_objects),
        )

        self._assign_field_ids(still_unmatched, sky_positions)

        return stellar_objects

    def _project_stars_to_sky(
        self,
        stellar_objects: list[StellarObject],
        wcs: WCS,
        ra_center: float,
        dec_center: float,
    ) -> tuple[dict[int, tuple[float, float]], float, float, float | None]:
        """Project every star's pixel position to sky coordinates.

        This allows us to ask SIMBAD/Gaia only for the specific area
        where our stars are, rather than the entire picture. If the
        stars are only in one corner of the image, this makes the
        download much smaller and faster, and prevents the internet
        connection from timing out.

        Returns
        -------
        sky_positions : `dict`
            Maps `id(stellar_object)` to its `(ra, dec)` in degrees,
            for every star that could be projected.
        ra_center, dec_center : `float`
            The query center: the star field's bounding-box midpoint
            if any star projected, otherwise `ra_center`/`dec_center`
            unchanged.
        query_radius_deg : `float` or `None`
            The radius that covers every projected star, or `None` if
            no star could be projected.
        """
        # `sky_positions` stores the RA/Dec for every star we successfully
        # located.
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
            except DATA_ERRORS as e:
                logger.warning("Failed to project pixel (%s, %s) to sky: %s", x, y, e)

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

        return sky_positions, ra_center, dec_center, query_radius_deg

    def _match_stars_against_simbad(
        self,
        stellar_objects: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
        ra_center: float,
        dec_center: float,
        wcs: WCS,
        width: int,
        height: int,
        query_radius_deg: float | None,
    ) -> list[StellarObject]:
        """Match every detected star against a bulk SIMBAD region query.

        A star within `CATALOG_MATCH_RADIUS_ARCSEC` of a SIMBAD entry
        is identified from that match; every other star is left with
        its sky position recorded and deferred to the Gaia fallback.

        Returns
        -------
        unmatched_after_simbad : `list` [`StellarObject`]
            The stars SIMBAD could not identify.
        """
        result_table, simbad_coords = self._query_simbad_region(
            ra_center, dec_center, wcs, width, height, radius_deg_override=query_radius_deg
        )

        unmatched_after_simbad: list[StellarObject] = []

        logger.info(
            "Matching %s detected stars against %s SIMBAD entries...",
            len(stellar_objects),
            len(simbad_coords) if simbad_coords is not None else 0,
        )
        for stellar_object in stellar_objects:
            position = sky_positions.get(id(stellar_object))
            if position is None:
                logger.warning("Star %s missing centroid in star_data; skipping.", stellar_object.name)
                unmatched_after_simbad.append(stellar_object)
                continue
            ra, dec = position

            star_coord = SkyCoord(ra * u.deg, dec * u.deg)

            if simbad_coords is not None and result_table is not None:
                idx, d2d, _ = star_coord.match_to_catalog_sky(simbad_coords)
                if d2d < CATALOG_MATCH_RADIUS_ARCSEC * u.arcsec:
                    is_ambiguous = _is_unresolved_match(star_coord, simbad_coords)
                    idx = brightest_unresolved_entry_index(
                        star_coord, int(np.ravel(idx)[0]), simbad_coords, result_table
                    )
                    self._apply_simbad_match(stellar_object, result_table[idx], ra, dec)
                    # The separation between where the solved WCS put this
                    # star and where the catalog says it is, which is the
                    # only direct measure of how good the solution is.
                    # match_to_catalog_sky returns an array-like even for
                    # a single coordinate, so ravel before taking a scalar.
                    separation_arcsec = float(np.ravel(d2d.arcsec)[0])
                    self.catalog_match_separations_arcsec.append(separation_arcsec)
                    stellar_object.catalog_match_quality = assess_match_quality(
                        "simbad", separation_arcsec, is_ambiguous=is_ambiguous
                    )
                    logger.info("  SIMBAD match: %s at (%.4f, %.4f)", stellar_object.name, ra, dec)
                    continue

            # SIMBAD had no match — record sky position and defer to Gaia.
            stellar_object.right_ascension = ra
            stellar_object.declination = dec
            unmatched_after_simbad.append(stellar_object)

        simbad_match_count = len(stellar_objects) - len(unmatched_after_simbad)
        logger.info(
            "SIMBAD matched %s / %s stars; %s deferred to Gaia DR3 fallback.",
            simbad_match_count,
            len(stellar_objects),
            len(unmatched_after_simbad),
        )

        return unmatched_after_simbad

    def _match_stars_against_gaia(
        self,
        unmatched_after_simbad: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
        ra_center: float,
        dec_center: float,
        wcs: WCS,
        width: int,
        height: int,
        query_radius_deg: float | None,
        magnitude_limit: float | None = None,
    ) -> list[StellarObject]:
        """Match stars SIMBAD couldn't identify against a bulk Gaia query.

        Gaia positions are moved from epoch 2016.0 to the time of the image
        before matching (see `_move_gaia_coords_to_observation_epoch`).

        Parameters
        ----------
        unmatched_after_simbad : `list` [`StellarObject`]
            The stars SIMBAD could not identify.
        sky_positions : `dict`
            Maps `id(stellar_object)` to its `(ra, dec)` in degrees.
        ra_center, dec_center : `float`
            The search centre in degrees.
        wcs : `astropy.wcs.WCS`
            The solved sky map, used to size the search when no radius
            is given.
        width, height : `int`
            The image size in pixels.
        query_radius_deg : `float` or `None`
            The search radius in degrees, or `None` to derive it from the WCS.
        magnitude_limit : `float`, optional
            Only Gaia stars brighter than this G magnitude are searched.
            `GAIA_DEFAULT_MAGNITUDE_LIMIT` when left out.

        Returns
        -------
        still_unmatched : `list` [`StellarObject`]
            The stars neither catalog could identify.
        """
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
            except DATA_ERRORS as exc:
                logger.debug("Could not derive search radius from WCS pixel scale: %s", exc)

        gaia_table, gaia_coords = self._query_gaia_region(
            ra_center,
            dec_center,
            radius_deg,
            GAIA_DEFAULT_MAGNITUDE_LIMIT if magnitude_limit is None else magnitude_limit,
        )
        if gaia_table is not None and gaia_coords is not None:
            if gaia_table.meta.get("row_limit_reached"):
                self._add_flag(FLAG_GAIA_ROW_LIMIT_REACHED)
            gaia_coords = self._move_gaia_coords_to_observation_epoch(gaia_table, gaia_coords)

        still_unmatched: list[StellarObject] = []
        if gaia_table is not None and gaia_coords is not None:
            logger.info(
                "Matching %s stars against %s Gaia DR3 sources...",
                len(unmatched_after_simbad),
                len(gaia_coords),
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
                    separation_arcsec = float(np.ravel(d2d.arcsec)[0])
                    self.catalog_match_separations_arcsec.append(separation_arcsec)
                    # Gaia matching runs no unresolved-companion check
                    # today (see _is_unresolved_match/
                    # brightest_unresolved_entry_index, SIMBAD-only), so
                    # is_ambiguous is always False here, not unknown.
                    stellar_object.catalog_match_quality = assess_match_quality("gaia", separation_arcsec)
                    logger.info("  Gaia match: %s at (%.4f, %.4f)", stellar_object.name, ra, dec)
                else:
                    still_unmatched.append(stellar_object)
        else:
            still_unmatched = unmatched_after_simbad

        gaia_match_count = len(unmatched_after_simbad) - len(still_unmatched)
        logger.info(
            "Gaia matched %s additional stars; %s will receive position-based IDs.",
            gaia_match_count,
            len(still_unmatched),
        )

        return still_unmatched

    def _move_gaia_coords_to_observation_epoch(self, gaia_table: Any, gaia_coords: SkyCoord) -> SkyCoord:
        """Move Gaia star positions from epoch 2016.0 to the image's time.

        A star with a proper motion of 1 arcsecond per year sits 10
        arcseconds from its Gaia position ten years later. The match radius
        is also 10 arcseconds, so a fast star would otherwise go unmatched
        or be matched to a neighbour. The positions stay at epoch 2016.0,
        and a flag says so, when the table has no proper motions (an older
        cache) or the image has no ``DATE-OBS``.

        Parameters
        ----------
        gaia_table : `astropy.table.Table`
            The Gaia stars, with ``pmra`` and ``pmdec`` columns if known.
        gaia_coords : `astropy.coordinates.SkyCoord`
            The positions at epoch 2016.0, lined up with `gaia_table`.

        Returns
        -------
        coords : `astropy.coordinates.SkyCoord`
            The positions at the observation time, or `gaia_coords`
            unchanged when they cannot be moved.
        """
        if not self._table_has_proper_motion(gaia_table):
            logger.warning(
                "The Gaia stars carry no proper motion (an older cache?), so their positions stay at "
                "epoch 2016.0."
            )
            self._add_flag(FLAG_GAIA_PROPER_MOTION_UNKNOWN)
            return gaia_coords
        if self.observation_time is None:
            logger.warning("The image has no usable DATE-OBS, so Gaia positions stay at epoch 2016.0.")
            self._add_flag(FLAG_GAIA_EPOCH_UNKNOWN)
            return gaia_coords
        try:
            ra_deg, dec_deg = propagate_gaia_positions(
                gaia_coords.ra.deg,
                gaia_coords.dec.deg,
                np.ma.filled(np.ma.asarray(gaia_table["pmra"], dtype=float), np.nan),
                np.ma.filled(np.ma.asarray(gaia_table["pmdec"], dtype=float), np.nan),
                self.observation_time,
            )
        except DATA_ERRORS:
            logger.exception("Could not move Gaia positions to the observation epoch.")
            return gaia_coords
        return SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)

    def _assign_field_ids(
        self,
        still_unmatched: list[StellarObject],
        sky_positions: dict[int, tuple[float, float]],
    ) -> None:
        """Give a coordinate-based name to every star no catalog matched.

        Using the format FIELD_J{ra:.4f}{dec:+.4f} ensures that the
        same star will get the exact same name every time we run the
        program. It also makes it obvious that this isn't a famous
        star, so the user interface can easily hide them if desired.
        """
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

    def _identify_stars_with_simbad(
        self,
        wcs: WCS | None,
        center_ra: float | None = None,
        center_dec: float | None = None,
        width: int = 1000,
        height: int = 1000,
        target_name: str | None = None,
    ) -> None:
        """Ask SIMBAD for stars in the image area and match them up.

        If we successfully mapped the image, we name every star. If the map
        failed, we just try to name the star closest to the center of the image
        using our best guess of where the telescope was pointing.

        That guess is the mount's report and can be minutes of arc off, so the
        nearest catalog entry is not always the right star. When `target_name`
        is given, `_choose_center_catalog_entry` picks the entry by the
        target's name first, and by brightness second. A solar-system target
        (a planet, the Moon, the Sun) is skipped: it has no catalog entry, and
        the nearest star would be a chance background star.
        """
        if not self.stellar_objects:
            return

        if wcs:
            self.identify_stars_with_wcs(self.stellar_objects, wcs, width, height)
            return

        if center_ra is None or center_dec is None:
            logger.warning("No center coordinates available for SIMBAD query.")
            return

        if is_solar_system_target(target_name):
            logger.info(
                "Target %r is a solar-system body: leaving the frame-centre star without a catalog name.",
                target_name,
            )
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
                    "Star %s is missing centroid coordinates in star_data: %s", stellar_object.name, star
                )
                continue

            dist_sq = (x - (width / 2)) ** 2 + (y - (height / 2)) ** 2
            star["center_dist_sq"] = dist_sq

        if not self.stellar_objects:
            return

        center_star_obj = min(self.stellar_objects, key=lambda o: o.star_data.get("center_dist_sq", 999999))
        try:
            hint_coord = SkyCoord(center_ra * u.deg, center_dec * u.deg)
            idx, basis = self._choose_center_catalog_entry(
                result_table, simbad_coords, hint_coord, target_name
            )
            hint_offset_arcsec = float(hint_coord.separation(simbad_coords[idx]).arcsec)
            logger.info(
                "Frame-centre star labelled by %s: SIMBAD entry %s, %.0f arcsec from the hint.",
                basis,
                result_table[idx]["main_id"] if "main_id" in result_table.colnames else idx,
                hint_offset_arcsec,
            )
            if basis != "nearest":
                target_star = self._select_target_star(width, height)
                if target_star is None:
                    logger.warning(
                        "No detection near the frame centre is bright enough to be the target, so the "
                        "catalog entry %s is not applied to any star.",
                        result_table[idx]["main_id"] if "main_id" in result_table.colnames else idx,
                    )
                    return
                center_star_obj = target_star
            if basis == "nearest" and hint_offset_arcsec > HINT_MATCH_WARNING_ARCSEC:
                logger.warning(
                    "The nearest stellar SIMBAD entry is %.0f arcsec from the position hint, so the star at "
                    "the frame centre may be labelled with the wrong catalog star. Check the hint: the FITS "
                    "position is only the mount's report.",
                    hint_offset_arcsec,
                )
            # The star gets the catalog star's own position. The hint is only
            # where the telescope was thought to point, and on the Vega
            # session it was 22 arcsec (13 pixels) from Vega itself, so
            # stamping it on the star put the overlay marker in the wrong
            # place.
            matched_position = simbad_coords[idx]
            self._apply_simbad_match(
                center_star_obj,
                result_table[idx],
                float(matched_position.ra.deg),
                float(matched_position.dec.deg),
            )
        except DATA_ERRORS as e:
            logger.warning("Failed to match hint coordinates against SIMBAD results: %s", e)

    def _choose_center_catalog_entry(
        self,
        result_table: Any,
        simbad_coords: SkyCoord,
        hint_coord: SkyCoord,
        target_name: str | None,
    ) -> tuple[int, str]:
        """Pick the SIMBAD entry that names the star at the frame centre.

        The position hint is the mount's report. On the 2026-10-04 spectral
        stacks it was 2.3 arcminutes from Alhena, and the nearest stellar
        entry was a magnitude 14 star 90 arcseconds from the hint, so
        distance alone named Alhena's zero order after the wrong star. The
        order of choices here avoids that:

        1. With no target name, take the nearest entry (the plain behaviour).
        2. If SIMBAD knows the target's name, take that entry, provided it is
           within `POINTING_ERROR_SEARCH_RADIUS_ARCSEC` of the hint. A name
           that resolves to something that is not a star in the region (a
           nebula or a cluster) falls back to the nearest entry.
        3. If SIMBAD does not know the name, take the brightest entry within
           the same radius. The star at the centre of a spectral frame is the
           target, which is chosen for being bright, so a faint neighbour is
           the less likely answer. With no magnitude to compare, take the
           nearest entry.

        Parameters
        ----------
        result_table : `astropy.table.Table`
            The stellar SIMBAD entries around the hint.
        simbad_coords : `astropy.coordinates.SkyCoord`
            The position of each entry in `result_table`.
        hint_coord : `astropy.coordinates.SkyCoord`
            The mount's reported position.
        target_name : `str` or `None`
            The name of the target being imaged.

        Returns
        -------
        index : `int`
            The row of `result_table` to use.
        basis : `str`
            ``"target name"``, ``"brightest"`` or ``"nearest"``: how the row
            was chosen.
        """
        separations_arcsec = np.atleast_1d(hint_coord.separation(simbad_coords).arcsec)
        nearest_index = int(np.argmin(separations_arcsec))
        if not target_name:
            return nearest_index, "nearest"

        status, named_coord = self._resolve_target_position(target_name)
        if status == "error":
            return nearest_index, "nearest"
        if status == "resolved":
            named_index = self._entry_at_position(named_coord, simbad_coords)
            if (
                named_index is not None
                and separations_arcsec[named_index] <= POINTING_ERROR_SEARCH_RADIUS_ARCSEC
            ):
                return named_index, "target name"
            return nearest_index, "nearest"

        nearby_indices = np.flatnonzero(separations_arcsec <= POINTING_ERROR_SEARCH_RADIUS_ARCSEC)
        brightest_index = self._brightest_entry_index(result_table, nearby_indices)
        if brightest_index is not None:
            return brightest_index, "brightest"
        return nearest_index, "nearest"

    def _select_target_star(self, width: int, height: int) -> StellarObject | None:
        """Find the detection that is the target of the frame.

        The brightest detection within `CENTRE_STAR_SEARCH_RADIUS_FRACTION` of
        the frame centre, provided it reaches
        `CENTRE_STAR_MINIMUM_PEAK_FRACTION` of the frame's brightest pixel.
        Used when the catalog entry was picked by name or by brightness. The
        nearest detection to the centre is not safe then: when the real star
        is missed (a smeared zero order can fail the roundness test), the
        nearest detection is a noise blob, and it would be given the star's
        name.

        Parameters
        ----------
        width, height : `int`
            The size of the image in pixels.

        Returns
        -------
        star : `StellarObject` or `None`
            The detection to name, or `None` when no detection qualifies.
        """
        radius_px = CENTRE_STAR_SEARCH_RADIUS_FRACTION * min(width, height)
        candidates = [
            star
            for star in self.stellar_objects
            if star.star_data.get("center_dist_sq", math.inf) <= radius_px**2
        ]
        if not candidates:
            return None
        brightest = max(candidates, key=lambda star: star.star_data.get("peak", 0.0))
        if (
            self.frame_peak
            and brightest.star_data.get("peak", 0.0) < CENTRE_STAR_MINIMUM_PEAK_FRACTION * self.frame_peak
        ):
            return None
        return brightest

    def _resolve_target_position(self, target_name: str) -> tuple[str, SkyCoord | None]:
        """Look a target's name up in SIMBAD and return where it is.

        Parameters
        ----------
        target_name : `str`
            The target's name, for example ``"Alhena"`` or ``"M 57"``.

        Returns
        -------
        status : `str`
            ``"resolved"`` when SIMBAD knows the name, ``"unresolved"`` when
            it does not, and ``"error"`` when the lookup failed (no network,
            for example) so nothing is known either way.
        position : `astropy.coordinates.SkyCoord` or `None`
            The object's position when `status` is ``"resolved"``.
        """
        try:
            table = self.simbad.query_object(target_name.replace("_", " "))
        except IndexError:
            # astroquery raises IndexError, not an empty result, for a name
            # SIMBAD does not know.
            return "unresolved", None
        except ExternalServiceError as lookup_error:
            logger.warning("SIMBAD name lookup for %r failed: %s", target_name, lookup_error)
            return "error", None
        if table is None or len(table) == 0:
            return "unresolved", None
        try:
            return "resolved", SkyCoord(float(table["ra"][0]) * u.deg, float(table["dec"][0]) * u.deg)
        except DATA_ERRORS as column_error:
            logger.warning(
                "SIMBAD name lookup for %r returned no usable position: %s", target_name, column_error
            )
            return "error", None

    @staticmethod
    def _entry_at_position(position: SkyCoord, simbad_coords: SkyCoord) -> int | None:
        """Find the catalog entry that sits at a given position.

        Parameters
        ----------
        position : `astropy.coordinates.SkyCoord`
            The position to look for.
        simbad_coords : `astropy.coordinates.SkyCoord`
            The position of each catalog entry.

        Returns
        -------
        index : `int` or `None`
            The entry within `CATALOG_MATCH_RADIUS_ARCSEC` of `position`, or
            `None` when there is none.
        """
        index, separation, _ = position.match_to_catalog_sky(simbad_coords)
        if float(np.ravel(separation.arcsec)[0]) <= CATALOG_MATCH_RADIUS_ARCSEC:
            return int(np.ravel(index)[0])
        return None

    @staticmethod
    def _brightest_entry_index(result_table: Any, candidate_indices: np.ndarray) -> int | None:
        """Find the brightest of some catalog entries.

        Parameters
        ----------
        result_table : `astropy.table.Table`
            The SIMBAD entries.
        candidate_indices : `numpy.ndarray`
            The rows to compare.

        Returns
        -------
        index : `int` or `None`
            The row with the smallest V magnitude, or `None` when no
            candidate has a V magnitude.
        """
        brightest_index: int | None = None
        brightest_magnitude = math.inf
        for candidate in candidate_indices:
            magnitude = _read_catalog_magnitude(result_table[int(candidate)], _SIMBAD_V_MAGNITUDE_COLUMNS)
            if magnitude is not None and magnitude < brightest_magnitude:
                brightest_index = int(candidate)
                brightest_magnitude = magnitude
        return brightest_index

    @staticmethod
    def _filter_stellar_rows(result_table):  # ruff: ignore[missing-return-type-static-method, missing-type-function-argument]
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

        def _is_masked(value):  # ruff: ignore[missing-return-type-private-function, missing-type-function-argument]
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

    def _apply_simbad_match(self, stellar_object, match, ra, dec) -> None:  # ruff: ignore[missing-type-function-argument]
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

        # Map SIMBAD flux (magnitude); stays None when SIMBAD has no V value.
        magnitude = _read_catalog_magnitude(match, ["V", "FLUX_V", "flux_v", "flux(V)"])
        blue_magnitude = _read_catalog_magnitude(match, ["B", "FLUX_B", "flux_b", "flux(B)"])

        stellar_object.simbad_object_types = read_simbad_object_types(match)
        stellar_object.name = common_name if common_name else str(main_id)
        stellar_object.id = str(main_id)
        stellar_object.spectral_type = str(spectral_type)
        stellar_object.stellar_spectral_type = str(spectral_type)
        stellar_object.magnitude = magnitude
        stellar_object.b_minus_v = (
            blue_magnitude - magnitude if blue_magnitude is not None and magnitude is not None else None
        )
        stellar_object.right_ascension = float(ra)
        stellar_object.declination = float(dec)
        stellar_object.is_catalog_identified = True
        magnitude_text = f"{magnitude:.2f}" if magnitude is not None else "unknown"
        logger.info(
            "  Identified: %s (%s, mag: %s) at %.4f, %.4f",
            stellar_object.name,
            stellar_object.spectral_type,
            magnitude_text,
            ra,
            dec,
        )

    def _apply_gaia_match(self, stellar_object: StellarObject, match: Any, ra: float, dec: float) -> None:
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

        # Stays None when Gaia has no G-band value for this star.
        magnitude = _read_catalog_magnitude(match, ["phot_g_mean_mag", "PHOT_G_MEAN_MAG", "g_mag"])

        stellar_object.name = source_id
        stellar_object.id = source_id
        stellar_object.spectral_type = "Unknown"
        stellar_object.stellar_spectral_type = "Unknown"
        stellar_object.magnitude = magnitude
        stellar_object.right_ascension = float(ra)
        stellar_object.declination = float(dec)
        stellar_object.is_catalog_identified = True
        magnitude_text = f"{magnitude:.2f}" if magnitude is not None else "unknown"
        logger.info(
            "  Identified (Gaia): %s (mag: %s) at %.4f, %.4f", stellar_object.name, magnitude_text, ra, dec
        )
