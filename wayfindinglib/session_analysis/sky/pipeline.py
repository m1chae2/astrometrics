"""Purpose: Run the sky-position analysis across every recorded night.

Description: Ties the three sky stages to the shared session-analysis
interface. The other analyses judge one night. This one judges all of them
together, because a comparison between parts of the sky needs several nights
in each part. The request's `session_id` names the span of nights
(``"first..last"``). The analysis is a pure function of the request, and
gathering the request from storage is the caller's job
(`control.history.query(kind="sky_coverage")`).
"""

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from wayfindinglib.analytics.performance_envelope import BASELINE_SPREAD_MULTIPLIER
from wayfindinglib.models.session.session_quality import RecommendationSeverity
from wayfindinglib.models.session.sky_quality import (
    SkyAnalysis,
    SkyInputQuality,
    SkyPerformance,
    SkySample,
)
from wayfindinglib.session_analysis.pipeline_base import (
    SessionAnalysisPipeline,
    SessionAnalysisRequest,
    SessionAnalysisResult,
    run_session_pipeline,
)
from wayfindinglib.session_analysis.sky.post_processing.recommend_for_sky import recommend_for_sky
from wayfindinglib.session_analysis.sky.pre_processing.assess_sky_input_quality import (
    MINIMUM_NIGHTS_PER_BIN,
    assess_sky_input_quality,
)
from wayfindinglib.session_analysis.sky.processing.measure_sky_performance import (
    METRIC_SPECS,
    measure_sky_performance,
)


@dataclass
class SkyAnalysisRequest(SessionAnalysisRequest):
    """Everything the sky analysis needs.

    Attributes
    ----------
    samples : `list` [`SkySample`]
        Every measurement with a known position.
    samples_without_position : `int`
        Measurements left out because they have no position.
    guiding_nights_excluded : `int`
        Nights whose guiding runs were left out as unreliable.
    minimum_altitude_degrees : `float`
        The lowest altitude the telescope is configured to observe at.
    maximum_altitude_degrees : `float`
        The highest.
    blur_tolerance_fraction : `float`
        How much worse than the rest of the night a part of the sky must be to
        count as poor.
    """

    samples: list[SkySample] = field(default_factory=list)
    samples_without_position: int = 0
    guiding_nights_excluded: int = 0
    minimum_altitude_degrees: float = 0.0
    maximum_altitude_degrees: float = 90.0
    blur_tolerance_fraction: float = 0.10


class SkySessionPipeline(SessionAnalysisPipeline):
    """The three-stage analysis of performance across the sky."""

    @property
    def pipeline_name(self) -> str:
        """The name this analysis records itself under.

        Returns
        -------
        name : `str`
            ``"sky"``.
        """
        return "sky"

    def process_input(self, request: SkyAnalysisRequest) -> SessionAnalysisResult:
        """Judge whether the data can answer the question (pre-processing).

        Returns
        -------
        result : `SessionAnalysisResult`
            Carries the coverage. `has_work` is `False` when too few nights
            exist.
        """
        input_quality = assess_sky_input_quality(
            request.samples,
            request.samples_without_position,
            request.guiding_nights_excluded,
            request.minimum_altitude_degrees,
            request.maximum_altitude_degrees,
        )
        return SessionAnalysisResult(has_work=input_quality.has_enough_data, input_quality=input_quality)

    def run(self, request: SkyAnalysisRequest, result: SessionAnalysisResult) -> SessionAnalysisResult:
        """Compare each part of the sky with the typical night (processing).

        Returns
        -------
        result : `SessionAnalysisResult`
            The same result with the comparisons added.
        """
        result.performance = measure_sky_performance(
            request.samples,
            request.minimum_altitude_degrees,
            request.maximum_altitude_degrees,
            request.blur_tolerance_fraction,
        )
        return result

    def validate_output(self, request: SkyAnalysisRequest, result: SessionAnalysisResult) -> SkyAnalysis:
        """Recommend, and build the summary (post-processing).

        Returns
        -------
        summary : `SkyAnalysis`
            The three stages' results and the recommendations.
        """
        input_quality: SkyInputQuality = result.input_quality
        performance: SkyPerformance = result.performance or SkyPerformance()
        recommendations = recommend_for_sky(input_quality, performance, request.blur_tolerance_fraction)
        warnings = [r for r in recommendations if r.severity == RecommendationSeverity.WARNING]
        return SkyAnalysis(
            session_id=request.session_id,
            equipment_fingerprint=request.equipment_fingerprint,
            flagged=bool(warnings),
            flag_reasons=[warning.kind.value for warning in warnings],
            resolved_parameters={
                "blur_tolerance_fraction": request.blur_tolerance_fraction,
                "minimum_z_score": BASELINE_SPREAD_MULTIPLIER,
                "minimum_nights_per_bin": MINIMUM_NIGHTS_PER_BIN,
                "metrics": list(METRIC_SPECS),
            },
            input_quality=input_quality,
            performance=performance,
            recommendations=recommendations,
        )

    def to_result_dict(
        self, request: SkyAnalysisRequest, result: SessionAnalysisResult, summary: BaseModel
    ) -> dict[str, Any]:
        """Shape the summary for callers.

        Returns
        -------
        result_dict : `dict`
            The summary as plain data, keyed by its camelCase aliases.
        """
        return summary.model_dump(mode="json", by_alias=True)


def analyze_sky_request(request: SkyAnalysisRequest) -> SkyAnalysis:
    """Run the sky analysis on a prepared request.

    Parameters
    ----------
    request : `SkyAnalysisRequest`
        The measurements, the telescope's altitude range and the tolerance.

    Returns
    -------
    analysis : `SkyAnalysis`
        The three-stage analysis across the nights.
    """
    return cast("SkyAnalysis", run_session_pipeline(SkySessionPipeline(), request))
