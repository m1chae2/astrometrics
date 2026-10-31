"""Builds measurements of the expected noise, the flat and the star peaks.

Three families of stack values are made here, next to the ones in
`record_quality.py`. None of them judges anything. Each is stored as parts.
The one ratio stored is the measured noise over the predicted noise
(`noise_ratio_measurements`), with both parts beside it. The noise the master
dark adds (`master_dark_noise_measurements`) is stored here too.

- The noise the stack's frames predict in an empty aperture: the sky (photon)
  part, the read part, their sum, and the two numbers that turn a pixel's
  variance into an aperture's noise in the stack's units (see
  `expected_noise.py`). They are stored on every stack, next to the noise that
  was measured (see `stack_noise.py`), so the two are read side by side.
- The noise the master flat adds to the same apertures, with the stack's sky
  level it is multiplied by (see `flat_noise_contribution.py`).
- The brightest star's peak against the ceiling for each exposure group (see
  `exposure_group_report.summarize_group_peak`). The values sit on the
  group's own entry in the summary (`ExposureGroupSummary`), and the rows are
  built from that entry. They replace the recommended exposure, which stays in
  the summary until it is removed.

A value that cannot be worked out is stored as not measured, with the reason.
Nothing here reads the clock, and a failure never fails the stack.

The expected noise is stored only where the frames' weights and sky describe
the stack. A stack combined from several exposure lengths divides each group
by its exposure and rescales it, and the group formula in `expected_noise.py`
describes that. A group that holds folded frames of another length is
described too, with each frame's scale to the group's registration reference
frame; the exposure of that reference frame is stored for each group
(`group_reference_exposure_measurements`). A stack that is clipped at zero, or
a group whose reference frame cannot be identified, gets no expected noise.
A stack whose member list is uncertain gets an assumed one. A spectral stack
gets none, because its measured noise is not stored either.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from astrometricslib.drivers.calibration_library import DARK_FRAME_PATHS_KEY
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.quality_summary import ExposureGroupSummary
from astrometricslib.pipelines.shared.quality.frame_measurement import (
    frame_subject_id,
    look_up_frame_sensor_noise,
)
from astrometricslib.pipelines.shared.quality.metrics import FRAME_SKY_RATE_E_PER_S_PER_PX
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
    STACKING_EXPECTED_MASTER_DARK_NOISE_STACK_UNIT,
    STACKING_EXPECTED_NOISE_APERTURE_AREA_PX,
    STACKING_EXPECTED_NOISE_SCALE_STACK_UNIT_PER_ADU,
    STACKING_EXPECTED_READ_NOISE_STACK_UNIT,
    STACKING_EXPECTED_SKY_LEVEL_STACK_UNIT,
    STACKING_EXPECTED_SKY_NOISE_STACK_UNIT,
    STACKING_FLAT_NOISE_AFTER_SMOOTHING_FRACTION,
    STACKING_FLAT_NOISE_IN_APERTURE_STACK_UNIT,
    STACKING_GROUP_CLIP_CEILING_ADU,
    STACKING_GROUP_CLIPPED_FRAME_COUNT,
    STACKING_GROUP_LINEARITY_LIMIT_ADU,
    STACKING_GROUP_PEAK_ABOVE_SKY_ADU,
    STACKING_GROUP_PEAK_ESTIMATED_FRAME_COUNT,
    STACKING_GROUP_PEAK_FRACTION,
    STACKING_GROUP_RAW_SKY_LEVEL_ADU,
    STACKING_GROUP_REFERENCE_EXPOSURE_S,
    STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO,
    STACKING_METRICS,
    STACKING_NORMALIZATION_SCALE,
    STACKING_STACK_SKY_LEVEL_STACK_UNIT,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    ExpectedNoiseOutcome,
    ExposureGroupNoiseFacts,
    FrameNoiseInput,
    MasterDarkNoiseOutcome,
    expected_exposure_group_noise,
    expected_master_dark_noise,
    expected_stack_noise,
    gather_frame_noise_inputs,
    measure_stack_sky_level,
)
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS,
    CHECKED_ESTIMATE_ERROR_FRACTION,
    CHECKED_ESTIMATE_SHARE_WITHIN_ERROR,
)
from astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution import (
    FlatNoiseOutcome,
    flat_noise_contribution,
)
from astrometricslib.pipelines.stacking.post_processing.normalization_scale import (
    NormalizationScaleOutcome,
    apply_normalization_scale,
    measure_normalization_scale,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    GroupReference,
    StackMembership,
    normalized_weights,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "StackScience",
    "compute_expected_noise",
    "compute_stack_science",
    "expected_noise_measurements",
    "expected_noise_row_sequence",
    "flat_noise_measurements",
    "frame_noise_inputs_from_store",
    "group_peak_measurements",
    "group_reference_exposure_measurements",
    "master_dark_noise_measurements",
    "noise_ratio_measurements",
    "normalization_scale_measurements",
    "resolve_stack_sky_level",
    "scale_expected_noise",
    "science_measurements",
    "stack_sky_level_measurements",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

_NO_PEAK_REASON = "no sampled frame of the group gave a star peak above the sky"
_NO_SKY_LEVEL_REASON = (
    "the stack's sky level was not read (no finite pixel above zero, or the file was unreadable)"
)
SPECTRAL_STACK_REASON = (
    "a spectral stack's measured empty-aperture noise is not stored, so no expected noise is stored beside it"
)
CLIPPED_EXPOSURE_GROUPS_REASON = (
    "a group of the stack is clipped at zero in its raw frames, and the combine drops its sky pixels near "
    "zero, which the frame weights and the sky model do not describe (a clipped single-group stack is "
    "refused too, on purpose)"
)


_SCALE_NOT_MEASURED = NormalizationScaleOutcome(None, "the caller did not measure the normalization scale")


@dataclass(frozen=True)
class StackScience:
    """What the record step needs for expected noise, flat noise and sky level.

    The star peaks are not here: they sit on each exposure group's entry in
    the summary (see `ExposureGroupSummary`).

    Parameters
    ----------
    expected_noise : `ExpectedNoiseOutcome`
        The noise the stack's frames predict, or the reason there is none.
    flat_noise : `FlatNoiseOutcome`
        The master flat's contribution, or the reason there is none.
    stack_sky_level : `float` or `None`
        The stack's sky level, in the stack's units.
    normalization_scale : `NormalizationScaleOutcome`
        The scale Siril gave the stack, or the reason there is none. The
        expected noise in `expected_noise` has already been multiplied by it.
    group_references : `tuple` of `GroupReference`
        For a stack of exposure groups, the exposure of each combined group's
        registration reference frame (see `stack_membership.group_references`).
        Empty for a stack that is not made of groups.
    measured_noise : `StackNoiseOutcome` or `None`
        The stack's measured empty-aperture noise, for the
        measured-to-predicted ratio. `None` when the caller gave none.
    master_dark_noise : `MasterDarkNoiseOutcome`
        The noise the master dark adds, or the reason there is none.
    """

    expected_noise: ExpectedNoiseOutcome
    flat_noise: FlatNoiseOutcome
    stack_sky_level: float | None
    normalization_scale: NormalizationScaleOutcome = _SCALE_NOT_MEASURED
    group_references: tuple[GroupReference, ...] = ()
    measured_noise: StackNoiseOutcome | None = None
    master_dark_noise: MasterDarkNoiseOutcome = field(
        default_factory=lambda: MasterDarkNoiseOutcome(
            None, "the caller did not work out the master dark's noise"
        )
    )


def resolve_stack_sky_level(noise: StackNoiseOutcome | None, stack_path: str) -> float | None:
    """Give a stack's sky level without reading the stack twice.

    Parameters
    ----------
    noise : `StackNoiseOutcome` or `None`
        The noise step's outcome. Its ``stack_sky_level`` was taken from the
        plane the step already read.
    stack_path : `str`
        The stack file. It is read (sampled) only when the noise step did not
        read it, for example for a spectral stack.

    Returns
    -------
    level : `float` or `None`
        The sky level in the stack's units, or `None` when it cannot be read.
        A failure is logged, never raised.
    """
    if noise is not None and noise.stack_sky_level is not None:
        return noise.stack_sky_level
    try:
        return measure_stack_sky_level(stack_path)
    except Exception:  # a by-product again
        logger.exception("The sky level of the stack %s could not be read", stack_path)
        return None


def compute_expected_noise(
    membership: StackMembership | None,
    unavailable_reason: str,
    target_id: str,
    configuration: object = None,
    store: QualityStore | None = None,
    diagnostics: dict[str, Any] | None = None,
    is_spectral: bool = False,
) -> ExpectedNoiseOutcome:
    """Work out the noise a stack's frames predict, from stored measurements.

    Parameters
    ----------
    membership : `StackMembership` or `None`
        The stack's members and weights, or `None` when they could not be
        worked out.
    unavailable_reason : `str`
        Why there is no membership.
    target_id : `str`
        The target.
    configuration : `AppConfiguration`, optional
        Where the quality store and the camera profiles live. The
        process-wide one if omitted.
    store : `QualityStore`, optional
        The quality store to read, instead of opening the configured one. A
        run that already holds a store passes it here.
    diagnostics : `dict`, optional
        What the stacking run reported. A stack with an
        ``"exposure_group_weights"`` entry was combined from several exposure
        groups and gets the group formula (`expected_exposure_group_noise`).
        A stack with a non-empty ``"clipped_exposure_groups"`` gets no
        expected noise. This includes a clipped single-group stack, on
        purpose.
    is_spectral : `bool`, optional
        Whether the stack is spectral. Its measured noise is not stored, so no
        expected noise is stored beside it.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        The expected noise, or the reason there is none (a spectral stack,
        a clipped group, no members, unknown weights, a frame with no
        stored sky measurement, or a setting with no gain and read noise in
        the camera profile). When the member list is uncertain (Siril stacked
        fewer frames than there were candidates) the result is marked assumed,
        with that reason. A failure is returned as a reason, never raised.
    """
    if is_spectral:
        return ExpectedNoiseOutcome(None, SPECTRAL_STACK_REASON)
    if membership is None or not membership.members:
        return ExpectedNoiseOutcome(None, unavailable_reason or "the stack has no member frames")
    if diagnostics and diagnostics.get("clipped_exposure_groups"):
        return ExpectedNoiseOutcome(None, CLIPPED_EXPOSURE_GROUPS_REASON)
    group_entries = (diagnostics or {}).get("exposure_group_weights") or []
    try:
        if group_entries:
            outcome = _expected_group_noise_from_store(
                membership, group_entries, target_id, configuration, store
            )
        else:
            outcome = _expected_noise_from_store(membership, target_id, configuration, store)
    except Exception as error:  # a by-product: no failure here may fail the stack
        logger.exception("The expected noise of '%s' could not be worked out", target_id)
        return ExpectedNoiseOutcome(None, f"the expected noise failed: {type(error).__name__}")
    if outcome.result is not None and membership.membership_reason:
        reasons = [
            reason for reason in (outcome.result.assumed_reason, membership.membership_reason) if reason
        ]
        outcome = ExpectedNoiseOutcome(replace(outcome.result, assumed_reason="; ".join(reasons)))
    return outcome


def frame_noise_inputs_from_store(
    members: list[Any],
    weights: Sequence[float | None],
    target_id: str,
    configuration: object,
    store: QualityStore | None,
    weights_reason: str = "",
    exposure_scales: Sequence[float] | None = None,
) -> ExpectedNoiseOutcome | list[FrameNoiseInput]:
    """Join frames' stored sky measurements with their camera profiles.

    Parameters
    ----------
    members : `list`
        The frame records.
    weights : `Sequence` [`float` or `None`]
        Each frame's weight, in the order of ``members``. `None` means unknown.
    target_id : `str`
        The target.
    configuration : `AppConfiguration` or `None`
        Where the store and the camera profiles live.
    store : `QualityStore` or `None`
        The store to read, or `None` to open the configured one.
    weights_reason : `str`, optional
        Why the weights are unknown.
    exposure_scales : `Sequence` [`float`], optional
        Each frame's ``k_i`` (see `FrameNoiseInput.exposure_scale`). Left out,
        every frame gets 1.

    Returns
    -------
    inputs : `list` [`FrameNoiseInput`] or `ExpectedNoiseOutcome`
        One entry for each frame (its sky, read noise, gain, weight and
        scale), or
        the reason there is none (see `gather_frame_noise_inputs`).
    """
    try:
        if store is None:
            store = QualityStore.from_configuration(configuration)
        rows = store.query(
            metric=FRAME_SKY_RATE_E_PER_S_PER_PX, level=SubjectLevel.FRAME, target_id=target_id
        )
    except Exception as error:  # reading the store may not fail the stack
        logger.exception("The stored sky measurements of '%s' could not be read", target_id)
        return ExpectedNoiseOutcome(
            None, f"the stored sky measurements could not be read: {type(error).__name__}"
        )
    sky_rows = {row.subject.subject_id: row for row in rows if row.subject.channel is None}

    def noise_setting(frame: Any) -> tuple[Any, Any, str]:
        """Look up the gain and read noise of a frame's setting.

        Parameters
        ----------
        frame : `Any`
            A member frame record.

        Returns
        -------
        gain, read_noise, reason : `tuple`
            See `look_up_frame_sensor_noise`.
        """
        # `gather_frame_noise_inputs` calls this only for a frame whose stored
        # sky rate has a value. The frame measurement gives a sky rate only
        # when it found a gain, and it uses another offset's gain only for a
        # frame it found unclipped at zero. So a stored value means the offset
        # fallback is allowed here, and the same entry is found again.
        return look_up_frame_sensor_noise(
            resolve_camera_profile(frame.camera, configuration), frame, clipped_at_zero=False
        )

    return gather_frame_noise_inputs(
        members,
        weights,
        sky_rows,
        lambda frame: frame_subject_id(frame.path, target_id),
        noise_setting,
        weights_reason,
        exposure_scales,
    )


def _expected_noise_from_store(
    membership: StackMembership, target_id: str, configuration: object, store: QualityStore | None
) -> ExpectedNoiseOutcome:
    """Join the members' stored sky measurements with their camera profiles.

    Parameters
    ----------
    membership : `StackMembership`
        The stack's members and weights (it has members).
    target_id : `str`
        The target.
    configuration : `AppConfiguration` or `None`
        Where the store and the camera profiles live.
    store : `QualityStore` or `None`
        The store to read, or `None` to open the configured one.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        The expected noise or the reason there is none.
    """
    inputs = frame_noise_inputs_from_store(
        [member.frame for member in membership.members],
        normalized_weights(membership),
        target_id,
        configuration,
        store,
        membership.weights_reason,
        [member.exposure_scale for member in membership.members],
    )
    if isinstance(inputs, ExpectedNoiseOutcome):
        return inputs
    return expected_stack_noise(inputs)


def exposure_group_facts(group_entries: Sequence[dict[str, Any]]) -> list[ExposureGroupNoiseFacts] | None:
    """Read the combine's facts about each exposure group.

    Parameters
    ----------
    group_entries : `Sequence` of `dict`
        The stack's ``exposure_group_weights`` entries.

    Returns
    -------
    facts : `list` [`ExposureGroupNoiseFacts`] or `None`
        One entry for each group, or `None` when an entry lacks its weight,
        exposure, frame count, gain or sky level.
    """
    facts = []
    for entry in group_entries:
        try:
            facts.append(
                ExposureGroupNoiseFacts(
                    combine_weight=float(entry["weight"]),
                    exposure_seconds=float(entry["exposure_seconds"]),
                    frame_count=int(entry["frame_count"]),
                    gain=float(entry["gain"]),
                    gain_measured=bool(entry["gain_measured"]),
                    sky_level_per_second=float(entry["sky_level_per_second"]),
                    member_paths=tuple(str(path) for path in entry.get("paths") or []),
                )
            )
        except KeyError, TypeError, ValueError:
            return None
    return facts


def _expected_group_noise_from_store(
    membership: StackMembership,
    group_entries: Sequence[dict[str, Any]],
    target_id: str,
    configuration: object,
    store: QualityStore | None,
) -> ExpectedNoiseOutcome:
    """Work out the expected noise of a stack combined from exposure groups.

    Parameters
    ----------
    membership : `StackMembership`
        The stack's members and their raw weights ``W_g w_i`` (it has members).
    group_entries : `Sequence` of `dict`
        The stack's ``exposure_group_weights`` entries.
    target_id : `str`
        The target.
    configuration : `AppConfiguration` or `None`
        Where the store and the camera profiles live.
    store : `QualityStore` or `None`
        The store to read, or `None` to open the configured one.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        The expected noise, or the reason there is none (unknown weights with
        the membership's reason, an entry that lacks a fact, a member in no
        group, or a missing sky measurement).
    """
    inputs = frame_noise_inputs_from_store(
        [member.frame for member in membership.members],
        [member.raw_weight for member in membership.members],
        target_id,
        configuration,
        store,
        membership.weights_reason,
        [member.exposure_scale for member in membership.members],
    )
    if isinstance(inputs, ExpectedNoiseOutcome):
        return inputs
    facts = exposure_group_facts(group_entries)
    if facts is None:
        return ExpectedNoiseOutcome(
            None, "an exposure group's entry lacks its weight, exposure, gain or sky level"
        )
    group_of_path = {
        str(path): position
        for position, entry in enumerate(group_entries)
        for path in entry.get("paths") or []
    }
    indices = [group_of_path.get(str(member.frame.path), -1) for member in membership.members]
    return expected_exposure_group_noise(inputs, indices, facts)


def scale_expected_noise(
    expected: ExpectedNoiseOutcome, noise: StackNoiseOutcome | None, diagnostics: dict[str, Any] | None
) -> tuple[ExpectedNoiseOutcome, NormalizationScaleOutcome]:
    """Measure the stack's normalization scale and apply it to the noise.

    Parameters
    ----------
    expected : `ExpectedNoiseOutcome`
        The noise the frames predict, before any scale.
    noise : `StackNoiseOutcome` or `None`
        The noise step's outcome, which holds the stars' flux ratio against
        the registration reference frame.
    diagnostics : `dict` or `None`
        What the stacking run reported. An ``"exposure_group_weights"`` entry
        marks a stack combined from several exposure groups.

    Returns
    -------
    expected, scale : `tuple`
        The expected noise multiplied by the scale (or kept, with a note that
        says it is not corrected), and the scale outcome. A failure while
        measuring is logged and becomes a reason, never a raised error.
    """
    try:
        group_entries = (diagnostics or {}).get("exposure_group_weights") or []
        groups = exposure_group_facts(group_entries) if group_entries else []
        if groups is None:
            scale = NormalizationScaleOutcome(
                None, "an exposure group's entry lacks its weight, exposure, gain or sky level"
            )
        else:
            scale = measure_normalization_scale(noise, groups)
    except Exception as error:  # a by-product: no failure here may fail the stack
        logger.exception("The normalization scale of a stack could not be measured")
        scale = NormalizationScaleOutcome(None, f"the normalization scale failed: {type(error).__name__}")
    return apply_normalization_scale(expected, scale), scale


def compute_stack_science(
    stack_path: str,
    summary_flat_noise_fraction: float | None,
    summary_flat_sigma_px: float | None,
    membership: StackMembership | None,
    membership_reason: str,
    target_id: str,
    diagnostics: dict[str, Any],
    configuration: object = None,
    noise: StackNoiseOutcome | None = None,
    is_spectral: bool = False,
) -> StackScience:
    """Gather the expected noise, flat noise and sky level of a stack.

    Parameters
    ----------
    stack_path : `str`
        The finished stack. It is read only when the noise step did not read
        it (see `resolve_stack_sky_level`).
    summary_flat_noise_fraction : `float` or `None`
        The stack summary's flat noise before any blur.
    summary_flat_sigma_px : `float` or `None`
        The stack summary's flat blur width.
    membership : `StackMembership` or `None`
        The stack's members and weights.
    membership_reason : `str`
        Why there is no membership.
    target_id : `str`
        The target.
    diagnostics : `dict`
        What the stacking run reported; ``"exposure_group_weights"`` marks a
        stack combined from several exposure groups and
        ``"clipped_exposure_groups"`` one the expected noise is refused for.
    configuration : `AppConfiguration`, optional
        Where the quality store and the camera profiles live.
    noise : `StackNoiseOutcome`, optional
        The noise step's outcome, which holds the sky level it took from the
        plane it read.
    is_spectral : `bool`, optional
        Whether the stack is spectral.

    Returns
    -------
    science : `StackScience`
        The three values. Any failure becomes a reason, never a raised error,
        and one failure does not drop the others.
    """
    expected, scale = scale_expected_noise(
        compute_expected_noise(
            membership,
            membership_reason,
            target_id,
            configuration,
            diagnostics=diagnostics,
            is_spectral=is_spectral,
        ),
        noise,
        diagnostics,
    )
    sky_level = resolve_stack_sky_level(noise, stack_path)
    try:
        flat = flat_noise_contribution(summary_flat_noise_fraction, summary_flat_sigma_px, sky_level)
    except Exception as error:  # a by-product again
        logger.exception("The flat noise of the stack %s could not be worked out", stack_path)
        flat = FlatNoiseOutcome(None, f"the flat's noise failed: {type(error).__name__}")
    try:
        dark_paths = diagnostics.get(DARK_FRAME_PATHS_KEY) or []
        dark = expected_master_dark_noise(
            expected.result, len(dark_paths), bool(diagnostics.get("exposure_group_weights"))
        )
    except Exception as error:  # a by-product again
        logger.exception("The master dark's noise for the stack %s could not be worked out", stack_path)
        dark = MasterDarkNoiseOutcome(None, f"the master dark's noise failed: {type(error).__name__}")
    return StackScience(
        expected,
        flat,
        sky_level,
        scale,
        membership.group_references if membership is not None else (),
        measured_noise=noise,
        master_dark_noise=dark,
    )


def noise_ratio_measurements(
    noise: StackNoiseOutcome | None,
    expected: ExpectedNoiseOutcome,
    context: PipelineRunContext,
    subject: QualitySubject,
) -> list[QualityMeasurement]:
    """Build the row that divides the measured noise by the predicted noise.

    Parameters
    ----------
    noise : `StackNoiseOutcome` or `None`
        The stack's measured empty-aperture noise.
    expected : `ExpectedNoiseOutcome`
        The noise the stack's frames predict, already multiplied by the
        normalization scale when that was measured. Both are in the stack's
        units.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack (a subset stack too).

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row: measured over predicted, with the measured noise's relative
        error as its uncertainty (the model's own error is not included), or
        not measured with the reason when either noise is missing. The row is
        ``assumed`` when the prediction rests on an assumed input.
    """
    spec = _SPEC_BY_NAME[STACKING_MEASURED_TO_PREDICTED_NOISE_RATIO]
    measured = noise.result if noise is not None else None
    predicted = expected.result
    if measured is None:
        reason = (noise.reason if noise is not None else "") or "the stack's noise was not measured"
        return [build_measurement(spec, None, context, subject, reason=f"no measured noise: {reason}")]
    if predicted is None:
        reason = expected.reason or "the expected noise was not worked out"
        return [build_measurement(spec, None, context, subject, reason=f"no predicted noise: {reason}")]
    if not predicted.total_noise > 0:
        return [
            build_measurement(spec, None, context, subject, reason="the predicted noise is not above zero")
        ]
    ratio = measured.noise_adu / predicted.total_noise
    relative_error = measured.noise_uncertainty_adu / measured.noise_adu if measured.noise_adu > 0 else None
    assumed = bool(predicted.assumed_reason)
    return [
        build_measurement(
            spec,
            ratio,
            context,
            subject,
            uncertainty=None if relative_error is None else ratio * relative_error,
            sample_count=measured.aperture_count,
            status=MeasurementStatus.ASSUMED if assumed else MeasurementStatus.MEASURED,
            reason=predicted.assumed_reason,
            provenance=predicted.scale_note,
        )
    ]


def master_dark_noise_measurements(
    outcome: MasterDarkNoiseOutcome, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the row of the noise the master dark adds to an aperture sum.

    Parameters
    ----------
    outcome : `MasterDarkNoiseOutcome`
        The value or the reason there is none.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row; assumed when the read noise or gain behind it is not measured.
    """
    spec = _SPEC_BY_NAME[STACKING_EXPECTED_MASTER_DARK_NOISE_STACK_UNIT]
    if outcome.value is None:
        return [
            build_measurement(
                spec,
                None,
                context,
                subject,
                reason=outcome.reason or "the master dark's noise was not worked out",
            )
        ]
    return [
        build_measurement(
            spec,
            outcome.value,
            context,
            subject,
            sample_count=outcome.dark_frame_count,
            status=MeasurementStatus.ASSUMED if outcome.assumed_reason else MeasurementStatus.MEASURED,
            reason=outcome.assumed_reason,
            provenance=" ".join(
                part
                for part in (
                    f"Made from {outcome.dark_frame_count} dark frame(s); the dark current's shot noise "
                    "is not included.",
                    outcome.scale_note,
                )
                if part
            ),
        )
    ]


def expected_noise_measurements(
    outcome: ExpectedNoiseOutcome, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the six expected-noise measurements of a stack.

    Parameters
    ----------
    outcome : `ExpectedNoiseOutcome`
        The expected noise or the reason there is none.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The sky part, the read part, the total, the aperture's pixel count,
        the unit scale and the sky level the frames predict. All not
        measured, with the reason, when there is no result. A result built on
        a gain or sky that is not measured, or on an uncertain member list,
        makes every row ``assumed``. A result built only on measured inputs
        stays ``measured``; the metric's estimator says it is a model
        prediction from measured inputs (a measured row keeps no reason).
    """
    names = (
        STACKING_EXPECTED_SKY_NOISE_STACK_UNIT,
        STACKING_EXPECTED_READ_NOISE_STACK_UNIT,
        STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
        STACKING_EXPECTED_NOISE_APERTURE_AREA_PX,
        STACKING_EXPECTED_NOISE_SCALE_STACK_UNIT_PER_ADU,
        STACKING_EXPECTED_SKY_LEVEL_STACK_UNIT,
    )
    result = outcome.result
    if result is None:
        reason = outcome.reason or "the expected noise was not worked out"
        return [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=reason) for name in names
        ]
    values = (
        result.sky_noise,
        result.read_noise,
        result.total_noise,
        result.aperture_area_px,
        result.stack_units_per_adu,
        result.predicted_sky_level,
    )
    status = MeasurementStatus.ASSUMED if result.assumed_reason else MeasurementStatus.MEASURED
    # An assumed row keeps its reason; a measured row keeps the scale note as
    # its provenance. Only the three noise rows were multiplied by the scale.
    scaled_names = names[:3]
    return [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=" ".join(
                part
                for part in (result.assumed_reason, result.scale_note if name in scaled_names else "")
                if part
            ),
            provenance=result.scale_note if name in scaled_names else "",
            sample_count=result.frame_count,
            status=status,
        )
        for name, value in zip(names, values, strict=True)
    ]


def normalization_scale_measurements(
    outcome: NormalizationScaleOutcome, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the stack's normalization scale measurement.

    Parameters
    ----------
    outcome : `NormalizationScaleOutcome`
        The scale or the reason there is none.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack (a subset stack too).

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row: the scale with its uncertainty, the number of stars and a
        note on how it was measured, or not measured with the reason.
    """
    result = outcome.result
    if result is None:
        return [
            build_measurement(
                _SPEC_BY_NAME[STACKING_NORMALIZATION_SCALE],
                None,
                context,
                subject,
                reason=outcome.reason or "the normalization scale was not measured",
            )
        ]
    return [
        build_measurement(
            _SPEC_BY_NAME[STACKING_NORMALIZATION_SCALE],
            result.value,
            context,
            subject,
            uncertainty=result.uncertainty,
            sample_count=result.star_count,
            provenance=result.note,
        )
    ]


def stack_sky_level_measurements(
    stack_sky_level: float | None, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the stack's sky level measurement.

    Parameters
    ----------
    stack_sky_level : `float` or `None`
        The stack's sky level, in the stack's units.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack (a subset stack too).

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row: the level, or not measured with the reason.
    """
    return [
        build_measurement(
            _SPEC_BY_NAME[STACKING_STACK_SKY_LEVEL_STACK_UNIT],
            stack_sky_level,
            context,
            subject,
            reason=_NO_SKY_LEVEL_REASON,
        )
    ]


def flat_noise_measurements(
    outcome: FlatNoiseOutcome,
    stack_sky_level: float | None,
    context: PipelineRunContext,
    subject: QualitySubject,
) -> list[QualityMeasurement]:
    """Build the stack's sky level and the master flat's two noise values.

    Parameters
    ----------
    outcome : `FlatNoiseOutcome`
        The flat's contribution or the reason there is none.
    stack_sky_level : `float` or `None`
        The stack's sky level, in the stack's units.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The sky level, the master's noise after any blur, and the noise it
        adds to an aperture sum.
    """
    sky_rows = stack_sky_level_measurements(stack_sky_level, context, subject)
    result = outcome.result
    if result is None:
        reason = outcome.reason or "the master flat's noise was not worked out"
        return [
            *sky_rows,
            *(
                build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=reason)
                for name in (
                    STACKING_FLAT_NOISE_AFTER_SMOOTHING_FRACTION,
                    STACKING_FLAT_NOISE_IN_APERTURE_STACK_UNIT,
                )
            ),
        ]
    return [
        *sky_rows,
        build_measurement(
            _SPEC_BY_NAME[STACKING_FLAT_NOISE_AFTER_SMOOTHING_FRACTION],
            result.noise_after_smoothing_fraction,
            context,
            subject,
        ),
        build_measurement(
            _SPEC_BY_NAME[STACKING_FLAT_NOISE_IN_APERTURE_STACK_UNIT],
            result.aperture_noise,
            context,
            subject,
        ),
    ]


def _provenance_note(label: str, kind: str | None) -> str:
    """Say that a camera profile value is not a measured one.

    Parameters
    ----------
    label : `str`
        The value's name, such as ``"clip ceiling"``.
    kind : `str` or `None`
        How the profile obtained it (``measured``, ``datasheet``, ``assumed``)
        or `None` when unknown.

    Returns
    -------
    note : `str`
        A sentence for the row's reason, or an empty string when the value is
        measured.
    """
    if kind == "measured":
        return ""
    return f"the camera profile's {label} is {kind or 'of unknown origin'}, not measured"


def group_peak_measurements(
    group: ExposureGroupSummary, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the brightest-star peak measurements of one exposure group.

    Parameters
    ----------
    group : `ExposureGroupSummary`
        The group's entry, which holds the peak values (see
        `exposure_group_report.GroupPeak`).
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The group's stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The fraction, the peak, the sky, the clip ceiling, the linearity
        limit, the count of estimated peaks and the count of clipped frames.

        A peak estimated from the wings of a clipped star is ``assumed``, with
        the error of the check that backs the estimate in the reason, when the
        estimate lies within the range the check covered
        (`checked_estimate_range`, kept on the group). Beyond that range the
        fraction and the peak are not measured, and the reason gives the size
        of the estimate. The clip ceiling and the linearity limit are
        ``assumed`` when the camera profile's number is a datasheet or generic
        value; the sky and the counts are read as they are.
    """
    with_peak = group.peak_frames_with_peak
    estimated = group.peak_frames_estimated
    sampled = group.peak_frames_sampled
    largest_ratio = group.peak_largest_estimate_to_room_ratio
    checked_range = group.peak_checked_range_room_multiples
    beyond_range = estimated > 0 and largest_ratio is not None and largest_ratio > checked_range
    low, middle, high = CHECKED_ESTIMATE_AGAINST_MEASURED_RATIOS
    estimate_note = (
        f"{estimated} of {with_peak} sampled frame(s) have a clipped star, whose peak is estimated from the "
        "pixels around its core with a Moffat profile (beta 3), not measured; estimates up to "
        f"{checked_range:.0f} times the room were checked: they grew within "
        f"{CHECKED_ESTIMATE_ERROR_FRACTION:.0%} of the exposure ratio in "
        f"{CHECKED_ESTIMATE_SHARE_WITHIN_ERROR:.0%} of cases, but against an unclipped star at a shorter "
        f"exposure they were a median {middle:.2f} ({low:.2f} to {high:.1f}) of its measured peak "
        "(register entry STK-41)"
    )
    beyond_note = (
        f"estimate beyond the checked range ({0.0 if largest_ratio is None else largest_ratio:.0f} x); "
        + (
            f"estimates up to {checked_range:.0f} x were checked for this camera"
            if checked_range > 0
            else "no range was checked for this camera"
        )
    )

    def row(
        name: str,
        value: float | None,
        reason: str,
        *,
        status: MeasurementStatus = MeasurementStatus.MEASURED,
        sample_count: int | None = None,
    ) -> QualityMeasurement:
        """Build one row of the group.

        Parameters
        ----------
        name : `str`
            The metric name.
        value : `float` or `None`
            The value, or `None` for not measured.
        reason : `str`
            The reason that goes with an assumed or missing value.
        status : `MeasurementStatus`, optional
            ``measured`` or ``assumed``.
        sample_count : `int`, optional
            How many frames are behind the value.

        Returns
        -------
        measurement : `QualityMeasurement`
            The row.
        """
        return build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=reason,
            sample_count=sample_count,
            status=status,
        )

    rows = []
    for name, value in (
        (STACKING_GROUP_PEAK_FRACTION, group.brightest_star_peak_fraction),
        (STACKING_GROUP_PEAK_ABOVE_SKY_ADU, group.brightest_star_peak_above_sky_adu),
    ):
        if beyond_range:
            rows.append(row(name, None, beyond_note, sample_count=with_peak or None))
        elif estimated > 0:
            rows.append(
                row(
                    name,
                    value,
                    estimate_note,
                    status=MeasurementStatus.ASSUMED,
                    sample_count=with_peak or None,
                )
            )
        else:
            rows.append(row(name, value, _NO_PEAK_REASON, sample_count=with_peak or None))
    rows.append(
        row(
            STACKING_GROUP_RAW_SKY_LEVEL_ADU,
            group.peak_sky_adu,
            _NO_PEAK_REASON,
            sample_count=with_peak or None,
        )
    )
    ceiling_note = (
        _provenance_note("clip ceiling", group.peak_clip_ceiling_provenance)
        if group.peak_frames_on_profile_ceiling > 0
        else ""
    )
    rows.append(
        row(
            STACKING_GROUP_CLIP_CEILING_ADU,
            group.peak_clip_ceiling_adu,
            ceiling_note or _NO_PEAK_REASON,
            status=MeasurementStatus.ASSUMED if ceiling_note else MeasurementStatus.MEASURED,
            sample_count=with_peak or None,
        )
    )
    limit_note = _provenance_note("linearity limit", group.peak_linearity_limit_provenance)
    rows.append(
        row(
            STACKING_GROUP_LINEARITY_LIMIT_ADU,
            group.peak_linearity_limit_adu,
            limit_note or "the camera profile has no photometric linearity limit, or could not be read",
            status=MeasurementStatus.ASSUMED if limit_note else MeasurementStatus.MEASURED,
        )
    )
    rows.append(
        row(
            STACKING_GROUP_PEAK_ESTIMATED_FRAME_COUNT,
            float(estimated) if with_peak else None,
            _NO_PEAK_REASON,
            sample_count=with_peak or None,
        )
    )
    rows.append(
        row(
            STACKING_GROUP_CLIPPED_FRAME_COUNT,
            float(group.peak_frames_clipped) if sampled else None,
            "no frame of the group could be read",
            sample_count=sampled or None,
        )
    )
    return rows


def group_reference_exposure_measurements(
    references: Sequence[GroupReference],
    summary_index: int,
    context: PipelineRunContext,
    subject: QualitySubject,
) -> list[QualityMeasurement]:
    """Build the registration reference frame's exposure row of one group.

    Parameters
    ----------
    references : `Sequence` [`GroupReference`]
        The stack's combined groups' references (see
        `stack_membership.group_references`).
    summary_index : `int`
        The group's place in the summary's list of exposure groups.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The group's stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row: the exposure of the group's registration reference frame, or
        not measured with the reason (the reference frame cannot be identified,
        or the group was not combined, or the stack is of one group).
    """
    spec = _SPEC_BY_NAME[STACKING_GROUP_REFERENCE_EXPOSURE_S]
    reference = next((item for item in references if item.summary_index == summary_index), None)
    if reference is None:
        reason = (
            "the group was left out of the combination, or the stack's member frames could not be worked out"
        )
        return [build_measurement(spec, None, context, subject, reason=reason)]
    if reference.exposure_seconds is None:
        return [
            build_measurement(
                spec,
                None,
                context,
                subject,
                reason=reference.reason or "the registration reference frame's exposure is not known",
            )
        ]
    return [build_measurement(spec, reference.exposure_seconds, context, subject)]


def expected_noise_row_sequence(
    expected: ExpectedNoiseOutcome,
    measured_noise: StackNoiseOutcome | None,
    normalization_scale: NormalizationScaleOutcome,
    stack_sky_level: float | None,
    context: PipelineRunContext,
    subject: QualitySubject,
    *,
    master_dark_noise: MasterDarkNoiseOutcome | None = None,
    flat_noise: FlatNoiseOutcome | None = None,
    write_sky_level: bool = True,
) -> list[QualityMeasurement]:
    """Build the expected-noise rows of one stack, in their stored order.

    Both the library stack and a subset stack store this sequence, so it is
    written once here. The two paths differ only in the optional parts,
    which are parameters.

    Parameters
    ----------
    expected : `ExpectedNoiseOutcome`
        The noise the frames predict, already multiplied by the scale.
    measured_noise : `StackNoiseOutcome` or `None`
        The measured empty-aperture noise, for the measured-to-predicted ratio.
    normalization_scale : `NormalizationScaleOutcome`
        The scale Siril gave the stack, or the reason there is none.
    stack_sky_level : `float` or `None`
        The stack's sky level, in the stack's units.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.
    master_dark_noise : `MasterDarkNoiseOutcome`, optional
        When given, its rows go between the ratio and the scale (the library
        path). A subset stack leaves it out.
    flat_noise : `FlatNoiseOutcome`, optional
        When given, the sky level and the flat's two rows close the sequence
        (the library path), and `write_sky_level` is not used.
    write_sky_level : `bool`, optional
        Without `flat_noise`: whether to close the sequence with the sky level
        row. A subset stack with no stack file writes none.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        Expected noise, ratio, master dark (if asked), scale, then the sky
        level (with the flat's rows if `flat_noise` is given).
    """
    rows = expected_noise_measurements(expected, context, subject)
    rows.extend(noise_ratio_measurements(measured_noise, expected, context, subject))
    if master_dark_noise is not None:
        rows.extend(master_dark_noise_measurements(master_dark_noise, context, subject))
    rows.extend(normalization_scale_measurements(normalization_scale, context, subject))
    if flat_noise is not None:
        rows.extend(flat_noise_measurements(flat_noise, stack_sky_level, context, subject))
    elif write_sky_level:
        rows.extend(stack_sky_level_measurements(stack_sky_level, context, subject))
    return rows


def science_measurements(
    science: StackScience | None,
    context: PipelineRunContext,
    stack_subject: QualitySubject,
    groups: Sequence[ExposureGroupSummary],
    group_subjects: Sequence[QualitySubject],
) -> list[QualityMeasurement]:
    """Build every value of the three families for one stack.

    Parameters
    ----------
    science : `StackScience` or `None`
        What was gathered, or `None` when the caller gathered nothing (every
        value is then not measured, with the reason).
    context : `PipelineRunContext`
        The run's shared facts.
    stack_subject : `QualitySubject`
        The combined stack.
    groups : `Sequence` [`ExposureGroupSummary`]
        The summary's exposure groups, which hold the peak values.
    group_subjects : `Sequence` [`QualitySubject`]
        The subject of each exposure group, in the same order as `groups`.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The expected noise, the sky level and flat noise, and each group's
        peak values.
    """
    if science is None:
        science = StackScience(
            ExpectedNoiseOutcome(None, "the caller did not work out the expected noise"),
            FlatNoiseOutcome(None, "the caller did not work out the flat's noise"),
            None,
        )
    rows = expected_noise_row_sequence(
        science.expected_noise,
        science.measured_noise,
        science.normalization_scale,
        science.stack_sky_level,
        context,
        stack_subject,
        master_dark_noise=science.master_dark_noise,
        flat_noise=science.flat_noise,
    )
    # `group_subjects` is built from `groups` in the same order (see
    # `record_quality.stacking_measurements`); a strict zip makes any drift
    # fail loudly.
    for index, (group, subject) in enumerate(zip(groups, group_subjects, strict=True)):
        rows.extend(group_peak_measurements(group, context, subject))
        rows.extend(group_reference_exposure_measurements(science.group_references, index, context, subject))
    return rows
