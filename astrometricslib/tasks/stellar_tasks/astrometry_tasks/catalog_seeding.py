"""Tool to download the star database before we start processing.

If we try to download star data while we're processing 6 images at
once, we overwhelm the online database and it kicks us out. This tool
looks at all our photos, figures out where they are pointing, and
downloads the stars for those areas one by one before we start the
heavy lifting.
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
    """Find out where the photo was pointing by reading its metadata.

    We try the most accurate coordinates first (if the image has already
    been mapped), then fall back to whatever the telescope thought it
    was looking at.

    Parameters
    ----------
    header : `Any`
        The metadata from the image file.

    Returns
    -------
    center : `tuple` [`float`, `float`] or `None`
        The center coordinates (Right Ascension, Declination), or None
        if we couldn't find them.
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
    """Calculate the distance between two points in the sky.

    We have to use complex spherical math (haversine) instead of simple
    subtraction because the sky is a globe. For example, if you are near
    the North Star, the lines of longitude are very close together.

    Parameters
    ----------
    first_right_ascension, first_declination : `float`
        First point in degrees.
    second_right_ascension, second_declination : `float`
        Second point in degrees.

    Returns
    -------
    separation_degrees : `float`
        The distance between them in degrees.
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
    """Make a list of all the unique places we took pictures of.

    If we took 100 pictures of the exact same spot, we combine them into
    one spot so we don't download the same star map 100 times. We also
    stop checking after the first few photos of a target to save time,
    since a telescope rarely moves far while shooting one object.

    Parameters
    ----------
    targets : `list`
        The list of targets (folders of photos) we plan to process.
    max_frames_per_target : `int`, optional
        How many photos to check per target before we get the idea.
    separation_threshold_degrees : `float`, optional
        If two spots are closer than this, we treat them as the same spot.

    Returns
    -------
    field_centers : `list` [`dict`]
        A clean list of the unique sky coordinates we need to download.
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
    """Check how many stars we already have saved on our hard drive.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The system settings (so we know where the database file is).

    Returns
    -------
    coverage : `dict`
        Stats about our database: where it is, how many stars it holds,
        and how much disk space it takes up.
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
    """Go through the list of spots and download the stars for each one.

    If we get interrupted, we can just run this again and it will skip
    the ones we already downloaded. It pauses between downloads so we
    don't get banned from the server.

    Parameters
    ----------
    targets : `list`
        The list of targets (folders of photos) we plan to process.
    radius_degrees : `float`, optional
        How wide of an area to download around each spot.
    magnitude_limit : `float`, optional
        How dim of a star we care about (ignore the super faint ones).
    request_delay_seconds : `float`, optional
        How long to wait between downloads so we don't overwhelm the server.
    max_attempts : `int`, optional
        How many times to retry if the server gives us an error.
    progress_callback : `Callable`, optional
        A function we can call to update a loading bar on the screen.

    Returns
    -------
    report : `dict`
        A summary of what we did: how many spots we checked, how many
        failed, and how many new stars we added to the database.
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
