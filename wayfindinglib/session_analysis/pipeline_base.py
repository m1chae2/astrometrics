"""Purpose: The shared shape of every session-quality analysis.

Description: The science library's pipelines all follow one pattern: check
the input, do the work, check the output, hand back a result. Session-quality
analysis follows the same four steps. They map onto the three stages a reader
cares about:

1. `process_input` is pre-processing. It asks whether the data is good and
   reports problems in how it was gathered.
2. `run` is processing. It measures what the data shows.
3. `validate_output` is post-processing. It turns the measurements into
   recommendations and builds the night's quality summary.
4. `to_result_dict` shapes that summary for callers.

This mirrors `astrometricslib.pipelines.pipeline_base.AnalysisPipeline`
without importing it, because that interface is built around a `Target`
(an astronomical object with frames) and a night's telescope records have
no target.

Every step is a pure function of the request: nothing reads a database or
the equipment configuration here. The caller gathers the inputs, which is
why the same request always gives the same analysis.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from wayfindinglib.models.equipment_and_site.performance_envelope import PerformanceEnvelope


@dataclass
class SessionAnalysisRequest:
    """What every session analysis needs, gathered into one object.

    Attributes
    ----------
    session_id : `str`
        The observing night to analyse.
    equipment_fingerprint : `str`
        The equipment the limits were worked out for.
    envelope : `PerformanceEnvelope` or `None`
        The equipment-derived limits, or `None` if no equipment is active.
    limits_equipment_match : `str`
        How well the night's equipment matches the equipment the limits
        were worked out for: ``"exact"``, ``"guide_optics_only"`` or
        ``"none"``.
    """

    session_id: str
    equipment_fingerprint: str
    envelope: PerformanceEnvelope | None
    limits_equipment_match: str = "none"


@dataclass
class SessionAnalysisResult:
    """What one analysis produced, stage by stage.

    Attributes
    ----------
    has_work : `bool`
        Whether there is enough data for `run` to do anything. `False` skips
        `run`, and the result still goes through `validate_output` so the
        night still gets a summary that says why nothing was analysed.
    input_quality : `Any`
        The pre-processing result.
    performance : `Any`
        The processing result.
    payload : `dict`
        Anything else a stage hands to the next one.
    """

    has_work: bool = True
    input_quality: Any = None
    performance: Any = None
    payload: dict[str, Any] = field(default_factory=dict)


class SessionAnalysisPipeline(ABC):
    """The blueprint every session-quality analysis follows."""

    @property
    @abstractmethod
    def pipeline_name(self) -> str:
        """The name this analysis records itself under."""

    @abstractmethod
    def process_input(self, request: SessionAnalysisRequest) -> SessionAnalysisResult:
        """Judge the quality of the input data (the pre-processing stage).

        Parameters
        ----------
        request : `SessionAnalysisRequest`
            The night to analyse.

        Returns
        -------
        result : `SessionAnalysisResult`
            `has_work=False` when there is too little data to analyse.
            Otherwise `has_work=True`, with the input-quality assessment.
        """

    @abstractmethod
    def run(self, request: SessionAnalysisRequest, result: SessionAnalysisResult) -> SessionAnalysisResult:
        """Measure what the data shows (the processing stage).

        Parameters
        ----------
        request : `SessionAnalysisRequest`
            The night being analysed.
        result : `SessionAnalysisResult`
            What `process_input` returned.

        Returns
        -------
        result : `SessionAnalysisResult`
            The same result with the measurements added.
        """

    @abstractmethod
    def validate_output(self, request: SessionAnalysisRequest, result: SessionAnalysisResult) -> BaseModel:
        """Recommend, and build the summary (the post-processing stage).

        Parameters
        ----------
        request : `SessionAnalysisRequest`
            The night that was analysed.
        result : `SessionAnalysisResult`
            What the earlier stages produced.

        Returns
        -------
        summary : `pydantic.BaseModel`
            The night's quality summary, including its recommendations and
            any flags they raise.
        """

    @abstractmethod
    def to_result_dict(
        self, request: SessionAnalysisRequest, result: SessionAnalysisResult, summary: BaseModel
    ) -> dict[str, Any]:
        """Shape the summary for callers.

        Returns
        -------
        result_dict : `dict`
            The summary as plain data.
        """


def run_session_pipeline(adapter: SessionAnalysisPipeline, request: SessionAnalysisRequest) -> BaseModel:
    """Run one analysis through pre-processing, processing and post-processing.

    The one place that calls the steps in order, so no caller has to know
    the sequence.

    Parameters
    ----------
    adapter : `SessionAnalysisPipeline`
        The analysis to run.
    request : `SessionAnalysisRequest`
        The night to analyse.

    Returns
    -------
    summary : `pydantic.BaseModel`
        The night's quality summary. When `process_input` found too little
        data, `run` is skipped and the summary says so.
    """
    result = adapter.process_input(request)
    if result.has_work:
        result = adapter.run(request, result)
    return adapter.validate_output(request, result)
