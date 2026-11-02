"""Purpose: Record a notice when the preview of a stack skips a step.

Description: The preview picture of a stack runs optional cleanup steps
(GraXpert, the colour calibration, StarNet, Cosmic Clarity and the star
toning). A step that fails is skipped, and the preview is made without it, so
the picture differs from the one the settings describe. Before this module the
only trace was a line in the log. Here the skip is stored as a notice row
(`astrometricslib.pipelines.shared.quality.notices`) on the stack, so
`Jobs.query(detail="warnings")` lists it next to the stack's other warnings.

The preview runs after the stack's quality rows are stored, so the notice is
stored in its own call (`record_preview_step_notices`).
"""

from datetime import datetime
from typing import Any

from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
from astrometricslib.pipelines.shared.quality.notices import notice_measurement
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    record_run_measurements,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_METRICS,
    STACKING_NOTICE_PREVIEW_STEP_SKIPPED,
)
from astrometricslib.pipelines.stacking.post_processing.stack_preview import SkippedPreviewStep

__all__ = [
    "SkippedPreviewStep",
    "preview_step_skipped_measurements",
    "record_preview_step_notices",
]

_SPEC = next(spec for spec in STACKING_METRICS if spec.name == STACKING_NOTICE_PREVIEW_STEP_SKIPPED)


def preview_step_skipped_measurements(
    skipped_steps: list[SkippedPreviewStep], context: PipelineRunContext, stack_file_name: str
) -> list[QualityMeasurement]:
    """Build the notice row for the steps a preview skipped.

    Parameters
    ----------
    skipped_steps : `list` of `SkippedPreviewStep`
        The steps that were skipped.
    context : `PipelineRunContext`
        The run's shared facts. The row carries its job id.
    stack_file_name : `str`
        The stack's file name.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row whose value is the number of skipped steps and whose reason
        joins their sentences, or an empty list when no step was skipped.
    """
    if not skipped_steps:
        return []
    return [
        notice_measurement(
            _SPEC,
            float(len(skipped_steps)),
            context,
            context.stack_subject(stack_file_name),
            "the preview skipped a step: " + "; ".join(step.reason for step in skipped_steps),
            action_taken="the preview picture was made without the skipped step",
        )
    ]


def record_preview_step_notices(
    skipped_steps: list[SkippedPreviewStep],
    target_id: str,
    stack_file_name: str,
    job_id: str | None,
    measured_at: datetime | None,
    configuration: Any | None,
) -> None:
    """Store the notice row for the steps a preview skipped.

    This never stops a preview. Nothing is stored when no step was skipped or
    the caller gave no measurement time (pipeline code does not read the
    clock).

    Parameters
    ----------
    skipped_steps : `list` of `SkippedPreviewStep`
        The steps that were skipped.
    target_id : `str`
        The target's id.
    stack_file_name : `str`
        The stack's file name.
    job_id : `str` or `None`
        The job the preview ran in. The row carries it, so the job lists the
        notice as a warning.
    measured_at : `datetime` or `None`
        When the run started, with a time zone.
    configuration : `AppConfiguration` or `None`
        Where the quality store lives. The process-wide one if `None`.
    """
    if not skipped_steps or measured_at is None:
        return
    context = PipelineRunContext.from_options(
        target_id, "stacking", STACKING_PIPELINE_VERSION, {"job_id": job_id, "measured_at": measured_at}
    )
    if context is None:
        return
    record_run_measurements(
        preview_step_skipped_measurements(skipped_steps, context, stack_file_name), config=configuration
    )
