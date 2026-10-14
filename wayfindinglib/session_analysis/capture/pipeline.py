"""Purpose: Run the capture analysis for one observing night.

Description: Ties the three capture stages to the shared session-analysis
interface. Pre-processing judges the data, processing measures it, and
post-processing recommends. The request carries everything the stages need,
so the analysis is a pure function of it: the same request always gives the
same summary. Gathering the request from storage is the caller's job
(`control.history.query(kind="capture")`).
"""

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from astrometricslib import (
    DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    SATURATED_BLOB_MINIMUM_PIXELS,
    SATURATED_FRAME_FRACTION,
)
from wayfindinglib.models.session.capture_frame import CaptureFrame, StackSaturationVerdict
from wayfindinglib.models.session.capture_quality import (
    CaptureInputQuality,
    CapturePerformance,
    CaptureSessionAnalysis,
)
from wayfindinglib.models.session.ekos_session import EkosCapture
from wayfindinglib.models.session.session_quality import RecommendationSeverity
from wayfindinglib.session_analysis.capture.post_processing.recommend_for_capture import recommend_for_capture
from wayfindinglib.session_analysis.capture.pre_processing.assess_capture_input_quality import (
    assess_capture_input_quality,
)
from wayfindinglib.session_analysis.capture.processing.measure_capture_performance import (
    measure_capture_performance,
)
from wayfindinglib.session_analysis.pipeline_base import (
    SessionAnalysisPipeline,
    SessionAnalysisRequest,
    SessionAnalysisResult,
    run_session_pipeline,
)

_RECORDED_LIMITS = (
    "night_star_width_high_limit",
    "night_star_roundness_low_limit",
    "capture_abort_fraction_high_limit",
    "minimum_star_measurement_exposure",
)
"""The limits copied into a summary, so a result can be traced."""


@dataclass
class CaptureAnalysisRequest(SessionAnalysisRequest):
    """Everything the capture analysis needs for one night.

    Attributes
    ----------
    frames : `list` [`CaptureFrame`]
        The night's light frames taken with the equipment in use.
    captures : `list` [`EkosCapture`]
        Every exposure Ekos finished that night.
    aborted_captures : `int`
        How many exposures Ekos cancelled that night.
    has_ekos_record : `bool`
        Whether any Ekos session record exists for the night.
    stack_saturation : `list` [`StackSaturationVerdict`]
        The science library's saturation verdicts for stacked exposures.
    sensor_pixel_count : `int` or `None`
        Pixels on the camera's sensor with no binning.
    """

    frames: list[CaptureFrame] = field(default_factory=list)
    captures: list[EkosCapture] = field(default_factory=list)
    aborted_captures: int = 0
    has_ekos_record: bool = False
    stack_saturation: list[StackSaturationVerdict] = field(default_factory=list)
    sensor_pixel_count: int | None = None


class CaptureSessionPipeline(SessionAnalysisPipeline):
    """The three-stage analysis of one night of captured frames."""

    @property
    def pipeline_name(self) -> str:
        """The name this analysis records itself under.

        Returns
        -------
        name : `str`
            ``"capture"``.
        """
        return "capture"

    def process_input(self, request: CaptureAnalysisRequest) -> SessionAnalysisResult:
        """Judge the night's capture data (pre-processing).

        Returns
        -------
        result : `SessionAnalysisResult`
            Carries the input-quality assessment. `has_work` is `False` when
            there are too few frames to analyse.
        """
        input_quality = assess_capture_input_quality(
            request.frames,
            request.captures,
            request.aborted_captures,
            request.has_ekos_record,
            request.envelope,
            request.limits_equipment_match,
        )
        return SessionAnalysisResult(has_work=input_quality.has_enough_frames, input_quality=input_quality)

    def run(self, request: CaptureAnalysisRequest, result: SessionAnalysisResult) -> SessionAnalysisResult:
        """Measure clipping, star quality and efficiency (processing).

        Returns
        -------
        result : `SessionAnalysisResult`
            The same result with the measurements added.
        """
        result.performance = measure_capture_performance(
            request.frames,
            request.stack_saturation,
            request.sensor_pixel_count,
            request.envelope,
            request.limits_equipment_match,
        )
        return result

    def validate_output(
        self, request: CaptureAnalysisRequest, result: SessionAnalysisResult
    ) -> CaptureSessionAnalysis:
        """Recommend, and build the night's summary (post-processing).

        Returns
        -------
        summary : `CaptureSessionAnalysis`
            The three stages' results, the recommendations, and a flag if any
            recommendation is a warning.
        """
        input_quality: CaptureInputQuality = result.input_quality
        performance: CapturePerformance = result.performance or CapturePerformance()
        recommendations = recommend_for_capture(input_quality, performance)
        warnings = [r for r in recommendations if r.severity == RecommendationSeverity.WARNING]
        resolved: dict[str, Any] = {
            "limits_equipment_match": request.limits_equipment_match,
            "saturated_star_minimum_pixels": SATURATED_BLOB_MINIMUM_PIXELS,
            "clipped_frame_fraction": SATURATED_FRAME_FRACTION,
            "dark_temperature_tolerance_c": DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
        }
        if request.envelope is not None:
            for name in _RECORDED_LIMITS:
                resolved[name] = request.envelope.value(name)
        return CaptureSessionAnalysis(
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
        self, request: CaptureAnalysisRequest, result: SessionAnalysisResult, summary: BaseModel
    ) -> dict[str, Any]:
        """Shape the summary for callers.

        Returns
        -------
        result_dict : `dict`
            The summary as plain data, keyed by its camelCase aliases.
        """
        return summary.model_dump(mode="json", by_alias=True)


def analyze_capture_session_request(request: CaptureAnalysisRequest) -> CaptureSessionAnalysis:
    """Run the capture analysis on a prepared request.

    Parameters
    ----------
    request : `CaptureAnalysisRequest`
        The night's data and limits.

    Returns
    -------
    analysis : `CaptureSessionAnalysis`
        The three-stage analysis of the night.
    """
    return cast("CaptureSessionAnalysis", run_session_pipeline(CaptureSessionPipeline(), request))
