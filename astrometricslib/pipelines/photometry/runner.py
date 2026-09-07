"""Adapts photometry's per-session runs to the shared pipeline shape.

A target's images span many separately registered observing sessions,
and brightness tracking only works within one session at a time
(consistent framing and rotation); the actual per-session and
cross-session mechanics live in `batch.py` -- this file is the thin
`AnalysisPipeline` adapter that drives them.
"""

import logging
from typing import Any

from astrometricslib.models.target import Target
from astrometricslib.pipelines.contract import (
    AnalysisPipeline,
    PipelineRequest,
    Result,
    run_pipeline,
)
from astrometricslib.pipelines.photometry.batch import (
    _match_and_merge_across_sessions,
    _run_variability_analysis_for_session,
)
from astrometricslib.pipelines.shared.star_recording import (
    merge_photometry_stellar_object,
    record_pipeline_stars,
)

logger = logging.getLogger(__name__)

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


def _empty_photometry_result(no_work_reason: str) -> Result:
    """Build the `has_work=False` `Result` for photometry's give-up case.

    Shaped exactly like a real run that happened to process zero
    sessions and find zero stars, so `run` can be skipped while
    `validate_output`/`to_result_dict` still produce a normal (empty)
    summary and result dict, with `no_work_reason` surfaced as a flag
    rather than a one-off status/message pair.

    Parameters
    ----------
    no_work_reason : `str`
        Why there was nothing to do; recorded as a flag reason on the
        quality summary `validate_output` builds from this `Result`.

    Returns
    -------
    result : `Result`
        `has_work=False`, with every key `run` would otherwise have
        populated in `payload` set to its zero/empty value.
    """
    from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown

    return Result(
        has_work=False,
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=0, position_only=0, unresolved=0
            ),
            "photometry_sessions": [],
            "all_rejected_files": [],
            "all_frame_ensemble_composition": [],
            "session_empty_reasons": [],
            "sessions_missing_wcs": [],
            "cross_session_match_count": 0,
            "long_term_candidate_count": 0,
            "astrometry_identified_star_count": 0,
            "sessions_with_reused_header_wcs": [],
            "sessions_with_replaced_header_wcs": [],
            "frames_processed": 0,
            "candidates_formatted": [],
            "long_term_candidates_formatted": [],
            "image_paths": [],
            "photometry_frames_without_timestamp": [],
            "no_work_reason": no_work_reason,
        },
    )


class PhotometryPipelineAdapter(AnalysisPipeline):
    """Adapts per-session `VariabilityAnalyzer` runs to the shared shape."""

    @property
    def pipeline_name(self) -> str:
        """See `AnalysisPipeline.pipeline_name`.

        Returns
        -------
        pipeline_name : `str`
            Always ``"photometry"``.
        """
        return "photometry"

    def process_input(self, request: PipelineRequest) -> Result:
        """Check there are frames to use and sessions to assign them to.

        Photometry is the one pipeline with a real "nothing to do" case:
        a filter that matches nothing, or frames with no usable capture
        timestamp to build a session from. Either produces an empty,
        `has_work=False` `Result` instead of running -- `validate_output`
        and `to_result_dict` still run on it, same as a real run's
        `Result`, so the target still gets a real (empty) quality
        summary and the reason still surfaces, as a flag rather than a
        one-off status/message pair.

        Returns
        -------
        result : `Result`
            `has_work=False` for either "nothing to do" case; otherwise
            `has_work=True` with the filtered frames and derived
            sessions carried in `payload`, so `run` does not have to
            redo this work.
        """
        from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions

        target = request.target
        filter_type = request.filter_type

        image_paths = []
        photometry_frames = []
        target_frames = request.frames if request.frames is not None else target.frames
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
                        or getattr(frame.filter, "value", "").upper() in ["L", "LUMINANCE", "NONE", "UNKNOWN"]
                    )
                )
            ):
                image_paths.append(frame.path)
                photometry_frames.append(frame)

        if not image_paths:
            return _empty_photometry_result(f"No frames found for filter: {filter_type}")

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
            return _empty_photometry_result(
                f"No frames with a usable capture timestamp to assign a session for filter: {filter_type}"
            )

        return Result(
            payload={
                "image_paths": image_paths,
                "photometry_frames_without_timestamp": photometry_frames_without_timestamp,
                "photometry_sessions": photometry_sessions,
            },
        )

    def run(self, request: PipelineRequest, result: Result) -> Result:
        """Run one `VariabilityAnalyzer` pass per session, then merge them.

        Returns
        -------
        result : `Result`
            `stellar_objects` is every saved star; `candidates` is the
            raw (pre-merge) list of stars flagged as variable in their
            own session. Everything `validate_output` and
            `to_result_dict` need is in `payload`.
        """
        from astrometricslib.models.stellar_source import VariableCandidate

        target = request.target
        catalog_access = request.catalog_access
        options = request.options
        photometry_sessions = result.payload["photometry_sessions"]
        image_paths = result.payload["image_paths"]
        photometry_frames_without_timestamp = result.payload["photometry_frames_without_timestamp"]

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
        use_astrometry_seed = bool(options.get("use_astrometry_seed", True))
        star_identifier = None
        if use_astrometry_seed:
            from astrometricslib.pipelines.astrometry.star_identifier import StarIdentifier

            star_identifier = StarIdentifier()

        session_wcs_map: dict[str, Any] = {}
        astrometry_identified_star_count = 0
        sessions_with_reused_header_wcs: list[str] = []
        sessions_with_replaced_header_wcs: list[str] = []

        for session in photometry_sessions:
            id_prefix = f"{session.id}:" if id_prefix_enabled else ""
            analyzer, session_candidates, identify_result = _run_variability_analysis_for_session(
                session,
                options.get("max_workers"),
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
                from astrometricslib.pipelines.photometry.variability_analyzer import (
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

        all_stellar_objects, star_id_breakdown = record_pipeline_stars(
            all_stellar_objects,
            catalog_access=catalog_access,
            target_id=target.id,
            merge_function=merge_photometry_stellar_object,
            pipeline_name="photometry",
        )

        frames_processed = sum(len(session.frame_paths) for session in photometry_sessions) - len(
            all_rejected_files
        )

        return Result(
            stellar_objects=all_stellar_objects,
            candidates=all_candidates,
            payload={
                "star_id_breakdown": star_id_breakdown,
                "photometry_sessions": photometry_sessions,
                "all_rejected_files": all_rejected_files,
                "all_frame_ensemble_composition": all_frame_ensemble_composition,
                "session_empty_reasons": session_empty_reasons,
                "sessions_missing_wcs": sessions_missing_wcs,
                "cross_session_match_count": cross_session_match_count,
                "long_term_candidate_count": len(long_term_candidates),
                "astrometry_identified_star_count": astrometry_identified_star_count,
                "sessions_with_reused_header_wcs": sessions_with_reused_header_wcs,
                "sessions_with_replaced_header_wcs": sessions_with_replaced_header_wcs,
                "frames_processed": frames_processed,
                "candidates_formatted": candidates_formatted,
                "long_term_candidates_formatted": long_term_candidates_formatted,
                "image_paths": image_paths,
                "photometry_frames_without_timestamp": photometry_frames_without_timestamp,
            },
        )

    def validate_output(self, request: PipelineRequest, result: Result) -> Any:
        """Build the quality summary and every flag this run's data earns.

        Returns
        -------
        summary : `PhotometryQualitySummary`
            Flagged for a high global-outlier rejection rate, frames
            excluded for a missing timestamp, a session with zero stars
            detected, a session that could not be plate-solved for
            cross-session matching, or (when `process_input` found
            nothing to do) the reason why -- any, all, or none of these.
        """
        from astrometricslib.models.quality_summary import (
            ExcludedFrame,
            PhotometryPipelineQualityMetrics,
            PhotometryQualitySummary,
        )
        from astrometricslib.pipelines.photometry.variability_analyzer import (
            median_light_curve_scatter_mag,
        )
        from astrometricslib.pipelines.shared.target_sessions import build_target_session_breakdown

        target = request.target
        payload = result.payload
        photometry_sessions = payload["photometry_sessions"]
        all_rejected_files = payload["all_rejected_files"]
        photometry_frames_without_timestamp = payload["photometry_frames_without_timestamp"]
        star_id_breakdown = payload["star_id_breakdown"]
        sessions_missing_wcs = payload["sessions_missing_wcs"]
        session_empty_reasons = payload["session_empty_reasons"]
        frames_processed = payload["frames_processed"]

        rejected_paths = set(all_rejected_files)
        photometry_session_breakdown = build_target_session_breakdown(photometry_sessions, rejected_paths)

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

        summary = PhotometryQualitySummary(
            target_id=target.id,
            target_session_ids=[session.id for session in photometry_sessions],
            target_session_breakdown=photometry_session_breakdown,
            photometry_metrics=PhotometryPipelineQualityMetrics(
                stars_processed=len(result.stellar_objects),
                stars_found=len(result.stellar_objects),
                frames_processed=frames_processed,
                rejected_frames=rejected_frames,
                frame_ensemble_composition=payload["all_frame_ensemble_composition"],
                variable_candidate_count=len(result.candidates),
                cross_session_match_count=payload["cross_session_match_count"],
                sessions_missing_wcs=sessions_missing_wcs,
                long_term_variable_candidate_count=payload["long_term_candidate_count"],
                astrometry_identified_star_count=payload["astrometry_identified_star_count"],
                sessions_with_reused_header_wcs=payload["sessions_with_reused_header_wcs"],
                sessions_with_replaced_header_wcs=payload["sessions_with_replaced_header_wcs"],
                catalog_matched_star_count=star_id_breakdown.catalog_matched,
                position_only_star_count=star_id_breakdown.position_only,
                unresolved_star_count=star_id_breakdown.unresolved,
                light_curve_scatter_rms_mag=median_light_curve_scatter_mag(result.stellar_objects),
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
            summary.flagged = True
            summary.flag_reasons.append(
                f"{len(all_rejected_files)} of {frames_contributed_total} frame(s) "
                f"({rejection_fraction:.0%}) rejected as global ensemble outliers, which is high "
                "enough to suspect the comparison ensemble or the observing conditions"
            )
        if photometry_frames_without_timestamp:
            summary.flagged = True
            summary.flag_reasons.append(
                f"{len(photometry_frames_without_timestamp)} frame(s) excluded for missing capture timestamp"
            )
        if session_empty_reasons:
            summary.flagged = True
            summary.flag_reasons.extend(session_empty_reasons)
        if sessions_missing_wcs:
            summary.flagged = True
            summary.flag_reasons.append(
                f"{len(sessions_missing_wcs)} session(s) could not be plate-solved for "
                f"cross-session star matching: {', '.join(sessions_missing_wcs)}"
            )
        no_work_reason = payload.get("no_work_reason")
        if no_work_reason:
            summary.flagged = True
            summary.flag_reasons.append(no_work_reason)
        return summary

    def to_result_dict(self, request: PipelineRequest, result: Result, summary: Any) -> dict[str, Any]:
        """Build the result dict photometry's callers expect back.

        Returns
        -------
        result_dict : `dict`
            The completed shape carrying every brightness-tracking metric.
        """
        payload = result.payload
        return {
            "status": "completed",
            "targetId": request.target.id,
            "totalImages": len(payload["image_paths"]),
            "analysisMode": "photometry",
            "starsProcessed": len(result.stellar_objects),
            "spectraExtracted": 0,
            "starsFound": len(result.stellar_objects),
            "framesProcessed": payload["frames_processed"],
            "rejectedCount": len(payload["all_rejected_files"]),
            "rejectedFiles": payload["all_rejected_files"],
            "variableCandidates": payload["candidates_formatted"],
            "longTermVariableCandidates": payload["long_term_candidates_formatted"],
            "crossSessionMatchCount": payload["cross_session_match_count"],
        }


def run_photometry_analysis(
    target: Target,
    frames,  # ruff: ignore[missing-type-function-argument]
    filter_type,  # ruff: ignore[missing-type-function-argument]
    catalog_access,  # ruff: ignore[missing-type-function-argument]
    path,  # ruff: ignore[missing-type-function-argument] -- unused; photometry works from `frames`/`target.frames`
    **kwargs,  # ruff: ignore[missing-type-kwargs]
) -> dict[str, Any]:
    """Track star brightness across a target's images, session by session.

    A thin wrapper kept at this name and signature for
    `pipelines.PIPELINE_RUNNERS` -- the actual work is
    `PhotometryPipelineAdapter`, run through the shared
    input/main/output processing cycle in `run_pipeline`.

    Parameters
    ----------
    target : `Target`
        The target being tracked. Its `photometry_quality_summary` is set
        by this call.
    frames : `list` [`FrameRecord`] or `None`
        The frames to use; `target.frames` if not given.
    filter_type : `str` or `None`
        Only frames with this filter are used, if given.
    catalog_access : `Any`
        Saves the stars this run found.
    path : `Any`
        Unused. Present so every pipeline runner shares one call signature.

    Returns
    -------
    result : `dict`
        The completed shape carrying every brightness-tracking metric,
        even when there was no usable data -- in that case every metric
        is zero/empty and the reason surfaces as a flag in
        `target.photometry_quality_summary.flag_reasons` rather than as
        a distinct return shape.
    """
    request = PipelineRequest(
        target=target,
        catalog_access=catalog_access,
        frames=frames,
        filter_type=filter_type,
        path=path,
        options=kwargs,
    )
    return run_pipeline(PhotometryPipelineAdapter(), request)
