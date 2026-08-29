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
    InputScreening,
    PipelineRequest,
    RunOutcome,
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

    def screen_input(self, request: PipelineRequest) -> InputScreening:
        """Asteroid recovery has no screening failure mode to check.

        `AsteroidRecoveryPipeline.process` already tolerates a target
        with no usable frames -- it just reports zero candidates -- so
        there is no "nothing to do" case to catch before running it.

        Returns
        -------
        screening : `InputScreening`
            Always `can_proceed=True`.
        """
        return InputScreening(can_proceed=True)

    def run(self, request: PipelineRequest, screening: InputScreening) -> RunOutcome:
        """Search for moving objects and keep only the surviving candidates.

        Returns
        -------
        outcome : `RunOutcome`
            `candidates` is the surviving subset, already written onto
            `request.target.asteroid_candidates` -- this pipeline's one
            genuine output side effect, since its result has no catalog
            counterpart to persist through the catalog_access.
        """
        from astrometricslib.pipelines.asteroid_recovery.pipeline import (
            AsteroidRecoveryPipeline,
        )

        target = request.target
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

        return RunOutcome(candidates=target.asteroid_candidates, payload={"metrics": metrics})

    def validate_output(
        self, request: PipelineRequest, outcome: RunOutcome
    ) -> AsteroidRecoveryQualitySummary:
        """Build the quality summary and flag anything worth a look.

        Returns
        -------
        summary : `AsteroidRecoveryQualitySummary`
            Flagged when frames were excluded for missing pointing
            metadata, or when a candidate was confirmed as a mover but
            not matched to a known body.
        """
        from astrometricslib.models.quality_summary import TargetSessionContribution
        from astrometricslib.pipelines.shared.target_sessions import derive_target_sessions

        target = request.target
        metrics = outcome.payload["metrics"]

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
            for candidate in outcome.candidates
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
        self, request: PipelineRequest, outcome: RunOutcome, summary: AsteroidRecoveryQualitySummary
    ) -> dict[str, Any]:
        """Build the result dict asteroid recovery's callers expect back.

        Returns
        -------
        result : `dict`
            Has ``"status"``, ``"targetId"``, ``"analysisMode"``,
            candidate counts at each stage of the discrimination
            cascade, and ``"candidates"`` (the surviving candidates).
        """
        metrics = outcome.payload["metrics"]
        return {
            "status": "completed",
            "targetId": request.target.id,
            "analysisMode": "asteroid_recovery",
            "candidatesDetected": metrics.get("candidates_detected", 0),
            "candidatesRateLinearityConfirmed": metrics.get("candidates_rate_linearity_confirmed", 0),
            "candidatesEphemerisMatched": metrics.get("candidates_ephemeris_matched", 0),
            "candidates": outcome.candidates,
        }


def run_asteroid_recovery_analysis(
    target,  # ruff: ignore[missing-type-function-argument]
    frames,  # ruff: ignore[missing-type-function-argument] -- unused; asteroid recovery reads target.frames itself
    filter_type,  # ruff: ignore[missing-type-function-argument] -- unused; asteroid recovery has no filter concept
    catalog_access,  # ruff: ignore[missing-type-function-argument] -- unused; candidates persist on the target record, not via the catalog_access
    path,  # ruff: ignore[missing-type-function-argument] -- unused; asteroid recovery reads target.frames itself
    **kwargs,  # ruff: ignore[missing-type-kwargs] -- unused
) -> dict[str, Any]:
    """Search a target's light frames for moving objects.

    A thin wrapper kept at this name and signature for
    `pipelines.PIPELINE_RUNNERS` -- the actual work is
    `AsteroidRecoveryPipelineAdapter`, run through the shared
    screen/run/validate/report cycle in `run_pipeline`.

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
