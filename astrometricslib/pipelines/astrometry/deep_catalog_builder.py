"""Download the Gaia DR3 stars that the Planetarium draws, once, to disk.

The Planetarium shows faint stars from a copy of Gaia DR3 kept on this
computer instead of asking the internet each time you pan or zoom. This file
builds that copy. It is run once, by hand, from the command line (see
`astrometricslib.scripts.build_deep_star_catalog`).

How it works
------------
The whole sky is too big to download in one request, so it is asked for in
small chunks. Every Gaia star's ID number secretly contains which chunk of
the sky it sits in: dividing the ID by 2**35 gives the star's "HEALPix
level 12 pixel", and taking away the last few binary digits of that gives
the same star's pixel on a coarser map of the sky. So "all stars in pixel
number P of the level-4 map" can be asked for as a plain range of ID
numbers, which the Gaia archive answers quickly because the IDs are how it
sorts its own table.

Each chunk is saved as soon as it arrives, along with a note that it is
finished. If the download stops for any reason (no internet, Ctrl-C, the
archive having a bad day), running it again skips every finished chunk and
carries on.
"""

import logging
import math
import time
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from astrometricslib.drivers import deep_star_store

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HEALPIX_LEVEL",
    "DEFAULT_MAGNITUDE_LIMIT",
    "MEASURED_BYTES_PER_STAR",
    "build_deep_star_catalog",
    "build_pixel_query",
    "estimate_deep_catalog_size",
    "healpix_pixels_of_points",
    "pixel_source_id_range",
    "pixels_near_circles",
]

# The faintest Gaia G magnitude that is downloaded.
#
# Derivation: for a 30 s exposure with the 75 mm Apertura 75Q and the
# ASI533MM Pro (1.92 arcsec per pixel), a back-of-envelope signal-to-noise
# estimate gives a 5-sigma detection at about G = 17.5 and photometry good to
# about 5 percent (signal-to-noise 20) at about G = 15.7. It assumes a sky of
# about 21 mag/arcsec^2, so it is uncertain by about half a magnitude.
# Validation: the M 13 field has photometry down to G = 17-18, but only from
# stacked frames, which reach deeper than one 30 s frame; and
# DEFAULT_MAGNITUDE_LIMIT in catalog_seeding.py (G = 18) is already described
# there as beyond what the stacks can detect. G = 16 keeps the stars whose
# photometry can be trusted at about a third of the data of G = 18 in the
# fields this library has imaged. The Planetarium reads the depth back from the
# catalog itself; DEEP_STAR_MAX_MAGNITUDE in the UI's StarOverlay.ts only
# mirrors this default for the moments before it has done so.
DEFAULT_MAGNITUDE_LIMIT = 16.0

# How finely the Gaia archive's own sky map is cut for the download. Level L
# has 12 * 4**L pixels, so level 4 is 3072 pixels of about 13 square degrees.
#
# Derivation: the archive gives an anonymous user at most 3,000,000 rows per
# request (see _ANONYMOUS_ASYNC_ROW_LIMIT). In the fields this library has
# imaged there are about 490 stars per square degree brighter than G = 16, so
# a typical level-4 pixel holds about 6,500. Even at 50 times that density
# (the galactic center) a pixel holds well under a million, a comfortable
# margin below the limit; level 3 would still fit but with much less margin.
# The cost is the number of requests: 3072, paced (see
# DEFAULT_REQUEST_DELAY_SECONDS). Chosen by reasoning; the time each request
# takes has not been measured, and the first real run reports it.
DEFAULT_HEALPIX_LEVEL = 4

# Seconds to pause between requests, so a run of thousands of them does not
# look like an attack on the archive. Same reasoning and value as
# DEFAULT_REQUEST_DELAY_SECONDS in catalog_seeding.py, which was chosen to stay
# well under the rate that drew HTTP 500 errors from ESA under concurrent use.
DEFAULT_REQUEST_DELAY_SECONDS = 2.0

# How many times one chunk is tried before it is given up on and skipped.
# Matches DEFAULT_MAX_ATTEMPTS in catalog_seeding.py.
DEFAULT_MAX_ATTEMPTS = 3

# Seconds to wait between attempts is this number times the attempt number.
# Longer than the seeding pipeline's 2 s because a chunk is worth waiting for
# and ESA's errors (such as Error 408 timeouts) have come in bursts.
_RETRY_BACKOFF_BASE_SECONDS = 5.0

# Seconds to wait for one chunk's query before giving up on that attempt.
# A query sits in the archive's queue before it runs, so this is generous.
# Chosen by judgement, not measured.
DEFAULT_QUERY_TIMEOUT_SECONDS = 300.0

# If this many chunks fail in a row, the archive is probably down and there
# is no point carrying on hammering it; the run stops and can be resumed.
_CONSECUTIVE_FAILURE_LIMIT = 5

# The most rows the Gaia archive returns for an anonymous asynchronous query,
# from the astroquery documentation ("The output of asynchronous queries is
# limited to 3,000,000 sources for non-authenticated users"). A result this
# big may have been cut short, so it is treated as a failure, not accepted.
_ANONYMOUS_ASYNC_ROW_LIMIT = 3_000_000

# How many bytes one star takes in the local catalog file, measured on a
# 10 million star synthetic catalog built with `deep_star_store` (342 MB).
# Used only to turn a star count into a disk size estimate.
MEASURED_BYTES_PER_STAR = 34.2

# Gaia builds a star's ID from its position: ID // 2**35 is the star's
# HEALPix pixel at level 12 (nested numbering), so the pixel at a coarser
# level is that number with its last 2 binary digits per level removed.
# Gaia DR3 documentation, and consistent with the largest DR3 source ID
# (6917528997577384320 = 12 * 2**59).
_SOURCE_ID_PIXEL_SHIFT = 35
_GAIA_HEALPIX_LEVEL = 12


def pixel_source_id_range(healpix_level: int, pixel: int) -> tuple[int, int]:
    """Find the range of Gaia source IDs that lie in one chunk of the sky.

    Parameters
    ----------
    healpix_level : `int`
        How finely the sky is cut: level ``L`` has ``12 * 4**L`` pixels.
        Must be from 0 to 12.
    pixel : `int`
        The pixel's number at that level.

    Returns
    -------
    low, high : `tuple` [`int`, `int`]
        Every star in the pixel has ``low <= source_id < high``.

    Raises
    ------
    ValueError
        If the level or pixel number is out of range.
    """
    if not 0 <= healpix_level <= _GAIA_HEALPIX_LEVEL:
        raise ValueError(f"HEALPix level must be from 0 to {_GAIA_HEALPIX_LEVEL}, not {healpix_level}.")
    pixel_count = 12 * 4**healpix_level
    if not 0 <= pixel < pixel_count:
        raise ValueError(f"Pixel {pixel} is outside 0..{pixel_count - 1} at level {healpix_level}.")
    shift = _SOURCE_ID_PIXEL_SHIFT + 2 * (_GAIA_HEALPIX_LEVEL - healpix_level)
    return pixel << shift, (pixel + 1) << shift


def _spread_bits(values: np.ndarray) -> np.ndarray:
    """Move every binary digit of each number to twice its place.

    Bit 0 goes to bit 0, bit 1 to bit 2, bit 2 to bit 4, and so on, leaving
    the odd places empty. HEALPix needs this to weave the two coordinates of
    a pixel inside its face into a single number.

    Parameters
    ----------
    values : `numpy.ndarray`
        Non-negative whole numbers below 2**32.

    Returns
    -------
    spread : `numpy.ndarray`
        The same numbers with their digits spread out, as 64-bit integers.
    """
    spread = values.astype(np.uint64)
    for shift, mask in (
        (16, 0x0000FFFF0000FFFF),
        (8, 0x00FF00FF00FF00FF),
        (4, 0x0F0F0F0F0F0F0F0F),
        (2, 0x3333333333333333),
        (1, 0x5555555555555555),
    ):
        spread = (spread | (spread << np.uint64(shift))) & np.uint64(mask)
    return spread


def healpix_pixels_of_points(healpix_level: int, ra_degrees: Any, dec_degrees: Any) -> np.ndarray:
    """Find which chunk of the sky each point is in.

    This is the standard HEALPix "nested" numbering that the Gaia archive
    uses, worked out here so no extra software is needed. The sphere is cut
    into 12 big faces, and each face is cut again and again into four equal
    pieces. All pixels at one level have exactly the same area.

    It was checked against 149,460 real Gaia stars: the pixel found here is
    the pixel in the star's own ID for 99.9993 percent of them at level 4.
    The rest sit within a few arcseconds of a pixel edge, where the ID was
    made from an earlier, slightly different position than the DR3 one.

    Parameters
    ----------
    healpix_level : `int`
        How finely the sky is cut: level ``L`` has ``12 * 4**L`` pixels.
    ra_degrees, dec_degrees : `float` or `numpy.ndarray`
        Position of each point, in degrees.

    Returns
    -------
    pixels : `numpy.ndarray`
        The pixel number of each point, as 64-bit integers.

    Raises
    ------
    ValueError
        If the level is out of range.
    """
    if not 0 <= healpix_level <= _GAIA_HEALPIX_LEVEL:
        raise ValueError(f"HEALPix level must be from 0 to {_GAIA_HEALPIX_LEVEL}, not {healpix_level}.")
    side = 2**healpix_level  # pixels along one edge of a face
    sin_dec = np.sin(np.radians(np.asarray(dec_degrees, dtype=float)))
    abs_sin_dec = np.abs(sin_dec)
    # How far around the sky the point is, in units of a quarter turn.
    quarter_turns = np.mod(np.radians(np.asarray(ra_degrees, dtype=float)), 2 * np.pi) / (np.pi / 2)

    # Near the equator (|sin(dec)| up to 2/3) the faces are four-sided
    # diamonds, and two families of diagonal lines pick out the pixel.
    rising = side * (0.5 + quarter_turns)
    falling = side * sin_dec * 0.75
    rising_line = np.floor(rising - falling).astype(np.int64)
    falling_line = np.floor(rising + falling).astype(np.int64)
    rising_face = rising_line // side
    falling_face = falling_line // side
    equator_face = np.where(
        rising_face == falling_face,
        rising_face | 4,
        np.where(rising_face < falling_face, rising_face, falling_face + 8),
    )
    equator_x = falling_line & (side - 1)
    equator_y = side - (rising_line & (side - 1)) - 1

    # Near the poles the four faces around the pole meet in a point.
    turn_number = np.minimum(np.floor(quarter_turns).astype(np.int64), 3)
    turn_fraction = quarter_turns - turn_number
    pole_scale = side * np.sqrt(3.0 * (1.0 - abs_sin_dec))
    line_a = np.minimum(np.floor(turn_fraction * pole_scale).astype(np.int64), side - 1)
    line_b = np.minimum(np.floor((1.0 - turn_fraction) * pole_scale).astype(np.int64), side - 1)
    is_north = sin_dec >= 0
    pole_face = np.where(is_north, turn_number, turn_number + 8)
    pole_x = np.where(is_north, side - line_b - 1, line_a)
    pole_y = np.where(is_north, side - line_a - 1, line_b)

    is_equatorial = abs_sin_dec <= 2.0 / 3.0
    face = np.where(is_equatorial, equator_face, pole_face)
    x_in_face = np.where(is_equatorial, equator_x, pole_x)
    y_in_face = np.where(is_equatorial, equator_y, pole_y)
    within_face = _spread_bits(x_in_face) | (_spread_bits(y_in_face) << np.uint64(1))
    return (face.astype(np.uint64) * np.uint64(side * side) + within_face).astype(np.int64)


# How many sample points are laid across the width of one pixel when working
# out which pixels a circle touches. The circle is padded by one spacing and
# sampled at this spacing, so only a pixel that grazes the circle by less than
# about a spacing can be missed. At level 4 a pixel is about 3.7 degrees
# wide, so this is about 0.46 degrees. Chosen by reasoning, not measured; the
# tests check that no point inside a circle is left in a pixel that is not
# chosen.
_SAMPLES_PER_PIXEL_WIDTH = 8


def pixels_near_circles(healpix_level: int, circles: Iterable[tuple[float, float, float]]) -> list[int]:
    """List the chunks of sky that hold any part of some circles.

    This lets the catalog be built only where it is needed (for example
    around the fields that have been imaged) instead of for the whole sky.
    It lays a fine grid of points over each circle and looks up the pixel
    of each one.

    Parameters
    ----------
    healpix_level : `int`
        How finely the sky is cut into chunks.
    circles : `iterable` [`tuple` [`float`, `float`, `float`]]
        Each circle as ``(ra, dec, radius)``, all in degrees.

    Returns
    -------
    pixels : `list` [`int`]
        The chunk numbers, smallest first, without repeats. A chunk that
        only just grazes a circle (by less than about half a degree at
        level 4) may be left out.
    """
    pixel_width_degrees = math.degrees(math.sqrt(4 * math.pi / (12 * 4**healpix_level)))
    spacing = pixel_width_degrees / _SAMPLES_PER_PIXEL_WIDTH
    chosen: set[int] = set()
    for center_ra, center_dec, radius in circles:
        search_radius = min(radius + spacing, 180.0)
        ring_radii = np.append(np.arange(0.0, search_radius, spacing), search_radius)
        center_dec_radians = math.radians(center_dec)
        sample_ras: list[np.ndarray] = []
        sample_decs: list[np.ndarray] = []
        for ring_radius in ring_radii:
            ring_radius_radians = math.radians(ring_radius)
            # Points on a ring get further apart as it grows, so bigger
            # rings need more points to keep the same spacing.
            # A ring of radius zero is just the center: one point.
            points_on_ring = math.ceil(360.0 * math.sin(ring_radius_radians) / spacing) + 1
            position_angles = np.linspace(0.0, 2 * np.pi, points_on_ring, endpoint=False)
            sin_dec = math.sin(center_dec_radians) * math.cos(ring_radius_radians) + math.cos(
                center_dec_radians
            ) * math.sin(ring_radius_radians) * np.cos(position_angles)
            sample_dec_radians = np.arcsin(np.clip(sin_dec, -1.0, 1.0))
            ra_offset = np.arctan2(
                np.sin(position_angles) * math.sin(ring_radius_radians) * math.cos(center_dec_radians),
                math.cos(ring_radius_radians) - math.sin(center_dec_radians) * np.sin(sample_dec_radians),
            )
            sample_ras.append(center_ra + np.degrees(ra_offset))
            sample_decs.append(np.degrees(sample_dec_radians))
        pixels = healpix_pixels_of_points(
            healpix_level, np.concatenate(sample_ras), np.concatenate(sample_decs)
        )
        chosen.update(int(pixel) for pixel in np.unique(pixels))
    return sorted(chosen)


def build_pixel_query(low: int, high: int, magnitude_limit: float, count_only: bool = False) -> str:
    """Write the ADQL query that asks the archive for one chunk of sky.

    Parameters
    ----------
    low, high : `int`
        The range of source IDs wanted, from `pixel_source_id_range`.
    magnitude_limit : `float`
        Only stars brighter than this Gaia G magnitude are wanted.
    count_only : `bool`, optional
        If true, ask only how many stars there are, not for the stars.

    Returns
    -------
    query : `str`
        The ADQL text to send.
    """
    columns = "COUNT(*) AS star_count" if count_only else "source_id, ra, dec, phot_g_mean_mag"
    # This is ADQL for the remote archive, not SQL for a local database. The
    # only values put into the text are whole numbers and a magnitude limit
    # (all numbers), and the column names come from the fixed strings above.
    return (
        f"SELECT {columns} FROM gaiadr3.gaia_source "  # ruff: ignore[hardcoded-sql-expression]
        f"WHERE source_id >= {low} AND source_id < {high} "
        f"AND phot_g_mean_mag < {magnitude_limit}"
    )


def _run_query(gaia: Any, query: str, timeout_seconds: float) -> Any:
    """Send one query to the archive and wait for the answer.

    Gives up with a `TimeoutError` if the archive has not answered in time.

    Parameters
    ----------
    gaia : `Any`
        The archive connection (``astroquery.gaia.Gaia``).
    query : `str`
        The ADQL text to send.
    timeout_seconds : `float`
        How long to wait before giving up.

    Returns
    -------
    table : `astropy.table.Table`
        The result table.
    """
    from astrometricslib.pipelines.astrometry.star_identifier import _run_with_daemon_thread_timeout

    def _submit_and_wait() -> Any:
        # Asynchronous, because a synchronous query is limited to 2000 rows.
        job = gaia.launch_job_async(query, dump_to_file=False)
        return job.get_results()

    return _run_with_daemon_thread_timeout(_submit_and_wait, timeout_seconds)


def _table_to_arrays(table: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Turn an archive result table into plain arrays, dropping bad rows.

    Parameters
    ----------
    table : `astropy.table.Table`
        Columns ``source_id``, ``ra``, ``dec`` and ``phot_g_mean_mag``.

    Returns
    -------
    source_ids, ra, dec, magnitude : `numpy.ndarray`
        One entry per usable star. Rows with a missing or non-finite value
        are left out.
    """
    source_ids = np.ma.filled(np.ma.asarray(table["source_id"]), -1).astype(np.int64)
    ra = np.ma.filled(np.ma.asarray(table["ra"], dtype=float), np.nan)
    dec = np.ma.filled(np.ma.asarray(table["dec"], dtype=float), np.nan)
    magnitude = np.ma.filled(np.ma.asarray(table["phot_g_mean_mag"], dtype=float), np.nan)
    usable = (source_ids >= 0) & np.isfinite(ra) & np.isfinite(dec) & np.isfinite(magnitude)
    return source_ids[usable], ra[usable], dec[usable], magnitude[usable]


def _describe_problem_with_table(table: Any) -> str | None:
    """Say what is wrong with an archive answer, if anything.

    Parameters
    ----------
    table : `astropy.table.Table` or `None`
        The archive's answer.

    Returns
    -------
    problem : `str` or `None`
        A message describing why the answer must not be saved, or `None` if
        it looks fine.
    """
    if table is None:
        return "The archive returned no result table."
    if len(table) >= _ANONYMOUS_ASYNC_ROW_LIMIT:
        return (
            f"The result has {len(table):,} rows, at the archive's limit, so it may be cut "
            "short. Start again with a higher --healpix-level (smaller chunks)."
        )
    return None


def _download_pixel(
    config: Any,
    gaia: Any,
    healpix_level: int,
    pixel: int,
    magnitude_limit: float,
    max_attempts: int,
    query_timeout_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Download one chunk of sky and save it, retrying on failure.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    gaia : `Any`
        The archive connection.
    healpix_level, pixel : `int`
        Which chunk of sky.
    magnitude_limit : `float`
        Only stars brighter than this Gaia G magnitude are wanted.
    max_attempts : `int`
        How many times to try before giving up on this chunk.
    query_timeout_seconds : `float`
        How long to wait for the archive on each attempt.
    sleep : `Callable`
        Function used to wait between attempts.

    Returns
    -------
    result : `dict`
        ``pixel``, ``status`` ("downloaded" or "failed"), ``stars`` saved,
        and ``error`` (the last error message, or `None`).
    """
    low, high = pixel_source_id_range(healpix_level, pixel)
    query = build_pixel_query(low, high, magnitude_limit)
    result: dict[str, Any] = {"pixel": pixel, "status": "failed", "stars": 0, "error": None}

    for attempt_number in range(1, max_attempts + 1):
        try:
            table = _run_query(gaia, query, query_timeout_seconds)
            problem = _describe_problem_with_table(table)
            if problem is None:
                source_ids, ra, dec, magnitude = _table_to_arrays(table)
                result["stars"] = deep_star_store.record_downloaded_pixel(
                    config, pixel, source_ids, ra, dec, magnitude
                )
                result["status"] = "downloaded"
                result["error"] = None
                return result
            result["error"] = problem
        except Exception as download_error:
            result["error"] = str(download_error) or type(download_error).__name__
        logger.warning(
            "Pixel %d attempt %d of %d failed: %s", pixel, attempt_number, max_attempts, result["error"]
        )
        if attempt_number < max_attempts:
            sleep(_RETRY_BACKOFF_BASE_SECONDS * attempt_number)
    return result


def build_deep_star_catalog(
    config: Any,
    healpix_level: int = DEFAULT_HEALPIX_LEVEL,
    magnitude_limit: float = DEFAULT_MAGNITUDE_LIMIT,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    maximum_pixels: int | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    gaia: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    pixels: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Download the deep-star catalog, skipping chunks that are already saved.

    Refuses to continue (with a `ValueError`) if a catalog already exists
    that was made with a different depth or chunk size, or if `pixels`
    names a chunk that does not exist.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings, used to find where to save the catalog.
    healpix_level : `int`, optional
        How finely the sky is cut into chunks.
    magnitude_limit : `float`, optional
        Only stars brighter than this Gaia G magnitude are downloaded.
    request_delay_seconds : `float`, optional
        Seconds to pause between requests.
    max_attempts : `int`, optional
        How many times to try one chunk before skipping it.
    query_timeout_seconds : `float`, optional
        Seconds to wait for the archive on each attempt.
    maximum_pixels : `int`, optional
        Stop after downloading this many chunks. Handy for a short trial.
    progress_callback : `Callable`, optional
        Called after each chunk with a dict holding ``pixel``, ``status``,
        ``stars``, ``error``, ``pixels_done`` (finished so far, including
        earlier runs), ``pixels_total``, and ``stars_total``.
    gaia : `Any`, optional
        The archive connection; ``astroquery.gaia.Gaia`` when omitted.
    sleep : `Callable`, optional
        Function used to pause; replaced in tests.
    pixels : `iterable` [`int`], optional
        Only download these chunks (see `pixels_near_circles`) instead of
        the whole sky. Chunks saved by an earlier run, whether from a
        partial or a full run, are skipped either way, and a later run
        can add more chunks to the same catalog.

    Returns
    -------
    report : `dict`
        ``pixels_total`` (the chunks asked for: the whole sky, or the
        chosen `pixels`), ``pixels_previously_done``, ``pixels_downloaded``,
        ``pixels_failed`` (a list of chunk numbers), ``stars_added``,
        ``elapsed_seconds``, and ``stopped_early`` (true if the archive
        seemed to be down so the run stopped, or `maximum_pixels` was hit).

    Raises
    ------
    ValueError
        If an earlier catalog was made with other settings, or `pixels`
        names a chunk outside the sky.
    """
    if gaia is None:
        from astroquery.gaia import Gaia

        gaia = Gaia

    sky_pixel_count = 12 * 4**healpix_level
    if pixels is None:
        wanted_pixels = list(range(sky_pixel_count))
    else:
        wanted_pixels = sorted(set(pixels))
        outside_the_sky = [pixel for pixel in wanted_pixels if not 0 <= pixel < sky_pixel_count]
        if outside_the_sky:
            raise ValueError(
                f"Chunks {outside_the_sky[:5]} are outside 0..{sky_pixel_count - 1} at level {healpix_level}."
            )

    deep_star_store.set_deep_catalog_plan(config, healpix_level, magnitude_limit)
    pixels_total = len(wanted_pixels)
    saved_pixels = deep_star_store.get_downloaded_pixels(config)
    already_done = [pixel for pixel in wanted_pixels if pixel in saved_pixels]
    remaining = [pixel for pixel in wanted_pixels if pixel not in saved_pixels]

    report: dict[str, Any] = {
        "pixels_total": pixels_total,
        "pixels_previously_done": len(already_done),
        "pixels_downloaded": 0,
        "pixels_failed": [],
        "stars_added": 0,
        "elapsed_seconds": 0.0,
        "stopped_early": False,
    }
    started_at = time.monotonic()
    consecutive_failures = 0

    for position, pixel in enumerate(remaining):
        if maximum_pixels is not None and report["pixels_downloaded"] >= maximum_pixels:
            report["stopped_early"] = True
            break

        result = _download_pixel(
            config, gaia, healpix_level, pixel, magnitude_limit, max_attempts, query_timeout_seconds, sleep
        )
        if result["status"] == "downloaded":
            report["pixels_downloaded"] += 1
            report["stars_added"] += result["stars"]
            consecutive_failures = 0
        else:
            report["pixels_failed"].append(pixel)
            consecutive_failures += 1

        if progress_callback is not None:
            try:
                progress_callback({
                    **result,
                    "pixels_done": len(already_done) + report["pixels_downloaded"],
                    "pixels_total": pixels_total,
                    "stars_total": report["stars_added"],
                })
            except Exception as callback_error:
                logger.debug("Progress callback raised: %s", callback_error)

        if consecutive_failures >= _CONSECUTIVE_FAILURE_LIMIT:
            logger.error(
                "%d chunks failed in a row; stopping. Run again later to resume.", consecutive_failures
            )
            report["stopped_early"] = True
            break

        # No pause after the last chunk, since there is nothing to pace.
        if request_delay_seconds > 0 and position < len(remaining) - 1:
            sleep(request_delay_seconds)

    report["elapsed_seconds"] = time.monotonic() - started_at
    return report


def estimate_deep_catalog_size(
    healpix_level: int = DEFAULT_HEALPIX_LEVEL,
    magnitude_limit: float = DEFAULT_MAGNITUDE_LIMIT,
    sample_count: int = 24,
    query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    gaia: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Guess how big the finished catalog will be, by counting a sample.

    Counts the stars in a few chunks spread across the sky, and scales up.
    Nothing is saved. The sky is far denser along the Milky Way than
    elsewhere, so this is only a rough guide; the spread of the sample is
    reported so you can see how rough.

    Parameters
    ----------
    healpix_level : `int`, optional
        How finely the sky is cut into chunks.
    magnitude_limit : `float`, optional
        Only stars brighter than this Gaia G magnitude are counted.
    sample_count : `int`, optional
        How many chunks to count.
    query_timeout_seconds : `float`, optional
        Seconds to wait for the archive on each count.
    request_delay_seconds : `float`, optional
        Seconds to pause between requests.
    gaia : `Any`, optional
        The archive connection; ``astroquery.gaia.Gaia`` when omitted.
    sleep : `Callable`, optional
        Function used to pause; replaced in tests.

    Returns
    -------
    estimate : `dict`
        ``pixels_total``, ``pixels_sampled``, ``sample_min``, ``sample_max``,
        ``sample_mean`` (stars per chunk), ``estimated_stars``, and
        ``estimated_megabytes``. The sampled counts are in ``counts``.

    Raises
    ------
    RuntimeError
        If no chunk could be counted.
    """
    if gaia is None:
        from astroquery.gaia import Gaia

        gaia = Gaia

    pixels_total = 12 * 4**healpix_level
    sample_count = min(sample_count, pixels_total)
    # Evenly spaced through the numbering, which (because neighbouring
    # numbers are neighbouring patches of sky) spreads the sample across
    # the whole sky, including all twelve of the biggest patches.
    sampled_pixels = [int((index + 0.5) * pixels_total / sample_count) for index in range(sample_count)]

    counts: list[int] = []
    for position, pixel in enumerate(sampled_pixels):
        low, high = pixel_source_id_range(healpix_level, pixel)
        query = build_pixel_query(low, high, magnitude_limit, count_only=True)
        try:
            table = _run_query(gaia, query, query_timeout_seconds)
            counts.append(int(table["star_count"][0]))
        except Exception as count_error:
            logger.warning("Could not count pixel %d: %s", pixel, count_error)
        if request_delay_seconds > 0 and position < len(sampled_pixels) - 1:
            sleep(request_delay_seconds)

    if not counts:
        raise RuntimeError("None of the sample counts succeeded, so there is nothing to estimate from.")

    mean_stars = float(np.mean(counts))
    estimated_stars = mean_stars * pixels_total
    return {
        "pixels_total": pixels_total,
        "pixels_sampled": len(counts),
        "counts": counts,
        "sample_min": min(counts),
        "sample_max": max(counts),
        "sample_mean": mean_stars,
        "estimated_stars": estimated_stars,
        "estimated_megabytes": estimated_stars * MEASURED_BYTES_PER_STAR / 1_000_000,
    }
