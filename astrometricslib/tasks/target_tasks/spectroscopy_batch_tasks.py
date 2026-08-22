"""Batch spectroscopy frame processing operations.

Thin adapter wiring the generic parallel-batch engine to per-frame
spectroscopy analysis, mirroring batch_processing_operations.py's
shape. Owns only spectroscopy-specific concerns (worker lookup,
worker-count resolution); the actual process-pool mechanics live in
astrometricslib.utilities.parallel_batch, which knows nothing about
spectroscopy frames.

`process_spectroscopy_frames_by_session` is the session-grouped entry
point: unlike the older flat `process_spectroscopy_frames` (kept for
now, see its docstring), it identifies each observing session's stars
once via `session_identification.identify_session_stars` and extracts
spectra for those same identified stars from every frame in that
session, instead of each frame independently re-detecting its own,
disconnected set of anonymous point sources.
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

    Module-level, picklable worker. Constructs a fresh Astrometrics
    astrometrics (its own DiskButler/config) so each worker process is
    self-contained, mirroring the pattern already used by
    batch_processing_operations.py's _process_single_target_worker.
    Returns only summary counts, not the full analyze_target()
    payload (which carries a pipeline context object not worth
    pickling back across the process boundary); persistence already
    happens inside analyze_target() via
    Butler.merge_and_persist_records(), which is safe under
    concurrent multi-process writers.

    Parameters
    ----------
    path : `str`
        The filesystem path of the frame to analyze.
    target_id : `str`
        The identifier of the target this frame belongs to.

    Returns
    -------
    result : `dict`
        A dict with keys "status" (`str`, "success" or "failed"),
        "error" (`str` or `None`), and "stars_processed" (`int`).
    """
    from astrometricslib import Astrometrics
    from astrometricslib.models.target import FrameRecord
    from astrometricslib.tasks.target_tasks.pipeline_tasks import analyze_target

    result = {"status": "failed", "error": None, "stars_processed": 0}
    try:
        astrometrics = Astrometrics()
        target = astrometrics.targets.get(target_id)
        if target is None:
            result["error"] = "Target not found in catalog"
            return result

        analysis_outcome = analyze_target(
            target, frames=[FrameRecord(path=path)], pipeline_type="spectroscopy", butler=astrometrics.butler
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
    """Process many spectroscopy frames for one target concurrently.

    Uses the generic parallel-batch engine to run each frame's
    spectroscopy analysis in its own worker process.

    Superseded by `process_spectroscopy_frames_by_session`, which
    identifies each observing session's stars once and gives every
    frame in that session real, cross-frame-consistent star
    identities, rather than each frame here independently detecting
    its own disconnected top-N brightest point sources with no shared
    identity. Kept, unused by the orchestrator, until the new path is
    verified against real data -- not deleted outright in case a quick
    revert is needed.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing config and target lookup.
    target_id : `str`
        The target these frames belong to.
    paths : `List[str]`
        Frame file paths to analyze, one worker call each.
    max_workers : `Optional[int]`, optional
        Number of concurrent worker processes, default `None`, in
        which case it is resolved from configuration via
        resolve_worker_counts().
    on_item_complete : `Callable`, optional
        Per-frame completion callback of the form
        ``(path, result, done_count, total_count) -> None``, default
        `None`. Passed through to run_parallel_batch() unchanged.

    Returns
    -------
    summary : `parallel_batch.BatchRunSummary`
        Aggregated success/failure/result state across all frames.
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
    """Fall back to today's fully independent per-frame spectroscopy flow.

    Used when no session-identified stars are available to seed with
    at all (e.g. the session's own reference-frame identification
    found nothing), or a usable WCS can't be obtained for this
    specific frame either from its own header or the session's
    reference frame. Should be rare in practice -- it only triggers
    when the session-level identify step itself already failed to
    resolve anything for this session.

    Mutates and returns `result` in place, matching
    `_process_single_spectroscopy_frame_worker`'s existing shape, so
    both fallback and seeded results are the same dict shape from the
    caller's perspective.

    Returns
    -------
    result : `dict`
        The same `result` dict passed in, mutated with `"status"`,
        `"error"`, and `"stars_processed"`.
    """
    from astrometricslib.tasks.target_tasks.pipeline_tasks import analyze_target

    target = astrometrics.targets.get(target_id)
    if target is None:
        result["error"] = "Target not found in catalog"
        return result

    analysis_outcome = analyze_target(
        target, frames=[FrameRecord(path=path)], pipeline_type="spectroscopy", butler=astrometrics.butler
    )
    result["stars_processed"] = len(analysis_outcome.get("stellar_objects") or [])
    result["status"] = "success"
    return result


def _project_session_stars_to_frame_pixels(
    session_stars: list[StellarObject], wcs: Any
) -> list[StellarObject]:
    """Project each session-identified star's sky position to `wcs` pixels.

    Returns deep copies (never the original `session_stars` entries):
    `SpectroscopyPipeline.process_image` mutates a processed star's
    `star_data["xcentroid"/"ycentroid"]` in place with the refined
    position it locates, which must not leak back onto the shared
    session identity when other frames in the same session are
    projected from the same `session_stars` list.

    Returns
    -------
    projected_stars : `list` [`StellarObject`]
        One deep-copied, frame-pixel-positioned entry per input star
        that had a usable sky position and projected successfully.
        Stars lacking `right_ascension`/`declination` (never
        SIMBAD-matched) or whose projection raised are silently
        skipped.
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
    """Extract spectra for one frame's session-identified stars.

    Module-level, picklable worker (see
    `_process_single_spectroscopy_frame_worker`'s docstring for why
    each worker process constructs its own `Astrometrics` astrometrics).

    WCS resolution priority, most to least accurate:
    1. This frame's own FITS-header WCS, if valid -- most accurate,
       since it accounts for this specific frame's own pointing (e.g.
       a dithered exposure with genuinely different framing/rotation
       within the session).
    2. `session_wcs_header` -- the session's own reference-frame WCS,
       reconstructed from the plain header dict passed across the
       process boundary (a `WCS` object itself isn't reliably
       picklable in all astropy versions), used as an approximation
       when this frame has no usable WCS of its own.
    3. Neither available, or no `session_stars` to seed with at all --
       fall back to `_fallback_independent_frame_analysis`, matching
       today's blind per-frame detection with no cross-frame star
       identity. Should be rare, since it only triggers when the
       session's own reference-frame resolution already failed.

    Returns
    -------
    result : `dict`
        Keys: "status" (`"success"` or `"failed"`), "error", and
        "stars_processed" (matching
        `_process_single_spectroscopy_frame_worker`'s shape). On a
        successful seeded (non-fallback) extraction, also includes
        "dispersion_angles" (`list` [`float`]), "trail_widths"
        (`list` [`float`]), and "zero_order_saturation_fractions"
        (`list` [`float`]) -- raw per-star quality inputs for the
        parent process to aggregate into a
        `SpectroscopyPipelineQualityMetrics` (the fallback path leaves
        these as empty lists, since it doesn't track that level of
        detail).
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
        from astrometricslib.tasks.target_tasks.pipeline_tasks import merge_spectroscopy_stellar_object
        from astrometricslib.utilities.image import AstrometricsImage

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

        from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import (
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

        astrometrics.butler.merge_and_persist_records(
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
    """Concatenate multiple sessions' BatchRunSummary objects into one.

    Returns
    -------
    merged : `parallel_batch.BatchRunSummary`
        A single summary combining every input summary's succeeded,
        failed, and results entries.
    """
    merged = parallel_batch.BatchRunSummary()
    for summary in summaries:
        merged.succeeded.extend(summary.succeeded)
        merged.failed.extend(summary.failed)
        merged.results.update(summary.results)
    return merged


def process_spectroscopy_frames_by_session(
    api: Any,
    target: Target,
    frame_records: list[FrameRecord],
    max_workers: int | None = None,
    on_item_complete: Callable[[str, dict, int, int], None] | None = None,
) -> tuple[parallel_batch.BatchRunSummary, list]:
    """Process a target's spectroscopy frames one observing session at a time.

    For each session (grouped via `derive_target_sessions`, mirroring
    the photometry pipeline's own session-safety boundary -- framing
    and rotation can differ between a target's separate observing
    sessions, so a single astrometric solve is only trusted within one
    session, never reused across sessions): identifies that session's
    stars once, from its reference frame, via
    `session_identification.identify_session_stars` -- reusing an
    existing FITS-header WCS when present, otherwise plate-solving --
    then every frame in that session extracts spectra for those same
    identified stars, each projected to its own pixel coordinates.
    This gives every extracted spectrum a real, stable star identity
    shared consistently across the whole session, instead of each
    frame's own independent, disconnected top-N brightest detections
    (`process_spectroscopy_frames`'s existing behavior).

    Session identification runs sequentially in this (parent) process,
    once per session, before that session's frames are dispatched to
    worker processes -- cheap, since SIMBAD identification is one bulk
    region query per solve, not one call per star or per frame.

    Parameters
    ----------
    api : `Any`
        the high-level interface providing config.
    target : `Target`
        Supplies the RA/Dec hint for astrometry identification and the
        target id frames are associated with.
    frame_records : `list` [`FrameRecord`]
        The frames to process. Frames with no `timestamp` can't be
        assigned a session and are silently excluded, matching the
        photometry pipeline's existing behavior.
    max_workers : `int`, optional
        Number of concurrent worker processes, default `None`, in
        which case it is resolved from configuration via
        `resolve_worker_counts()`.
    on_item_complete : `Callable`, optional
        Per-frame completion callback of the form
        ``(path, result, done_count, total_count) -> None``, default
        `None`. Passed through to `run_parallel_batch()` unchanged;
        `done_count`/`total_count` are scoped to each session's own
        batch, not the run overall, since sessions are dispatched as
        separate `run_parallel_batch()` calls.

    Returns
    -------
    summary : `parallel_batch.BatchRunSummary`
        Aggregated success/failure/result state across every session's
        frames.
    session_results : `list` [`tuple`]
        One `(session, identify_result)` pair per session -- a
        `TargetSession` paired with its `SessionIdentificationResult`
        -- in session order, so the caller has each session's
        id/frame_paths alongside its identification result, enough
        to aggregate into a `SpectroscopyQualitySummary`'s
        `target_session_breakdown` without re-deriving sessions.

    Notes
    -----
    Also builds and attaches `target.spectroscopy_quality_summary`
    from the aggregated per-frame results before returning, mirroring
    the metrics `pipeline_tasks.py`'s single-stacked-frame
    `"spectroscopy"` case computes (zero-order saturation, dispersion
    angle, trail-width profile), plus a `target_session_breakdown` the
    single-frame case has no concept of. Does not persist `target` --
    the caller is responsible for that.
    """
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.session_identification import (
        identify_session_stars,
    )
    from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier
    from astrometricslib.tasks.target_tasks.target_session_tasks import derive_target_sessions
    from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
    from astrometricslib.utilities.image import AstrometricsImage

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
    """Aggregate per-frame worker results into a quality summary.

    Mutates `target.spectroscopy_quality_summary` in place; does not
    persist -- the caller is responsible for that.
    """
    import statistics

    from astrometricslib.models.quality_summary import (
        SpectroscopyPipelineQualityMetrics,
        SpectroscopyQualitySummary,
        TargetSessionContribution,
    )
    from astrometricslib.tasks.shared.saturation_analysis import is_saturation_significant

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
