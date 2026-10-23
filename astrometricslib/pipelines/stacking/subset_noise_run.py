"""Purpose: Measure how stack noise falls with frames and what each night gave.

Description: Two jobs share this module. Both stack some of the frames of a
target's library stack into the target's ``_subsets`` folder and measure each
result's noise in the same empty sky apertures as the library stack (task E's
`stack_noise.measure_stack_noise`), on the same flux scale. They never change
the library stack, its previews, its previous-stack copy or its
``_inputs.json`` (see `subset_stack.py`).

Noise against frame count (`run_noise_growth`). The frames of the library stack
are split by observing night. The subsets are the first night, the first two
nights, and so on, up to one night fewer than the library stack. A target with
one night uses the first quarter, half and three quarters of its frames in time
order instead. Each subset stores the stack noise (three rows) and the weighted
frame count and time (two rows) at level ``stack``, with the subset's label in
the subject id. At most `DEFAULT_GROWTH_STEPS` subsets are made unless the
caller asks for more, spaced evenly through the possible ones. The library
stack is the last point of the curve and is not stacked again. Both jobs raise
`NotFoundError` when the library stack has no ``_inputs.json``, because then
its frames are not known.

Each night's contribution (`run_night_contributions`). For each night, the
library stack's frames without that night are stacked and measured. Each night
gets two rows on the leave-out stack's subject (``stacking.night_weight_share``
and ``stacking.noise_without_night``), with the night's id in the subject's
observing night. It costs one stack per night, so it runs only when asked.

Where the frames and weights come from. The library stack's ``_inputs.json``
lists the frames given to the stacker (after the frame quarantine step), so
that list is the stack's population. A night's weight share comes from the
``stacking.frame_weight`` rows the library stack stored; when the stack's
weights are unknown (Siril weighted the frames itself), the share is stored as
not measured with that reason. Equal weights are never assumed.

A storage failure is logged and never fails a subset. Nothing here reads the
clock: the caller supplies the measurement time.
"""

import logging
import math
import os
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import AstrometricsError, InvalidArgumentError, NotFoundError
from astrometricslib.foundation.jobs.runner import get_current_job
from astrometricslib.models.quality.measurement import (
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.subset_stacks import (
    NightContribution,
    NightContributionResult,
    NoiseGrowthResult,
    SubsetStackResult,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    record_run_measurements,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EFFECTIVE_FRAME_COUNT,
    STACKING_FRAME_WEIGHT,
    STACKING_METRICS,
    STACKING_NIGHT_WEIGHT_SHARE,
    STACKING_NOISE_WITHOUT_NIGHT,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import (
    measure_noise_safely,
    membership_count_measurements,
    noise_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMembership,
    determine_stack_members,
    effective_exposure_seconds,
    effective_frame_count,
    normalized_weights,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    APERTURE_FILE_NAME,
    StackNoiseOutcome,
)
from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import read_stack_inputs
from astrometricslib.pipelines.stacking.subset_stack import (
    MINIMUM_SUBSET_FRAMES,
    SUBSET_FOLDER_NAME,
    check_subset_label,
    stack_frame_subset,
    subset_stack_path,
)
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GROWTH_STEPS",
    "GrowthStep",
    "frames_by_night",
    "library_member_frames",
    "night_measurements",
    "night_weight_shares",
    "plan_growth_steps",
    "run_night_contributions",
    "run_noise_growth",
    "stack_chosen_subset",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

# How many subset stacks the growth series makes by default. Four points, with
# the library stack as the fifth, are enough to see whether the noise still
# falls like 1 / sqrt(frames) or has flattened, and each point costs one stack.
# A design choice; no data set decided it.
DEFAULT_GROWTH_STEPS = 4

# The parts of one night's frames, in time order, used when a target has a
# single night: a quarter, a half and three quarters.
SINGLE_NIGHT_FRACTIONS = (0.25, 0.5, 0.75)


@dataclass(frozen=True)
class GrowthStep:
    """One subset of the noise-against-frame-count series.

    Parameters
    ----------
    key : `str`
        The step's name, such as ``"nights-2"`` or ``"fraction-50"``.
    frames : `tuple` [`FrameRecord`, ...]
        The frames of the subset.
    """

    key: str
    frames: tuple[FrameRecord, ...]


def _report(progress_current: int, message: str) -> None:
    """Tell the running job which step the series has reached.

    Parameters
    ----------
    progress_current : `int`
        How far along the series is, out of 100.
    message : `str`
        A short description of the step.
    """
    job = get_current_job()
    if job is not None:
        job.stage(progress_current, message)


def library_member_frames(target: Target, stack_path: str) -> tuple[list[FrameRecord], list[str]]:
    """List the frames the library stack was made from.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack.
    stack_path : `str`
        The library stack.

    Returns
    -------
    members : `list` [`FrameRecord`]
        The target's records of the frames in the stack's ``_inputs.json``, in
        path order.
    missing_paths : `list` [`str`]
        Frames the record lists that the target no longer has (moved or
        deleted since the stack was made).

    Raises
    ------
    NotFoundError
        If the stack has no readable ``_inputs.json``. The stack must be made
        again to write one; without it the stack's frames are not known.
    """
    saved = read_stack_inputs(stack_path)
    if saved is None:
        raise NotFoundError(
            f"The stack {os.path.basename(stack_path)!r} has no record of its input frames "
            "(`_inputs.json`), so its frames are not known. Stack the target again to write one.",
            details={"stack": stack_path},
        )
    listed = sorted(str(entry[0]) for entry in saved["record"].get("frames", []))
    by_path = {frame.path: frame for frame in target.frames}
    members = [by_path[path] for path in listed if path in by_path]
    return members, [path for path in listed if path not in by_path]


def frames_by_night(frames: Sequence[FrameRecord]) -> tuple[dict[str, list[FrameRecord]], list[FrameRecord]]:
    """Group frames by observing night.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames.

    Returns
    -------
    nights : `dict` [`str`, `list` [`FrameRecord`]]
        Each night's frames in time order, nights sorted by name (a name is
        the night's start date, so this is time order).
    undated : `list` [`FrameRecord`]
        Frames with no timestamp, which belong to no night.
    """
    grouped: dict[str, list[FrameRecord]] = defaultdict(list)
    undated = []
    for frame in frames:
        if frame.timestamp is None:
            undated.append(frame)
        else:
            grouped[observing_night_id(frame.timestamp)].append(frame)
    return (
        {
            night: sorted(grouped[night], key=lambda frame: (frame.timestamp or 0.0, frame.path))
            for night in sorted(grouped)
        },
        undated,
    )


def _spread(candidates: list[GrowthStep], maximum: int) -> list[GrowthStep]:
    """Keep at most ``maximum`` steps, spaced evenly through the list.

    Parameters
    ----------
    candidates : `list` [`GrowthStep`]
        All possible steps, smallest first.
    maximum : `int`
        The most steps to keep.

    Returns
    -------
    chosen : `list` [`GrowthStep`]
        The candidates if there are no more than ``maximum``, otherwise
        ``maximum`` of them including the first and the last.
    """
    if len(candidates) <= maximum:
        return candidates
    if maximum == 1:
        return [candidates[-1]]
    positions = sorted({round(index * (len(candidates) - 1) / (maximum - 1)) for index in range(maximum)})
    return [candidates[position] for position in positions]


def plan_growth_steps(
    nights: dict[str, list[FrameRecord]], maximum_steps: int = DEFAULT_GROWTH_STEPS
) -> list[GrowthStep]:
    """Choose the subsets of the noise-against-frame-count series.

    Parameters
    ----------
    nights : `dict` [`str`, `list` [`FrameRecord`]]
        The library stack's dated frames by night, as `frames_by_night` gives.
    maximum_steps : `int`, optional
        The most subsets to make.

    Returns
    -------
    steps : `list` [`GrowthStep`]
        With two or more nights, the first night, the first two nights and so
        on up to all but the last night. With one night, the first quarter,
        half and three quarters of its frames. Smallest first. The whole set is
        the library stack and is not a step. A step with fewer than
        `MINIMUM_SUBSET_FRAMES` frames is left out.

    Raises
    ------
    InvalidArgumentError
        If ``maximum_steps`` is below 1.
    """
    if maximum_steps < 1:
        raise InvalidArgumentError("maximum_steps must be at least 1.")
    names = list(nights)
    candidates: list[GrowthStep] = []
    if len(names) >= 2:
        for count in range(1, len(names)):
            frames = tuple(frame for name in names[:count] for frame in nights[name])
            candidates.append(GrowthStep(f"nights-{count}", frames))
    elif len(names) == 1:
        only = nights[names[0]]
        for fraction in SINGLE_NIGHT_FRACTIONS:
            frames = tuple(only[: math.floor(len(only) * fraction)])
            candidates.append(GrowthStep(f"fraction-{round(fraction * 100)}", frames))
    candidates = [step for step in candidates if len(step.frames) >= MINIMUM_SUBSET_FRAMES]
    return _spread(candidates, maximum_steps)


def night_weight_shares(
    weights: Sequence[tuple[str | None, float | None]],
) -> tuple[dict[str, float], str]:
    """Work out each night's share of the stack's total frame weight.

    Parameters
    ----------
    weights : `Sequence` [`tuple` [`str` or `None`, `float` or `None`]]
        The observing night and the weight of each frame that went into the
        stack. A weight is `None` when it is unknown.

    Returns
    -------
    shares : `dict` [`str`, `float`]
        Each night's summed weight divided by the total. The shares add up to
        1. Empty when no share can be given.
    reason : `str`
        Why there are no shares. Empty when there are.
    """
    if not weights:
        return {}, "the library stack has no recorded frame weights"
    if any(weight is None for _, weight in weights):
        return {}, "the library stack's frame weights are unknown (the stacker did not report them)"
    if any(night is None for night, _ in weights):
        return {}, "some of the library stack's frames have no observing night"
    totals: dict[str, float] = defaultdict(float)
    for night, weight in weights:
        totals[str(night)] += float(weight or 0.0)
    grand_total = math.fsum(totals.values())
    if grand_total <= 0:
        return {}, "the library stack's frame weights add up to zero"
    return {night: total / grand_total for night, total in totals.items()}, ""


def _recorded_weights(
    store: QualityStore, target_id: str, stack_file_name: str, member_subject_ids: set[str]
) -> tuple[list[tuple[str | None, float | None]], str]:
    """Read back the frame weights the library stack stored.

    Parameters
    ----------
    store : `QualityStore`
        The quality database.
    target_id : `str`
        The target.
    stack_file_name : `str`
        The library stack's file name.
    member_subject_ids : `set` [`str`]
        The subject ids of the stack's frames (to leave out other stacks'
        frames that share the run).

    Returns
    -------
    weights : `list` [`tuple`]
        The night and weight of each frame the stack's run recorded.
    reason : `str`
        Why there are none, empty when there are some.
    """
    stack_subject_id = f"{target_id}/{stack_file_name}"
    try:
        count_rows = [
            row
            for row in store.query(
                metric=STACKING_EFFECTIVE_FRAME_COUNT, level=SubjectLevel.STACK, target_id=target_id
            )
            if row.subject.subject_id == stack_subject_id
        ]
        if not count_rows:
            return [], (
                "the library stack has no stored frame weights (it was stacked before they were "
                "recorded; stack it again)"
            )
        run_id = count_rows[-1].run_id
        weight_rows = [
            row
            for row in store.query(
                metric=STACKING_FRAME_WEIGHT, level=SubjectLevel.FRAME, target_id=target_id, latest_only=False
            )
            if row.run_id == run_id and row.subject.subject_id in member_subject_ids
        ]
    except AstrometricsError as error:
        return [], f"the stored frame weights could not be read: {error.message}"
    return [(row.subject.observing_night_id, row.value) for row in weight_rows], ""


def _subset_subject(context: PipelineRunContext, label: str, night_id: str | None = None) -> QualitySubject:
    """Name a subset stack as the subject of its measurements.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    label : `str`
        The subset's label.
    night_id : `str`, optional
        For a leave-one-night-out stack, the night left out.

    Returns
    -------
    subject : `QualitySubject`
        Level ``stack``; its id is the target, ``_subsets``, the label and the
        subset's file name.
    """
    subject = context.stack_subject(f"{SUBSET_FOLDER_NAME}/{label}/{label}.fits")
    if night_id is None:
        return subject
    return subject.model_copy(update={"observing_night_id": night_id})


def _stack_and_measure(
    target: Target,
    frames: Sequence[FrameRecord],
    label: str,
    library_stack_path: str,
    context: PipelineRunContext | None,
    *,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    night_id: str | None = None,
    output_folder: str | None = None,
    extra_measurements: Callable[[SubsetStackResult, StackNoiseOutcome | None], list[QualityMeasurement]]
    | None = None,
) -> SubsetStackResult:
    """Stack one subset, measure its noise and members, and store the rows.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    frames : `Sequence` [`FrameRecord`]
        The subset's frames.
    label : `str`
        The subset's label; it names the folder and file.
    library_stack_path : `str`
        The library stack, whose folder holds the target's stored apertures.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    night_id : `str`, optional
        For a leave-one-night-out stack, the night left out.
    output_folder : `str`, optional
        The folder to write the subset stack to. Left out, it is
        ``_subsets/<label>`` beside the library stack.
    extra_measurements : `Callable`, optional
        Builds more rows from the finished subset result and its noise outcome
        (the night metrics). They are stored with the others.

    Returns
    -------
    result : `SubsetStackResult`
        What was stacked and measured. A subset that cannot be stacked or
        measured is reported with the reason, not raised.

    Raises
    ------
    InvalidArgumentError
        If the subset's path would be the library stack's path.
    """
    library_folder = os.path.dirname(library_stack_path)
    if output_folder is None:
        output_path = subset_stack_path(library_folder, label, f"{label}.fits")
    else:
        check_subset_label(label)
        output_path = os.path.join(os.path.abspath(output_folder), f"{label}.fits")
    if os.path.abspath(output_path) == os.path.abspath(library_stack_path):
        raise InvalidArgumentError("A subset stack may never be written to the library stack's path.")
    nights = sorted({observing_night_id(frame.timestamp) for frame in frames if frame.timestamp is not None})
    result = SubsetStackResult(
        targetId=target.id, label=label, framesSubmitted=len(frames), observingNightIds=nights
    )
    with stacking_slot():
        outcome = stack_frame_subset(
            target.id,
            list(frames),
            output_path,
            configuration=configuration,
            drivers=drivers,
            job_id=None if context is None else context.job_id,
        )
    result.stack_path = outcome.stack_path
    subject = _subset_subject(context, label, night_id) if context is not None else None
    membership: StackMembership | None = None
    membership_reason = ""
    noise: StackNoiseOutcome | None
    if outcome.stack_path is None:
        noise = StackNoiseOutcome(None, "the stacker made no stack from these frames")
        membership_reason = "the stacker made no stack from these frames"
    else:
        apertures_path = os.path.join(library_folder, APERTURE_FILE_NAME)
        if os.path.isfile(apertures_path):
            noise = measure_noise_safely(
                outcome.stack_path,
                outcome.registration_reference_frame,
                outcome.is_spectral,
                outcome.crop_offset,
                apertures_path,
            )
        else:
            noise = StackNoiseOutcome(
                None,
                "the target has no stored noise apertures; stack the target once so its first stack "
                "chooses them",
            )
        try:
            membership = determine_stack_members(frames, outcome.diagnostics, outcome.stack_weight)
        except Exception as error:  # a by-product: a failure here must not lose the stack
            logger.exception("The member frames of the subset stack %s could not be worked out", output_path)
            membership_reason = f"the member frames could not be worked out: {type(error).__name__}"
    if noise.result is not None:
        result.normalized_noise = noise.result.normalized_noise
        result.normalized_noise_uncertainty = noise.result.normalized_uncertainty
    else:
        result.noise_reason = noise.reason
    if membership is not None and membership.members:
        weights = normalized_weights(membership)
        exposures = [
            float(member.frame.exposure) if member.frame.exposure else None for member in membership.members
        ]
        result.frames_stacked = len(membership.members)
        result.effective_frame_count = effective_frame_count(weights)
        result.effective_time_seconds = effective_exposure_seconds(weights, exposures)
    if context is not None and subject is not None:
        measurements = noise_measurements(noise, context, subject)
        measurements.extend(membership_count_measurements(membership, membership_reason, context, subject))
        if extra_measurements is not None:
            measurements.extend(extra_measurements(result, noise))
        result.rows_written = record_run_measurements(measurements, config=configuration)
    return result


def run_noise_growth(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    maximum_steps: int = DEFAULT_GROWTH_STEPS,
) -> NoiseGrowthResult:
    """Stack growing subsets of the library stack's frames and measure each.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library_stack_path : `str`
        The library stack whose frames to use. Its ``_inputs.json`` must exist.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    maximum_steps : `int`, optional
        The most subsets to stack.

    Returns
    -------
    result : `NoiseGrowthResult`
        The subsets, smallest first, and notes about steps left out.
    """
    members, missing = library_member_frames(target, library_stack_path)
    nights, undated = frames_by_night(members)
    notes: list[str] = []
    if missing:
        notes.append(
            f"{len(missing)} frame(s) of the library stack are no longer on the target and were left out."
        )
    if undated:
        notes.append(f"{len(undated)} frame(s) have no timestamp and were left out of the series.")
    steps = plan_growth_steps(nights, maximum_steps)
    if not steps:
        notes.append(f"No subset has at least {MINIMUM_SUBSET_FRAMES} frames, so no subset stack was made.")
    stem = os.path.splitext(os.path.basename(library_stack_path))[0]
    subsets = []
    for index, step in enumerate(steps):
        _report(int(100 * index / len(steps)), f"Stacking subset {step.key} ({len(step.frames)} frames)")
        subsets.append(
            _stack_and_measure(
                target,
                step.frames,
                f"{stem}.{step.key}",
                library_stack_path,
                context,
                configuration=configuration,
                drivers=drivers,
                stacking_slot=stacking_slot,
            )
        )
    return NoiseGrowthResult(
        targetId=target.id,
        libraryStackPath=library_stack_path,
        subsets=subsets,
        notes=notes,
        rowsWritten=sum(subset.rows_written for subset in subsets),
    )


def run_night_contributions(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
) -> NightContributionResult:
    """Measure each night's share of the library stack and its absence.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library_stack_path : `str`
        The library stack whose frames to use. Its ``_inputs.json`` must exist.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.

    Returns
    -------
    result : `NightContributionResult`
        One entry for each night. A target with one night has nothing to leave
        out: its entry has no subset and a note says so.
    """
    members, missing = library_member_frames(target, library_stack_path)
    nights, undated = frames_by_night(members)
    notes: list[str] = []
    if missing:
        notes.append(
            f"{len(missing)} frame(s) of the library stack are no longer on the target and were left out."
        )
    if undated:
        notes.append(
            f"{len(undated)} frame(s) have no timestamp. They stay in every leave-one-night-out stack."
        )
    stack_file_name = os.path.basename(library_stack_path)
    stem = os.path.splitext(stack_file_name)[0]
    from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id

    member_ids = {frame_subject_id(frame.path, target.id) for frame in members}
    recorded, weights_reason = _recorded_weights(
        QualityStore.from_configuration(configuration), target.id, stack_file_name, member_ids
    )
    shares, share_reason = night_weight_shares(recorded)
    share_reason = weights_reason or share_reason
    contributions = []
    if len(nights) < 2:
        notes.append("The stack has fewer than two observing nights, so there is no night to leave out.")
    for index, night in enumerate(nights if len(nights) >= 2 else []):
        remaining = [frame for other, group in nights.items() if other != night for frame in group] + undated
        label = f"{stem}.without-{night}"
        check_subset_label(label)
        _report(
            int(100 * index / len(nights)), f"Stacking without the night {night} ({len(remaining)} frames)"
        )

        def night_rows(
            result: SubsetStackResult,
            noise: StackNoiseOutcome | None,
            night: str = night,
            label: str = label,
        ) -> list[QualityMeasurement]:
            """Build the two night rows of one leave-out stack.

            Parameters
            ----------
            result : `SubsetStackResult`
                The leave-out stack's result.
            noise : `StackNoiseOutcome` or `None`
                Its noise or the reason there is none.
            night : `str`, optional
                The night left out (bound at definition time).
            label : `str`, optional
                The leave-out stack's label (bound at definition time).

            Returns
            -------
            measurements : `list` [`QualityMeasurement`]
                The night's share and the noise without the night.
            """
            return night_measurements(context, label, night, shares.get(night), share_reason, result, noise)

        if len(remaining) < MINIMUM_SUBSET_FRAMES:
            subset = None
            notes.append(
                f"Without the night {night} only {len(remaining)} frame(s) would be left, "
                f"fewer than the {MINIMUM_SUBSET_FRAMES} a stack needs."
            )
            if context is not None:
                reason = f"fewer than {MINIMUM_SUBSET_FRAMES} frames would be left without this night"
                skipped = SubsetStackResult(targetId=target.id, label=label)
                rows = night_measurements(
                    context,
                    label,
                    night,
                    shares.get(night),
                    share_reason,
                    skipped,
                    StackNoiseOutcome(None, reason),
                )
                record_run_measurements(rows, config=configuration)
        else:
            subset = _stack_and_measure(
                target,
                remaining,
                label,
                library_stack_path,
                context,
                configuration=configuration,
                drivers=drivers,
                stacking_slot=stacking_slot,
                night_id=night,
                extra_measurements=night_rows,
            )
        contributions.append(
            NightContribution(
                nightId=night,
                frameCount=len(nights[night]),
                weightShare=shares.get(night),
                weightReason="" if night in shares else share_reason,
                subset=subset,
            )
        )
    return NightContributionResult(
        targetId=target.id,
        libraryStackPath=library_stack_path,
        nights=contributions,
        notes=notes,
        rowsWritten=sum(entry.subset.rows_written for entry in contributions if entry.subset is not None),
    )


def night_measurements(
    context: PipelineRunContext | None,
    label: str,
    night_id: str,
    share: float | None,
    share_reason: str,
    result: SubsetStackResult,
    noise: StackNoiseOutcome | None,
) -> list[QualityMeasurement]:
    """Build the night's weight share and the noise without the night.

    Parameters
    ----------
    context : `PipelineRunContext` or `None`
        The run's shared facts. Nothing is built without it.
    label : `str`
        The leave-out stack's label.
    night_id : `str`
        The night left out.
    share : `float` or `None`
        The night's share of the library stack's total weight.
    share_reason : `str`
        Why there is no share.
    result : `SubsetStackResult`
        The leave-out stack's result.
    noise : `StackNoiseOutcome` or `None`
        The leave-out stack's noise or the reason there is none.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        Two rows on the leave-out stack's subject.
    """
    if context is None:
        return []
    subject = _subset_subject(context, label, night_id)
    rows = [
        build_measurement(
            _SPEC_BY_NAME[STACKING_NIGHT_WEIGHT_SHARE],
            share,
            context,
            subject,
            reason=share_reason or "the night's share is not known",
        )
    ]
    outcome = noise.result if noise is not None else None
    if outcome is None:
        rows.append(
            build_measurement(
                _SPEC_BY_NAME[STACKING_NOISE_WITHOUT_NIGHT],
                None,
                context,
                subject,
                reason=(noise.reason if noise is not None else "")
                or result.noise_reason
                or "the noise was not measured",
            )
        )
    else:
        rows.append(
            build_measurement(
                _SPEC_BY_NAME[STACKING_NOISE_WITHOUT_NIGHT],
                outcome.normalized_noise,
                context,
                subject,
                uncertainty=outcome.normalized_uncertainty,
                sample_count=outcome.aperture_count,
            )
        )
    return rows


def stack_chosen_subset(
    target: Target,
    frames: Sequence[FrameRecord],
    label: str,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    output_folder: str | None = None,
) -> SubsetStackResult:
    """Stack exactly the frames given and measure the result.

    This is the public way to make one subset stack. The target is only read:
    nothing is assigned on it and nothing is saved.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    frames : `Sequence` [`FrameRecord`]
        The frames to stack, all of one camera.
    label : `str`
        The subset's label. It names the folder and the file.
    library_stack_path : `str`
        The target's library stack (or the path it will have). Its folder
        holds the target's stored noise apertures and the default
        ``_subsets`` folder.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    output_folder : `str`, optional
        Where to write the stack instead of the default subset folder.

    Returns
    -------
    result : `SubsetStackResult`
        The stack's path, its frame counts and its noise.
    """
    return _stack_and_measure(
        target,
        frames,
        label,
        library_stack_path,
        context,
        configuration=configuration,
        drivers=drivers,
        stacking_slot=stacking_slot,
        output_folder=output_folder,
    )
