"""The shared shape every analysis pipeline follows.

Astrometry, spectroscopy, photometry, and asteroid recovery are four very
different pieces of science -- different inputs, different algorithms,
different output shapes -- but the architecture doc describes all four
the same way: check the input, do the work, check the output, hand back
a result. `AnalysisPipeline` names that shape directly in the code
instead of leaving it as something four similarly-organized functions
merely happen to share.

This interface is imposed, not discovered: the four existing algorithm
classes (`AstrometryPipeline`, `SpectroscopyPipeline`, `VariabilityAnalyzer`,
`AsteroidRecoveryPipeline`) are not touched by it. Each
`pipelines/<domain>.py` module instead gets an *adapter* class that owns
one of those algorithm instances and reshapes its calls to fit this
interface -- adding the interface costs zero changes to code that
already works.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from astrometricslib.models.quality_summary import PipelineQualitySummaryBase
from astrometricslib.models.target import FrameRecord, Target


@dataclass
class PipelineRequest:
    """Everything one pipeline run needs, gathered into one object.

    Plain data, not a pydantic model: nothing here is serialized or
    validated against user input -- it is an in-process handoff from
    `analyze_target` to whichever pipeline it dispatches to -- so a
    dataclass is the right amount of structure, the same choice already
    made for `StarIdentificationBreakdown`.

    Attributes
    ----------
    target : `Target`
        The target being analyzed.
    catalog_access : `Any`
        Reads and writes the shared star catalog.
    frames : `list` [`FrameRecord`] or `None`
        The frames to use, for pipelines that work from a frame list
        rather than a single image. `target.frames` if not given.
    filter_type : `str` or `None`
        Restricts which frames are used, for pipelines that care.
    path : `str` or `None`
        The single image to analyze, for pipelines that work that way.
    options : `dict`
        Everything else a caller passed as a keyword argument.
    """

    target: Target
    catalog_access: Any
    frames: list[FrameRecord] | None = None
    filter_type: str | None = None
    path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    """What one pipeline's science work produced -- or is about to.

    The same type flows through every stage: `process_input` hands back
    a `Result` before any science work has run, `run` hands back the
    real one, and `validate_output`/`to_result_dict` read whichever one
    they got. This is deliberate: when `process_input` decides there is
    nothing to do (e.g. photometry finding no frames for the requested
    filter), it returns an empty, `has_work=False` `Result` instead of a
    separate early-exit shape -- `run` is skipped, but that empty
    `Result` still goes through `validate_output`/`to_result_dict`
    exactly like a real run's would, so the target still gets a real
    (if empty) quality summary and the caller still gets the normal
    result shape, with the reason recorded as a flag rather than a
    one-off status/message pair.

    Beyond that, this is a grab-bag by design, not a forced common
    shape: the four pipelines produce genuinely different things -- a
    solved WCS and detection context, a star list, light curves,
    moving-object candidates -- and unifying that would just move the
    four-way divergence into this class instead of removing it.
    `payload` carries whatever a given pipeline's `run`, `validate_output`,
    and `to_result_dict` need that does not fit `stellar_objects` /
    `candidates` / `context`.

    Attributes
    ----------
    has_work : `bool`
        Whether there is real work for `run` to do. `True` unless
        `process_input` decided there is nothing to process, in which
        case `run` is skipped and this `Result` goes straight to
        `validate_output`.
    stellar_objects : `list`
        Stars this run found and saved, for the three pipelines that
        deal in stars.
    candidates : `list`
        Moving-object candidates, for asteroid recovery.
    context : `Any`
        The astrometry `AnalysisContext`, for the two pipelines built on it.
    payload : `dict`
        Everything else `run` / `validate_output` / `to_result_dict` need.
        Also how `process_input` hands `run` anything it already
        computed, so `run` does not have to redo the same work --
        photometry's already-derived session list, for example.
    """

    has_work: bool = True
    stellar_objects: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    context: Any = None
    payload: dict[str, Any] = field(default_factory=dict)


class AnalysisPipeline(ABC):
    """The blueprint every analysis pipeline follows.

    Every pipeline the architecture doc describes goes through the same
    four steps: check the input, do the work, check the output, and hand
    back a result. Naming each step means a pipeline's "wart" -- the
    exact shape of dict it hands back to its caller, a different shape
    for each of the four pipelines -- is visible as `to_result_dict`
    instead of hidden inside a hundred-line function.
    """

    @property
    @abstractmethod
    def pipeline_name(self) -> str:
        """The name this pipeline records itself under.

        Must match the `pipeline_name` literal on the matching
        `*QualitySummary` class -- `test_pipeline_contract_conformance.py`
        checks this, so the two cannot silently drift apart.
        """

    @abstractmethod
    def process_input(self, request: PipelineRequest) -> Result:
        """Check the input and hand back a starting `Result`.

        Parameters
        ----------
        request : `PipelineRequest`
            The run being started.

        Returns
        -------
        result : `Result`
            `has_work=False` when there is nothing for `run` to do --
            `run` is skipped, and this empty `Result` goes straight to
            `validate_output`. Otherwise `has_work=True`, with anything
            already computed here (so `run` does not have to redo it)
            in `payload`.
        """
        pass

    @abstractmethod
    def run(self, request: PipelineRequest, result: Result) -> Result:
        """Do the pipeline's actual science work.

        Parameters
        ----------
        request : `PipelineRequest`
            The run being performed.
        result : `Result`
            What `process_input` returned; always `has_work=True` here,
            since `run_pipeline` skips calling this method otherwise.

        Returns
        -------
        result : `Result`
            What this run produced.
        """
        pass

    @abstractmethod
    def validate_output(self, request: PipelineRequest, result: Result) -> PipelineQualitySummaryBase:
        """Check the results and build this run's quality record.

        Does not assign the summary onto `request.target` -- that is
        `run_pipeline`'s job, so this method only builds and returns an
        object it owns, and can be tested without a real `Target`.

        Parameters
        ----------
        request : `PipelineRequest`
            The run being validated.
        result : `Result`
            What `run` produced, or the empty `Result` from
            `process_input` when there was no work to do.

        Returns
        -------
        summary : `PipelineQualitySummaryBase`
            This run's quality record, including any flags it raised.
        """
        pass

    @abstractmethod
    def to_result_dict(
        self, request: PipelineRequest, result: Result, summary: PipelineQualitySummaryBase
    ) -> dict[str, Any]:
        """Build the dict this pipeline's callers still expect back.

        Parameters
        ----------
        request : `PipelineRequest`
            The run that was performed.
        result : `Result`
            What `run` produced, or the empty `Result` from
            `process_input` when there was no work to do.
        summary : `PipelineQualitySummaryBase`
            What `validate_output` built.

        Returns
        -------
        result_dict : `dict`
            The dict `analyze_target`'s caller receives.
        """
        pass


def run_pipeline(adapter: AnalysisPipeline, request: PipelineRequest) -> dict[str, Any]:
    """Run one pipeline through the full input/main/output processing cycle.

    This is the one place that calls all four `AnalysisPipeline` methods
    in order, so no pipeline's caller has to know the sequence -- or,
    critically, remember that `validate_output` does not assign
    `target.<pipeline_name>_quality_summary` itself.

    Parameters
    ----------
    adapter : `AnalysisPipeline`
        The pipeline to run.
    request : `PipelineRequest`
        The run to perform.

    Returns
    -------
    result_dict : `dict`
        `adapter.to_result_dict(...)`, built from either `run`'s
        `Result` or, when `process_input` found nothing to do, its own
        empty one.
    """
    result = adapter.process_input(request)
    if result.has_work:
        result = adapter.run(request, result)

    summary = adapter.validate_output(request, result)
    setattr(request.target, f"{adapter.pipeline_name}_quality_summary", summary)
    return adapter.to_result_dict(request, result, summary)
