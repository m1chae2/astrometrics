"""Tools for running spectroscopy processing on many images at once.

This file connects the specific spectroscopy tasks to the general
parallel processing system (which handles running multiple tasks
at the same time). It groups images by the observing session they
belong to, identifies the stars in one reference image, and then
uses those same stars for all the other images in the session.
"""

import logging
from collections.abc import Callable
from typing import Any

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.utilities import parallel_batch
from astrometricslib.utilities.concurrency import resolve_worker_counts

logger = logging.getLogger(__name__)


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
    from astrometricslib.pipelines.dispatch import analyze_target

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
    from astrometricslib.pipelines.dispatch import analyze_target

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
    }
    try:
        from astrometricslib import Astrometrics
        from astrometricslib.image_processing.image import AstrometricsImage
        from astrometricslib.pipelines.dispatch import merge_spectroscopy_stellar_object

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

        astrometrics.catalog_access.merge_and_persist_records(
            "stellar_catalog", stellar_objects, merge_spectroscopy_stellar_object
        )

        result["stars_processed"] = len(stellar_objects)
        result["dispersion_angles"] = [
            obj.dispersion_angle for obj in stellar_objects if obj.dispersion_angle is not None
        ]
        result["trail_widths"] = [
            width
            for obj in stellar_objects
            if obj.trail_width_px
            for width in obj.trail_width_px
            if width > 0.0  # 0.0 marks a per-position fixed-box fallback, not a real fit
        ]
        result["zero_order_saturation_fractions"] = [
            res["zero_order_saturated_pixel_fraction"]
            for res in extraction_results
            if "zero_order_saturated_pixel_fraction" in res
        ]
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
    from astrometricslib.image_processing.image import AstrometricsImage
    from astrometricslib.pipelines.astrometry.session_identification import (
        identify_session_stars,
    )
    from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier
    from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions
    from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

    if max_workers is None:
        worker_counts = resolve_worker_counts("1", api.config.get_photometry_workers())
        max_workers = worker_counts.inner_worker_count

    frames_with_timestamp = [frame for frame in frame_records if frame.timestamp is not None]
    sessions = derive_target_sessions(target.id, frames_with_timestamp)

    center_ra = None
    center_dec = None
    try:
        center_ra = parse_coordinate_string(str(target.ra), is_ra=True)
        center_dec = parse_coordinate_string(str(target.dec), is_ra=False)
    except Exception as exc:
        # Blind solve (no center hint) if the target has no usable RA/Dec yet.
        logger.debug("Falling back to blind solve, could not parse target RA/Dec: %s", exc)

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
        session_summaries.append(
            parallel_batch.run_parallel_batch(
                session.frame_paths,
                _process_single_spectroscopy_frame_worker_v2,
                worker_arguments=(target.id, identify_result.stellar_objects, session_wcs_header),
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

    from astrometricslib.image_processing.saturation import is_saturation_significant
    from astrometricslib.models.quality_summary import (
        SpectroscopyPipelineQualityMetrics,
        SpectroscopyQualitySummary,
        TargetSessionContribution,
    )

    all_dispersion_angles = []
    all_trail_widths = []
    all_zero_order_fractions = []
    for frame_result in summary.results.values():
        all_dispersion_angles.extend(frame_result.get("dispersion_angles") or [])
        all_trail_widths.extend(frame_result.get("trail_widths") or [])
        all_zero_order_fractions.extend(frame_result.get("zero_order_saturation_fractions") or [])

    max_zero_order_fraction = max(all_zero_order_fractions) if all_zero_order_fractions else None
    zero_order_flagged = (
        is_saturation_significant(max_zero_order_fraction) if max_zero_order_fraction is not None else False
    )
    trail_width_profile_available = bool(all_trail_widths)
    median_trail_width_px = statistics.median(all_trail_widths) if trail_width_profile_available else None

    failed_paths = {path for path, _error in summary.failed}
    target_session_breakdown = [
        TargetSessionContribution(
            session_id=session.id,
            frames_contributed=len(session.frame_paths),
            frames_clipped=sum(1 for path in session.frame_paths if path in failed_paths),
        )
        for session, _identify_result in session_results
    ]

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
        ),
    )
    if zero_order_flagged:
        target.spectroscopy_quality_summary.flagged = True
        target.spectroscopy_quality_summary.flag_reasons.append(
            "zero-order saturated in at least one processed star"
        )
