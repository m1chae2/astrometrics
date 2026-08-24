"""Populate the local Gaia DR3 cache ahead of time, from a target catalog.

`star_identifier` already prefers a local SQLite cache over ESA's Gaia
TAP service and keeps using that cache after its remote circuit breaker
trips. What it lacks is a way to *fill* the cache deliberately: regions
are only written as a side effect of a remote query that happened to
succeed, so the first run over a new field still depends on the network
at the worst possible moment -- inside a parallel batch, where several
worker processes query at once.

That concurrency is the actual failure mode. On the 2026-08-24 batch run
six worker processes issued 0.8-degree cone searches simultaneously and
drew timeouts and HTTP 500s until the breaker latched off, leaving Moon
with "0 catalog-matched, 100 position-only"; the same service answered a
single serial query in 3.4 seconds minutes later. Seeding therefore runs
*serially*, with a pause between requests, outside any batch run.

Field centers come from the frames themselves rather than from
`Target.ra`/`Target.dec`, which are free-text sexagesimal strings that
are frequently unset (all 45 targets in the 2026-08-24 catalog held the
placeholder ``0h 0m 0s``). Reading them from headers costs no network
and works for any observer's data, so only the Gaia download itself
needs connectivity.
"""

import logging
import math
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_FIELD_RADIUS_DEGREES",
    "DEFAULT_MAGNITUDE_LIMIT",
    "derive_field_centers",
    "seed_local_gaia_catalog",
    "summarize_local_catalog_coverage",
]


# A 0.8-degree radius covers the widest field this library is used with:
# the ASI533MM's 3008 pixels at 1.9153 arcsec/pixel span 1.6 degrees, so
# a half-diagonal of 0.8 degrees reaches every corner. It also matches
# the radius already present in every `cached_regions` row written by
# `astrometry_pipeline`, so seeded regions share the existing key format
# rather than creating a second, near-duplicate set.
DEFAULT_FIELD_RADIUS_DEGREES = 0.8

# Gaia DR3 is essentially complete to G=20, but sources fainter than the
# stacks' own detection limit only inflate the cache. G=18 is the
# threshold `star_identifier._seed_gaia_cache_for_field` has always
# used; keeping it identical means a seeded region and an
# opportunistically cached one hold the same population.
DEFAULT_MAGNITUDE_LIMIT = 18.0

# Two pointings closer together than half the field radius overlap so
# heavily that a second download would return mostly rows already
# stored. Deduplicating at 0.4 degrees collapsed the 2026-08-24
# catalog's per-frame pointings (which drift by a few arcmin between
# sessions on the same object) without merging genuinely distinct
# fields.
DEFAULT_DEDUPLICATION_SEPARATION_DEGREES = DEFAULT_FIELD_RADIUS_DEGREES / 2

# Serial pacing is the whole point of this module -- see the module
# docstring. Two seconds between requests keeps a full catalog sweep to
# a couple of minutes of added delay while staying far below the request
# rate that drew HTTP 500s from the TAP service under six-way
# concurrency.
DEFAULT_REQUEST_DELAY_SECONDS = 2.0

# Matches the retry shape `plate_solver._call_with_transient_retry`
# already uses for the same class of problem (a transient remote
# failure), so both remote services behave the same way under a blip.
DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 2.0


def _coordinate_from_header(header: Any) -> tuple[float, float] | None:
    """Read a field center in degrees from one FITS header.

    Prefers the solved WCS reference point, since a plate-solved
    ``CRVAL1``/``CRVAL2`` pair describes where the camera actually
    looked rather than where the mount believed it was pointing. The
    decimal ``RA``/``DEC`` pair and the sexagesimal
    ``OBJCTRA``/``OBJCTDEC`` pair are progressively weaker fallbacks
    for frames that were never solved.

    Parameters
    ----------
    header : `Any`
        FITS header to read.

    Returns
    -------
    center : `tuple` [`float`, `float`] or `None`
        ``(right_ascension_degrees, declination_degrees)``, or `None`
        when the header carries no usable pointing.
    """
    right_ascension = header.get("CRVAL1")
    declination = header.get("CRVAL2")
    if right_ascension is not None and declination is not None:
        try:
            return float(right_ascension), float(declination)
        except TypeError, ValueError:
            pass

    right_ascension = header.get("RA")
    declination = header.get("DEC")
    if right_ascension is not None and declination is not None:
        try:
            return float(right_ascension), float(declination)
        except TypeError, ValueError:
            pass

    right_ascension = header.get("OBJCTRA")
    declination = header.get("OBJCTDEC")
    if right_ascension is None or declination is None:
        return None
    try:
        from astropy.coordinates import SkyCoord

        sky_coordinate = SkyCoord(f"{right_ascension} {declination}", unit=("hourangle", "deg"))
        return float(sky_coordinate.ra.deg), float(sky_coordinate.dec.deg)
    except Exception as coordinate_error:
        logger.debug("Could not parse OBJCTRA/OBJCTDEC pair: %s", coordinate_error)
        return None


def _angular_separation_degrees(
    first_right_ascension: float,
    first_declination: float,
    second_right_ascension: float,
    second_declination: float,
) -> float:
    """Return the great-circle separation between two sky positions.

    Uses the haversine form rather than a plain coordinate difference
    so that right-ascension wrap-around at 0h and convergence of the
    meridians near the pole are both handled -- Polaris sits in this
    library's own catalog, where a naive difference is meaningless.

    Parameters
    ----------
    first_right_ascension, first_declination : `float`
        First position in degrees.
    second_right_ascension, second_declination : `float`
        Second position in degrees.

    Returns
    -------
    separation_degrees : `float`
        Angular separation in degrees.
    """
    first_right_ascension_radians = math.radians(first_right_ascension)
    second_right_ascension_radians = math.radians(second_right_ascension)
    first_declination_radians = math.radians(first_declination)
    second_declination_radians = math.radians(second_declination)

    declination_difference = second_declination_radians - first_declination_radians
    right_ascension_difference = second_right_ascension_radians - first_right_ascension_radians
    haversine = (
        math.sin(declination_difference / 2) ** 2
        + math.cos(first_declination_radians)
        * math.cos(second_declination_radians)
        * math.sin(right_ascension_difference / 2) ** 2
    )
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(haversine))))


def derive_field_centers(
    targets: list[Any],
    max_frames_per_target: int = 12,
    separation_threshold_degrees: float = DEFAULT_DEDUPLICATION_SEPARATION_DEGREES,
) -> list[dict[str, Any]]:
    """Work out which sky positions a catalog's frames actually cover.

    Reads pointing from FITS headers only, so this is entirely local
    and needs no network and no name resolution. Positions closer
    together than `separation_threshold_degrees` are merged, because a
    seeded region already covers its neighbours and re-downloading them
    would return rows that are mostly stored already.

    Parameters
    ----------
    targets : `list`
        Target objects whose ``frames`` carry readable ``path``
        attributes.
    max_frames_per_target : `int`, optional
        How many of each target's frames to read before moving on
        (default 12). A target's frames sit within a few arcmin of one
        another, so reading all of them would cost time without
        producing new field centers; the cap keeps a 4,000-frame
        catalog to a few seconds.
    separation_threshold_degrees : `float`, optional
        Positions closer than this are treated as one field.

    Returns
    -------
    field_centers : `list` [`dict`]
        One entry per distinct field, each with ``right_ascension_deg``,
        ``declination_deg``, ``target_ids`` (`list` [`str`]), and
        ``frames_examined`` (`int`).
    """
    from astropy.io import fits

    field_centers: list[dict[str, Any]] = []

    for target in targets:
        target_id = getattr(target, "id", "unknown")
        frames = getattr(target, "frames", None) or []
        for frame in frames[:max_frames_per_target]:
            frame_path = getattr(frame, "path", None)
            if not frame_path:
                continue
            try:
                header = fits.getheader(frame_path)
            except Exception as header_error:
                logger.debug("Could not read header for %s: %s", frame_path, header_error)
                continue

            center = _coordinate_from_header(header)
            if center is None:
                continue
            right_ascension_degrees, declination_degrees = center

            # A pointing of exactly (0, 0) is the placeholder written
            # when a mount reports nothing, not a real field in Cetus.
            # `_seed_gaia_cache_for_field` rejects it too.
            # ruff: ignore[float-equality-comparison]
            if right_ascension_degrees == 0.0 and declination_degrees == 0.0:
                continue

            for existing_center in field_centers:
                if (
                    _angular_separation_degrees(
                        existing_center["right_ascension_deg"],
                        existing_center["declination_deg"],
                        right_ascension_degrees,
                        declination_degrees,
                    )
                    <= separation_threshold_degrees
                ):
                    existing_center["frames_examined"] += 1
                    if target_id not in existing_center["target_ids"]:
                        existing_center["target_ids"].append(target_id)
                    break
            else:
                field_centers.append({
                    "right_ascension_deg": right_ascension_degrees,
                    "declination_deg": declination_degrees,
                    "target_ids": [target_id],
                    "frames_examined": 1,
                })

    return field_centers


def summarize_local_catalog_coverage(config: Any = None) -> dict[str, Any]:
    """Report what the local Gaia cache currently holds.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        Configuration locating the catalog directory; loaded from the
        application configuration when omitted.

    Returns
    -------
    coverage : `dict`
        ``cache_path`` (`str`), ``exists`` (`bool`), ``source_count``
        (`int`), ``region_count`` (`int`), and ``size_megabytes``
        (`float`).
    """
    import os
    import sqlite3

    if config is None:
        from astrometricslib.utilities.config_loader import get_configuration

        config = get_configuration()

    cache_path = config.get_library_path() / "catalogs" / "catalog_cache.db"
    coverage: dict[str, Any] = {
        "cache_path": str(cache_path),
        "exists": os.path.exists(cache_path),
        "source_count": 0,
        "region_count": 0,
        "size_megabytes": 0.0,
    }
    if not coverage["exists"]:
        return coverage

    coverage["size_megabytes"] = round(os.path.getsize(cache_path) / 1_000_000, 2)
    try:
        connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        try:
            coverage["source_count"] = connection.execute("SELECT COUNT(*) FROM gaia_sources").fetchone()[0]
            coverage["region_count"] = connection.execute("SELECT COUNT(*) FROM cached_regions").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as cache_error:
        # A cache that has never been written has no tables yet, which
        # is an empty result rather than a failure worth raising.
        logger.debug("Could not read local catalog cache: %s", cache_error)

    return coverage


def seed_local_gaia_catalog(
    targets: list[Any],
    radius_degrees: float = DEFAULT_FIELD_RADIUS_DEGREES,
    magnitude_limit: float = DEFAULT_MAGNITUDE_LIMIT,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Download Gaia sources for every field a target catalog covers.

    Safe to re-run: `_seed_gaia_cache_for_field` records each region it
    writes and returns early for one already stored, so a sweep
    interrupted halfway resumes rather than re-downloading.

    Requests are issued one at a time with a pause between them. That is
    deliberate and is the reason this exists as a separate step instead
    of running inside the batch -- see the module docstring.

    Parameters
    ----------
    targets : `list`
        Target objects to derive field centers from.
    radius_degrees : `float`, optional
        Cone-search radius per field.
    magnitude_limit : `float`, optional
        Faintest Gaia G magnitude to store.
    request_delay_seconds : `float`, optional
        Pause between consecutive remote requests.
    max_attempts : `int`, optional
        Attempts per field before giving up on it and moving to the
        next; one unreachable field must not abandon the sweep.
    progress_callback : `Callable`, optional
        Called with each field's result dict as it completes, for
        callers that want to report progress as it happens.

    Returns
    -------
    report : `dict`
        ``fields_total``, ``fields_seeded``, ``fields_already_cached``,
        ``fields_failed`` (`int`), ``sources_cached`` (`int`),
        ``results`` (`list` [`dict`], one per field), and ``coverage``
        (`dict`, the post-run `summarize_local_catalog_coverage`).
    """
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

    field_centers = derive_field_centers(targets)
    report: dict[str, Any] = {
        "fields_total": len(field_centers),
        "fields_seeded": 0,
        "fields_already_cached": 0,
        "fields_failed": 0,
        "sources_cached": 0,
        "results": [],
    }

    coverage_before = summarize_local_catalog_coverage()
    sources_before = coverage_before["source_count"]

    for field_index, field_center in enumerate(field_centers):
        right_ascension_degrees = field_center["right_ascension_deg"]
        declination_degrees = field_center["declination_deg"]
        field_result: dict[str, Any] = {
            "right_ascension_deg": right_ascension_degrees,
            "declination_deg": declination_degrees,
            "target_ids": field_center["target_ids"],
            "status": "failed",
            "sources": 0,
            "error": None,
        }

        for attempt_number in range(1, max_attempts + 1):
            try:
                cached_count = StarIdentifier._seed_gaia_cache_for_field(
                    right_ascension_degrees,
                    declination_degrees,
                    radius_deg=radius_degrees,
                    max_magnitude=magnitude_limit,
                )
                field_result["status"] = "seeded"
                field_result["sources"] = int(cached_count or 0)
                break
            except Exception as seeding_error:
                field_result["error"] = str(seeding_error)
                if attempt_number < max_attempts:
                    time.sleep(_RETRY_BACKOFF_BASE_SECONDS * attempt_number)

        if field_result["status"] == "seeded":
            report["fields_seeded"] += 1
            report["sources_cached"] += field_result["sources"]
        else:
            report["fields_failed"] += 1
            logger.warning(
                "Could not seed field at RA %.4f Dec %.4f: %s",
                right_ascension_degrees,
                declination_degrees,
                field_result["error"],
            )

        report["results"].append(field_result)
        if progress_callback is not None:
            try:
                progress_callback(field_result)
            except Exception as callback_error:
                logger.debug("Seeding progress callback raised: %s", callback_error)

        # Skipped after the final field so the sweep does not end on a
        # pause that buys nothing.
        if request_delay_seconds > 0 and field_index < len(field_centers) - 1:
            time.sleep(request_delay_seconds)

    coverage_after = summarize_local_catalog_coverage()
    report["coverage"] = coverage_after
    # A field whose region row already existed returns its stored rows
    # without adding any, so "already cached" is what the sweep reports
    # as seeded minus what actually grew the table.
    if coverage_after["source_count"] == sources_before and report["fields_seeded"]:
        report["fields_already_cached"] = report["fields_seeded"]

    return report
