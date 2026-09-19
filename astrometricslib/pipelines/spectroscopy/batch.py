"""Tools for running spectroscopy processing on many images at once.

This file connects the specific spectroscopy tasks to the general
parallel processing system (which handles running multiple tasks
at the same time). It groups images by the observing session they
belong to, identifies the stars in one reference image, and then
uses those same stars for all the other images in the session.
"""

import logging
import math
from collections.abc import Callable
from typing import Any

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.utilities import parallel_batch
from astrometricslib.utilities.concurrency import resolve_worker_counts

logger = logging.getLogger(__name__)

# The most stars followed in each raw frame. Following the spectrum of a
# star over time only needs the target itself and a few well-known
# reference stars, and every extra star is one more spectrum to extract
# from every frame of every night. Ten matches the per-image limit the
# other spectroscopy entry points already use.
MAXIMUM_TRACKED_STARS_PER_FRAME = 10

# A magnitude below this is an instrument reading, not a catalog
# magnitude. Must match _BRIGHTEST_CATALOG_MAGNITUDE in
# backend/services/data/stellar_service.py.
_BRIGHTEST_CATALOG_MAGNITUDE = -2.0

# Prefix of the id given to a star that was found in an image but never
# matched to a catalog. Must match POSITION_ONLY_STAR_ID_PREFIX in
# astrometricslib/drivers/catalog_access.py.
_POSITION_ONLY_STAR_ID_PREFIX = "FIELD_J"


def _is_verified_catalog_star(star: StellarObject) -> bool:
    """Say whether a star's identity and properties are known from a catalog.

    A star counts as verified when it was matched to a catalog entry (for
    example SIMBAD or Gaia) and that entry gave both a real magnitude and
    a spectral type. A star found only by its position in an image, or
    matched but with no spectral type on record, is not verified.

    Parameters
    ----------
    star : `StellarObject`
        The star to check.

    Returns
    -------
    is_verified : `bool`
        `True` when the star is a catalog match with a magnitude and a
        spectral type.
    """
    if not star.is_catalog_identified or star.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX):
        return False
    magnitude = star.magnitude
    has_magnitude = (
        isinstance(magnitude, int | float)
        and not isinstance(magnitude, bool)
        and math.isfinite(magnitude)
        and magnitude >= _BRIGHTEST_CATALOG_MAGNITUDE
    )
    spectral_type = (star.spectral_type or "").strip()
    return has_magnitude and spectral_type not in ("", "Unknown")


def select_temporal_tracking_stars(
    stellar_objects: list[StellarObject],
    center_ra: float | None,
    center_dec: float | None,
    limit: int = MAXIMUM_TRACKED_STARS_PER_FRAME,
) -> list[StellarObject]:
    """Choose the few stars whose spectra are followed across raw frames.

    A noisy raw frame can show over a hundred detections, and most of them
    are faint, unnamed, or not real stars. Extracting a spectrum for each
    would cost a lot of time and mostly record noise. Following how a
    spectrum changes over time only needs:

    1. The primary target star: the identified star closest to the
       target's coordinates.
    2. Verified catalog stars (see `_is_verified_catalog_star`), brightest
       first.

    Stars known only by their position (ids starting with ``FIELD_J``)
    are never chosen.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        The stars identified in the session's reference image.
    center_ra : `float` or `None`
        The target's right ascension in degrees, if known.
    center_dec : `float` or `None`
        The target's declination in degrees, if known.
    limit : `int`, optional
        The most stars to return.

    Returns
    -------
    tracked_stars : `list` [`StellarObject`]
        The primary target star first (when it can be found), then
        verified catalog stars by increasing magnitude, at most `limit`.
    """
    candidates = [
        star
        for star in stellar_objects
        if star.is_catalog_identified
        and not star.id.startswith(_POSITION_ONLY_STAR_ID_PREFIX)
        and star.right_ascension not in (None, "")
        and star.declination not in (None, "")
    ]

    primary_star = None
    if center_ra is not None and center_dec is not None and candidates:
        primary_star = min(
            candidates,
            key=lambda star: _angular_separation_degrees(
                float(star.right_ascension), float(star.declination), center_ra, center_dec
            ),
        )

    verified_stars = sorted(
        (star for star in candidates if star is not primary_star and _is_verified_catalog_star(star)),
        key=lambda star: float(star.magnitude),
    )
    tracked_stars = ([primary_star] if primary_star is not None else []) + verified_stars
    return tracked_stars[:limit]


def _angular_separation_degrees(
    ra_first: float, dec_first: float, ra_second: float, dec_second: float
) -> float:
    """Measure the angle between two points on the sky.

    Parameters
    ----------
    ra_first, dec_first : `float`
        The first point's right ascension and declination, in degrees.
    ra_second, dec_second : `float`
        The second point's right ascension and declination, in degrees.

    Returns
    -------
    separation : `float`
        The angle between the points, in degrees.
    """
    ra_first_rad, dec_first_rad = math.radians(ra_first), math.radians(dec_first)
    ra_second_rad, dec_second_rad = math.radians(ra_second), math.radians(dec_second)
    # The haversine formula stays accurate for very small angles, where a
    # plain arccos of a dot product would lose precision.
    half_delta_dec = math.sin((dec_second_rad - dec_first_rad) / 2.0)
    half_delta_ra = math.sin((ra_second_rad - ra_first_rad) / 2.0)
    haversine = half_delta_dec**2 + math.cos(dec_first_rad) * math.cos(dec_second_rad) * half_delta_ra**2
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(haversine))))


def _process_single_spectroscopy_frame_worker(path: str, target_id: str) -> dict:
    """Run spectroscopy analysis on one frame in its own process.

    This function is designed to run independently in the background
    (parallel processing). It sets up its own environment so it doesn't
    interfere with other tasks.

    Parameters
    ----------
    path : `str`
        The location of the image file to analyze.
    target_id : `str`
        The ID of the target this image belongs to.

    Returns
    -------
    result : `dict`
        A dictionary with "status" (success/failed), "error" (if any),
        and "stars_processed" (how many stars were analyzed).
    """
    from astrometricslib import Astrometrics
    from astrometricslib.models.target import FrameRecord
    from astrometricslib.pipelines.tasks import analyze_target

    result = {"status": "failed", "error": None, "stars_processed": 0}
    try:
        astrometrics = Astrometrics()
        target = astrometrics.targets.get(target_id)
        if target is None:
            result["error"] = "Target not found in catalog"
            return result

        analysis_outcome = analyze_target(
            target,
            frames=[FrameRecord(path=path)],
            pipeline_type="spectroscopy",
            catalog_access=astrometrics.catalog_access,
        )
        result["stars_processed"] = len(analysis_outcome.get("stellar_objects") or [])
        result["status"] = "success"
    except Exception as processing_error:
        result["error"] = str(processing_error)

    return result


def process_spectroscopy_frames(
    api: Any,
    target_id: str,
    paths: list[str],
    max_workers: int | None = None,
    on_item_complete: Callable[[str, dict, int, int], None] | None = None,
) -> parallel_batch.BatchRunSummary:
    """Process many spectroscopy frames at the same time.

    (Note: This is an older function. The newer version,
    `process_spectroscopy_frames_by_session`, is better because it
    groups images by session so stars can be tracked across frames.)

    Parameters
    ----------
    api : `Any`
        The main program interface.
    target_id : `str`
        The ID of the target the images belong to.
    paths : `list` of `str`
        The file locations of the images to process.
    max_workers : `int` or `None`, optional
        How many processes to run at once.
    on_item_complete : `Callable`, optional
        A function to call every time one image finishes.

    Returns
    -------
    summary : `BatchRunSummary`
        A report showing how many images succeeded or failed.
    """
    if max_workers is None:
        worker_counts = resolve_worker_counts("1", api.config.get_photometry_workers())
        max_workers = worker_counts.inner_worker_count

    return parallel_batch.run_parallel_batch(
        paths,
        _process_single_spectroscopy_frame_worker,
        worker_arguments=(target_id,),
        max_workers=max_workers,
        niceness=api.config.get_worker_niceness(),
        on_item_complete=on_item_complete,
    )


def _fallback_independent_frame_analysis(astrometrics: Any, target_id: str, path: str, result: dict) -> dict:
    """Analyze a single frame when session grouping fails.

    This is a backup plan. If we can't figure out the star coordinates
    for the whole session (e.g., if plate solving failed), we fall back
    to treating this image independently and just trying to find whatever
    bright stars we can.

    Returns
    -------
    result : `dict`
        The same `result` dictionary that was passed in, updated with
        success/failure details.
    """
    from astrometricslib.pipelines.tasks import analyze_target

    target = astrometrics.targets.get(target_id)
    if target is None:
        result["error"] = "Target not found in catalog"
        return result

    analysis_outcome = analyze_target(
        target,
        frames=[FrameRecord(path=path)],
        pipeline_type="spectroscopy",
        catalog_access=astrometrics.catalog_access,
    )
    result["stars_processed"] = len(analysis_outcome.get("stellar_objects") or [])
    result["status"] = "success"
    return result


def _project_session_stars_to_frame_pixels(
    session_stars: list[StellarObject], wcs: Any
) -> list[StellarObject]:
    """Calculate where the session's stars appear in this specific image.

    This takes the real-world sky coordinates (RA/Dec) of the stars and
    uses the image's coordinate mapping (WCS) to find their exact X/Y
    pixel locations in this particular picture.

    Returns
    -------
    projected_stars : `list` of `StellarObject`
        A new list of star objects with their X/Y positions updated for
        this specific image. Stars that couldn't be mapped are skipped.
    """
    from astropy import units as astropy_units
    from astropy.coordinates import SkyCoord

    projected_stars = []
    for star in session_stars:
        if not star.right_ascension or not star.declination:
            continue
        try:
            coord = SkyCoord(
                ra=float(star.right_ascension) * astropy_units.deg,
                dec=float(star.declination) * astropy_units.deg,
            )
            x, y = wcs.world_to_pixel(coord)
        except Exception as exc:
            logger.debug("Skipping star projection for one identified star: %s", exc)
            continue

        projected = star.model_copy(deep=True)
        star_data = dict(projected.star_data) if isinstance(projected.star_data, dict) else {}
        star_data["xcentroid"] = float(x)
        star_data["ycentroid"] = float(y)
        projected.star_data = star_data
        projected_stars.append(projected)

    return projected_stars


def _process_single_spectroscopy_frame_worker_v2(
    path: str,
    target_id: str,
    session_stars: list[StellarObject],
    session_wcs_header: dict | None,
) -> dict:
    """Extract spectra for one image, using the session's known stars.

    This tries to map the known stars from the observing session onto
    this specific image. If this image has its own coordinate mapping (WCS),
    it uses that. Otherwise, it uses the session's mapping. If all else
    fails, it uses the fallback independent analysis.

    Returns
    -------
    result : `dict`
        A dictionary containing the success/failure status, error messages,
        number of stars processed, and data about the quality of the
        extracted spectra (like dispersion angle and trail width).
    """
    result = {
        "status": "failed",
        "error": None,
        "stars_processed": 0,
        "dispersion_angles": [],
        "trail_widths": [],
        "zero_order_saturation_fractions": [],
        "spectral_classification_concerns": [],
    }
    try:
        from astrometricslib import Astrometrics
        from astrometricslib.drivers.image import AstrometricsImage
        from astrometricslib.pipelines.shared.star_recording import merge_spectroscopy_stellar_object

        astrometrics = Astrometrics()

        if not session_stars:
            return _fallback_independent_frame_analysis(astrometrics, target_id, path, result)

        frame_image = AstrometricsImage(path)
        wcs = frame_image.wcs if frame_image.wcs is not None and frame_image.wcs.is_celestial else None

        if wcs is None and session_wcs_header is not None:
            import warnings

            from astropy.wcs import WCS, FITSFixedWarning

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                wcs = WCS(session_wcs_header, naxis=2)

        if wcs is None:
            return _fallback_independent_frame_analysis(astrometrics, target_id, path, result)

        projected_stars = _project_session_stars_to_frame_pixels(session_stars, wcs)
        if not projected_stars:
            return _fallback_independent_frame_analysis(astrometrics, target_id, path, result)

        from astrometricslib.pipelines.spectroscopy.pipeline import (
            SpectroscopyPipeline,
        )

        spectroscopy = SpectroscopyPipeline()
        extraction_results = spectroscopy.process_image(
            frame_image, target_stars=projected_stars, limit=len(projected_stars)
        )
        stellar_objects = [res["star_source"] for res in extraction_results if "error" not in res]

        for obj in stellar_objects:
            if target_id not in obj.target_ids:
                obj.target_ids.append(target_id)

        astrometrics.catalog_access.merge_and_record(
            "stellar_catalog", stellar_objects, merge_spectroscopy_stellar_object
        )

        result["stars_processed"] = len(stellar_objects)
        result["dispersion_angles"] = [
            obj.spectroscopy.dispersion_angle
            for obj in stellar_objects
            if obj.spectroscopy and obj.spectroscopy.dispersion_angle is not None
        ]
        result["trail_widths"] = [
            width
            for obj in stellar_objects
            if obj.spectroscopy and obj.spectroscopy.trail_width_px
            for width in obj.spectroscopy.trail_width_px
            if width > 0.0  # 0.0 marks a per-position fixed-box fallback, not a real fit
        ]
        result["zero_order_saturation_fractions"] = [
            res["zero_order_saturated_pixel_fraction"]
            for res in extraction_results
            if "zero_order_saturated_pixel_fraction" in res
        ]

        from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
            build_spectral_classification_concerns,
        )

        result["spectral_classification_concerns"] = build_spectral_classification_concerns(stellar_objects)
        result["status"] = "success"
    except Exception as processing_error:
        result["error"] = str(processing_error)

    return result


def _merge_batch_summaries(summaries: list[parallel_batch.BatchRunSummary]) -> parallel_batch.BatchRunSummary:
    """Combine the results from multiple batch processing runs into one.

    Returns
    -------
    merged : `BatchRunSummary`
        A single summary containing all the successes and failures.
    """
    merged = parallel_batch.BatchRunSummary()
    for summary in summaries:
        merged.succeeded.extend(summary.succeeded)
        merged.failed.extend(summary.failed)
        merged.skipped.extend(summary.skipped)
        merged.results.update(summary.results)
    return merged


def process_spectroscopy_frames_by_session(
    api: Any,
    target: Target,
    frame_records: list[FrameRecord],
    max_workers: int | None = None,
    on_item_complete: Callable[[str, dict, int, int], None] | None = None,
) -> tuple[parallel_batch.BatchRunSummary, list]:
    """Process a target's spectroscopy images, grouped by observing session.

    Images taken at different times (different sessions) might have the
    telescope pointing slightly differently. By grouping images into sessions,
    we can find the stars once in a 'reference image' for that session, and
    then reliably track those exact same stars across all other images taken
    that night.

    Parameters
    ----------
    api : `Any`
        The main program interface.
    target : `Target`
        The target the images belong to.
    frame_records : `list` of `FrameRecord`
        The image records to process.
    max_workers : `int` or `None`, optional
        How many processes to run at once.
    on_item_complete : `Callable`, optional
        A function called every time an image finishes processing.

    Returns
    -------
    summary : `BatchRunSummary`
        A report of all successes and failures across all sessions.
    session_results : `list` of `tuple`
        The results for each session, pairing the session data with its
        star identification data.
    """
    from astrometricslib.drivers.image import AstrometricsImage
    from astrometricslib.pipelines.astrometry.session_identification import (
        identify_session_stars,
    )
    from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier
    from astrometricslib.pipelines.shared.target_center_hint import resolve_target_center_hint
    from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions

    if max_workers is None:
        worker_counts = resolve_worker_counts("1", api.config.get_photometry_workers())
        max_workers = worker_counts.inner_worker_count

    frames_with_timestamp = [frame for frame in frame_records if frame.timestamp is not None]
    sessions = derive_target_sessions(target.id, frames_with_timestamp)

    center_ra, center_dec = resolve_target_center_hint(target)

    star_identifier = StarIdentifier()
    session_results = []
    session_summaries = []

    for session in sessions:
        reference_image = AstrometricsImage(session.frame_paths[0])
        identify_result = identify_session_stars(
            reference_image, star_identifier, center_ra=center_ra, center_dec=center_dec
        )
        session_results.append((session, identify_result))

        session_wcs_header = identify_result.wcs.to_header() if identify_result.wcs is not None else None
        # Only follow the target star and a few verified catalog stars.
        # Sending every detection would extract hundreds of unvetted
        # spectra from every frame.
        tracked_stars = select_temporal_tracking_stars(identify_result.stellar_objects, center_ra, center_dec)
        logger.info(
            "[%s] Following %d of %d identified stars across %d frame(s) of session %s.",
            target.id,
            len(tracked_stars),
            len(identify_result.stellar_objects),
            len(session.frame_paths),
            session.id,
        )
        session_summaries.append(
            parallel_batch.run_parallel_batch(
                session.frame_paths,
                _process_single_spectroscopy_frame_worker_v2,
                worker_arguments=(target.id, tracked_stars, session_wcs_header),
                max_workers=max_workers,
                niceness=api.config.get_worker_niceness(),
                on_item_complete=on_item_complete,
            )
        )

    merged_summary = _merge_batch_summaries(session_summaries)
    _attach_spectroscopy_quality_summary(target, merged_summary, session_results)
    return merged_summary, session_results


def _attach_spectroscopy_quality_summary(
    target: Target, summary: parallel_batch.BatchRunSummary, session_results: list
) -> None:
    """Gather up all the worker results into a single quality report.

    This updates the target with a summary of how well the spectroscopy
    processing went across all the images.
    """
    import statistics

    from astrometricslib.models.quality_summary import (
        SpectroscopyPipelineQualityMetrics,
        SpectroscopyQualitySummary,
    )
    from astrometricslib.pipelines.shared.quality.saturation import is_saturation_significant
    from astrometricslib.pipelines.shared.target_sessions import build_target_session_breakdown

    all_dispersion_angles = []
    all_trail_widths = []
    all_zero_order_fractions = []
    all_spectral_classification_concerns = []
    for frame_result in summary.results.values():
        all_dispersion_angles.extend(frame_result.get("dispersion_angles") or [])
        all_trail_widths.extend(frame_result.get("trail_widths") or [])
        all_zero_order_fractions.extend(frame_result.get("zero_order_saturation_fractions") or [])
        all_spectral_classification_concerns.extend(
            frame_result.get("spectral_classification_concerns") or []
        )

    low_confidence_count = sum(
        1 for concern in all_spectral_classification_concerns if "low_confidence" in concern["reason"]
    )
    ambiguous_count = sum(
        1 for concern in all_spectral_classification_concerns if "ambiguous" in concern["reason"]
    )

    max_zero_order_fraction = max(all_zero_order_fractions) if all_zero_order_fractions else None
    zero_order_flagged = (
        is_saturation_significant(max_zero_order_fraction) if max_zero_order_fraction is not None else False
    )
    trail_width_profile_available = bool(all_trail_widths)
    median_trail_width_px = statistics.median(all_trail_widths) if trail_width_profile_available else None

    failed_paths = {path for path, _error in summary.failed}
    sessions = [session for session, _identify_result in session_results]
    target_session_breakdown = build_target_session_breakdown(sessions, failed_paths)

    target.spectroscopy_quality_summary = SpectroscopyQualitySummary(
        target_id=target.id,
        target_session_ids=[session.id for session, _identify_result in session_results],
        target_session_breakdown=target_session_breakdown,
        # Unlike the single-stacked-frame path this model's default
        # describes, this run analyzed raw per-session frames
        # directly, never a stacked spectral image.
        upstream_quality_summary_reference="raw_frames",
        spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(
            zero_order_saturated_pixel_fraction=max_zero_order_fraction,
            zero_order_saturation_flagged=zero_order_flagged,
            dispersion_angle_deg=all_dispersion_angles[0] if all_dispersion_angles else None,
            trail_width_profile_available=trail_width_profile_available,
            median_trail_width_px=median_trail_width_px,
            low_confidence_classification_count=low_confidence_count,
            ambiguous_classification_count=ambiguous_count,
            flagged_spectral_classifications=all_spectral_classification_concerns,
        ),
    )
    if zero_order_flagged:
        target.spectroscopy_quality_summary.flagged = True
        target.spectroscopy_quality_summary.flag_reasons.append(
            "zero-order saturated in at least one processed star"
        )
    if all_spectral_classification_concerns:
        target.spectroscopy_quality_summary.flagged = True
        target.spectroscopy_quality_summary.flag_reasons.append(
            f"spectral classification uncertain for {len(all_spectral_classification_concerns)} star(s)"
        )
