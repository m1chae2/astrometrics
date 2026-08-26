"""Main control center for processing target images.

This file acts as the conductor, calling the different analysis
programs (like astrometry and photometry) in the right order.
It keeps the actual image data models separate from the processing logic.
"""

import logging
import os
import re
import statistics
import threading
import time
from typing import Any, NamedTuple

from astrometricslib.drivers import disk_interface
from astrometricslib.models.moving_object import CascadeStage
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.enums import FilterType

logger = logging.getLogger(__name__)

# Maximum allowed time for an external stacking task to run.
# The timeout grows with the number of frames (N) because adding more
# frames increases the amount of work the stacking process has to do.
#
# The base time (300s) and per-frame time (30s) act as a safety net
# to detect if the process is stuck, not as an expected run time. These
# limits come from tests on different targets (e.g., the 2026-08-24 DSLR
# survey of NGC 7000). They make sure that slow color processing
# (~9.5s/frame) can finish without being cut off early, while giving
# plenty of extra time for fast black-and-white processing (<1s/frame).
STACKING_TIMEOUT_BASE_SECONDS = 300
STACKING_TIMEOUT_PER_FRAME_SECONDS = 30

# Retained for callers that still pass an explicit budget; equivalent to
# the previous flat value and used only as a floor.
STACKING_TIMEOUT_SECONDS = 600

# Poll interval while waiting on a stacking thread. Small relative to
# the minimum 600s budget, so a genuine timeout overshoots by at most
# this much, while still letting the deadline pick up lock waits that
# accrue after the wait began.
_STACKING_TIMEOUT_POLL_SECONDS = 2.0

# The percentage of rejected frames needed to trigger a quality warning flag.
# Normal processing naturally rejects a small number of frames (around 7.2%
# based on past runs). If we trigger a warning for anything less, we get
# too many false alarms. Setting the limit to 0.25 (25%) helps us catch
# real issues (like passing clouds) that need a human to check.
MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG = 0.25

# The minimum number of rejected frames needed to trigger a quality warning.
# This prevents false alarms when dealing with a small number of frames
# (under 20), where a single rejected frame could cause a high percentage.
MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG = 5


def compute_stacking_timeout_seconds(frame_count: int) -> int:
    """Calculate how long the stacking process is allowed to run.

    Stacking takes longer when there are more images (frames). This
    function adds a base time allowance to a per-image allowance to
    set a deadline. If it takes longer than this, something is probably stuck.

    Parameters
    ----------
    frame_count : `int`
        The number of images being stacked.

    Returns
    -------
    timeout_seconds : `int`
        The maximum allowed time in seconds.
    """
    scaled_timeout = STACKING_TIMEOUT_BASE_SECONDS + STACKING_TIMEOUT_PER_FRAME_SECONDS * max(0, frame_count)
    return int(max(STACKING_TIMEOUT_SECONDS, scaled_timeout))


# Matches the synthetic placeholder id assigned by
# `StarIdentifier._build_stellar_objects_from_sources` and
# `VariabilityAnalyzer.process`'s blind-detection path (optionally
# prefixed, e.g. "sess_20260101:Star_3") to a star that was never
# resolved to a real catalog id (SIMBAD/Gaia) or a position-derived
# one (FIELD_J...). A real catalog or position-derived id never
# matches this pattern.
_UNRESOLVED_STAR_ID_PATTERN = re.compile(r"^(?:.*:)?Star_\d+$")


class StarIdentificationBreakdown(NamedTuple):
    """How a batch of stars resolved; see `_drop_unresolved_stars`."""

    catalog_matched: int
    position_only: int
    unresolved: int


def select_frames_for_camera(target: Any, camera_name: str) -> list:
    """Find all images taken with a specific camera.

    You can provide either the full camera name or just a part of it
    (like 'ZWO' or 'Canon'). It ignores capitalization.

    Parameters
    ----------
    target : `Any`
        The target object containing the images.
    camera_name : `str`
        The name (or part of the name) of the camera to look for.

    Returns
    -------
    camera_frames : `list`
        A list of images taken by that camera.
    """
    return [frame for frame in target.frames if camera_name.lower() in (frame.camera or "").lower()]


def frame_configuration_key(frame: Any) -> str | None:
    """Create a label identifying the camera and telescope combination.

    We can only combine (stack) images taken with the exact same
    camera and telescope (focal length). Mixing different ones creates
    a bad image that can't be measured properly. This function creates
    a label like 'Canon@300mm' to help group matching images together.

    Parameters
    ----------
    frame : `Any`
        The image record to look at.

    Returns
    -------
    configuration_key : `str` or `None`
        The label (like "<camera>@<focal_length>mm"), or None if the
        focal length is missing.
    """
    focal_length = getattr(frame, "focal_length_mm", None)
    if not focal_length or focal_length <= 0:
        return None
    camera = (getattr(frame, "camera", None) or "Unknown").strip()
    # Rounded to whole millimetres so 405.0 and 405 key identically; no
    # real optic is distinguished by a fraction of a millimetre.
    return f"{camera}@{round(float(focal_length))}mm"


def group_frames_by_configuration(target: Any, camera_name: str | None = None) -> dict[str, list]:
    """Sort a target's images into groups that can be stacked together.

    Parameters
    ----------
    target : `Any`
        The target containing the images.
    camera_name : `str`, optional
        Only include images from this camera (ignores capitalization).

    Returns
    -------
    frames_by_configuration : `dict` [`str`, `list`]
        A dictionary where the keys are the camera/telescope combo labels
        and the values are lists of images. The largest group is first.
        Images missing focal length data are skipped.
    """
    grouped: dict[str, list] = {}
    for frame in target.frames or []:
        if camera_name and camera_name.lower() not in (frame.camera or "").lower():
            continue
        key = frame_configuration_key(frame)
        if key is None:
            continue
        grouped.setdefault(key, []).append(frame)
    return dict(sorted(grouped.items(), key=lambda item: -len(item[1])))


def frames_missing_focal_length(target: Any, camera_name: str | None = None) -> list:
    """Find images that are missing focal length information.

    Images without a focal length can't be safely stacked because we don't
    know how zoomed in they are. This function finds them so we can warn
    the user, instead of just silently ignoring them.

    Parameters
    ----------
    target : `Any`
        The target to check.
    camera_name : `str`, optional
        Only check images from this specific camera.

    Returns
    -------
    unassignable_frames : `list`
        A list of images that are missing focal length data.
    """
    return [
        frame
        for frame in target.frames or []
        if (not camera_name or camera_name.lower() in (frame.camera or "").lower())
        and frame_configuration_key(frame) is None
    ]


def select_frames_for_configuration(target: Any, configuration_key: str) -> list:
    """Select the frames belonging to one camera-and-optic configuration.

    Parameters
    ----------
    target : `Any`
        The target whose `frames` are filtered.
    configuration_key : `str`
        A key as produced by `frame_configuration_key`.

    Returns
    -------
    configuration_frames : `list`
        The matching frames, in their original order.
    """
    return [frame for frame in target.frames or [] if frame_configuration_key(frame) == configuration_key]


def _drop_unresolved_stars(
    stellar_objects: list, *, target_id: str, pipeline_name: str
) -> tuple[list, StarIdentificationBreakdown]:
    """Filter out stars that were never resolved to a real identity.

    A star that can't be matched to SIMBAD/Gaia and can't even be
    given a stable position-derived id (its sky position couldn't be
    determined) is worthless as a persistent catalog entry -- its
    placeholder id is arbitrary and not reproducible across runs, so
    saving it would only pollute `stellar_catalog` with rows that can
    never be merged back into the real star they came from. Dropping
    it here, right before persistence, keeps this rule in one place
    regardless of which pipeline (astrometry, spectroscopy,
    photometry) produced the star.

    Also logs and returns a breakdown of every star's outcome
    (catalog-matched / position-only / unresolved-and-dropped), so a
    caller worried about spurious detections has a concrete per-run
    number to look at instead of only transient DEBUG-level logging
    from the identification step itself.

    Parameters
    ----------
    stellar_objects : `list`
        Candidate stars to filter.
    target_id : `str`
        The target this batch of stars belongs to, for the log line.
    pipeline_name : `str`
        Which pipeline produced `stellar_objects` ("astrometry",
        "spectroscopy", or "photometry"), for the log line.

    Returns
    -------
    resolved : `list`
        The subset of `stellar_objects` with a real or position-derived
        identity.
    breakdown : `StarIdentificationBreakdown`
        Counts of every star's outcome, computed before filtering.
    """
    resolved = []
    catalog_matched = 0
    position_only = 0
    unresolved = 0
    for stellar_object in stellar_objects:
        if _UNRESOLVED_STAR_ID_PATTERN.match(stellar_object.id):
            unresolved += 1
            continue
        if stellar_object.is_catalog_identified:
            catalog_matched += 1
        else:
            position_only += 1
        resolved.append(stellar_object)

    logger.info(
        f"[{target_id}] {pipeline_name} star identification: {catalog_matched} catalog-matched, "
        f"{position_only} position-only (no catalog match), {unresolved} dropped (no sky position at all)"
    )
    return resolved, StarIdentificationBreakdown(catalog_matched, position_only, unresolved)


# Prefix minted by star_identifier.identify_stars_with_wcs's Step 3 for a
# star with a solved sky position but no SIMBAD/Gaia match. Duplicated
# here rather than imported, matching _UNRESOLVED_STAR_ID_PATTERN's own
# precedent of matching the format by convention instead of taking a
# dependency in the other direction.
_POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"


def _reconcile_position_only_star_ids(
    stellar_objects: list,
    *,
    butler,  # ruff: ignore[missing-type-function-argument]
    target_id: str,
) -> list:
    """Reconcile and merge catalog IDs based on star positions.

    Standard naming based on position assumes we can measure star locations
    perfectly every time. In reality, tests show star positions can shift
    by 1-6 arcseconds between different runs of the same field. If we don't
    merge them, this shift causes the same physical star to be saved multiple
    times under slightly different names.

    This function checks new stars before saving them, comparing them to
    existing stars in the catalog. If they are close enough (within
    `CATALOG_MATCH_RADIUS_ARCSEC`), it merges them. This ensures we update
    the existing star instead of creating a duplicate.

    Note: This handles active pipeline outputs; legacy catalog
    deduplication is addressed separately via
    `scripts/reconcile_position_only_star_catalog.py`.

    Parameters
    ----------
    stellar_objects : `list`
        Candidate stars about to be persisted, mutated in place (each
        reassigned star's `id`/`name` are overwritten with the id of
        the existing catalog row it matched).
    butler : `Any`
        Provides `list_projected` for reading the target's existing
        position-only rows.
    target_id : `str`
        The target these stars belong to. Scoped to one target both to
        keep the candidate set small and because that is where this
        catalog's own measured duplication was concentrated; a
        position-only star shared between two overlapping targets'
        fields is not reconciled by this pass.

    Returns
    -------
    stellar_objects : `list`
        The same list, for chaining alongside `_drop_unresolved_stars`.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
        CATALOG_MATCH_RADIUS_ARCSEC,
    )

    # `StellarObject.right_ascension`/`.declination` are typed `Any` and
    # default to `""`, not `None` -- an `is not None` check alone would
    # let that default through and crash the `SkyCoord` arithmetic
    # below. Every real `FIELD_J...` star has both set to real floats at
    # the same place its id is minted (star_identifier.py's Step 3), so
    # this only excludes a malformed star that should never reach
    # persistence in the first place.
    position_only_stars = [
        stellar_object
        for stellar_object in stellar_objects
        if stellar_object.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and isinstance(stellar_object.right_ascension, int | float)
        and isinstance(stellar_object.declination, int | float)
    ]
    if not position_only_stars:
        return stellar_objects

    try:
        existing_rows = butler.list_projected("stellar_catalog", ["id", "ra", "dec", "target_id"])
    except Exception as lookup_error:
        # Reconciliation is an optimization over an already-correct (if
        # duplicative) persistence path; a lookup failure must not block
        # a run's own stars from being saved.
        logger.debug(
            "[%s] Could not read existing catalog for id reconciliation: %s", target_id, lookup_error
        )
        return stellar_objects

    # target_id is a comma-joined string (a star can belong to more than
    # one target), so membership is checked in Python -- same reasoning
    # as StellarCatalog.list_object_summaries's identical filter.
    existing_position_only = [
        row
        for row in existing_rows
        if row["id"].startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and row["ra"] is not None
        and row["dec"] is not None
        and target_id in (row["target_id"] or "").split(",")
    ]
    if not existing_position_only:
        return stellar_objects

    existing_coords = SkyCoord(
        ra=[row["ra"] for row in existing_position_only] * u.deg,
        dec=[row["dec"] for row in existing_position_only] * u.deg,
    )

    reused_ids: set[str] = set()
    reused_count = 0
    for stellar_object in position_only_stars:
        star_coord = SkyCoord(
            ra=stellar_object.right_ascension * u.deg, dec=stellar_object.declination * u.deg
        )
        idx, d2d, _ = star_coord.match_to_catalog_sky(existing_coords)
        if d2d >= CATALOG_MATCH_RADIUS_ARCSEC * u.arcsec:
            continue

        existing_id = existing_position_only[idx]["id"]
        if existing_id in reused_ids:
            # Already claimed by another star from this same run -- two
            # distinct stars should never collapse onto one row. Leave
            # this one with its own freshly minted id rather than
            # colliding; if it's a genuine duplicate of the star that
            # already claimed the match, that will still be caught the
            # next time this reconciliation runs.
            continue
        if existing_id == stellar_object.id:
            continue

        stellar_object.id = existing_id
        stellar_object.name = existing_id
        reused_ids.add(existing_id)
        reused_count += 1

    if reused_count:
        logger.info(
            f"[{target_id}] Reconciled {reused_count} position-only star id(s) onto existing "
            f"catalog rows within {CATALOG_MATCH_RADIUS_ARCSEC:g} arcsec, instead of minting new ones."
        )
    return stellar_objects


def merge_astrometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for astrometry updates to a star.

    Keeps any old target names but adds new ones. It also updates
    things we just solved (like identity or position) without throwing
    away data from other targets that might be attached to this star.

    Returns
    -------
    merged_object : StellarObject
        The combined star record.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    return existing_stellar_object


def merge_spectroscopy_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for spectroscopy updates to a star.

    Adds new target names to the list and updates the light spectrum
    data and dispersion angle, but leaves everything else alone.

    Returns
    -------
    merged_object : StellarObject
        The combined star record.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    existing_stellar_object.name = updated_stellar_object.name
    existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
    existing_stellar_object.declination = updated_stellar_object.declination
    existing_stellar_object.spectral_type = updated_stellar_object.spectral_type
    existing_stellar_object.stellar_spectral_type = updated_stellar_object.stellar_spectral_type
    existing_stellar_object.magnitude = updated_stellar_object.magnitude
    existing_stellar_object.is_catalog_identified = updated_stellar_object.is_catalog_identified
    existing_stellar_object.star_data = updated_stellar_object.star_data
    existing_stellar_object.detected_angle = updated_stellar_object.detected_angle
    existing_stellar_object.dispersion_angle = updated_stellar_object.dispersion_angle
    existing_stellar_object.trail_centerline_px = updated_stellar_object.trail_centerline_px
    existing_stellar_object.trail_width_px = updated_stellar_object.trail_width_px
    existing_stellar_object.rectangle = updated_stellar_object.rectangle
    existing_stellar_object.spectrum_data_processed = updated_stellar_object.spectrum_data_processed
    return existing_stellar_object


def merge_photometry_stellar_object(existing_stellar_object, updated_stellar_object):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Merge rule for photometry updates to a star.

    Adds new target names, updates cross-session identity data,
    and brings in new brightness variation (variability) metrics.

    Returns
    -------
    merged_object : StellarObject
        The combined star record.
    """
    if existing_stellar_object is None:
        return updated_stellar_object
    existing_stellar_object.light_curve = updated_stellar_object.light_curve
    if getattr(updated_stellar_object, "mean_flux", None) is not None:
        existing_stellar_object.mean_flux = updated_stellar_object.mean_flux
        existing_stellar_object.coefficient_of_variation = updated_stellar_object.coefficient_of_variation
        existing_stellar_object.variability_score = updated_stellar_object.variability_score
    # Cross-session matching (see _match_and_merge_across_sessions)
    # recomputes both fresh each run, so a full replace keeps a repeat
    # run's result authoritative rather than accumulating stale matches.
    existing_stellar_object.session_matches = updated_stellar_object.session_matches
    if updated_stellar_object.right_ascension:
        existing_stellar_object.right_ascension = updated_stellar_object.right_ascension
        existing_stellar_object.declination = updated_stellar_object.declination
    for target_id in updated_stellar_object.target_ids:
        if target_id not in existing_stellar_object.target_ids:
            existing_stellar_object.target_ids.append(target_id)
    return existing_stellar_object


def _run_variability_analysis_for_session(
    session: Any,
    max_workers: int | None,
    id_prefix: str,
    target: Target | None = None,
    star_identifier: Any = None,
    use_astrometry_seed: bool = True,
) -> tuple[Any, list[Any], Any | None]:
    """Track star brightness over a single observing session.

    If `use_astrometry_seed` is turned on, this function tries to
    figure out the sky coordinates (plate solve) of the reference image.
    It then looks up the stars in SIMBAD/Gaia databases before tracking
    their brightness. This known identity stays with the star.

    Returns
    -------
    analyzer : VariabilityAnalyzer
        The tool that ran the analysis.
    candidates : list
        Stars that might be changing brightness (variable stars).
    identify_result : IdentifyStarsResult or None
        The result of looking up the stars, if we tried to do it.
        Useful for getting the sky coordinate map (WCS) later.
    """
    from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
        VariabilityAnalyzer,
    )

    seed_stars = None
    identify_result = None
    if use_astrometry_seed and star_identifier is not None and target is not None:
        from astrometricslib.tasks.stellar_tasks.astrometry_tasks.session_identification import (
            identify_session_stars,
        )
        from astrometricslib.utilities.image import AstrometricsImage

        center_ra = None
        center_dec = None
        try:
            center_ra = parse_coordinate_string(str(target.ra), is_ra=True)
            center_dec = parse_coordinate_string(str(target.dec), is_ra=False)
        except Exception as exc:
            # Blind solve (no center hint) if the target has no usable
            # RA/Dec yet.
            logger.debug("Falling back to blind solve, could not parse target RA/Dec: %s", exc)

        reference_image = AstrometricsImage(session.frame_paths[0])
        if reference_image.wcs is None and target and target.stacked_image:
            stacked_img = AstrometricsImage(target.stacked_image)
            swcs = stacked_img.wcs
            if swcs is not None and (swcs.is_celestial or swcs.has_celestial):
                reference_image.wcs = swcs

        identify_result = identify_session_stars(
            reference_image, star_identifier, center_ra=center_ra, center_dec=center_dec
        )
        seed_stars = identify_result.stellar_objects

    analyzer = VariabilityAnalyzer()
    analyzer.process(session.frame_paths, max_workers=max_workers, id_prefix=id_prefix, seed_stars=seed_stars)
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    candidates = analyzer.identify_variable_stars()
    return analyzer, candidates, identify_result


def _solve_session_wcs(session: Any, target: Target) -> Any | None:
    """Plate-solve a session's reference frame to get its sky coordinates.

    We need this when we want to match stars across different sessions,
    but we haven't already looked up their identities in a database.
    (For example, if we skipped the SIMBAD lookup step earlier).

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The map from pixel to sky position, or None if the solve
        failed.
    """
    import warnings

    from astropy.wcs import WCS, FITSFixedWarning

    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import PlateSolver

    reference_path = session.frame_paths[0]
    try:
        center_ra = None
        center_dec = None
        try:
            center_ra = parse_coordinate_string(str(target.ra), is_ra=True)
            center_dec = parse_coordinate_string(str(target.dec), is_ra=False)
        except Exception as exc:
            # Blind solve (no center hint) if the target has no usable
            # RA/Dec yet.
            logger.debug("Falling back to blind solve, could not parse target RA/Dec: %s", exc)

        solver = PlateSolver()
        header = solver.solve(
            image_path=reference_path,
            center_ra=center_ra,
            center_dec=center_dec,
            radius=2.0,
            # A real M 81 solve with a center hint measured 2.27s; 30s
            # gives >10x margin while still failing fast (vs. the
            # shared 300s default star_identifier.py uses for a real,
            # possibly hint-less solve) when a session's reference
            # frame isn't solvable -- this call is best-effort only,
            # already tolerating a failed solve by skipping cross-
            # session matching for that session (see docstring above).
            solve_timeout=30,
        )
        if header is None:
            logger.warning(
                f"Session {session.id} plate solve failed ({reference_path}); "
                "skipping cross-session star matching for this session."
            )
            return None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            return WCS(header, naxis=2)
    except Exception as solve_error:
        logger.warning(
            f"Session {session.id} plate solve failed ({reference_path}); "
            f"skipping cross-session star matching for this session: {solve_error}"
        )
        return None


def _stars_to_sky(stellar_objects: list[Any], wcs: Any) -> list[Any]:
    """Convert star pixel locations into real sky coordinates (RA/Dec).

    Updates the input stars with their new right ascension and declination.

    Returns
    -------
    stars_with_position : `list`
        Only the stars that successfully got sky coordinates.
    """
    import numpy as np

    x_positions = []
    y_positions = []
    stars_with_position = []
    for star in stellar_objects:
        star_data = star.star_data
        if not isinstance(star_data, dict):
            continue
        x = star_data.get("xcentroid", star_data.get("x_centroid"))
        y = star_data.get("ycentroid", star_data.get("y_centroid"))
        if x is None or y is None:
            continue
        x_positions.append(x)
        y_positions.append(y)
        stars_with_position.append(star)

    if not stars_with_position:
        return []

    ra_array, dec_array = wcs.wcs_pix2world(np.array(x_positions), np.array(y_positions), 0)
    for star, ra, dec in zip(stars_with_position, ra_array, dec_array, strict=True):
        star.right_ascension = float(ra)
        star.declination = float(dec)

    return stars_with_position


def _positive_median_or_none(values: list[float]) -> float | None:
    """Median of the positive values in `values`, or `None` if none exist.

    Returns
    -------
    median : `float` or `None`
        The median of the positive values, or `None` if none exist.
    """
    import numpy as np

    array = np.array(values, dtype=float)
    array = array[array > 0]
    return float(np.median(array)) if array.size else None


def _rescale_flux_segment(
    values: list[float], own_median: float | None, target_median: float | None
) -> list[float]:
    """Rescale a flux segment so its own median matches `target_median`.

    Returns
    -------
    rescaled : `list` [`float`]
        `values` unchanged if either median is unavailable or non-positive;
        otherwise each value scaled by `target_median / own_median`.
    """
    if not own_median or not target_median:
        return list(values)
    factor = target_median / own_median
    return [float(value) * factor for value in values]


def _rescale_and_merge_light_curve(canonical: Any, new: Any) -> Any:
    """Merge a star's brightness data from two different nights.

    inter-session zero-point offset. The incoming (`new`) segment's
    `fluxes_normalized`/`fluxes_detrended` are each independently
    rescaled so their own median matches the canonical curve's existing
    median before concatenating, then the combined curve is sorted by
    timestamp. `magnitudes` (always empty today) is carried over
    untouched; `periodogram`/`transit_candidate` are single computed
    results, not per-timestamp arrays, and are dropped rather than
    carrying a stale single-session value forward on the merged curve.

    Returns
    -------
    merged : `LightCurve`
        A new `LightCurve` combining both segments, sorted by timestamp.
    """
    from astrometricslib.models.stellar_source import LightCurve

    canonical_median = _positive_median_or_none(canonical.fluxes_normalized)
    new_median = _positive_median_or_none(new.fluxes_normalized)
    rescaled_new_normalized = _rescale_flux_segment(new.fluxes_normalized, new_median, canonical_median)

    canonical_detrended_median = _positive_median_or_none(canonical.fluxes_detrended)
    new_detrended_median = _positive_median_or_none(new.fluxes_detrended)
    rescaled_new_detrended = _rescale_flux_segment(
        new.fluxes_detrended, new_detrended_median, canonical_detrended_median
    )

    combined_timestamps = canonical.timestamps + new.timestamps
    combined_fluxes = canonical.fluxes + new.fluxes
    combined_fluxes_normalized = canonical.fluxes_normalized + rescaled_new_normalized
    combined_fluxes_detrended = canonical.fluxes_detrended + rescaled_new_detrended
    combined_airmasses = canonical.airmasses + new.airmasses
    combined_is_saturated = canonical.is_saturated + new.is_saturated

    sort_order = sorted(range(len(combined_timestamps)), key=lambda i: combined_timestamps[i])

    def _reordered(values):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return [values[i] for i in sort_order] if len(values) == len(sort_order) else list(values)

    return LightCurve(
        timestamps=_reordered(combined_timestamps),
        fluxes=_reordered(combined_fluxes),
        fluxes_normalized=_reordered(combined_fluxes_normalized),
        fluxes_detrended=_reordered(combined_fluxes_detrended),
        airmasses=_reordered(combined_airmasses),
        is_saturated=_reordered(combined_is_saturated),
        magnitudes=canonical.magnitudes,
        periodogram=None,
        transit_candidate=None,
    )


def _match_and_merge_across_sessions(
    photometry_sessions: list[Any],
    per_session_results: list[tuple[Any, list[Any]]],
    target: Target,
    tolerance_arcsec: float = 5.0,
    session_wcs_map: dict[str, Any] | None = None,
) -> tuple[list[Any], list[str], int]:
    """Find the same real star in different sessions and combine its data.

    This takes the sky coordinates for stars in each session and pairs
    them up if they are very close to each other (under `tolerance_arcsec`).
    If they match, their light curves are merged into a single star record.
    If a star only appears once, or if we don't have sky coordinates for
    that session, it stays as its own separate record.

    Parameters
    ----------
    photometry_sessions : list
        The list of observing sessions, in chronological order.
    per_session_results : list of tuples
        The analysis tool and variable star candidates for each session.
    target : Target
        The target name and RA/Dec hint used to help the plate solver
        figure out coordinates if they are missing.
    tolerance_arcsec : float, optional
        How close two stars must be in arcseconds to be considered the
        same physical star (default is 5.0").
    session_wcs_map : dict, optional
        A map of session IDs to their known coordinate systems (WCS).
        This stops us from having to run the plate solver twice for the
        same image.

    Returns
    -------
    merged_stellar_objects : list
        The final list of stars, with matching ones combined.
    sessions_missing_wcs : list of str
        Names of sessions where we couldn't figure out the coordinates.
    match_count : int
        The total number of times we merged a star into another one.
    """
    from astropy import units as astropy_units
    from astropy.coordinates import SkyCoord, search_around_sky

    from astrometricslib.models.stellar_source import StellarSessionMatch

    sessions_missing_wcs: list[str] = []
    match_count = 0
    # One (star, ra_deg, dec_deg) entry per distinct physical star found
    # so far. Kept as plain floats rather than individual SkyCoord
    # objects so a matching SkyCoord *array* can be built in one call
    # per session below -- vectorized, KD-tree-backed matching instead
    # of a per-pair Python loop, which does not scale to the thousands
    # of stars a dense field like M 81 detects per session.
    canonical_registry: list[tuple[Any, float, float]] = []
    merged_stellar_objects: list[Any] = []

    for session, (analyzer, _session_candidates) in zip(
        photometry_sessions, per_session_results, strict=True
    ):
        if session_wcs_map is not None and session.id in session_wcs_map:
            wcs = session_wcs_map[session.id]
        else:
            wcs = _solve_session_wcs(session, target)
        if wcs is None:
            sessions_missing_wcs.append(session.id)
            merged_stellar_objects.extend(analyzer.stellar_objects)
            continue

        session_stars_with_sky = _stars_to_sky(analyzer.stellar_objects, wcs)
        if not session_stars_with_sky:
            sessions_missing_wcs.append(session.id)
            merged_stellar_objects.extend(analyzer.stellar_objects)
            continue

        stars_with_sky_ids = {id(star) for star in session_stars_with_sky}
        merged_stellar_objects.extend(
            star for star in analyzer.stellar_objects if id(star) not in stars_with_sky_ids
        )

        if not canonical_registry:
            for star in session_stars_with_sky:
                canonical_registry.append((star, star.right_ascension, star.declination))
                merged_stellar_objects.append(star)
            continue

        # Greedy nearest-first one-to-one assignment: find every
        # (canonical, session_star) pair under tolerance via a KD-tree
        # search (`search_around_sky`, not an O(canonical x session)
        # pairwise Python loop -- that does not scale to a dense
        # field's thousands of stars per session), then assign in
        # ascending-separation order while both sides remain unclaimed.
        # A naive "first canonical entry within tolerance wins" per-star
        # loop can double-assign in a crowded field at this tolerance.
        canonical_coords = SkyCoord(
            ra=[entry[1] for entry in canonical_registry] * astropy_units.deg,
            dec=[entry[2] for entry in canonical_registry] * astropy_units.deg,
        )
        session_coords = SkyCoord(
            ra=[star.right_ascension for star in session_stars_with_sky] * astropy_units.deg,
            dec=[star.declination for star in session_stars_with_sky] * astropy_units.deg,
        )
        search_result = search_around_sky(
            canonical_coords, session_coords, tolerance_arcsec * astropy_units.arcsec
        )
        candidate_pairs = sorted(
            zip(
                search_result.angular_separation.arcsecond,
                search_result.indices_to_first_set,
                search_result.indices_to_second_set,
                strict=False,
            ),
            key=lambda pair: pair[0],
        )

        claimed_canonical_indices: set[int] = set()
        claimed_session_star_indices: set[int] = set()
        for separation_arcsec, canonical_index, session_star_index in candidate_pairs:
            canonical_index = int(canonical_index)
            session_star_index = int(session_star_index)
            if (
                canonical_index in claimed_canonical_indices
                or session_star_index in claimed_session_star_indices
            ):
                continue
            claimed_canonical_indices.add(canonical_index)
            claimed_session_star_indices.add(session_star_index)

            canonical_star, _canonical_ra, _canonical_dec = canonical_registry[canonical_index]
            new_star = session_stars_with_sky[session_star_index]
            canonical_star.light_curve = _rescale_and_merge_light_curve(
                canonical_star.light_curve, new_star.light_curve
            )
            canonical_star.session_matches.append(
                StellarSessionMatch(session_id=session.id, angular_separation_arcsec=float(separation_arcsec))
            )
            match_count += 1

        for session_star_index, star in enumerate(session_stars_with_sky):
            if session_star_index in claimed_session_star_indices:
                continue
            canonical_registry.append((star, star.right_ascension, star.declination))
            merged_stellar_objects.append(star)

    return merged_stellar_objects, sessions_missing_wcs, match_count


def analyze_frame_spectroscopy(target: Target, path: str, limit: int = 10) -> tuple[Any, list[Any]]:
    """Run the light spectrum (spectroscopy) tool on one image frame.

    Returns
    -------
    result : tuple
        The analysis tool and the list of stars it found.
    """
    from astrometricslib.tasks.target_tasks.stacking_tasks import analyze_frame_spectroscopy as _analyze

    return _analyze(target, path, limit)


def analyze_target(
    target: Target,
    frames: list[FrameRecord] | None = None,
    pipeline_type: str = "astrometry",
    filter_type: str | None = None,
    butler=None,  # ruff: ignore[missing-type-function-argument]
    register_job: bool = True,
    path: str | None = None,
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Run a specific analysis pipeline on the given target.

    You can ask it to run "astrometry" (finding star positions),
    "spectroscopy" (light spectrum), "photometry" (brightness changes),
    or "asteroid_recovery" (finding moving rocks).

    Parameters
    ----------
    register_job : bool, optional
        Set to True (default) if you want this run to automatically
        show up in the user interface's job tracker. Set to False if
        you are calling this from a tool that already tracks its own jobs
        (to prevent double-counting).

    Returns
    -------
    result : dict
        A dictionary with the final results and status info.

    Raises
    ------
    ValueError
        If you ask for an unknown pipeline type, or if we don't have
        the right images needed to run it.
    """
    if butler is None:
        from astrometricslib.data_access.butler import DiskButler

        butler = DiskButler()

    # Automatically register processing job in astrometrics_log.db so
    # execution from scripts, notebooks, or CLI automatically appears in
    # the UI job manager. Skipped when register_job=False -- see that
    # parameter's docstring.
    # Mirrors AnalysisOrchestrator._start_analysis_task's job_logger setup (a
    # FileHandler for the job's own log file, plus a DbLogHandler so
    # JobHistoryList's "select job -> view log" pulls the same milestone
    # messages a UI-started analysis run would produce) so a script-started
    # run gets the same terminal output as one started from the UI.
    job_id = None
    logger_if = None
    job_logger = None
    job_log_handlers: list = []
    pipeline_logger = None
    if register_job:
        try:
            import os
            import uuid
            from datetime import datetime

            from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
            from astrometricslib.utilities.config_loader import get_configuration
            from astrometricslib.utilities.pipeline_models import ProcessingJob

            cfg = get_configuration()
            log_db_path = cfg.get_logs_db_path()
            logger_if = LoggerInterface(log_db_path)
            job_id = str(uuid.uuid4())

            safe_target = target.id.replace(" ", "_").replace("/", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = cfg.get_logs_path()
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = str(log_dir / f"analysis_{safe_target}_{timestamp}.log")

            job_logger = logging.getLogger(f"job_{job_id}")
            job_logger.propagate = False
            job_logger.setLevel(logging.INFO)
            file_handler = logging.FileHandler(log_file_path)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            db_handler = DbLogHandler(logger_if, job_id=job_id)
            job_logger.addHandler(file_handler)
            job_logger.addHandler(db_handler)

            # Also attach to the "astrometricslib" package logger -- the
            # common ancestor of every module logger used deeper in the
            # pipeline (star_identifier, plate_solver,
            # variability_analyzer, etc, all via
            # logging.getLogger(__name__) with propagate=True by
            # default). Without this, only this function's own
            # hand-written milestone messages ever reached the job's log
            # file/DB rows -- everything the pipeline itself decides
            # along the way (plate-solve fallback stages, SIMBAD/Gaia
            # query results, per-star identification) was invisible
            # there. Removed in the finally block below once this run is
            # done, since every job attaches its own handler instances
            # here and leaving them would mean this job's file/DB rows
            # keep receiving every *future* job's log lines too.
            job_log_handlers = [file_handler, db_handler]
            pipeline_logger = logging.getLogger("astrometricslib")
            for handler in job_log_handlers:
                pipeline_logger.addHandler(handler)
            pipeline_logger.setLevel(logging.INFO)

            initial_job = ProcessingJob(
                id=job_id,
                target_id=target.id,
                job_type="analysis",
                status="started",
                progress_current=0,
                progress_total=100,
                log_file_path=log_file_path,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
            logger_if.upsert_job(initial_job)
            job_logger.info(
                f"[{target.id}] Analysis job started for {target.id} (type: {pipeline_type}, Job: {job_id})"
            )
        except Exception as job_err:
            logger.warning(f"Could not register job in astrometrics_log.db: {job_err}")

    def _update_job_status(status_val: str, progress_val: int = 100):  # ruff: ignore[missing-return-type-private-function]
        if logger_if and job_id:
            try:
                j = logger_if.get_job(job_id)
                if j:
                    j.status = status_val
                    j.progress_current = progress_val
                    j.updated_at = datetime.now().isoformat()
                    logger_if.upsert_job(j)
            except Exception as exc:
                logger.debug("Failed to persist job status update for job '%s': %s", job_id, exc)
        if job_logger:
            if status_val == "completed":
                job_logger.info(f"[{target.id}] Analysis completed successfully.")
            elif status_val == "failed":
                job_logger.error(f"[{target.id}] Analysis failed.")

    try:
        # Resolve image path for astrometry/spectroscopy modes
        # if not explicitly provided
        if not path and pipeline_type in ("astrometry", "spectroscopy"):
            if frames:
                path = frames[0].path
            elif pipeline_type == "spectroscopy" and target.stacked_spectral_target:
                path = target.stacked_spectral_target
            elif pipeline_type == "astrometry" and target.stacked_image:
                path = target.stacked_image
            elif target.frames:
                path = target.frames[0].path
            else:
                _update_job_status("failed", 0)
                raise ValueError(
                    f"No frames or stacked image available for {pipeline_type} analysis"
                    f" on target {target.id}."
                )

        res_dict = _run_analysis_pipeline_match(
            target, frames, pipeline_type, filter_type, butler, path, **kwargs
        )
        _update_job_status("completed", 100)
        return res_dict
    except Exception as exc:
        _update_job_status("failed", 0)
        raise exc
    finally:
        # See the comment where job_log_handlers/pipeline_logger are
        # built above: every job attaches its own handler instances to
        # the shared "astrometricslib" logger, so they must come off
        # again once this run is done (any exit path), or this job's
        # file/DB rows would keep receiving every *future* job's log
        # lines too.
        if pipeline_logger:
            for handler in job_log_handlers:
                pipeline_logger.removeHandler(handler)


def _run_analysis_pipeline_match(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument]
    pipeline_type,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    butler,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument]
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    match pipeline_type:
        case "astrometry":
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
                AstrometryPipeline,
            )
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import (
                reset_plate_solve_statistics,
            )
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
                reset_gaia_query_statistics,
            )

            # Reset before this target's own solve/query work starts, so
            # the counts read back below describe this target alone --
            # both tallies are process-global and a worker handles many
            # targets in sequence. reset_gaia_query_statistics leaves the
            # circuit breaker itself untouched; see its docstring.
            reset_plate_solve_statistics()
            reset_gaia_query_statistics()

            pipeline = AstrometryPipeline()
            context = pipeline.process(
                path, attempt_plate_solving=True, target_ra=target.ra, target_dec=target.dec, **kwargs
            )

            from astrometricslib.models.quality_summary import (
                AstrometryPipelineQualityMetrics,
                AstrometryQualitySummary,
            )

            context.stellar_objects, star_id_breakdown = _drop_unresolved_stars(
                context.stellar_objects, target_id=target.id, pipeline_name="astrometry"
            )
            simbad_matched_count = sum(
                1 for stellar_object in context.stellar_objects if stellar_object.spectral_type
            )
            # Read from the identification and solver modules rather than
            # threaded through the call chain: both keep per-process
            # tallies precisely so a summary can record what the run
            # actually experienced against the remote services.
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.plate_solver import (
                get_plate_solve_attempt_count,
            )
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
                get_gaia_query_statistics,
            )

            gaia_statistics = get_gaia_query_statistics()
            target.astrometry_quality_summary = AstrometryQualitySummary(
                target_id=target.id,
                astrometry_metrics=AstrometryPipelineQualityMetrics(
                    sources_detected=context.sources_detected,
                    solve_attempted=context.solve_attempted,
                    astrometric_residual_rms_arcsec=context.astrometric_residual_rms_arcsec,
                    plate_solve_succeeded=context.wcs is not None,
                    simbad_matched_count=simbad_matched_count,
                    remote_catalog_queries_attempted=int(gaia_statistics["attempted"]),
                    remote_catalog_queries_failed=int(gaia_statistics["failed"]),
                    remote_catalog_circuit_breaker_tripped=bool(gaia_statistics["circuit_breaker_tripped"]),
                    plate_solve_attempts=get_plate_solve_attempt_count(),
                    catalog_matched_star_count=star_id_breakdown.catalog_matched,
                    position_only_star_count=star_id_breakdown.position_only,
                    unresolved_star_count=star_id_breakdown.unresolved,
                ),
            )
            if not target.astrometry_quality_summary.astrometry_metrics.plate_solve_succeeded:
                target.astrometry_quality_summary.flagged = True
                target.astrometry_quality_summary.flag_reasons.append("plate solve failed")

            # If target RA and DEC are not populated or zero, pull
            # coordinates from plate solver
            is_ra_empty = not target.ra or target.ra.strip() in ("", "0", "0.0", "0h 0m 0s")
            is_dec_empty = not target.dec or target.dec.strip() in ("", "0", "0.0", "0° 0′ 0′′")
            is_zero = False
            if not (is_ra_empty or is_dec_empty):
                try:
                    resolved_ra_deg = parse_coordinate_string(str(target.ra), is_ra=True)
                    resolved_dec_deg = parse_coordinate_string(str(target.dec), is_ra=False)
                    # ruff: ignore[float-equality-comparison] -- exact
                    # sentinel check against the unset-coordinate
                    # default ("0h 0m 0s" / "0(deg) 0' 0''"), not a
                    # measured/computed value comparison.
                    if resolved_ra_deg == 0.0 and resolved_dec_deg == 0.0:
                        is_zero = True
                except Exception:
                    is_zero = True

            if is_ra_empty or is_dec_empty or is_zero:
                if context.wcs is not None:
                    try:
                        from astropy import units as u
                        from astropy.coordinates import SkyCoord

                        ra_deg = float(context.wcs.wcs.crval[0])
                        dec_deg = float(context.wcs.wcs.crval[1])
                        solved_coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
                        target.ra = solved_coord.ra.to_string(unit=u.hour, sep=" ", precision=2)
                        target.dec = solved_coord.dec.to_string(unit=u.deg, sep=" ", precision=2)
                        logger.info(
                            f"Updated Target {target.id} RA/Dec from plate solver: "
                            f"RA={target.ra}, DEC={target.dec}"
                        )
                    except Exception as wcs_error:
                        logger.warning(
                            f"Failed to extract center coordinate from WCS for target "
                            f"{target.id}: {wcs_error}"
                        )

            if context.wcs is not None and path:
                import os

                if os.path.exists(path):
                    try:
                        from astropy.io import fits

                        with fits.open(path, mode="update") as hdul:
                            wcs_header = context.wcs.to_header()
                            for card in wcs_header.cards:
                                if not card.keyword:
                                    continue
                                hdul[0].header[card.keyword] = (card.value, card.comment)
                            hdul.flush()
                        logger.info(f"Updated FITS file {path} header with solved WCS keywords.")
                    except Exception as wcs_error:
                        logger.warning(f"Failed to update FITS file header with WCS: {wcs_error}")

            for obj in context.stellar_objects:
                if target.id not in obj.target_ids:
                    obj.target_ids.append(target.id)

            context.stellar_objects = _reconcile_position_only_star_ids(
                context.stellar_objects, butler=butler, target_id=target.id
            )
            butler.merge_and_persist_records(
                "stellar_catalog", context.stellar_objects, merge_astrometry_stellar_object
            )

            return {
                "context": context,
                "stellar_objects": context.stellar_objects,
                "wcs": context.wcs,
                "image_stats": context.image.get_stats() if hasattr(context.image, "get_stats") else {},
            }

        case "spectroscopy":
            from astrometricslib.tasks.stellar_tasks.astrometry_tasks.astrometry_pipeline import (
                AstrometryPipeline,
            )
            from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import (
                SpectroscopyPipeline,
            )

            # Use the AstrometryPipeline to identify the stars in the field
            astrometry = AstrometryPipeline()
            context = astrometry.process(path, attempt_plate_solving=False)

            # The spectral stack has no WCS of its own (see the module
            # docstring on spectral_star_registration), so these stars
            # would otherwise stay permanently unidentified. If this
            # target already has a plate-solved, catalog-identified
            # star field (from an earlier astrometry run), register the
            # two point sets purely by their geometry and carry each
            # matched star's real identity over -- automatically,
            # whenever a reference field is available, no caller opt-in
            # needed. Registered against the *full* blind detection set
            # (`context.stellar_objects`, up to ~100 stars) rather than
            # just the handful spectroscopy.process() below goes on to
            # extract a spectrum for -- astroalign's triangle-asterism
            # matching needs a reasonably dense point set to find a
            # reliable transform, and 10ish points was regularly too few
            # to converge at all in practice. Registration only sets
            # identification fields, and these are the same object
            # instances spectroscopy.process() mutates next, so it
            # doesn't matter that most of them won't end up with a
            # spectrum extracted.
            reference_stellar_objects = [
                stellar_object
                for stellar_object in butler.get("stellar_catalog", {})
                if target.id in stellar_object.target_ids
                and stellar_object.is_catalog_identified
                and not stellar_object.id.endswith("::spectroscopy")
            ]
            if reference_stellar_objects:
                from astrometricslib.tasks.stellar_tasks.astrometry_tasks.spectral_star_registration import (
                    identify_spectral_stars_via_registration,
                )

                identify_spectral_stars_via_registration(context.stellar_objects, reference_stellar_objects)

            spectroscopy = SpectroscopyPipeline()
            limit = kwargs.get("limit", 10)
            stellar_objects, star_id_breakdown = _drop_unresolved_stars(
                spectroscopy.process(context, limit=limit), target_id=target.id, pipeline_name="spectroscopy"
            )

            for obj in stellar_objects:
                if target.id not in obj.target_ids:
                    obj.target_ids.append(target.id)

            stellar_objects = _reconcile_position_only_star_ids(
                stellar_objects, butler=butler, target_id=target.id
            )
            butler.merge_and_persist_records(
                "stellar_catalog", stellar_objects, merge_spectroscopy_stellar_object
            )

            from astrometricslib.models.quality_summary import (
                SpectroscopyPipelineQualityMetrics,
                SpectroscopyQualitySummary,
            )
            from astrometricslib.tasks.shared.saturation_analysis import is_saturation_significant

            zero_order_fractions = spectroscopy.last_run_zero_order_saturation_fractions
            max_zero_order_fraction = max(zero_order_fractions) if zero_order_fractions else None
            zero_order_flagged = (
                is_saturation_significant(max_zero_order_fraction)
                if max_zero_order_fraction is not None
                else False
            )
            dispersion_angles = [
                obj.dispersion_angle for obj in stellar_objects if obj.dispersion_angle is not None
            ]
            all_trail_widths = [
                width
                for obj in stellar_objects
                if obj.trail_width_px
                for width in obj.trail_width_px
                if width > 0.0  # 0.0 marks a per-position fixed-box fallback, not a real fit
            ]
            trail_width_profile_available = bool(all_trail_widths)
            median_trail_width_px = (
                statistics.median(all_trail_widths) if trail_width_profile_available else None
            )

            target.spectroscopy_quality_summary = SpectroscopyQualitySummary(
                target_id=target.id,
                spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(
                    zero_order_saturated_pixel_fraction=max_zero_order_fraction,
                    zero_order_saturation_flagged=zero_order_flagged,
                    dispersion_angle_deg=dispersion_angles[0] if dispersion_angles else None,
                    trail_width_profile_available=trail_width_profile_available,
                    median_trail_width_px=median_trail_width_px,
                    catalog_matched_star_count=star_id_breakdown.catalog_matched,
                    position_only_star_count=star_id_breakdown.position_only,
                    unresolved_star_count=star_id_breakdown.unresolved,
                ),
            )
            if zero_order_flagged:
                target.spectroscopy_quality_summary.flagged = True
                target.spectroscopy_quality_summary.flag_reasons.append(
                    "zero-order saturated in at least one processed star"
                )

            return {"context": context, "stellar_objects": stellar_objects}

        case "photometry":
            image_paths = []
            photometry_frames = []
            target_frames = frames if frames is not None else target.frames
            for frame in target_frames:
                if not frame.path:
                    continue
                if not filter_type:
                    image_paths.append(frame.path)
                    photometry_frames.append(frame)
                elif frame.filter and (
                    frame.filter.name == filter_type.upper()
                    or (
                        filter_type.upper() in ["L", "LUMINANCE"]
                        and (
                            frame.filter.name in ["L", "LUMINANCE", "NONE", "UNKNOWN"]
                            or getattr(frame.filter, "value", "").upper()
                            in ["L", "LUMINANCE", "NONE", "UNKNOWN"]
                        )
                    )
                ):
                    image_paths.append(frame.path)
                    photometry_frames.append(frame)

            if not image_paths:
                return {
                    "status": "failed",
                    "targetId": target.id,
                    "analysisMode": "photometry",
                    "message": f"No frames found for filter: {filter_type}",
                }

            from astrometricslib.models.stellar_source import VariableCandidate
            from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions

            # Photometry tracks stars via pixel-position re-centroiding
            # against a single reference frame per analysis run; that only
            # holds within one observing session (consistent framing and
            # rotation). A target's frames can span many separately
            # registered sessions, so each session gets its own
            # VariabilityAnalyzer run rather than one run spanning the
            # target's entire frame history (which corrupts tracking for
            # most stars once sessions mix).
            photometry_frames_with_timestamp = [f for f in photometry_frames if f.timestamp is not None]
            photometry_frames_without_timestamp = [f for f in photometry_frames if f.timestamp is None]
            photometry_sessions = derive_target_sessions(target.id, photometry_frames_with_timestamp)

            if not photometry_sessions:
                return {
                    "status": "failed",
                    "targetId": target.id,
                    "analysisMode": "photometry",
                    "message": (
                        "No frames with a usable capture timestamp to assign a session "
                        f"for filter: {filter_type}"
                    ),
                }

            per_session_results = []
            all_candidates = []
            all_rejected_files = []
            all_frame_ensemble_composition = []
            session_empty_reasons = []
            # Only session-prefix ids when there's more than one session,
            # so the common single-session target keeps today's plain
            # Star_N ids and doesn't churn its stellar_catalog rows.
            # Cross-session star matching (below) is likewise skipped
            # entirely for a single session -- there is nothing to
            # cross-match a lone session against.
            id_prefix_enabled = len(photometry_sessions) > 1

            # Analyze Target should identify the actual stars in an
            # image against a real catalog, not just track anonymous
            # per-run pixel detections. Opt-in for now (see rollout
            # notes) while this is verified against real data before
            # becoming the caller's default.
            use_astrometry_seed = bool(kwargs.get("use_astrometry_seed", True))
            star_identifier = None
            if use_astrometry_seed:
                from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import (
                    StarIdentifier,
                )

                star_identifier = StarIdentifier()

            session_wcs_map: dict[str, Any] = {}
            astrometry_identified_star_count = 0
            sessions_with_reused_header_wcs: list[str] = []
            sessions_with_replaced_header_wcs: list[str] = []

            for session in photometry_sessions:
                id_prefix = f"{session.id}:" if id_prefix_enabled else ""
                analyzer, session_candidates, identify_result = _run_variability_analysis_for_session(
                    session,
                    kwargs.get("max_workers"),
                    id_prefix,
                    target=target,
                    star_identifier=star_identifier,
                    use_astrometry_seed=use_astrometry_seed,
                )
                if identify_result is not None:
                    session_wcs_map[session.id] = identify_result.wcs
                    astrometry_identified_star_count += identify_result.simbad_matched_count
                    if identify_result.reused_existing_header_wcs:
                        sessions_with_reused_header_wcs.append(session.id)
                    if identify_result.header_wcs_replaced_after_verification:
                        sessions_with_replaced_header_wcs.append(session.id)
                if not analyzer.stellar_objects:
                    session_empty_reasons.append(
                        f"session {session.id}: reference-frame star detection failed, 0 stars processed"
                    )
                per_session_results.append((analyzer, session_candidates))
                all_candidates.extend(session_candidates)
                all_rejected_files.extend(analyzer.rejected_files)
                all_frame_ensemble_composition.extend(analyzer.frame_ensemble_composition)

            # Captured before cross-session merging/re-flagging below so
            # each candidate reflects its own session's local adaptive
            # cutoff -- VariableCandidate copies plain float values, so
            # later mutating the underlying StellarObjects (merging
            # light curves, recomputing a long-term CV) cannot retroactively
            # change an already-built VariableCandidate.
            candidates_formatted = [
                VariableCandidate(
                    id=star.id,
                    meanFlux=star.mean_flux,
                    coefficientOfVariation=star.coefficient_of_variation,
                    score=min(1.0, star.variability_score / 100.0),
                    ra=float(star.right_ascension) if star.right_ascension else 0.0,
                    dec=float(star.declination) if star.declination else 0.0,
                )
                for star in all_candidates
            ]

            sessions_missing_wcs: list[str] = []
            cross_session_match_count = 0
            long_term_candidates = []

            if id_prefix_enabled:
                all_stellar_objects, sessions_missing_wcs, cross_session_match_count = (
                    _match_and_merge_across_sessions(
                        photometry_sessions, per_session_results, target, session_wcs_map=session_wcs_map
                    )
                )
                if cross_session_match_count > 0:
                    from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
                        identify_long_term_variable_candidates,
                    )

                    long_term_candidates = identify_long_term_variable_candidates(all_stellar_objects)
            else:
                all_stellar_objects = per_session_results[0][0].stellar_objects

            long_term_candidates_formatted = [
                VariableCandidate(
                    id=star.id,
                    meanFlux=star.mean_flux,
                    coefficientOfVariation=star.coefficient_of_variation,
                    score=min(1.0, star.variability_score / 100.0),
                    ra=float(star.right_ascension) if star.right_ascension else 0.0,
                    dec=float(star.declination) if star.declination else 0.0,
                )
                for star in long_term_candidates
            ]

            all_stellar_objects, star_id_breakdown = _drop_unresolved_stars(
                all_stellar_objects, target_id=target.id, pipeline_name="photometry"
            )

            for obj in all_stellar_objects:
                if target.id not in obj.target_ids:
                    obj.target_ids.append(target.id)

            all_stellar_objects = _reconcile_position_only_star_ids(
                all_stellar_objects, butler=butler, target_id=target.id
            )
            butler.merge_and_persist_records(
                "stellar_catalog", all_stellar_objects, merge_photometry_stellar_object
            )

            from astrometricslib.models.quality_summary import (
                ExcludedFrame,
                PhotometryPipelineQualityMetrics,
                PhotometryQualitySummary,
                TargetSessionContribution,
            )
            from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import (
                median_light_curve_scatter_mag,
            )

            rejected_paths = set(all_rejected_files)
            photometry_session_breakdown = [
                TargetSessionContribution(
                    session_id=session.id,
                    frames_contributed=len(session.frame_paths),
                    frames_clipped=sum(1 for path in session.frame_paths if path in rejected_paths),
                )
                for session in photometry_sessions
            ]

            frames_processed = sum(len(session.frame_paths) for session in photometry_sessions) - len(
                all_rejected_files
            )

            rejected_frames = [
                ExcludedFrame(path=path, reason="global frame outlier (ensemble median MAD-clipped)")
                for path in all_rejected_files
            ] + [
                ExcludedFrame(
                    path=frame.path,
                    reason="no capture timestamp available; cannot be assigned to a session",
                )
                for frame in photometry_frames_without_timestamp
            ]

            target.photometry_quality_summary = PhotometryQualitySummary(
                target_id=target.id,
                target_session_ids=[session.id for session in photometry_sessions],
                target_session_breakdown=photometry_session_breakdown,
                photometry_metrics=PhotometryPipelineQualityMetrics(
                    stars_processed=len(all_stellar_objects),
                    stars_found=len(all_stellar_objects),
                    frames_processed=frames_processed,
                    rejected_frames=rejected_frames,
                    frame_ensemble_composition=all_frame_ensemble_composition,
                    variable_candidate_count=len(all_candidates),
                    cross_session_match_count=cross_session_match_count,
                    sessions_missing_wcs=sessions_missing_wcs,
                    long_term_variable_candidate_count=len(long_term_candidates),
                    astrometry_identified_star_count=astrometry_identified_star_count,
                    sessions_with_reused_header_wcs=sessions_with_reused_header_wcs,
                    sessions_with_replaced_header_wcs=sessions_with_replaced_header_wcs,
                    catalog_matched_star_count=star_id_breakdown.catalog_matched,
                    position_only_star_count=star_id_breakdown.position_only,
                    unresolved_star_count=star_id_breakdown.unresolved,
                    light_curve_scatter_rms_mag=median_light_curve_scatter_mag(all_stellar_objects),
                ),
            )
            # The rejected frames are recorded in the metrics either way;
            # this only decides whether the count is worth a human's
            # attention, which routine clipping is not.
            frames_contributed_total = sum(
                contribution.frames_contributed for contribution in photometry_session_breakdown
            )
            rejection_fraction = (
                len(all_rejected_files) / frames_contributed_total if frames_contributed_total else 0.0
            )
            if (
                len(all_rejected_files) >= MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG
                and rejection_fraction >= MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG
            ):
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{len(all_rejected_files)} of {frames_contributed_total} frame(s) "
                    f"({rejection_fraction:.0%}) rejected as global ensemble outliers, which is high "
                    "enough to suspect the comparison ensemble or the observing conditions"
                )
            if photometry_frames_without_timestamp:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{len(photometry_frames_without_timestamp)} frame(s) excluded for missing "
                    "capture timestamp"
                )
            if session_empty_reasons:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.extend(session_empty_reasons)
            if sessions_missing_wcs:
                target.photometry_quality_summary.flagged = True
                target.photometry_quality_summary.flag_reasons.append(
                    f"{len(sessions_missing_wcs)} session(s) could not be plate-solved for "
                    f"cross-session star matching: {', '.join(sessions_missing_wcs)}"
                )

            return {
                "status": "completed",
                "targetId": target.id,
                "totalImages": len(image_paths),
                "analysisMode": "photometry",
                "starsProcessed": len(all_stellar_objects),
                "spectraExtracted": 0,
                "starsFound": len(all_stellar_objects),
                "framesProcessed": frames_processed,
                "rejectedCount": len(all_rejected_files),
                "rejectedFiles": all_rejected_files,
                "variableCandidates": candidates_formatted,
                "longTermVariableCandidates": long_term_candidates_formatted,
                "crossSessionMatchCount": cross_session_match_count,
            }

        case "asteroid_recovery":
            from astrometricslib.models.quality_summary import (
                AsteroidRecoveryPipelineQualityMetrics,
                AsteroidRecoveryQualitySummary,
                TargetSessionContribution,
            )
            from astrometricslib.tasks.moving_object_tasks.moving_object_pipeline_tasks import (
                AsteroidRecoveryPipeline,
            )
            from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions

            pipeline = AsteroidRecoveryPipeline()
            all_candidates = pipeline.process(target)
            metrics = pipeline.last_run_metrics
            # Persist only candidates that survived the discrimination
            # cascade (or were matched to a known body) -- `process()`
            # deliberately returns every rejected chain too (cosmic
            # rays, hot pixels, missed stars) so `metrics` above can
            # summarize them, but writing all of those into the
            # target's persisted record is unbounded: a single dense
            # field can produce tens of thousands of single-frame noise
            # chains, each carrying its own frame-detection payload.
            target.asteroid_candidates = [
                candidate
                for candidate in all_candidates
                if candidate.cascade_stage
                in (CascadeStage.RATE_LINEARITY_CONFIRMED, CascadeStage.EPHEMERIS_MATCHED)
            ]

            light_frames = [frame for frame in target.frames if frame.role == "LIGHT"]
            asteroid_recovery_sessions = derive_target_sessions(target.id, light_frames)
            # Per-session frame-exclusion identity isn't tracked by the
            # pipeline today (only the aggregate
            # frames_excluded_missing_pointing_metadata count is), so
            # frames_clipped is left at 0 here rather than fabricating a
            # breakdown.
            asteroid_recovery_session_breakdown = [
                TargetSessionContribution(
                    session_id=session.id,
                    frames_contributed=len(session.frame_paths),
                    frames_clipped=0,
                )
                for session in asteroid_recovery_sessions
            ]

            target.asteroid_recovery_quality_summary = AsteroidRecoveryQualitySummary(
                target_id=target.id,
                target_session_ids=[session.id for session in asteroid_recovery_sessions],
                target_session_breakdown=asteroid_recovery_session_breakdown,
                asteroid_recovery_metrics=AsteroidRecoveryPipelineQualityMetrics(**metrics),
            )
            if metrics.get("frames_excluded_missing_pointing_metadata", 0) > 0:
                target.asteroid_recovery_quality_summary.flagged = True
                target.asteroid_recovery_quality_summary.flag_reasons.append(
                    f"{metrics['frames_excluded_missing_pointing_metadata']} frame(s) excluded for "
                    "missing RA/DEC/NAXIS pointing metadata"
                )
            candidates_awaiting_recovery = sum(
                1
                for candidate in target.asteroid_candidates
                if candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
            )
            if candidates_awaiting_recovery > 0:
                target.asteroid_recovery_quality_summary.flagged = True
                target.asteroid_recovery_quality_summary.flag_reasons.append(
                    f"{candidates_awaiting_recovery} candidate(s) confirmed as movers but not "
                    "matched to a known body -- worth a manual look"
                )

            return {
                "status": "completed",
                "targetId": target.id,
                "analysisMode": "asteroid_recovery",
                "candidatesDetected": metrics.get("candidates_detected", 0),
                "candidatesRateLinearityConfirmed": metrics.get("candidates_rate_linearity_confirmed", 0),
                "candidatesEphemerisMatched": metrics.get("candidates_ephemeris_matched", 0),
                "candidates": target.asteroid_candidates,
            }

        case _:
            raise ValueError(f"Unknown analysis type: {pipeline_type}")


def reindex_frames(
    target: Target,
    prune_missing: bool = False,
    butler=None,  # ruff: ignore[missing-type-function-argument]
    refresh_headers: bool = False,
) -> None:
    """Update our saved list of images from the actual files on disk.

    This function adds any new image files it finds and updates the
    total exposure time. If `refresh_headers` is True, it will also
    re-read the FITS header data for files we already know about.
    """
    if butler is None:
        from astrometricslib.data_access.butler import DiskButler

        butler = DiskButler()

    if prune_missing:
        target.frames = [
            f
            for f in target.frames
            if butler.exists("raw_frame", {"path": f.path})
            and not any(k in f.path.lower() for k in ("_stacked", "starless", "starmask"))
        ]

    butler.get("raw_frames", {"target": target, "refresh_headers": refresh_headers})


def get_header_information(target: Target, frame_path: str) -> list[dict[str, str]]:
    """Get the raw metadata (FITS header info) for an image.

    Checks to make sure the target name actually owns this image
    before reading it.

    Parameters
    ----------
    target : Target
        The target the image should belong to.
    frame_path : str
        The file path to the FITS image.

    Returns
    -------
    header_cards : list of dict
        A list of key-value pairs from the image header.

    Raises
    ------
    ValueError
        If the image file doesn't belong to this target.
    """
    is_valid = any(f.path == frame_path for f in target.frames)
    if not is_valid:
        if frame_path in [target.processed_image, target.stacked_image, target.stacked_spectral_target]:
            is_valid = True

    if not is_valid:
        raise ValueError(f"Path {frame_path} does not belong to target {target.id}")

    from astrometricslib.data_access import image_conversions

    return image_conversions.get_fits_header(frame_path)


def stack_and_solve(
    target: Target,
    log_file: str | None = None,
    frames_to_stack: list[FrameRecord] | None = None,
    filter_type: Any | None = None,
    rejection_sigma: tuple[float, float] | None = None,
    filter_wfwhm: str | None = None,
    filter_round: str | None = None,
    stack_weight: str | None = None,
    generate_rejmap: bool | None = None,
    register_job: bool = True,
) -> str | None:
    """Run the Siril stacking tool to combine the target's images.

    You can optionally provide a specific list of frames or a filter
    type. You can also override settings like the star roundness limit
    or rejection sigma. If you leave these blank, it uses the defaults.

    Parameters
    ----------
    register_job : bool, optional
        Set to True (default) if you want this run to automatically
        show up in the user interface's job tracker. Set to False if
        you are calling this from a tool that already tracks its own jobs.

    Returns
    -------
    stacked_path : str or None
        The path to the final combined image file, or None if it failed.
    """
    job_id = None
    logger_if = None
    job_logger = None
    job_log_handlers: list = []
    pipeline_logger = None

    if register_job:
        try:
            import os
            import uuid
            from datetime import datetime

            from astrometricslib.drivers.logger_interface import DbLogHandler, LoggerInterface
            from astrometricslib.utilities.config_loader import get_configuration
            from astrometricslib.utilities.pipeline_models import ProcessingJob

            cfg = get_configuration()
            logger_if = LoggerInterface(cfg.get_logs_db_path())
            job_id = str(uuid.uuid4())

            safe_target = target.id.replace(" ", "_").replace("/", "_")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = cfg.get_logs_path()
            os.makedirs(log_dir, exist_ok=True)
            job_log_file_path = log_file or str(log_dir / f"stacking_{safe_target}_{timestamp}.log")

            job_logger = logging.getLogger(f"job_{job_id}")
            job_logger.propagate = False
            job_logger.setLevel(logging.INFO)
            file_handler = logging.FileHandler(job_log_file_path)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            db_handler = DbLogHandler(logger_if, job_id=job_id)
            job_logger.addHandler(file_handler)
            job_logger.addHandler(db_handler)

            # Also attach to the "astrometricslib" package logger, the
            # common ancestor of every module logger used deeper in the
            # stacking pipeline (stacking_tasks, siril_interface, etc,
            # all via logging.getLogger(__name__) with propagate=True by
            # default) -- mirrors analyze_target's identical setup.
            job_log_handlers = [file_handler, db_handler]
            pipeline_logger = logging.getLogger("astrometricslib")
            for handler in job_log_handlers:
                pipeline_logger.addHandler(handler)
            pipeline_logger.setLevel(logging.INFO)

            logger_if.upsert_job(
                ProcessingJob(
                    id=job_id,
                    target_id=target.id,
                    job_type="stacking",
                    status="started",
                    progress_current=0,
                    progress_total=100,
                    log_file_path=job_log_file_path,
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                )
            )
            job_logger.info(f"[{target.id}] Stacking job started (Job: {job_id}).")
        except Exception as job_err:
            logger.warning(f"Could not register stacking job in astrometrics_log.db: {job_err}")

    def _update_job_status(status_val: str, progress_val: int = 100):  # ruff: ignore[missing-return-type-private-function]
        if logger_if and job_id:
            try:
                j = logger_if.get_job(job_id)
                if j:
                    j.status = status_val
                    j.progress_current = progress_val
                    j.updated_at = datetime.now().isoformat()
                    logger_if.upsert_job(j)
            except Exception as exc:
                logger.debug("Failed to persist stacking job status update for job '%s': %s", job_id, exc)
        if job_logger:
            if status_val == "completed":
                job_logger.info(f"[{target.id}] Stacking completed successfully.")
            elif status_val == "failed":
                job_logger.error(f"[{target.id}] Stacking failed.")

    try:
        from astrometricslib.tasks.target_tasks import stacking_tasks

        stacked_path = stacking_tasks.stack_frames(
            target,
            log_file,
            frames_to_stack,
            filter_type,
            rejection_sigma=rejection_sigma,
            filter_wfwhm=filter_wfwhm,
            filter_round=filter_round,
            stack_weight=stack_weight,
            generate_rejmap=generate_rejmap,
        )
        # analyze_target(pipeline_type="astrometry") plate-solves
        # target.stacked_image
        # specifically (see analyze_target's path-resolution logic) -- it has
        # no notion of a spectral stack, so only run it when this call just
        # produced a *standard* stack. Checking which of
        # stacked_image/stacked_spectral_target now equals stacked_path
        # tells us which one stacking_tasks.stack_frames just set,
        # without needing a separate return value for it. analyze_target
        # builds and assigns target.astrometry_quality_summary itself
        # (including flagging a failed-but-attempted solve) -- the only
        # case it can't cover is a hard solver error, which raises before
        # analyze_target gets to build the summary at all, so that's
        # handled here instead.
        if stacked_path and stacked_path == target.stacked_image:
            try:
                # register_job=False: this stacking run already registered
                # its own job above, and analyze_target's docstring is
                # explicit about why a nested call must suppress its own
                # registration -- otherwise one stack_and_solve(solve=True)
                # call produces two ownerless "started" rows in the UI job
                # manager ("stacking" and "analysis") for what the caller
                # sees as a single action.
                analyze_target(target, pipeline_type="astrometry", register_job=False)
            except Exception as stacking_error:
                logger.warning(f"Astrometry plate solving failed after stacking: {stacking_error}")
                from astrometricslib.models.quality_summary import (
                    AstrometryPipelineQualityMetrics,
                    AstrometryQualitySummary,
                )

                target.astrometry_quality_summary = AstrometryQualitySummary(
                    target_id=target.id,
                    flagged=True,
                    flag_reasons=["plate solve failed"],
                    astrometry_metrics=AstrometryPipelineQualityMetrics(
                        sources_detected=0,
                        solve_attempted=False,
                        plate_solve_succeeded=False,
                        simbad_matched_count=0,
                    ),
                )
        _update_job_status("completed" if stacked_path else "failed", 100)
        return stacked_path
    except Exception as exc:
        _update_job_status("failed", 0)
        raise exc
    finally:
        if pipeline_logger:
            for handler in job_log_handlers:
                pipeline_logger.removeHandler(handler)


def _stack_frames_with_timeout(
    target: Target, frames_to_stack: list[FrameRecord], timeout_seconds: int | None = None
) -> str | None:
    """Run the image stacker with a time limit so it doesn't freeze forever.

    The Siril stacking program can sometimes get stuck. This function
    runs it in the background and will kill it if it takes too long.
    The time limit only counts time spent actually working, not time
    spent waiting in line for the CPU.

    Parameters
    ----------
    target : Target
        The target name being stacked.
    frames_to_stack : list
        The image frames we want to combine.
    timeout_seconds : int, optional
        The maximum time allowed in seconds. If blank, it calculates
        a budget based on the number of frames.

    Returns
    -------
    stacked_path : str or None
        The path to the combined image file, or None if it timed out.

    Raises
    ------
    Exception
        Any error that happened during stacking is passed up to here.
    """  # ruff: ignore[docstring-extraneous-exception] -- `raise
    # outcome["error"]` re-raises a captured exception object,
    # which pydoclint cannot resolve to a declared type statically.
    from astrometricslib.drivers import siril_interface

    if timeout_seconds is None:
        timeout_seconds = compute_stacking_timeout_seconds(len(frames_to_stack))

    outcome: dict[str, Any] = {}

    def _run_stacking() -> None:
        try:
            outcome["path"] = stack_and_solve(target, frames_to_stack=frames_to_stack)
        except Exception as stacking_error:
            outcome["error"] = stacking_error

    siril_interface.reset_siril_lock_wait_seconds()
    stacking_thread = threading.Thread(target=_run_stacking, daemon=True)
    started_at = time.monotonic()
    stacking_thread.start()

    # Polled rather than a single join so the deadline can absorb lock
    # waits that only become known while this stack is queued. The
    # interval is short enough that the overshoot past a real timeout is
    # negligible against a budget measured in minutes.
    while True:
        stacking_thread.join(_STACKING_TIMEOUT_POLL_SECONDS)
        if not stacking_thread.is_alive():
            break
        elapsed_seconds = time.monotonic() - started_at
        if elapsed_seconds >= timeout_seconds + siril_interface.get_siril_lock_wait_seconds():
            break

    if stacking_thread.is_alive():
        lock_wait_seconds = siril_interface.get_siril_lock_wait_seconds()
        print(
            f"[{target.id}] Stacking timed out after {timeout_seconds} seconds "
            f"of working time ({lock_wait_seconds:.0f}s of Siril-lock wait excluded). "
            f"Abandoning this stack."
        )
        # A timeout is a quality event, not just a log line: recorded on
        # the target's existing stack summary when there is one, so the
        # abandoned stack is queryable rather than only discoverable by
        # reading the run's output. The summary may be absent entirely --
        # stack_frames builds it, and this stack never got that far -- in
        # which case the timeout stays a log-only fact.
        summary = getattr(target, "stack_quality_summary", None)
        metrics = getattr(summary, "stacking_metrics", None) if summary else None
        if metrics is not None:
            metrics.timed_out = True
            summary.flagged = True
            summary.flag_reasons.append(f"stacking timed out after {timeout_seconds}s")
        return None

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("path")


def run_full_pipeline(
    target: Target,
    astrometrics: Any,
    max_workers: int | None = None,
    *,
    camera_name: str,
    focal_length_mm: float | None = None,
) -> dict[str, str]:
    """Run the complete start-to-finish processing pipeline for a target.

    This runs stacking, position solving (astrometry), brightness
    tracking (photometry), and light spectrum (spectroscopy) in order,
    then saves the target's data to the database.

    Parameters
    ----------
    target : Target
        The target we want to process.
    astrometrics : Any
        The system interface that gives us config settings and database access.
    max_workers : int, optional
        How many parallel processes to use during the brightness tracking step.
    camera_name : str
        The name of the camera to process images for. Any images taken
        by a different camera will be ignored.

    Returns
    -------
    stack_outputs : dict
        A dictionary mapping the stack type ("standard" or "spectral")
        to the final saved image file path.

    Raises
    ------
    ValueError
        If the stacking process fails to make a final image file.
    """
    stack_outputs: dict[str, str] = {}
    print("\n==========================================")
    print(f"STARTING BATCH PROCESSING FOR TARGET: {target.id}")
    print("==========================================")

    # Restrict all processing to frames captured with the requested
    # camera; every other camera's frames on this target are excluded.
    camera_frames = select_frames_for_camera(target, camera_name)

    # Then narrow to a single optic. Frames of different focal length
    # image at different scales -- this library's 300mm and 405mm optics
    # differ by 1.35x -- so a stack blending them has no single pixel
    # scale, cannot be plate solved accurately, and produces fluxes that
    # are not comparable between frames. Seven targets were being
    # stacked that way, NGC 7023 worst at 424 frames of one optic mixed
    # with 111 of the other.
    if focal_length_mm is not None:
        requested_key_suffix = f"@{round(float(focal_length_mm))}mm"
        selected_frames = [
            frame
            for frame in camera_frames
            if (frame_configuration_key(frame) or "").endswith(requested_key_suffix)
        ]
        if not selected_frames:
            print(
                f"[{target.id}] No frames at {focal_length_mm:g}mm for camera '{camera_name}'. "
                "Skipping all processing steps."
            )
            return stack_outputs
        unassignable = frames_missing_focal_length(target, camera_name)
        if unassignable:
            # Never dropped silently: a frame with no FOCALLEN cannot be
            # grouped, and on this library that is 602 frames. See
            # scripts/backfill_focal_length.
            print(
                f"[{target.id}] {len(unassignable)} frame(s) excluded: no FOCALLEN recorded, "
                "so their optic is unknown."
            )
        camera_frames = selected_frames

    if not camera_frames:
        print(
            f"[{target.id}] No frames matching camera '{camera_name}' found for this target. "
            "Skipping all processing steps."
        )
        return stack_outputs

    print(f"[{target.id}] Stacking frames...")
    target_frames = [
        frame
        for frame in camera_frames
        if not any(k in frame.path.lower() for k in ("_stacked", "starless", "starmask"))
    ]

    # Check if there is a mixed set of spectral and standard frames.
    # If so, run standard stacking on standard frames, and spectral
    # stacking on spectral frames.
    standard_frames = []
    spectral_frames = []
    for frame in target_frames:
        is_spectral = (
            frame.filter == FilterType.SPEC
            or getattr(frame.filter, "name", None) == "SPEC"
            or str(frame.filter).upper() in ("SPEC", "STAR ANALYZER 200")
        )
        if is_spectral:
            spectral_frames.append(frame)
        else:
            standard_frames.append(frame)

    # Deliberately no slot acquired here. `siril_interface.siril_process_lock`
    # takes one around the Siril launch itself, and taking a second from the
    # same "siril" semaphore here nests them: with two slots and two workers
    # each takes this outer slot, then both wait forever for an inner slot
    # neither can release. Observed on 2026-08-24 as two workers parked on
    # "Waiting for a free Siril slot" with no Siril process running.
    #
    # The driver is also the better place for it -- it covers every Siril
    # launch, including the backend service's, not just this batch path.
    if standard_frames and spectral_frames:
        print(
            f"[{target.id}] Target contains mixed frames. Stacking standard and spectral frames separately."
        )
        stacked_output = _stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking failed on mixed target.")
        stacked_spectral = _stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking failed on mixed target.")
        print(f"[{target.id}] Stacking succeeded: Standard={stacked_output}, Spectral={stacked_spectral}")
        stack_outputs["standard"] = stacked_output
        stack_outputs["spectral"] = stacked_spectral
    elif standard_frames:
        stacked_output = _stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Standard stacking succeeded: {stacked_output}")
        stack_outputs["standard"] = stacked_output
    elif spectral_frames:
        stacked_spectral = _stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Spectral stacking succeeded: {stacked_spectral}")
        stack_outputs["spectral"] = stacked_spectral
    else:
        print(
            f"[{target.id}] No valid frames matching camera '{camera_name}' found for stacking. "
            "Skipping stacking step."
        )

    # 1b. Tracking Analysis. Read-only over the registration data
    # stacking just wrote, so it belongs right after stacking and
    # before any pipeline that could fail and end this run early --
    # rig-quality findings are worth having even if astrometry or
    # photometry later fails on this target.
    from astrometricslib.tasks.target_tasks.tracking_analysis_tasks import build_tracking_quality_summary

    try:
        target.tracking_quality_summary = build_tracking_quality_summary(target)
    except Exception as tracking_error:
        logger.warning(f"[{target.id}] Tracking analysis failed: {tracking_error}")

    # 2. Astrometry Analysis
    print(f"[{target.id}] Running Astrometry Analysis...")
    astrometry_results = analyze_target(target, pipeline_type="astrometry", butler=astrometrics.butler)
    if astrometry_results is None:
        raise ValueError("Astrometry analysis failed.")
    print(
        f"[{target.id}] Astrometry Analysis complete. "
        f"Resolved WCS: {astrometry_results.get('wcs') is not None}"
    )

    # 3. Photometry Analysis
    analysis_concurrency = astrometrics.config.get_analysis_concurrency()
    print(f"[{target.id}] Running Photometry/Variability Analysis...")
    with disk_interface.acquire_resource_slot(astrometrics.config, "analysis", analysis_concurrency):
        photometry_results = analyze_target(
            target,
            pipeline_type="photometry",
            frames=camera_frames,
            butler=astrometrics.butler,
            max_workers=max_workers,
        )
    if photometry_results.get("status") == "failed":
        raise ValueError(f"Photometry analysis failed: {photometry_results.get('message')}")
    print(
        f"[{target.id}] Photometry Analysis complete. Stars found: {photometry_results.get('starsFound', 0)}"
    )

    # 4. Spectroscopy Analysis (only when this target actually has a
    # SPEC stack)
    if spectral_frames:
        print(f"[{target.id}] Running Spectroscopy Analysis...")
        with disk_interface.acquire_resource_slot(astrometrics.config, "analysis", analysis_concurrency):
            spectroscopy_results = analyze_target(
                target, pipeline_type="spectroscopy", limit=10, butler=astrometrics.butler
            )
        if spectroscopy_results is None:
            raise ValueError("Spectroscopy analysis failed.")
        print(f"[{target.id}] Spectroscopy Analysis complete.")
    else:
        print(f"[{target.id}] No SPEC frames found for this target. Skipping spectroscopy analysis.")

    # Save this target's own record (safe under concurrent callers,
    # unlike a full-catalog resync)
    astrometrics.butler.put(target, "target_record", {})
    print(f"[{target.id}] Processing completed and metadata saved successfully.")

    return stack_outputs


def add_frame(
    target: Target, path: str, role: str = "LIGHT", filter_type: str | None = None, camera: str | None = None
) -> FrameRecord:
    """Add a single image frame to the target by reading its metadata.

    Returns
    -------
    frame_record : FrameRecord
        The newly added frame record.
    """
    from astrometricslib.tasks.target_tasks import stacking_tasks

    return stacking_tasks.add_frame(target, path, role, filter_type, camera)
