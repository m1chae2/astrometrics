"""Tool to download the star database before processing starts.

Attempting to download star data while processing 6 images at
once overwhelms the online database and causes disconnects. This tool
looks at all photos, figures out where they are pointing, and
downloads the Gaia sky tiles around those places one field at a time
before starting the heavy lifting. Each field is filled to the depth its
frames' equipment predicts (see `gaia_depth.predict_gaia_depth_from_header`).
"""

import logging
import math
import sqlite3
import time
from collections.abc import Callable
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib.foundation.errors import AstrometricsError, ExternalServiceError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.astrometry.processing.gaia_depth import predict_gaia_depth_from_header
from astrometricslib.utilities.exceptions import DATA_ERRORS, ONLINE_QUERY_ERRORS

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_FIELD_RADIUS_DEGREES",
    "derive_field_centers",
    "seed_field_tiles",
    "seed_local_gaia_catalog",
]


# A 0.8-degree radius covers the widest field this library is used with:
# the ASI533MM's 3008 pixels at 1.9153 arcsec/pixel span 1.6 degrees, so
# a half-diagonal of 0.8 degrees reaches every corner.
# Stands for the radius of the catalog download around a field center. It
# follows the field of view: the value is a factor times the field of view
# the run measured (0.8 on the reference setup).
DEFAULT_FIELD_RADIUS_DEGREES = TunedParameter(
    name="astrometry.default_field_radius_degrees",
    default=factor_for(0.8, RunBasis.FIELD_OF_VIEW_DEG),
    unit="× field of view",
    description="Radius of the sky area downloaded around each field center.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.FIELD_OF_VIEW_DEG,
    reference_value=0.8,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="AST-D20",
)


# Two image centers that are very close to each other will look at mostly
# the same stars. If they are closer than half the field radius, we treat
# them as the same location to avoid downloading the same data twice.
# This successfully combines separate sessions of the same target (where
# the telescope pointing drifts slightly) without accidentally merging
# two different targets. The field radius follows the field of view, so this
# is a function and not a constant fixed at import.
def default_deduplication_separation_degrees() -> float:
    """Return half the field radius, below which two field centers merge.

    Returns
    -------
    separation_degrees : `float`
        Half of the current field radius, in degrees.
    """
    return float(resolve(DEFAULT_FIELD_RADIUS_DEGREES).value) / 2


# Serial pacing is the whole point of this module -- see the module
# docstring. Two seconds between requests keeps a full catalog sweep to
# a couple of minutes of added delay while staying far below the request
# rate that drew HTTP 500s from the TAP service under six-way
# concurrency.
DEFAULT_REQUEST_DELAY_SECONDS = 2.0

# Matches the retry pattern `plate_solver._call_with_transient_retry`
# already uses for the same class of problem (a transient remote
# failure), so both remote services behave the same way under a blip.
DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 2.0


def _coordinate_from_header(header: Any) -> tuple[float, float] | None:
    """Find out where the photo was pointing by reading its metadata.

    Try the most accurate coordinates first (if the image has already
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
        if they couldn't be found.
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
    except DATA_ERRORS as coordinate_error:
        logger.debug("Could not parse OBJCTRA/OBJCTDEC pair: %s", coordinate_error)
        return None


def _angular_separation_degrees(
    first_right_ascension: float,
    first_declination: float,
    second_right_ascension: float,
    second_declination: float,
) -> float:
    """Calculate the distance between two points in the sky.

    Proper spherical math is needed instead of simple subtraction
    because the sky is a globe. For example, near the North Star, the
    lines of longitude are very close together.

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
    first = SkyCoord(ra=first_right_ascension * u.deg, dec=first_declination * u.deg)
    second = SkyCoord(ra=second_right_ascension * u.deg, dec=second_declination * u.deg)
    return float(first.separation(second).degree)


def derive_field_centers(
    targets: list[Any],
    max_frames_per_target: int = 12,
    separation_threshold_degrees: float | None = None,
) -> list[dict[str, Any]]:
    """Make a list of all the unique places pictures were taken of.

    If 100 pictures were taken of the exact same spot, they combine into
    one spot so the same star map isn't downloaded 100 times. Checking stops
    after the first few photos of a target to save time, since a telescope
    rarely moves far while shooting one object.

    Parameters
    ----------
    targets : `list`
        The list of targets (folders of photos) to process.
    max_frames_per_target : `int`, optional
        How many photos to check per target before we get the idea.
    separation_threshold_degrees : `float`, optional
        If two spots are closer than this, we treat them as the same spot.
        When not given, half the field radius is used.

    Returns
    -------
    field_centers : `list` [`dict`]
        A clean list of the unique sky coordinates needed to download. Each
        also has ``gaia_depth``: the deepest Gaia G magnitude its frames'
        equipment predicts.
    """
    if separation_threshold_degrees is None:
        separation_threshold_degrees = default_deduplication_separation_degrees()
    field_centers: list[dict[str, Any]] = []

    for target in targets:
        target_id = getattr(target, "id", "unknown")
        frames = getattr(target, "frames", None) or []
        for frame in frames[:max_frames_per_target]:
            frame_path = getattr(frame, "path", None)
            if not frame_path:
                continue
            try:
                header = read_header(frame_path, first_hdu_only=True)
            except FITS_READ_ERRORS as header_error:
                logger.debug("Could not read header for %s: %s", frame_path, header_error)
                continue

            center = _coordinate_from_header(header)
            if center is None:
                continue
            right_ascension_degrees, declination_degrees = center

            # A pointing of exactly (0, 0) is the placeholder written
            # when a mount reports nothing, not a real field in Cetus.
            if math.isclose(right_ascension_degrees, 0.0, abs_tol=1e-9) and math.isclose(
                declination_degrees, 0.0, abs_tol=1e-9
            ):
                continue
            gaia_depth = float(predict_gaia_depth_from_header(header).query_depth)

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
                    existing_center["gaia_depth"] = max(existing_center["gaia_depth"], gaia_depth)
                    if target_id not in existing_center["target_ids"]:
                        existing_center["target_ids"].append(target_id)
                    break
            else:
                field_centers.append({
                    "right_ascension_deg": right_ascension_degrees,
                    "declination_deg": declination_degrees,
                    "target_ids": [target_id],
                    "frames_examined": 1,
                    "gaia_depth": gaia_depth,
                })

    return field_centers


def seed_local_gaia_catalog(
    targets: list[Any],
    radius_degrees: float | None = None,
    magnitude_limit: float | None = None,
    request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Go through the list of spots and download the stars for each one.

    If interrupted, this can just be run again and it will skip
    the ones already downloaded. It pauses between downloads to
    avoid getting banned from the server.

    Parameters
    ----------
    targets : `list`
        The list of targets (folders of photos) to process.
    radius_degrees : `float`, optional
        How wide of an area to download around each spot. Defaults to
        `DEFAULT_FIELD_RADIUS_DEGREES`.
    magnitude_limit : `float`, optional
        How dim of a star to store. Defaults to each field's predicted
        depth (``gaia_depth`` of `derive_field_centers`).
    request_delay_seconds : `float`, optional
        How long to wait between downloads so we don't overwhelm the server.
    max_attempts : `int`, optional
        How many times to retry if the server gives us an error.
    progress_callback : `Callable`, optional
        A function we can call to update a loading bar on the screen.

    Returns
    -------
    report : `dict`
        A summary of what was done: how many spots were checked, how many
        failed, and how many new stars were added to the database.
    """
    from astrometricslib._internal.drivers.catalog_store import summarize_catalog_coverage

    if radius_degrees is None:
        radius_degrees = resolve(DEFAULT_FIELD_RADIUS_DEGREES).value

    field_centers = derive_field_centers(targets)
    report: dict[str, Any] = {
        "fields_total": len(field_centers),
        "fields_seeded": 0,
        "fields_already_cached": 0,
        "fields_failed": 0,
        "sources_cached": 0,
        "results": [],
    }

    coverage_before = summarize_catalog_coverage()
    sources_before = coverage_before["source_count"]

    for field_index, field_center in enumerate(field_centers):
        depth = field_center.get("gaia_depth") if magnitude_limit is None else magnitude_limit
        field_result = _seed_one_field(field_center, radius_degrees, depth, max_attempts)

        if field_result["status"] == "seeded":
            report["fields_seeded"] += 1
            report["sources_cached"] += field_result["sources"]
        else:
            report["fields_failed"] += 1

        _report_field_progress(
            report,
            field_result,
            progress_callback,
            request_delay_seconds,
            is_last_field=field_index == len(field_centers) - 1,
        )

    coverage_after = summarize_catalog_coverage()
    report["coverage"] = coverage_after
    # A field whose region row already existed returns its stored rows
    # without adding any, so "already cached" is what the sweep reports
    # as seeded minus what actually grew the table.
    if coverage_after["source_count"] == sources_before and report["fields_seeded"]:
        report["fields_already_cached"] = report["fields_seeded"]

    return report


def seed_field_tiles(
    right_ascension_degrees: float, declination_degrees: float, radius_degrees: float, depth: float | None
) -> int:
    """Fill the Gaia tiles of one circle of sky in the local cache.

    Parameters
    ----------
    right_ascension_degrees, declination_degrees : `float`
        The circle's center, in degrees.
    radius_degrees : `float`
        The circle's radius, in degrees.
    depth : `float` or `None`
        The Gaia G magnitude to fill to. `None` uses the offline default
        depth of the star identifier.

    Returns
    -------
    star_count : `int`
        How many cached stars the circle's complete tiles hold.

    Raises
    ------
    ExternalServiceError
        If the run may not go online, or some tiles are still not complete.
    """
    from astrometricslib._internal.drivers import catalog_store
    from astrometricslib._internal.drivers.sky_tiles import tiles_for_cone
    from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import load_field_gaia_stars
    from astrometricslib.pipelines.astrometry.processing.star_identifier import (
        GAIA_DEFAULT_MAGNITUDE_LIMIT,
        make_gaia_tile_fetcher,
    )

    if depth is None:
        depth = float(resolve(GAIA_DEFAULT_MAGNITUDE_LIMIT).value)
    level = int(resolve(catalog_store.GAIA_TILE_LEVEL).value)
    tiles = tiles_for_cone(right_ascension_degrees, declination_degrees, radius_degrees, level)
    fetch, reason = make_gaia_tile_fetcher()
    if fetch is None:
        raise ExternalServiceError(f"No Gaia download: {reason}.")
    field = load_field_gaia_stars(None, tiles, depth, fetch)
    if field.missing_tiles:
        raise ExternalServiceError(
            f"{len(field.missing_tiles)} of {len(tiles)} Gaia tiles are not complete to G < {depth:.1f}."
        )
    return 0 if field.table is None else len(field.table)


def _seed_one_field(
    field_center: dict[str, Any],
    radius_degrees: float,
    magnitude_limit: float | None,
    max_attempts: int,
) -> dict[str, Any]:
    """Try to seed the Gaia cache for one field, retrying on failure.

    Returns
    -------
    field_result : `dict`
        The outcome for this field: its coordinates, target ids,
        `status` ("seeded" or "failed"), source count, and any error.
    """
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
            cached_count = seed_field_tiles(
                right_ascension_degrees, declination_degrees, radius_degrees, magnitude_limit
            )
            field_result["status"] = "seeded"
            field_result["sources"] = int(cached_count or 0)
            break
        except (AstrometricsError, sqlite3.Error, *ONLINE_QUERY_ERRORS) as seeding_error:
            field_result["error"] = str(seeding_error)
            if attempt_number < max_attempts:
                time.sleep(_RETRY_BACKOFF_BASE_SECONDS * attempt_number)

    if field_result["status"] != "seeded":
        logger.warning(
            "Could not seed field at RA %.4f Dec %.4f: %s",
            right_ascension_degrees,
            declination_degrees,
            field_result["error"],
        )

    return field_result


def _report_field_progress(
    report: dict[str, Any],
    field_result: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], None] | None,
    request_delay_seconds: float,
    is_last_field: bool,
) -> None:
    """Record a field's result, notify the caller, and pace the next call."""
    report["results"].append(field_result)
    if progress_callback is not None:
        try:
            progress_callback(field_result)
        except Exception:
            # The callback is the caller's code and may raise anything. A
            # broken progress report must not stop the seeding, so the error
            # is logged with its traceback and the run goes on.
            logger.exception("Seeding progress callback raised")

    # Skipped after the final field so the sweep does not end on a
    # pause that buys nothing.
    if request_delay_seconds > 0 and not is_last_field:
        time.sleep(request_delay_seconds)
