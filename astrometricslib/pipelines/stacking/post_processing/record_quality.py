"""Builds and records the quality measurements of one stack.

The stacking stage keeps its checks where they are measured (the frame
quarantine, the background check, the flat assessment and so on), and collects
their results in the stack's quality summary. This file does not judge
anything. It only turns that finished summary into quality measurements and
stores them next to the summary, which stays as it is.

Five kinds of measurement are made:

- The summary's numbers, such as the rejected pixel fraction, the flat noise
  and the star widths, each under a name that says how it was measured.
- The summary's self-checks (gates), each stored with its outcome and limit.
- The noise of the stack in empty sky, on a flux scale that is the same for
  every stack of the target (see `stack_noise.py`), and the effective frame
  count and time (see `stack_membership.py`).
- The noise the stack's frames predict, the noise the master flat adds, the
  stack's sky level and the brightest star's peak of each exposure group (see
  `record_science.py`), each stored as parts.
- One weight for each frame that went into the stack.

Nothing here reads the clock: the caller supplies the time. A failure while
measuring or storing is logged and never fails the stack.
"""

import logging
import math
import os
import time
from collections.abc import Sequence
from typing import Any, NamedTuple

from astrometricslib.models.quality.gate_result import GateResult
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
)
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.models.quality.quality_summary import ExposureGroupSummary, StackQualitySummary
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    GateLimitSource,
    PipelineRunContext,
    build_measurement,
    gate_measurement,
    record_run_measurements,
)
from astrometricslib.pipelines.shared.registration_record import build_registration_record
from astrometricslib.pipelines.stacking.metrics import (
    GATE_METRIC_BY_NAME,
    STACKING_DURATION_S,
    STACKING_EFFECTIVE_FRAME_COUNT,
    STACKING_EFFECTIVE_TIME_READ_NOISE_S,
    STACKING_EFFECTIVE_TIME_S,
    STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
    STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_EXPECTED_STACK_FWHM_PX,
    STACKING_FLAT_FRAME_COUNT,
    STACKING_FLAT_NOISE_FRACTION,
    STACKING_FLAT_SMOOTHING_SIGMA_PX,
    STACKING_FRAME_WEIGHT,
    STACKING_FRAMES_ACCEPTED,
    STACKING_FRAMES_EXCLUDED_FOR_BACKGROUND,
    STACKING_FRAMES_EXCLUDED_FOR_GAIN,
    STACKING_FRAMES_QUARANTINED,
    STACKING_FRAMES_STACKED,
    STACKING_FRAMES_SUBMITTED,
    STACKING_GROUP_ALIGNMENT_SHIFT_PX,
    STACKING_GROUP_EXPOSURE_S,
    STACKING_GROUP_FRAMES_STACKED,
    STACKING_GROUP_FRAMES_SUBMITTED,
    STACKING_GROUP_GAIN_BRIGHT_END_RATIO,
    STACKING_GROUP_GAIN_DISAGREEMENT,
    STACKING_GROUP_GAIN_MID_RANGE,
    STACKING_MEDIAN_INPUT_FWHM_PX,
    STACKING_METRICS,
    STACKING_NEGATIVE_PIXEL_MAX_FRACTION,
    STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT,
    STACKING_REGISTRATION_REFERENCE_STAR_COUNT,
    STACKING_REJECTED_PIXEL_FRACTION,
    STACKING_SATURATED_PIXEL_FRACTION,
    STACKING_STACKED_FWHM_PX,
    STACKING_ZERO_PIXEL_FRACTION,
)
from astrometricslib.pipelines.stacking.post_processing.field_rotation import (
    MULTI_GROUP_REASON,
    UNMATCHED_REASON,
    field_rotation_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    StackScience,
    compute_stack_science,
    science_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.registration_shifts import pointing_measurements
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMembership,
    determine_stack_members,
    effective_exposure_seconds,
    effective_frame_count,
    effective_read_noise_time_seconds,
    normalized_weights,
    registration_reference_from_diagnostics,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    APERTURE_FILE_NAME,
    NO_WCS_REASON,
    StackDepth,
    StackNoiseOutcome,
    discard_noise_sums,
    measure_stack_noise,
    save_noise_sums,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GATE_LIMIT_SOURCES",
    "FrameGeometryRows",
    "exposure_group_subject",
    "frame_geometry_measurements",
    "gate_for_measurement",
    "measure_noise_safely",
    "membership_count_measurements",
    "noise_measurements",
    "record_stacking_quality",
    "stack_depth_of",
    "stacking_measurements",
]

_SPEC_BY_NAME: dict[str, MetricSpec] = {spec.name: spec for spec in STACKING_METRICS}

# How each gate's limit was obtained (decision 26); `GATE_LIMIT_SOURCES`
# below joins it with the metric that stores each gate. A limit that the
# validation register marks as measured is a validated-dataset limit, and its
# register entry goes with it. A limit derived from physics is a physical
# budget. A limit that is a designed value nobody has measured yet, even when
# a register entry holds the evidence so far, is a design estimate; the entry
# is cited where one exists. A rule that follows from how the data are used
# (a mismatch count, a dropped group) is geometry. The saturation level that
# the saturated-pixel check counts comes from the camera profile, but the
# share of saturated pixels that flags a stack does not, so that gate is a
# design estimate too.
_GATE_TIERS: dict[str, tuple[LimitTier, str | None]] = {
    # No limit at all: setting a minority gain aside corrects the stack.
    "gain_homogeneity": (LimitTier.GEOMETRY, None),
    # STK-03: the 25% cap is a sanity check (13% is the largest normal share).
    "frame_quarantine": (LimitTier.DESIGN_ESTIMATE, "STK-03"),
    # STK-04: the gap ratio of 4 rests on unit tests with made-up sky levels.
    "background_homogeneity": (LimitTier.DESIGN_ESTIMATE, "STK-04"),
    # The 10% and 90% of full scale are common practice, "design estimates"
    # in the code, with no validation on this observatory's data.
    "flat_level": (LimitTier.DESIGN_ESTIMATE, None),
    # STK-05: 0.5% adds under 3% to the stack's noise variance (arithmetic on
    # measured noise levels), so it is a budget derived from physics.
    "flat_noise": (LimitTier.PHYSICAL_BUDGET, "STK-05"),
    "calibration_metadata": (LimitTier.GEOMETRY, None),
    "calibration_frame_count": (LimitTier.GEOMETRY, None),
    # STK-20: the 5% value is a design choice inside a measured range.
    "exposure_group_linearity": (LimitTier.DESIGN_ESTIMATE, "STK-20"),
    "exposure_groups_combined": (LimitTier.GEOMETRY, None),
    # STK-22: the 20% zero share was placed between two groups of one session.
    "raw_frame_clipping": (LimitTier.DESIGN_ESTIMATE, "STK-22"),
    # STK-24: the normal range is measured, the detection side is not.
    "rejected_pixel_fraction": (LimitTier.DESIGN_ESTIMATE, "STK-24"),
    # STK-25: the false-positive side is measured, the detection side is not.
    "stack_sharpness": (LimitTier.DESIGN_ESTIMATE, "STK-25"),
    "spectral_registration": (LimitTier.GEOMETRY, None),
    # The 0.1% share is "a guess so far" in the code.
    "saturated_pixel_fraction": (LimitTier.DESIGN_ESTIMATE, None),
    "zero_pixel_fraction": (LimitTier.VALIDATED_DATASET, "STK-26"),
    "negative_pixels": (LimitTier.VALIDATED_DATASET, "STK-26"),
}

GATE_LIMIT_SOURCES: dict[str, GateLimitSource] = {
    gate_name: GateLimitSource(GATE_METRIC_BY_NAME[gate_name], tier, register_entry)
    for gate_name, (tier, register_entry) in _GATE_TIERS.items()
}

# Summary fields on `StackingPipelineQualityMetrics`: the metric each fills and
# the reason given when the field is empty.
_METRICS_FIELDS: dict[str, tuple[str, str]] = {
    "frames_submitted": (STACKING_FRAMES_SUBMITTED, "the stage reported no frame count"),
    "frames_stacked": (STACKING_FRAMES_STACKED, "the stage reported no frame count"),
    "rejected_pixel_fraction": (
        STACKING_REJECTED_PIXEL_FRACTION,
        "the stacker did not report how many pixels it rejected (a single-frame stack rejects none)",
    ),
    "flat_frame_count": (STACKING_FLAT_FRAME_COUNT, "the stack used no flat frames"),
    "flat_noise_fraction": (
        STACKING_FLAT_NOISE_FRACTION,
        "the stack used no flat frames, or their noise could not be estimated",
    ),
    "flat_smoothing_sigma_px": (STACKING_FLAT_SMOOTHING_SIGMA_PX, "the stack used no flat frames"),
    "saturated_pixel_fraction": (
        STACKING_SATURATED_PIXEL_FRACTION,
        "the stack's saturated pixels could not be measured",
    ),
    "zero_pixel_fraction": (
        STACKING_ZERO_PIXEL_FRACTION,
        "the stack file could not be read to count zero pixels",
    ),
    "negative_pixel_max_percent": (
        STACKING_NEGATIVE_PIXEL_MAX_FRACTION,
        "Siril printed no negative-pixel warning",
    ),
    "stacked_fwhm_px": (
        STACKING_STACKED_FWHM_PX,
        "the star width does not apply to this stack or could not be measured",
    ),
    "median_input_fwhm_px": (
        STACKING_MEDIAN_INPUT_FWHM_PX,
        "the star width does not apply to this stack or the input frames could not be measured",
    ),
    "expected_stack_fwhm_px": (
        STACKING_EXPECTED_STACK_FWHM_PX,
        "the star width does not apply to this stack or the input frames could not be measured",
    ),
    "stacking_duration_seconds": (STACKING_DURATION_S, "the stacking program reported no duration"),
    "registration_reference_star_count": (
        STACKING_REGISTRATION_REFERENCE_STAR_COUNT,
        "no single registration reference frame could be identified",
    ),
}

# Fields on `StackingInputQuality`.
_INPUT_FIELDS: dict[str, tuple[str, str]] = {
    "frames_accepted": (STACKING_FRAMES_ACCEPTED, "the input quality was not assessed"),
    "frames_excluded_for_gain": (STACKING_FRAMES_EXCLUDED_FOR_GAIN, "the input quality was not assessed"),
    "frames_excluded_for_background": (
        STACKING_FRAMES_EXCLUDED_FOR_BACKGROUND,
        "the input quality was not assessed",
    ),
    "frames_quarantined": (STACKING_FRAMES_QUARANTINED, "the input quality was not assessed"),
}

# Fields on `ExposureGroupSummary` that are plain numbers, with the reason
# given when the group has none.
_GROUP_FIELDS: dict[str, tuple[str, str]] = {
    "exposure_seconds": (STACKING_GROUP_EXPOSURE_S, "the group reported no exposure"),
    "frames_submitted": (STACKING_GROUP_FRAMES_SUBMITTED, "the group reported no frame count"),
    "frames_stacked": (STACKING_GROUP_FRAMES_STACKED, "the group reported no frame count"),
    "gain_mid_range": (
        STACKING_GROUP_GAIN_MID_RANGE,
        "this is the reference group, or its brightness ratio could not be measured",
    ),
    "gain_bright_end_ratio": (
        STACKING_GROUP_GAIN_BRIGHT_END_RATIO,
        "this is the reference group, or its brightness ratio could not be measured",
    ),
    "gain_disagreement": (
        STACKING_GROUP_GAIN_DISAGREEMENT,
        "this is the reference group, or its brightness ratios could not be compared",
    ),
}


def exposure_group_subject(
    context: PipelineRunContext, stack_file_name: str, group: ExposureGroupSummary, group_index: int
) -> QualitySubject:
    """Name the subject of one exposure group's values.

    A group is stacked on its own and its stack is kept in the ``groups``
    folder beside the combined stack, so the group's own stack file names the
    subject. A stack of one exposure length has one group whose file is the
    stack itself, so its group values sit on the stack's own subject. A group
    that could not be stacked has no file; it is named after the combined
    stack, its place in the list and its exposure.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str`
        The combined stack's file name.
    group : `ExposureGroupSummary`
        The group.
    group_index : `int`
        The group's place in the summary's list.

    Returns
    -------
    subject : `QualitySubject`
        Level ``stack``.
    """
    if group.stack_path:
        folder = os.path.basename(os.path.dirname(group.stack_path))
        name = os.path.basename(group.stack_path)
        return context.stack_subject(f"groups/{name}" if folder == "groups" else name)
    return context.stack_subject(f"{stack_file_name}#group-{group_index}-{group.exposure_seconds:g}s")


def _value_measurement(
    metric_name: str,
    value: float | int | None,
    reason: str,
    context: PipelineRunContext,
    subject: QualitySubject,
) -> QualityMeasurement:
    """Build the measurement of one summary number.

    Parameters
    ----------
    metric_name : `str`
        The metric's name.
    value : `float` or `int` or `None`
        The number, or `None` when the summary has none.
    reason : `str`
        Why there may be no number.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        What the number is about.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measured record, or a not-measured one with the reason.
    """
    return build_measurement(
        _SPEC_BY_NAME[metric_name],
        None if value is None else float(value),
        context,
        subject,
        reason=reason,
    )


def gate_for_measurement(gate: GateResult) -> GateResult:
    """Put a gate's number in the unit its metric declares.

    The negative-pixel check quotes whole percent; its metric is a fraction,
    like every other share. The gate itself is not changed.

    Parameters
    ----------
    gate : `GateResult`
        The gate as recorded on the summary.

    Returns
    -------
    gate : `GateResult`
        A copy for the measurement.
    """
    if gate.name != "negative_pixels":
        return gate
    return gate.model_copy(
        update={
            "measured_value": None if gate.measured_value is None else gate.measured_value / 100.0,
            "limit": None if gate.limit is None else gate.limit / 100.0,
        }
    )


def noise_measurements(
    outcome: StackNoiseOutcome, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the three noise measurements of a stack.

    Parameters
    ----------
    outcome : `StackNoiseOutcome`
        The noise measurement or the reason there is none.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The noise, the reference flux and their ratio; all not measured, with
        the reason, when there is no result.
    """
    result = outcome.result
    reason = outcome.reason or "the noise was not measured"
    if result is None:
        return [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=reason)
            for name in (
                STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
                STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT,
                STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
            )
        ]
    rows = [
        build_measurement(
            _SPEC_BY_NAME[STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT],
            result.noise_adu,
            context,
            subject,
            uncertainty=result.noise_uncertainty_adu,
            sample_count=result.aperture_count,
        ),
        build_measurement(
            _SPEC_BY_NAME[STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT],
            result.reference_flux_adu,
            context,
            subject,
            uncertainty=result.reference_uncertainty_adu,
            sample_count=result.reference_star_count,
        ),
        build_measurement(
            _SPEC_BY_NAME[STACKING_EMPTY_APERTURE_NOISE_NORMALIZED],
            result.normalized_noise,
            context,
            subject,
            uncertainty=result.normalized_uncertainty,
            sample_count=result.aperture_count,
        ),
    ]
    # A measured row keeps a note about how it was made: where the stack's
    # sky positions were moved to, and how well the stars agreed afterwards.
    notes = [result.position_note]
    if result.saturated_reference_stars:
        notes.append(
            f"{result.saturated_reference_stars} stored reference star(s) were left out because they "
            f"reach the camera's saturation level ({result.saturation_source})."
        )
    # Every row says which apertures it was measured in and how deep the stack
    # that chose them was, so rows of different sets are never compared.
    notes.append(result.aperture_definition_note)
    note = " ".join(part for part in notes if part)
    if not note:
        return rows
    return [row.model_copy(update={"reason": note}) for row in rows]


def membership_count_measurements(
    membership: StackMembership | None,
    unavailable_reason: str,
    context: PipelineRunContext,
    stack_subject: QualitySubject,
) -> list[QualityMeasurement]:
    """Build the effective frame count and effective time of one stack.

    Parameters
    ----------
    membership : `StackMembership` or `None`
        The stack's members, or `None` when they could not be worked out.
    unavailable_reason : `str`
        Why there is no membership.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_subject : `QualitySubject`
        The stack. A subset stack's subject works as well as a library
        stack's.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The effective frame count, the effective time, then the effective
        time for read noise. A stack with no members, or with weights that
        are unknown, gets not-measured records
        with the reason. A member list that may include frames Siril's
        filters dropped makes the records ``assumed``.
    """
    effective_count_spec = _SPEC_BY_NAME[STACKING_EFFECTIVE_FRAME_COUNT]
    effective_time_spec = _SPEC_BY_NAME[STACKING_EFFECTIVE_TIME_S]
    read_noise_time_spec = _SPEC_BY_NAME[STACKING_EFFECTIVE_TIME_READ_NOISE_S]
    if membership is None or not membership.members:
        reason = unavailable_reason or "the stack has no member frames"
        return [
            build_measurement(spec, None, context, stack_subject, reason=reason)
            for spec in (effective_count_spec, effective_time_spec, read_noise_time_spec)
        ]
    weights = normalized_weights(membership)
    exposures = [
        float(member.frame.exposure) if member.frame.exposure else None for member in membership.members
    ]
    count = effective_frame_count(weights)
    seconds = effective_exposure_seconds(weights, exposures)
    read_noise_seconds = effective_read_noise_time_seconds(weights, exposures)
    unknown_reason = membership.weights_reason or "the frames' weights are unknown"
    status = MeasurementStatus.ASSUMED if membership.membership_reason else MeasurementStatus.MEASURED
    return [
        build_measurement(
            spec,
            value,
            context,
            stack_subject,
            reason=unknown_reason if value is None else membership.membership_reason,
            sample_count=len(membership.members),
            status=status,
        )
        for spec, value in (
            (effective_count_spec, count),
            (effective_time_spec, seconds),
            (read_noise_time_spec, read_noise_seconds),
        )
    ]


def _membership_measurements(
    membership: StackMembership | None,
    unavailable_reason: str,
    context: PipelineRunContext,
    stack_subject: QualitySubject,
) -> list[QualityMeasurement]:
    """Build the effective counts and one weight row per member frame.

    Parameters
    ----------
    membership : `StackMembership` or `None`
        The stack's members, or `None` when they could not be worked out.
    unavailable_reason : `str`
        Why there is no membership.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_subject : `QualitySubject`
        The stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The effective frame count and time on the stack, and a weight for each
        member frame. A weight that is unknown is a not-measured record that
        still records the membership; a member list that may include frames
        Siril's filters dropped makes every row ``assumed``.
    """
    count_rows = membership_count_measurements(membership, unavailable_reason, context, stack_subject)
    if membership is None or not membership.members:
        return count_rows
    weights = normalized_weights(membership)
    unknown_reason = membership.weights_reason or "the frames' weights are unknown"
    uncertain = bool(membership.membership_reason)
    status = MeasurementStatus.ASSUMED if uncertain else MeasurementStatus.MEASURED
    rows = count_rows
    weight_spec = _SPEC_BY_NAME[STACKING_FRAME_WEIGHT]
    for member, weight in zip(membership.members, weights, strict=True):
        frame = member.frame
        subject = frame_subject(frame.path, context.target_id, frame.timestamp)
        if weight is None:
            reason = unknown_reason
            if uncertain:
                reason += "; " + membership.membership_reason
            rows.append(build_measurement(weight_spec, None, context, subject, reason=reason))
        else:
            rows.append(
                build_measurement(
                    weight_spec,
                    weight,
                    context,
                    subject,
                    reason=membership.membership_reason,
                    status=status,
                )
            )
    return rows


def stacking_measurements(
    summary: StackQualitySummary,
    context: PipelineRunContext,
    stack_file_name: str,
    noise: StackNoiseOutcome,
    membership: StackMembership | None,
    membership_reason: str = "",
    science: StackScience | None = None,
) -> list[QualityMeasurement]:
    """Build every measurement the stacking stage declares for one stack.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The finished summary, with its metrics, input quality and gates.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str`
        The stack's file name (not a mount path).
    noise : `StackNoiseOutcome`
        The empty-aperture noise or the reason there is none.
    membership : `StackMembership` or `None`
        The stack's members and weights, or `None` when they could not be
        worked out.
    membership_reason : `str`, optional
        Why there is no membership.
    science : `StackScience`, optional
        The expected noise, the master flat's noise, the stack's sky level and
        the exposure groups' star peaks (see `record_science.py`). Left out,
        those values are stored as not measured.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One record for each stack-level metric, each exposure group's values,
        one record for each gate the summary holds, the expected noise, the
        flat's noise, the star peaks and the weights. A number the summary
        does not have is ``not_measured`` with a reason.
    """
    stack_subject = context.stack_subject(stack_file_name)
    metrics = summary.stacking_metrics
    measurements: list[QualityMeasurement] = []
    for field_name, (metric_name, reason) in _METRICS_FIELDS.items():
        value = getattr(metrics, field_name)
        if field_name == "negative_pixel_max_percent" and value is not None:
            value = value / 100.0
        measurements.append(_value_measurement(metric_name, value, reason, context, stack_subject))
    for field_name, (metric_name, reason) in _INPUT_FIELDS.items():
        value = getattr(summary.input_quality, field_name) if summary.input_quality is not None else None
        measurements.append(_value_measurement(metric_name, value, reason, context, stack_subject))

    for index, group in enumerate(metrics.exposure_groups):
        group_subject = exposure_group_subject(context, stack_file_name, group, index)
        for field_name, (metric_name, reason) in _GROUP_FIELDS.items():
            measurements.append(
                _value_measurement(metric_name, getattr(group, field_name), reason, context, group_subject)
            )
        shift = group.alignment_shift_pixels
        measurements.append(
            _value_measurement(
                STACKING_GROUP_ALIGNMENT_SHIFT_PX,
                None if not shift else math.hypot(*shift),
                "this is the reference group, or the group could not be lined up",
                context,
                group_subject,
            )
        )

    for gate in summary.gates:
        metric_name = GATE_METRIC_BY_NAME.get(gate.name)
        if metric_name is None:
            logger.warning("The stacking gate %r has no declared metric, so it was not stored.", gate.name)
            continue
        limit_source = GATE_LIMIT_SOURCES[gate.name]
        measurements.append(
            gate_measurement(
                gate_for_measurement(gate),
                _SPEC_BY_NAME[metric_name],
                context,
                stack_subject,
                limit_tier=limit_source.tier,
                register_entry=limit_source.register_entry,
            )
        )

    measurements.extend(noise_measurements(noise, context, stack_subject))
    group_subjects = [
        exposure_group_subject(context, stack_file_name, group, index)
        for index, group in enumerate(metrics.exposure_groups)
    ]
    measurements.extend(
        science_measurements(science, context, stack_subject, metrics.exposure_groups, group_subjects)
    )
    measurements.extend(_membership_measurements(membership, membership_reason, context, stack_subject))
    return measurements


class FrameGeometryRows(NamedTuple):
    """The geometry rows of a stack, in the two groups that are stored apart.

    Attributes
    ----------
    rotation : `list` of `QualityMeasurement`
        The rotation of each frame about the registration reference.
    pointing : `list` of `QualityMeasurement`
        The eight step and drift rows of the stack.
    """

    rotation: list[QualityMeasurement]
    pointing: list[QualityMeasurement]


def frame_geometry_measurements(
    summary: StackQualitySummary,
    context: PipelineRunContext,
    target_frames: Sequence[Any],
    diagnostics: dict[str, Any],
    stack_path: str,
) -> FrameGeometryRows:
    """Build the geometry rows of a stack (rotation and pointing).

    Siril's registration is read once into a `RegistrationRecord`, and both
    groups of rows are built from it. A failure while building one group is
    logged and leaves that group empty, so it cannot take the other with it.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The finished summary (it says whether the stack is spectral).
    context : `PipelineRunContext`
        The run's shared facts.
    target_frames : `Sequence`
        The frames given to the stacking program.
    diagnostics : `dict`
        What the stacking run reported (Siril's registration results).
    stack_path : `str`
        The finished stack, whose header gives the image parity.

    Returns
    -------
    rows : `FrameGeometryRows`
        The rotation of each frame about the registration reference, and the
        eight step and drift rows of the stack. A stack that joins several
        exposure groups has no one registration, and its rows say so.
    """
    group_count = len(diagnostics.get("exposure_groups") or [])
    missing_reason = MULTI_GROUP_REASON if group_count > 1 else UNMATCHED_REASON
    record = None
    if group_count <= 1:
        reference = registration_reference_from_diagnostics(diagnostics)
        record = build_registration_record(
            diagnostics,
            (0, 0),
            None if reference is None else reference[0],
            is_spectral=summary.stacking_metrics.is_spectral,
        )
    rotation: list[QualityMeasurement] = []
    pointing: list[QualityMeasurement] = []
    try:
        rotation = field_rotation_measurements(
            target_frames,
            record,
            context,
            is_spectral=summary.stacking_metrics.is_spectral,
            stack_path=stack_path,
            missing_reason=missing_reason,
        )
    except Exception:  # a by-product of the stack: the pointing rows still go in
        logger.exception("The frame rotation rows of the stack %s could not be built", stack_path)
    try:
        pointing = pointing_measurements(
            target_frames, record, context, os.path.basename(stack_path), missing_reason=missing_reason
        )
    except Exception:  # a by-product of the stack: the rotation rows still go in
        logger.exception("The pointing rows of the stack %s could not be built", stack_path)
    return FrameGeometryRows(rotation, pointing)


def measure_noise_safely(
    stack_path: str,
    reference_frame_path: str | None,
    is_spectral: bool,
    crop_offset: tuple[int, int] = (0, 0),
    apertures_path: str | None = None,
    *,
    may_choose_apertures: bool = True,
    depth: StackDepth | None = None,
) -> StackNoiseOutcome:
    """Measure the stack's empty-aperture noise, never raising.

    Parameters
    ----------
    stack_path : `str`
        The finished stack.
    reference_frame_path : `str` or `None`
        The registration reference frame, for a stack with no solution.
    is_spectral : `bool`
        Whether the stack is spectral. A spectral stack has no point sources to
        set a flux scale from, so its noise is not measured.
    crop_offset : `tuple` [`int`, `int`], optional
        The (columns, rows) trimmed from the stack's left and top edges.
    apertures_path : `str`, optional
        The target's stored apertures. Left out, they are the file beside the
        stack. A subset stack is kept in a folder of its own, so its caller
        passes the file beside the target's library stack, which makes the
        subset's noise comparable with the library stack's.
    may_choose_apertures : `bool`, optional
        False for a subset stack, which never chooses or replaces apertures
        (see `stack_noise.measure_stack_noise`).
    depth : `StackDepth`, optional
        The stack's frame counts, for the rule that keeps the apertures
        matched to the stack's depth.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        The measurement, or the reason there is none. Any failure while
        reading or measuring is logged and returned as a reason.
    """
    if is_spectral:
        return StackNoiseOutcome(None, "a spectral stack has no point sources to set a flux scale from")
    if apertures_path is None:
        apertures_path = os.path.join(os.path.dirname(stack_path), APERTURE_FILE_NAME)
    started = time.monotonic()
    try:
        outcome = measure_stack_noise(
            stack_path,
            reference_frame_path,
            apertures_path,
            crop_offset,
            may_choose_apertures=may_choose_apertures,
            depth=depth,
        )
    except Exception as error:  # the noise is a by-product: no failure here may fail the stack
        logger.exception("The noise of the stack %s could not be measured", stack_path)
        return StackNoiseOutcome(None, f"the noise measurement failed: {type(error).__name__}")
    logger.info(
        "Measured the noise of %s in %.1f s%s.",
        os.path.basename(stack_path),
        time.monotonic() - started,
        "" if outcome.result is not None else f" (no result: {outcome.reason or NO_WCS_REASON})",
    )
    return outcome


def stack_depth_of(membership: StackMembership | None) -> StackDepth | None:
    """Give a stack's frame counts from its members.

    Parameters
    ----------
    membership : `StackMembership` or `None`
        The stack's members, or `None` when they could not be worked out.

    Returns
    -------
    depth : `StackDepth` or `None`
        The number of member frames and the effective frame count; `None`
        when the members or their weights are unknown.
    """
    if membership is None or not membership.members:
        return None
    try:
        count = effective_frame_count(normalized_weights(membership))
    except Exception:  # a by-product: unknown depth only stops a set from being replaced
        logger.exception("The effective frame count of a stack could not be worked out")
        return None
    if count is None:
        return None
    return StackDepth(frame_count=len(membership.members), effective_frame_count=float(count))


def record_stacking_quality(
    summary: StackQualitySummary,
    context: PipelineRunContext,
    stack_path: str,
    target_frames: Sequence[Any],
    diagnostics: dict[str, Any],
    stack_weight: str | None,
    crop_offset: tuple[int, int] = (0, 0),
    *,
    config: object = None,
) -> int:
    """Measure the stack's noise and members, then store every measurement.

    A failure at any point is logged and never fails the stack.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The finished summary.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_path : `str`
        The finished stack's path. Only its file name goes into a subject.
    target_frames : `Sequence`
        The frames given to the stacking program.
    diagnostics : `dict`
        What the stacking run reported.
    stack_weight : `str` or `None`
        The Siril weighting mode the stack used.
    crop_offset : `tuple` [`int`, `int`], optional
        The (columns, rows) trimmed from the stack's left and top edges.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new; 0 when anything failed.
    """
    try:
        membership = None
        membership_reason = ""
        try:
            membership = determine_stack_members(target_frames, diagnostics, stack_weight)
        except Exception as error:  # a by-product again
            logger.exception("The member frames of the stack %s could not be worked out", stack_path)
            membership_reason = f"the member frames could not be worked out: {type(error).__name__}"
        # The members are worked out first: their effective frame count says
        # how deep this stack is, which decides whether the stored apertures
        # are kept or replaced (see `stack_noise.REPLACEMENT_DEPTH_FACTOR`).
        noise = measure_noise_safely(
            stack_path,
            summary.stacking_metrics.registration_reference_frame,
            summary.stacking_metrics.is_spectral,
            crop_offset,
            depth=stack_depth_of(membership),
        )
        if noise.result is not None:
            # The library stack's sums, kept for the leave-one-night-out
            # comparison (see `paired_noise_difference`).
            save_noise_sums(stack_path, noise.result)
        else:
            # A failed measurement must not leave the sums of an older stack
            # of this name for a later comparison to read.
            discard_noise_sums(stack_path)
        science = compute_stack_science(
            stack_path,
            summary.stacking_metrics.flat_noise_fraction,
            summary.stacking_metrics.flat_smoothing_sigma_px,
            membership,
            membership_reason,
            context.target_id,
            diagnostics,
            config,
            noise=noise,
            is_spectral=summary.stacking_metrics.is_spectral,
        )
        measurements = stacking_measurements(
            summary, context, os.path.basename(stack_path), noise, membership, membership_reason, science
        )
        new_count = record_run_measurements(measurements, config=config)
    except Exception:  # quality rows must never stop a stack
        logger.exception("The quality measurements of the stack %s were not stored", stack_path)
        return 0
    # The frames' rotation rows and the stack's pointing rows each go in their
    # own call, after the stack rows, so a failure while building or storing
    # one group cannot undo the rows above or the other group.
    try:
        geometry = frame_geometry_measurements(summary, context, target_frames, diagnostics, stack_path)
    except Exception:  # a by-product of the stack: no failure here may fail it
        logger.exception("The frame geometry rows of the stack %s were not built", stack_path)
        return new_count
    for name, rows in (("rotation", geometry.rotation), ("pointing", geometry.pointing)):
        try:
            new_count += record_run_measurements(rows, config=config)
        except Exception:  # a by-product of the stack: no failure here may fail it
            logger.exception("The frame %s rows of the stack %s were not stored", name, stack_path)
    return new_count
