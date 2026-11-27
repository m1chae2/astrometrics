"""Download the Gaia DR3 stars the Planetarium draws, once, to disk.

The Planetarium shows faint stars from a copy of Gaia DR3 kept on this
computer instead of asking the internet each time you pan or zoom. This file
builds that copy. It is run once, by hand, from the command line (see
`wayfindinglib.scripts.build_deep_star_catalog`).

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
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from astrometricslib.drivers import build_pixel_query, get_gaia_archive, pixel_source_id_range, require_online
from astrometricslib.exceptions import AstrometricsError, ExternalServiceError, InvalidArgumentError
from astrometricslib.parameters import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib._internal.errors import ONLINE_QUERY_ERRORS
from wayfindinglib.drivers.catalog import deep_star_store

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HEALPIX_LEVEL",
    "DEFAULT_MAGNITUDE_LIMIT",
    "MEASURED_BYTES_PER_STAR",
    "build_deep_star_catalog",
    "estimate_catalog_size",
]

# The faintest Gaia G magnitude that is downloaded.
#
# Derivation: the value is tuned from this observatory's stored photometry
# (`magnitude_limit_tuning`). The stored stars carry no per-star magnitude
# error, so the tuner takes the 95th percentile of the Gaia G magnitudes of the
# stars that have a measured light curve and rounds it up to the next half
# magnitude: the download then reaches as deep as the stacks measure. The
# default is the earlier design estimate: for a 30 s exposure with the 75 mm
# Apertura 75Q and the ASI533MM Pro (1.92 arcsec per pixel), a
# back-of-envelope signal-to-noise estimate gives photometry good to about 5
# percent (signal-to-noise 20) at about G = 15.7, assuming a sky of about
# 21 mag/arcsec^2, so it is uncertain by about half a magnitude.
# Validation: on 2026-11-15 the library's 14,549 Gaia stars with a measured
# light curve (read-only) gave a 95th percentile of G = 17.14, so G = 17.5;
# the M 13 field has photometry down to G = 17-18, but only from stacked
# frames, which reach deeper than one 30 s frame. DEFAULT_MAGNITUDE_LIMIT in
# astrometricslib's catalog_seeding.py (G = 18) is already described there as
# beyond what the stacks can detect. The Planetarium reads the depth back from
# the catalog itself; DEEP_STAR_MAX_MAGNITUDE in the UI's StarOverlay.ts only
# mirrors this default for the moments before it has done so. Register entry
# WAY-D132.
DEFAULT_MAGNITUDE_LIMIT = TunedParameter(
    name="wayfinding.default_magnitude_limit",
    default=16.0,
    unit="Gaia G magnitude",
    description="The faintest Gaia G magnitude that is downloaded for the deep star catalog.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="WAY-D132",
    allowed_range=(12.0, 20.0),
    minimum_sample=200,
)

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
# DEFAULT_REQUEST_DELAY_SECONDS in astrometricslib's catalog_seeding.py,
# which was chosen to stay well under the rate that drew HTTP 500 errors
# from ESA under concurrent use.
DEFAULT_REQUEST_DELAY_SECONDS = 2.0

# How many times one chunk is tried before it is given up on and skipped.
# Matches DEFAULT_MAX_ATTEMPTS in astrometricslib's catalog_seeding.py.
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


def _run_query(gaia: Any, query: str, timeout_seconds: float) -> Any:
    """Send one query to the archive and wait for the answer.

    Gives up with a `TimeoutError` if the archive has not answered in time.

    Parameters
    ----------
    gaia : `Any`
        The archive connection (`get_gaia_archive`).
    query : `str`
        The ADQL text to send.
    timeout_seconds : `float`
        How long to wait before giving up.

    Returns
    -------
    table : `astropy.table.Table`
        The result table.

    Notes
    -----
    When this launch sends nothing off the computer, `require_online` raises
    `OfflineModeError`, a `ConnectionError`. The callers count it as the
    archive being down.
    """
    require_online("the Gaia archive")

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
        except (
            AstrometricsError,
            sqlite3.Error,
            *ONLINE_QUERY_ERRORS,
            KeyError,
            TypeError,
        ) as download_error:
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
    magnitude_limit: float | None = None,
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
        Only stars brighter than this Gaia G magnitude are downloaded. The
        default is `DEFAULT_MAGNITUDE_LIMIT`.
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
        The archive connection; the gateway's ESA Gaia archive when omitted.
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
        ``elapsed_s``, and ``stopped_early`` (true if the archive
        seemed to be down so the run stopped, or `maximum_pixels` was hit).

    Raises
    ------
    InvalidArgumentError
        If an earlier catalog was made with other settings, or `pixels`
        names a chunk outside the sky.
    """
    if magnitude_limit is None:
        magnitude_limit = resolve_parameter(DEFAULT_MAGNITUDE_LIMIT).value

    if gaia is None:
        gaia = get_gaia_archive()

    sky_pixel_count = 12 * 4**healpix_level
    if pixels is None:
        wanted_pixels = list(range(sky_pixel_count))
    else:
        wanted_pixels = sorted(set(pixels))
        outside_the_sky = [pixel for pixel in wanted_pixels if not 0 <= pixel < sky_pixel_count]
        if outside_the_sky:
            raise InvalidArgumentError(
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
        "elapsed_s": 0.0,
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
            except Exception:
                # The callback is the caller's code and may raise anything. A
                # broken progress report must not stop the download, so the
                # error is logged with its traceback and the build goes on.
                logger.exception("Progress callback raised")

        if consecutive_failures >= _CONSECUTIVE_FAILURE_LIMIT:
            logger.error(
                "%d chunks failed in a row; stopping. Run again later to resume.", consecutive_failures
            )
            report["stopped_early"] = True
            break

        # No pause after the last chunk, since there is nothing to pace.
        if request_delay_seconds > 0 and position < len(remaining) - 1:
            sleep(request_delay_seconds)

    report["elapsed_s"] = time.monotonic() - started_at
    return report


def estimate_catalog_size(
    healpix_level: int = DEFAULT_HEALPIX_LEVEL,
    magnitude_limit: float | None = None,
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
        Only stars brighter than this Gaia G magnitude are counted. The
        default is `DEFAULT_MAGNITUDE_LIMIT`.
    sample_count : `int`, optional
        How many chunks to count.
    query_timeout_seconds : `float`, optional
        Seconds to wait for the archive on each count.
    request_delay_seconds : `float`, optional
        Seconds to pause between requests.
    gaia : `Any`, optional
        The archive connection; the gateway's ESA Gaia archive when omitted.
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
    ExternalServiceError
        If no chunk could be counted.
    """
    if magnitude_limit is None:
        magnitude_limit = resolve_parameter(DEFAULT_MAGNITUDE_LIMIT).value

    if gaia is None:
        gaia = get_gaia_archive()

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
        except (AstrometricsError, *ONLINE_QUERY_ERRORS, KeyError, IndexError, TypeError) as count_error:
            logger.warning("Could not count pixel %d: %s", pixel, count_error)
        if request_delay_seconds > 0 and position < len(sampled_pixels) - 1:
            sleep(request_delay_seconds)

    if not counts:
        raise ExternalServiceError(
            "None of the sample counts succeeded, so there is nothing to estimate from."
        )

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
