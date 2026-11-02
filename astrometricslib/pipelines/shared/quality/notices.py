"""Notice rows: warnings that are not a gate, stored so a job can list them.

A pipeline records most of its warnings as gates: a self-check with a number,
a limit and an outcome. A few warnings are not gates. They are facts the
pipeline noticed and wants a person to know, such as "this camera has no
profile" or "40 frames were moved out of the stack". A
notice row keeps each of them in the measurement table, so
`Jobs.query(detail="warnings")` lists them next to the failed gates.

A notice row is a measurement with the check outcome ``failed``. Its value
counts what was noticed (frames moved, cameras without a profile). Its limit
is zero, because the notice is raised on any occurrence: there is no tuned
threshold. The row's reason is the sentence that says what was noticed.

This module builds the declarations (`notice_spec`) and the rows
(`notice_measurement`). Each pipeline's ``metrics.py`` declares its own
notices, and the code that stores a pipeline's rows builds them.
"""

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject, SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)

__all__ = [
    "NOTICE_REGISTER_ENTRY",
    "camera_without_profile_measurement",
    "camera_without_profile_metric_name",
    "camera_without_profile_reason",
    "camera_without_profile_spec",
    "notice_limit",
    "notice_measurement",
    "notice_spec",
]

NOTICE_REGISTER_ENTRY = "STK-58"
"""The validation register entry that describes notice rows."""

_NOTICE_LIMIT_DERIVATION = (
    "The notice is raised on any occurrence, so the limit is zero. No threshold was tuned "
    "(validation register STK-58)."
)
"""How a notice's limit is explained to a reader."""

_NO_UNCERTAINTY = "No uncertainty is reported: the value counts what was noticed."
"""The uncertainty note shared by every notice."""

_NOTICE_BAND = "Not tied to a filter or color channel."
"""The band text shared by every notice."""


def notice_limit(unit: str = "count", derivation: str = _NOTICE_LIMIT_DERIVATION) -> QualityLimit:
    """Build the limit of a notice: any occurrence is reported.

    Parameters
    ----------
    unit : `str`, optional
        The unit of the notice's value.
    derivation : `str`, optional
        How the limit is explained.

    Returns
    -------
    limit : `QualityLimit`
        A limit of zero. Its tier is ``geometry``: the rule follows from how
        the data are used, not from a tuned number.
    """
    return QualityLimit(
        value=0.0,
        unit=unit,
        tier=LimitTier.GEOMETRY,
        derivation=derivation,
        registerEntry=NOTICE_REGISTER_ENTRY,
    )


def notice_spec(
    name: str,
    checkpoint: Checkpoint,
    level: SubjectLevel,
    unit: str,
    meaning: str,
    value_description: str,
    computed_by: str,
    purpose_reason: str,
) -> MetricSpec:
    """Declare one notice metric.

    Parameters
    ----------
    name : `str`
        The metric name, ``<pipeline>.notice_<what was noticed>``.
    checkpoint : `Checkpoint`
        Where in processing the notice is raised.
    level : `SubjectLevel`
        The level of the subject the notice is about.
    unit : `str`
        The unit of the value.
    meaning : `str`
        One line in plain words.
    value_description : `str`
        What the stored number is.
    computed_by : `str`
        Dotted path of the function that builds the row, relative to
        ``astrometricslib``.
    purpose_reason : `str`
        Why the notice exists.

    Returns
    -------
    spec : `MetricSpec`
        A decision-purpose declaration whose rows carry the outcome
        ``failed`` and a limit of zero.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=checkpoint,
        levels=(level,),
        better=Direction.LOWER,
        meaning=meaning,
        decisions=("warn that a condition was noticed",),
        estimator=(
            f"{value_description} It is stored only when the condition occurred, with the check outcome "
            "failed and a limit of zero. It is a notice, not a measurement of the sky."
        ),
        sample_definition="The one stack or run the notice is about.",
        band=_NOTICE_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        register_entry=NOTICE_REGISTER_ENTRY,
        purpose=MetricPurpose.DECISION,
        purpose_reason=purpose_reason,
    )


def notice_measurement(
    spec: MetricSpec,
    value: float,
    context: PipelineRunContext,
    subject: QualitySubject,
    reason: str,
    *,
    action_taken: str | None = None,
    limit: QualityLimit | None = None,
) -> QualityMeasurement:
    """Build the row of one notice.

    Parameters
    ----------
    spec : `MetricSpec`
        The notice's declaration.
    value : `float`
        What was noticed, such as a number of frames.
    context : `PipelineRunContext`
        The run's shared facts. The row carries its job id.
    subject : `QualitySubject`
        What the notice is about.
    reason : `str`
        The sentence that says what was noticed.
    action_taken : `str`, optional
        What the pipeline did about it, when it acted.
    limit : `QualityLimit`, optional
        The limit to store. Defaults to `notice_limit` in the spec's unit.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measured row with the outcome ``failed``.
    """
    return build_measurement(
        spec,
        value,
        context,
        subject,
        reason=reason,
        check_outcome=GateStatus.FAILED,
        check_limit=limit if limit is not None else notice_limit(spec.unit),
        action_taken=action_taken,
    )


def camera_without_profile_metric_name(pipeline: str) -> str:
    """Give the name of a pipeline's "camera has no profile" notice.

    Parameters
    ----------
    pipeline : `str`
        The pipeline, such as ``"photometry"``.

    Returns
    -------
    name : `str`
        ``<pipeline>.notice_camera_without_profile``.
    """
    return f"{pipeline}.notice_camera_without_profile"


def camera_without_profile_spec(pipeline: str) -> MetricSpec:
    """Declare a pipeline's "camera has no profile" notice.

    Parameters
    ----------
    pipeline : `str`
        The pipeline, such as ``"photometry"``. It must also be the name of
        a `Checkpoint`.

    Returns
    -------
    spec : `MetricSpec`
        The declaration. A stack's notice is about the stack; any other
        pipeline's is about the run.
    """
    is_stack = pipeline == "stacking"
    return notice_spec(
        camera_without_profile_metric_name(pipeline),
        Checkpoint(pipeline),
        SubjectLevel.STACK if is_stack else SubjectLevel.RUN,
        "count",
        "Whether the frames came from a camera that has no camera profile.",
        "The number of cameras without a profile: 1 when the frames' camera has none.",
        "pipelines.shared.quality.notices.camera_without_profile_measurement",
        (
            "Without a profile, the saturation level and the other sensor facts are guesses about a "
            "made-up camera, so a person should add the camera's profile before trusting them."
        ),
    )


def camera_without_profile_measurement(
    camera_name: str, context: PipelineRunContext, subject: QualitySubject
) -> QualityMeasurement:
    """Build the row that says a camera has no profile.

    Parameters
    ----------
    camera_name : `str`
        The camera that took the frames.
    context : `PipelineRunContext`
        The run's shared facts. Its pipeline name picks the metric.
    subject : `QualitySubject`
        The stack, or the run, the notice is about.

    Returns
    -------
    measurement : `QualityMeasurement`
        A failed notice whose reason is the sentence for the camera.
    """
    return notice_measurement(
        camera_without_profile_spec(context.pipeline_name),
        1.0,
        context,
        subject,
        camera_without_profile_reason(camera_name),
    )


def camera_without_profile_reason(camera_name: str) -> str:
    """Write the sentence for a camera that has no profile.

    Parameters
    ----------
    camera_name : `str`
        The camera that took the frames.

    Returns
    -------
    reason : `str`
        The sentence.
    """
    return f"camera {camera_name!r} has no camera profile; generic assumptions about the sensor were used"
