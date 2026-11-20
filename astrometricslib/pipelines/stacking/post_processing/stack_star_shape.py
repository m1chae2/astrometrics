"""Records how round the stars of a stack are.

A frame's star shape is measured when the frame is measured (the elongation,
its direction and the star width at the center and in each corner of the
field). The stack image gets the same numbers, so a person can see whether the
stars of the finished stack are round and, from the direction and the corners,
why they are not.

Nothing here measures anything itself. `measure_image_star_profiles` in the
shared frame measurement reads the stack and fits the stars, the same code the
frames use, so the elongation is long axis divided by short axis (1 is round)
at both levels. This file builds one row for each metric in
`STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC` and stores them.

A spectral stack has no star field and gets no rows. A stack with too few
measurable stars gets rows that are not measured, each with the reason. A
failure to read or measure the stack is logged as a warning and recorded the
same way; it never stops the stack.
"""

import logging
import os

from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject, SubjectLevel
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.pipelines.shared.quality.frame_measurement import measure_image_star_profiles
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_STAR_ELONGATION,
    FRAME_STAR_ELONGATION_ANGLE_DEG,
    FRAME_STAR_WIDTH_REGION_PX,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    record_run_measurements,
)
from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
    MOFFAT_MINIMUM_STAR_COUNT,
    StarProfileResult,
    measure_field_region_widths,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC,
    STACKING_METRICS,
)

logger = logging.getLogger(__name__)

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}


def _spec_for(frame_metric: str) -> MetricSpec:
    """Give the stack metric that repeats a frame star-shape metric.

    Parameters
    ----------
    frame_metric : `str`
        A key of `STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC`.

    Returns
    -------
    spec : `MetricSpec`
        The stack metric's declaration.
    """
    return _SPEC_BY_NAME[STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC[frame_metric]]


def _not_measured_rows(
    context: PipelineRunContext, subject: QualitySubject, reason: str
) -> list[QualityMeasurement]:
    """Build a not-measured row for every stack star-shape metric.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack, or one plane of it.
    reason : `str`
        Why nothing was measured.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row for each metric.
    """
    return [
        build_measurement(_spec_for(frame_metric), None, context, subject, reason=reason)
        for frame_metric in STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC
    ]


def _plane_rows(
    context: PipelineRunContext,
    subject: QualitySubject,
    result: StarProfileResult,
    mosaic_note: str,
) -> list[QualityMeasurement]:
    """Turn one plane's star profile result into the stack's star-shape rows.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack, or one plane of it.
    result : `StarProfileResult`
        The plane's result.
    mosaic_note : `str`
        Text added to the notes for a raw mosaic, else empty.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row for each metric.
    """
    if result.width_px is None or result.elongation is None:
        return _not_measured_rows(
            context, subject, result.reason or "the stack's stars could not be measured"
        )
    note = (
        f"{result.star_count} stars from {result.candidate_count} tried "
        f"({result.refusals.describe()}){mosaic_note}"
    )
    rows = [
        build_measurement(
            _spec_for(FRAME_STAR_ELONGATION),
            result.elongation,
            context,
            subject,
            uncertainty=result.elongation_uncertainty,
            sample_count=result.star_count,
            provenance=note,
        ),
        build_measurement(
            _spec_for(FRAME_STAR_ELONGATION_ANGLE_DEG),
            result.angle_deg,
            context,
            subject,
            uncertainty=result.angle_uncertainty_deg,
            sample_count=result.star_count,
            reason="the stars have no measurable direction",
            provenance=(
                f"{note}; angles agree with a coherence of {result.angle_coherence or 0.0:.2f} "
                "(0 means no preferred direction, so the angle means little)"
            ),
        ),
    ]
    minimum_star_count = resolve(MOFFAT_MINIMUM_STAR_COUNT).value
    for region in measure_field_region_widths(result):
        spec = _spec_for(FRAME_STAR_WIDTH_REGION_PX[region.region])
        if region.width_px is None:
            rows.append(
                build_measurement(
                    spec,
                    None,
                    context,
                    subject,
                    reason=(
                        f"only {region.star_count} fitted stars lie in this region of the field; "
                        f"at least {minimum_star_count} are needed"
                    ),
                )
            )
            continue
        rows.append(
            build_measurement(
                spec,
                region.width_px,
                context,
                subject,
                uncertainty=region.width_px_uncertainty,
                sample_count=region.star_count,
                provenance=(
                    f"{region.star_count} of the stack's {result.star_count} fitted stars lie in this region"
                ),
            )
        )
    return rows


def stack_star_shape_measurements(
    context: PipelineRunContext, stack_path: str, *, is_spectral: bool
) -> list[QualityMeasurement]:
    """Measure the star shape of a stack and build its rows.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    stack_path : `str`
        The stack's FITS file. Only its file name goes into a subject.
    is_spectral : `bool`
        `True` for a spectral stack, which gets no rows.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row for each metric (and each color plane of a stack stored as
        planes), not measured with a reason when the stars could not be
        measured. Empty for a spectral stack.
    """
    if is_spectral:
        return []
    subject = context.stack_subject(os.path.basename(stack_path))
    try:
        planes = measure_image_star_profiles(stack_path)
    except Exception as error:  # a by-product of the stack: any failure is a not-measured row
        logger.exception("The star shape of the stack %s could not be measured", stack_path)
        return _not_measured_rows(context, subject, f"the stack's star shape could not be measured: {error}")
    rows: list[QualityMeasurement] = []
    for channel, result, mosaic_note in planes:
        plane_subject = subject if channel is None else subject.model_copy(update={"channel": channel})
        rows.extend(_plane_rows(context, plane_subject, result, mosaic_note))
    return rows


def _already_recorded(context: PipelineRunContext, stack_path: str, config: object) -> bool:
    """Tell whether the store already holds a star-shape row for the stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    stack_path : `str`
        The stack's FITS file.
    config : `AppConfiguration` or `None`
        Where the quality store lives.

    Returns
    -------
    recorded : `bool`
        `True` when a row exists, measured or not. `False` when none exists
        or the store cannot be read.
    """
    subject_id = context.stack_subject(os.path.basename(stack_path)).subject_id
    try:
        rows = QualityStore.from_configuration(config).query(
            metric=STACK_STAR_SHAPE_METRIC_BY_FRAME_METRIC[FRAME_STAR_ELONGATION],
            level=SubjectLevel.STACK,
            subject_ids=[subject_id],
        )
    except Exception:  # a by-product: reading the store may not fail the stack
        logger.warning(
            "The stored star-shape rows of the stack %s could not be read", stack_path, exc_info=True
        )
        return False
    return bool(rows)


def record_stack_star_shapes(
    context: PipelineRunContext,
    stack_path: str,
    *,
    is_spectral: bool,
    config: object = None,
    only_if_missing: bool = False,
) -> int:
    """Measure the star shape of a stack and store the rows.

    This never fails the stack. A measurement that fails is logged as a
    warning and stored as not measured.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    stack_path : `str`
        The stack's FITS file.
    is_spectral : `bool`
        `True` for a spectral stack, which gets no rows.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.
    only_if_missing : `bool`, optional
        Skip the measurement when the store already holds a row for this
        stack. An up-to-date stack that the stage skips passes `True`, so an
        older stack gets its rows once and is not measured again on each run.

    Returns
    -------
    new_count : `int`
        How many rows were new.
    """
    if is_spectral or not stack_path:
        return 0
    try:
        if only_if_missing and _already_recorded(context, stack_path, config):
            return 0
        rows = stack_star_shape_measurements(context, stack_path, is_spectral=is_spectral)
        return record_run_measurements(rows, config=config)
    except Exception:  # a by-product of the stack: no failure here may fail it
        logger.exception("The star shape rows of the stack %s were not stored", stack_path)
        return 0
