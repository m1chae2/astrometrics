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
class InputScreening:
    """What `screen_input` decided about whether there is anything to do.

    Attributes
    ----------
    can_proceed : `bool`
        Whether `run` should be called at all. `False` for, e.g.,
        photometry finding no frames for the requested filter --
        `run` and `validate_output` are skipped entirely in that case.
    early_result : `dict` or `None`
        The result to return immediately when `can_proceed` is `False`.
        Unused when `can_proceed` is `True`.
    context : `dict`
        Anything `screen_input` computed that `run` needs, so `run` does
        not have to redo the same work -- photometry's already-derived
        session list, for example.
    """

    can_proceed: bool = True
    early_result: dict[str, Any] | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunOutcome:
    """What one pipeline's science work produced.

    A grab-bag by design, not a forced common shape: the four pipelines
    produce genuinely different things -- a solved WCS and detection
    context, a star list, light curves, moving-object candidates -- and
    unifying that would just move the four-way divergence into this
    class instead of removing it. `payload` carries whatever a given
    pipeline's `validate_output` and `to_result_dict` need that does not
    fit `stellar_objects` / `candidates` / `context`.

    Attributes
    ----------
    stellar_objects : `list`
        Stars this run found and saved, for the three pipelines that
        deal in stars.
    candidates : `list`
        Moving-object candidates, for asteroid recovery.
    context : `Any`
        The astrometry `AnalysisContext`, for the two pipelines built on it.
    payload : `dict`
        Everything else `validate_output` / `to_result_dict` need.
    """

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
    def screen_input(self, request: PipelineRequest) -> InputScreening:
        """Check whether this run has anything to do.

        Parameters
        ----------
        request : `PipelineRequest`
            The run being screened.

        Returns
        -------
        screening : `InputScreening`
            Whether to proceed, and the result to return immediately if not.
        """
        pass

    @abstractmethod
    def run(self, request: PipelineRequest, screening: InputScreening) -> RunOutcome:
        """Do the pipeline's actual science work.

        Parameters
        ----------
        request : `PipelineRequest`
            The run being performed.
        screening : `InputScreening`
            The result of `screen_input`, already confirmed to allow
            proceeding.

        Returns
        -------
        outcome : `RunOutcome`
            What this run produced.
        """
        pass

    @abstractmethod
    def validate_output(self, request: PipelineRequest, outcome: RunOutcome) -> PipelineQualitySummaryBase:
        """Check the results and build this run's quality record.

        Does not assign the summary onto `request.target` -- that is
        `run_pipeline`'s job, so this method only builds and returns an
        object it owns, and can be tested without a real `Target`.

        Parameters
        ----------
        request : `PipelineRequest`
            The run being validated.
        outcome : `RunOutcome`
            What `run` produced.

        Returns
        -------
        summary : `PipelineQualitySummaryBase`
            This run's quality record, including any flags it raised.
        """
        pass

    @abstractmethod
    def to_result_dict(
        self, request: PipelineRequest, outcome: RunOutcome, summary: PipelineQualitySummaryBase
    ) -> dict[str, Any]:
        """Build the dict this pipeline's callers still expect back.

        Parameters
        ----------
        request : `PipelineRequest`
            The run that was performed.
        outcome : `RunOutcome`
            What `run` produced.
        summary : `PipelineQualitySummaryBase`
            What `validate_output` built.

        Returns
        -------
        result : `dict`
            The dict `analyze_target`'s caller receives.
        """
        pass


def run_pipeline(adapter: AnalysisPipeline, request: PipelineRequest) -> dict[str, Any]:
    """Run one pipeline through the full screen/run/validate/report cycle.

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
    result : `dict`
        `screening.early_result`, unchanged, when the input screening
        stopped the run; otherwise `adapter.to_result_dict(...)`.
    """
    screening = adapter.screen_input(request)
    if not screening.can_proceed:
        return screening.early_result

    outcome = adapter.run(request, screening)
    summary = adapter.validate_output(request, outcome)
    setattr(request.target, f"{adapter.pipeline_name}_quality_summary", summary)
    return adapter.to_result_dict(request, outcome, summary)
