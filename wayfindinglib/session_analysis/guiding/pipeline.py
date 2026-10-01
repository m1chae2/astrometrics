"""Purpose: Run the guiding analysis for one observing night.

Description: Ties the three guiding stages to the shared session-analysis
interface. Pre-processing judges the data, processing measures it, and
post-processing recommends. The request carries everything the stages need,
so the analysis is a pure function of it: the same request always gives the
same summary. Gathering the request from storage is the caller's job
(`ObservatoryControl.analyze_guiding_session`).
"""

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.session_quality import (
    GuidingInputQuality,
    GuidingPerformance,
    GuidingSessionAnalysis,
    RecommendationSeverity,
)
from wayfindinglib.session_analysis.guiding.post_processing.recommend_for_guiding import recommend_for_guiding
from wayfindinglib.session_analysis.guiding.pre_processing.assess_guiding_input_quality import (
    assess_guiding_input_quality,
)
from wayfindinglib.session_analysis.guiding.processing.measure_guiding_performance import (
    measure_guiding_performance,
)
from wayfindinglib.session_analysis.pipeline_base import (
    SessionAnalysisPipeline,
    SessionAnalysisRequest,
    SessionAnalysisResult,
    run_session_pipeline,
)

_RECORDED_LIMITS = (
    "guiding_rms_limit",
    "trailing_limit",
    "guide_excursion_limit",
    "guide_snr_low_limit",
    "guide_lost_fraction_high_limit",
    "guide_excursion_fraction_high_limit",
    "max_credible_guide_speed",
)
"""The limits copied into a summary, so a result can be traced."""


@dataclass
class GuidingAnalysisRequest(SessionAnalysisRequest):
    """Everything the guiding analysis needs for one night.

    Attributes
    ----------
    samples : `list` [`dict`]
        The night's measured guide samples.
    runs : `list` [`GuidingRunSummary`]
        The night's guiding runs.
    guide_scale_matches_configuration : `bool` or `None`
        Whether the guide log's plate scale agrees with the configured guide
        optics.
    logged_guide_scale : `float` or `None`
        The plate scale the guide log recorded, in arcseconds per pixel.
    configured_guide_scale : `float` or `None`
        The plate scale the configuration gives.
    exposure_lengths_seconds : `list` [`float`]
        The exposure lengths the equipment is used with, for the
        exposure-length view.
    """

    samples: list[dict[str, Any]] = field(default_factory=list)
    runs: list[GuidingRunSummary] = field(default_factory=list)
    guide_scale_matches_configuration: bool | None = None
    logged_guide_scale: float | None = None
    configured_guide_scale: float | None = None
    exposure_lengths_seconds: list[float] = field(default_factory=list)


class GuidingSessionPipeline(SessionAnalysisPipeline):
    """The three-stage analysis of one night of guiding."""

    @property
    def pipeline_name(self) -> str:
        """The name this analysis records itself under.

        Returns
        -------
        name : `str`
            ``"guiding"``.
        """
        return "guiding"

    def process_input(self, request: GuidingAnalysisRequest) -> SessionAnalysisResult:
        """Judge the night's guiding data (pre-processing).

        Returns
        -------
        result : `SessionAnalysisResult`
            Carries the input-quality assessment. `has_work` is `False` when
            there are too few samples to analyse.
        """
        input_quality = assess_guiding_input_quality(
            request.samples,
            request.runs,
            request.envelope,
            request.limits_equipment_match,
            request.guide_scale_matches_configuration,
        )
        return SessionAnalysisResult(has_work=input_quality.has_enough_samples, input_quality=input_quality)

    def run(self, request: GuidingAnalysisRequest, result: SessionAnalysisResult) -> SessionAnalysisResult:
        """Measure the guiding error and declination drift (processing).

        Returns
        -------
        result : `SessionAnalysisResult`
            The same result with the measurements added.
        """
        result.performance = measure_guiding_performance(
            request.samples,
            request.runs,
            request.envelope,
            request.limits_equipment_match,
            request.exposure_lengths_seconds,
        )
        return result

    def validate_output(
        self, request: GuidingAnalysisRequest, result: SessionAnalysisResult
    ) -> GuidingSessionAnalysis:
        """Recommend, and build the night's summary (post-processing).

        Returns
        -------
        summary : `GuidingSessionAnalysis`
            The three stages' results, the recommendations, and a flag if
            any recommendation is a warning.
        """
        input_quality: GuidingInputQuality = result.input_quality
        performance: GuidingPerformance = result.performance or GuidingPerformance()
        recommendations = recommend_for_guiding(
            input_quality,
            performance,
            request.envelope,
            request.logged_guide_scale,
            request.configured_guide_scale,
        )
        warnings = [r for r in recommendations if r.severity == RecommendationSeverity.WARNING]
        resolved: dict[str, Any] = {"limits_equipment_match": request.limits_equipment_match}
        if request.envelope is not None:
            resolved["blur_tolerance_fraction"] = request.envelope.blur_tolerance_fraction
            for name in _RECORDED_LIMITS:
                resolved[name] = request.envelope.value(name)
        return GuidingSessionAnalysis(
            session_id=request.session_id,
            equipment_fingerprint=request.equipment_fingerprint,
            flagged=bool(warnings),
            flag_reasons=[warning.kind.value for warning in warnings],
            resolved_parameters=resolved,
            input_quality=input_quality,
            performance=performance,
            recommendations=recommendations,
        )

    def to_result_dict(
        self, request: GuidingAnalysisRequest, result: SessionAnalysisResult, summary: BaseModel
    ) -> dict[str, Any]:
        """Shape the summary for callers.

        Returns
        -------
        result_dict : `dict`
            The summary as plain data, keyed by its camelCase aliases.
        """
        return summary.model_dump(mode="json", by_alias=True)


def analyze_guiding_session_request(request: GuidingAnalysisRequest) -> GuidingSessionAnalysis:
    """Run the guiding analysis on a prepared request.

    Parameters
    ----------
    request : `GuidingAnalysisRequest`
        The night's data and limits.

    Returns
    -------
    analysis : `GuidingSessionAnalysis`
        The three-stage analysis of the night.
    """
    return cast("GuidingSessionAnalysis", run_session_pipeline(GuidingSessionPipeline(), request))
