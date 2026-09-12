"""Searches a target's images for moving objects (asteroids).

Runs `AsteroidRecoveryPipeline`, which returns every candidate it found --
including the ones it eventually rejected, so their rejection reasons can
still be summarized -- and keeps only the candidates that survived far
enough to be worth a look. Unlike the other three pipelines, this one has
no stars to save: candidates go straight onto the target record, not into
the shared star catalog.
"""

from typing import Any

from astrometricslib.models.moving_object import CascadeStage
from astrometricslib.models.quality_summary import (
    AsteroidRecoveryPipelineQualityMetrics,
    AsteroidRecoveryQualitySummary,
)
from astrometricslib.pipelines.contract import (
    AnalysisPipeline,
    PipelineRequest,
    Result,
    run_pipeline,
)


class AsteroidRecoveryPipelineAdapter(AnalysisPipeline):
    """Adapts `AsteroidRecoveryPipeline` to the shared `AnalysisPipeline`."""

    @property
    def pipeline_name(self) -> str:
        """See `AnalysisPipeline.pipeline_name`.

        Returns
        -------
        pipeline_name : `str`
            Always ``"asteroid_recovery"``.
        """
        return "asteroid_recovery"

    def process_input(self, request: PipelineRequest) -> Result:
        """Asteroid recovery has no "nothing to do" case to check.

        `AsteroidRecoveryPipeline.process` already tolerates a target
        with no usable frames -- it just reports zero candidates -- so
        there is no "nothing to do" case to catch before running it.

        Returns
        -------
        result : `Result`
            Always `has_work=True`.
        """
        return Result()

    def run(self, request: PipelineRequest, result: Result) -> Result:
        """Search for moving objects and keep only the surviving candidates.

        Returns
        -------
        result : `Result`
            `candidates` is the surviving subset, already written onto
            `request.target.asteroid_candidates` -- this pipeline's one
            genuine output side effect, since its result has no catalog
            counterpart to record through the catalog_access.
        """
        from astrometricslib.pipelines.asteroid_recovery.pipeline import (
            AsteroidRecoveryPipeline,
        )

        target = request.target
        pipeline = AsteroidRecoveryPipeline()
        light_frames = [
            (frame.path, frame.timestamp)
            for frame in target.frames
            if frame.role == "LIGHT" and frame.timestamp is not None
        ]
        all_candidates = pipeline.process(target.id, target.stacked_image, light_frames)
        metrics = pipeline.last_run_metrics
        # Record only candidates that survived the discrimination
        # cascade (or were matched to a known body) -- `process()`
        # deliberately returns every rejected chain too (cosmic
        # rays, hot pixels, missed stars) so `metrics` above can
        # summarize them, but writing all of those into the
        # target's recorded record is unbounded: a single dense
        # field can produce tens of thousands of single-frame noise
        # chains, each carrying its own frame-detection payload.
        target.asteroid_candidates = [
            candidate
            for candidate in all_candidates
            if candidate.cascade_stage
            in (CascadeStage.RATE_LINEARITY_CONFIRMED, CascadeStage.EPHEMERIS_MATCHED)
        ]

        return Result(candidates=target.asteroid_candidates, payload={"metrics": metrics})

    def validate_output(self, request: PipelineRequest, result: Result) -> AsteroidRecoveryQualitySummary:
        """Build the quality summary and flag anything worth a look.

        Returns
        -------
        summary : `AsteroidRecoveryQualitySummary`
            Flagged when frames were excluded for missing pointing
            metadata, or when a candidate was confirmed as a mover but
            not matched to a known body.
        """
        from astrometricslib.pipelines.shared.target_sessions import (
            build_target_session_breakdown,
            derive_target_sessions,
        )

        target = request.target
        metrics = result.payload["metrics"]

        light_frames = [frame for frame in target.frames if frame.role == "LIGHT"]
        asteroid_recovery_sessions = derive_target_sessions(target.id, light_frames)
        # Per-session frame-exclusion identity isn't tracked by the
        # pipeline today (only the aggregate
        # frames_excluded_missing_pointing_metadata count is), so no
        # excluded_paths is passed here -- every session reports 0 frames
        # clipped rather than fabricating a breakdown.
        asteroid_recovery_session_breakdown = build_target_session_breakdown(asteroid_recovery_sessions)

        summary = AsteroidRecoveryQualitySummary(
            target_id=target.id,
            target_session_ids=[session.id for session in asteroid_recovery_sessions],
            target_session_breakdown=asteroid_recovery_session_breakdown,
            asteroid_recovery_metrics=AsteroidRecoveryPipelineQualityMetrics(**metrics),
        )
        if metrics.get("frames_excluded_missing_pointing_metadata", 0) > 0:
            summary.flagged = True
            summary.flag_reasons.append(
                f"{metrics['frames_excluded_missing_pointing_metadata']} frame(s) excluded for "
                "missing RA/DEC/NAXIS pointing metadata"
            )
        candidates_awaiting_recovery = sum(
            1
            for candidate in result.candidates
            if candidate.cascade_stage == CascadeStage.RATE_LINEARITY_CONFIRMED
        )
        if candidates_awaiting_recovery > 0:
            summary.flagged = True
            summary.flag_reasons.append(
                f"{candidates_awaiting_recovery} candidate(s) confirmed as movers but not "
                "matched to a known body -- worth a manual look"
            )
        return summary

    def to_result_dict(
        self, request: PipelineRequest, result: Result, summary: AsteroidRecoveryQualitySummary
    ) -> dict[str, Any]:
        """Build the result dict asteroid recovery's callers expect back.

        Returns
        -------
        result_dict : `dict`
            Has ``"status"``, ``"targetId"``, ``"analysisMode"``,
            candidate counts at each stage of the discrimination
            cascade, and ``"candidates"`` (the surviving candidates).
        """
        metrics = result.payload["metrics"]
        return {
            "status": "completed",
            "targetId": request.target.id,
            "analysisMode": "asteroid_recovery",
            "candidatesDetected": metrics.get("candidates_detected", 0),
            "candidatesRateLinearityConfirmed": metrics.get("candidates_rate_linearity_confirmed", 0),
            "candidatesEphemerisMatched": metrics.get("candidates_ephemeris_matched", 0),
            "candidates": result.candidates,
        }


def run_asteroid_recovery_analysis(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument] -- unused; asteroid recovery reads target.frames itself
    filter_type,  # ruff: ignore[missing-type-function-argument] -- unused; asteroid recovery has no filter concept
    catalog_access,  # ruff: ignore[missing-type-function-argument] -- unused; candidates record on the target record, not via the catalog_access
    path,  # ruff: ignore[missing-type-function-argument] -- unused; asteroid recovery reads target.frames itself
    **kwargs,  # ruff: ignore[missing-type-kwargs] -- unused
) -> dict[str, Any]:
    """Search a target's light frames for moving objects.

    A thin wrapper kept at this name and signature for
    `pipelines.PIPELINE_RUNNERS` -- the actual work is
    `AsteroidRecoveryPipelineAdapter`, run through the shared
    input/main/output processing cycle in `run_pipeline`.

    Parameters
    ----------
    target : `Target`
        The target to search. Its `asteroid_candidates` and
        `asteroid_recovery_quality_summary` are set by this call.
    frames : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    filter_type : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    catalog_access : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    path : `Any`
        Unused. Present so every pipeline runner shares one call signature.

    Returns
    -------
    result : `dict`
        Has ``"status"``, ``"targetId"``, ``"analysisMode"``, candidate
        counts at each stage of the discrimination cascade, and
        ``"candidates"`` (the surviving candidates, same list as
        `target.asteroid_candidates`).
    """
    request = PipelineRequest(
        target=target,
        catalog_access=catalog_access,
        frames=frames,
        filter_type=filter_type,
        path=path,
        options=kwargs,
    )
    return run_pipeline(AsteroidRecoveryPipelineAdapter(), request)
