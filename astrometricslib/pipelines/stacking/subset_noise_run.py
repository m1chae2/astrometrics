"""Purpose: Measure how stack noise falls with frames and what each night gave.

Description: Two jobs share this module. Both stack some of the frames of a
target's library stack into the target's ``_subsets`` folder and measure each
result's noise in the same empty sky apertures as the library stack (task E's
`stack_noise.measure_stack_noise`), on the same flux scale. They never change
the library stack, its previews, its previous-stack copy or its
``_inputs.json`` (see `subset_stack.py`). While a job runs, no library stack
of the target and no other subset job of the target can run
(`target_stack_guard.py`).

Noise against frame count (`run_noise_growth`). The frames of the library stack
are split by observing night. The subsets are the first night, the first two
nights, and so on, up to one night fewer than the library stack. A target with
one night uses a quarter, a half and three quarters of its frames, taken
evenly through the night (every fourth frame, and so on) so that a slow change
in the sky or the seeing does not fall on one subset only. Each subset stores
the stack noise (three rows) and the weighted frame count and times (three
rows) at level ``stack``, with the subset's label in the subject id and in the
subject's ``variant`` field. At most `DEFAULT_GROWTH_STEPS` subsets are made
unless the caller asks for more, spaced evenly through the possible ones. The
library stack is the last point of the curve and is not stacked again. If one
subset cannot be stacked, its rows say why and the series goes on.

Each subset also gets the noise ratio its own weighted frame count predicts
against the library stack (`expected_noise_ratio`). Siril works out the
star-width weights again for every set of frames, so a subset's prediction
uses its own weights, never the library stack's.

Each night's contribution (`run_night_contributions`). For each night, the
library stack's frames without that night are stacked and measured. Each night
gets four rows on the leave-out stack's subject: the night's share of the
weights (``stacking.night_weight_fraction``), the noise without the night
(``stacking.noise_without_night``), the difference from the library stack's
noise with its paired error (``stacking.noise_difference_without_night``) and
the expected ratio. The night's id is in the subject's observing night. It
costs one stack per night, so it runs only when asked.

Where the frames and weights come from. The library stack's members are the
frames of its ``stacking.frame_weight`` rows, which the stack's run stored for
each frame it really used. Only a stack made before those rows existed falls
back to ``_inputs.json``, which lists the frames given to the stacker and so
may include frames Siril dropped; a note says so. A night's weight share comes
from the weight rows; when the stack's weights are unknown (Siril weighted the
frames itself), the share is stored as not measured with that reason. Equal
weights are never assumed. A share built from assumed weights is stored as
assumed.

The noise of the difference of two halves has its own module,
`odd_even_difference_run.py`. It runs in the same lock, and the growth job
can start it, but it shares none of the planning here.

A subset for the forecast check (`stack_chosen_subset` with
``record_forecast_rows=True``). The caller picks the frames; the restack script
picks all nights of a library stack but the last and labels the subset
`EARLIER_NIGHTS_VARIANT`. Such a subset is the earlier member of the forecast
check's pair, and the library stack is the later one, so it stores what the
check reads from a library stack: the
empty-aperture noise (in the library stack's apertures) and the aperture set,
the effective counts, one weight for each frame, the exposure-group rows, the
predicted noise with each frame's scale and the stack's normalization scale,
and the calibration record. Its frame weights sit on frame subjects of their
own (`subset_frame_subject_id`), because the "newest row" view of the quality
store ranks rows by subject, and a weight on the library stack's frame subject
would hide the library stack's weight of the same frame. The other subset jobs
store none of these rows.

A storage failure is logged and never fails a subset. Nothing here reads the
clock: the caller supplies the measurement time.
"""

import logging
import math
import os
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import AstrometricsError, InvalidArgumentError, NotFoundError
from astrometricslib.foundation.jobs.runner import get_current_job
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.quality_summary import ExposureGroupSummary
from astrometricslib.models.quality.subset_stacks import (
    NightContribution,
    NightContributionResult,
    NoiseGrowthResult,
    SubsetStackResult,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject
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
    STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT,
    STACKING_NOISE_WITHOUT_NIGHT,
    STACKING_SUBSET_EXPECTED_NOISE_RATIO,
)
from astrometricslib.pipelines.stacking.post_processing.calibration_record import (
    record_calibration_identities,
)
from astrometricslib.pipelines.stacking.post_processing.difference_noise import (
    NoiseSums,
    load_noise_sums,
    paired_noise_difference,
)
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    APERTURE_FILE_NAME,
    load_apertures_with_reason,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import (
    frame_weight_measurements,
    group_summary_measurements,
    measure_noise_safely,
    membership_count_measurements,
    noise_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    compute_expected_noise,
    expected_noise_measurements,
    group_reference_exposure_measurements,
    noise_ratio_measurements,
    normalization_scale_measurements,
    resolve_stack_sky_level,
    scale_expected_noise,
    stack_sky_level_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMembership,
    determine_stack_members,
    effective_exposure_seconds,
    effective_frame_count,
    normalized_weights,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome
from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import read_stack_inputs
from astrometricslib.pipelines.stacking.subset_stack import (
    MINIMUM_SUBSET_FRAMES,
    SUBSET_FOLDER_NAME,
    check_subset_label,
    stack_frame_subset,
    stack_stem,
    subset_output_path,
)
from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GROWTH_STEPS",
    "GrowthStep",
    "LibraryRun",
    "expected_noise_ratio",
    "expected_ratio_measurement",
    "frames_by_night",
    "library_member_frames",
    "night_measurements",
    "night_weight_shares",
    "plan_growth_steps",
    "read_library_run",
    "run_night_contributions",
    "run_noise_growth",
    "stack_chosen_subset",
    "subset_frame_subject_id",
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


def report_progress(progress_current: int, message: str) -> None:
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
    """List the frames given to the stacker, from the stack's ``_inputs.json``.

    This is the fallback for a stack whose run stored no frame rows. The list
    holds the frames given to the stacker, so it may include frames Siril
    dropped. `read_library_run` prefers the stored rows.

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
        on up to all but the last night. With one night, a quarter, a half
        and three quarters of its frames, spread evenly through the night.
        Smallest first. The whole set is
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
            count = math.floor(len(only) * fraction)
            # Frames spread evenly through the night, not the first ones: the
            # sky and the seeing drift, and the first part of the night would
            # then differ from the library stack in more than frame count.
            picks = (
                sorted({math.floor((index + 0.5) * len(only) / count) for index in range(count)})
                if count
                else []
            )
            candidates.append(
                GrowthStep(f"fraction-{round(fraction * 100)}", tuple(only[pick] for pick in picks))
            )
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


@dataclass(frozen=True)
class LibraryRun:
    """What the library stack's own run stored about its frames and noise.

    Parameters
    ----------
    members : `list` [`FrameRecord`]
        The frames that went into the stack.
    notes : `list` [`str`]
        Plain sentences about frames that are gone or about where the member
        list came from.
    weights : `list` [`tuple`]
        The observing night and weight of each member (a weight is `None`
        when the stack did not report it).
    weights_reason : `str`
        Why there are no weights. Empty when there are.
    assumed_reason : `str`
        Why the weights or the effective count are assumed, when they are.
        Empty otherwise.
    effective_count : `float` or `None`
        The library stack's effective frame count.
    noise_sums : `NoiseSums` or `None`
        The library stack's per-aperture noise sums, the reference flux they
        are read against and the identity of the apertures they were
        measured in (see `difference_noise.load_noise_sums`); `None` when not
        saved.
    store : `QualityStore` or `None`
        The quality store the run read, so later steps (the expected noise of
        each subset) read from it instead of opening their own.
    weight_by_path : `dict` [`str`, `float`] or `None`
        The library stack's weight of each member frame by frame path, in the
        stack's own scale. `None` when the run stored no frame weights or any
        weight is unknown.
    """

    members: list[FrameRecord]
    notes: list[str] = field(default_factory=list)
    weights: list[tuple[str | None, float | None]] = field(default_factory=list)
    weights_reason: str = ""
    assumed_reason: str = ""
    effective_count: float | None = None
    noise_sums: NoiseSums | None = None
    store: QualityStore | None = None
    weight_by_path: dict[str, float] | None = None


def read_library_run(target: Target, library_stack_path: str, configuration: Any) -> LibraryRun:
    """Read what the library stack's run stored about its members.

    The members are the frames of the stack's ``stacking.frame_weight`` rows,
    which the run wrote for each frame it really used. A stack made before
    those rows existed falls back to ``_inputs.json`` (see
    `library_member_frames`), and a note says that frames Siril dropped may be
    in the list.

    Parameters
    ----------
    target : `Target`
        The target that owns the stack. It is only read.
    library_stack_path : `str`
        The library stack.
    configuration : `AppConfiguration`
        Where the quality store lives.

    Returns
    -------
    run : `LibraryRun`
        The members, their weights and the stack's effective count.

    Notes
    -----
    `NotFoundError` is raised (by `library_member_frames`) when the run stored
    no frame rows and the stack has no readable ``_inputs.json`` either.
    """
    from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject_id

    store = QualityStore.from_configuration(configuration)
    stack_file_name = os.path.basename(library_stack_path)
    count_row, weight_rows, store_reason = _library_rows(store, target.id, stack_file_name)
    sums = load_noise_sums(library_stack_path)
    by_subject = {frame_subject_id(frame.path, target.id): frame for frame in target.frames}
    notes: list[str] = []
    if count_row is not None and weight_rows:
        members = [
            by_subject[row.subject.subject_id] for row in weight_rows if row.subject.subject_id in by_subject
        ]
        gone = len(weight_rows) - len(members)
        if gone:
            notes.append(
                f"{gone} frame(s) of the library stack are no longer on the target and were left out."
            )
        weights = [(row.subject.observing_night_id, row.value) for row in weight_rows]
        assumed = [
            row.reason for row in weight_rows if row.status == MeasurementStatus.ASSUMED and row.reason
        ]
        if count_row.status == MeasurementStatus.ASSUMED and count_row.reason:
            assumed.append(count_row.reason)
        known = {
            by_subject[row.subject.subject_id].path: row.value
            for row in weight_rows
            if row.subject.subject_id in by_subject and row.value is not None
        }
        all_known = len(known) == len(members) and len(members) > 0
        return LibraryRun(
            members=members,
            notes=notes,
            weights=weights,
            assumed_reason=assumed[0] if assumed else "",
            effective_count=count_row.value,
            noise_sums=sums,
            store=store,
            weight_by_path=known if all_known else None,
        )
    members, missing = library_member_frames(target, library_stack_path)
    if missing:
        notes.append(
            f"{len(missing)} frame(s) of the library stack are no longer on the target and were left out."
        )
    notes.append(
        "The library stack stored no frame rows, so its members are the frames given to the stacker "
        "(`_inputs.json`); frames Siril dropped may be among them."
    )
    return LibraryRun(
        members=members,
        notes=notes,
        weights_reason=store_reason,
        assumed_reason="the member list is the frames given to the stacker, not the frames Siril kept",
        noise_sums=sums,
        store=store,
    )


def _library_rows(
    store: QualityStore, target_id: str, stack_file_name: str
) -> tuple[QualityMeasurement | None, list[QualityMeasurement], str]:
    """Read the effective count and frame weights the library stack stored.

    Parameters
    ----------
    store : `QualityStore`
        The quality database.
    target_id : `str`
        The target.
    stack_file_name : `str`
        The library stack's file name.

    Returns
    -------
    count_row : `QualityMeasurement` or `None`
        The stack's newest effective-frame-count row.
    weight_rows : `list` [`QualityMeasurement`]
        The frame-weight rows of the same run (one for each member frame).
    reason : `str`
        Why there are none. Empty when there are some.
    """
    stack_subject_id = f"{target_id}/{stack_file_name}"
    try:
        count_rows = [
            row
            for row in store.query(
                metric=STACKING_EFFECTIVE_FRAME_COUNT,
                level=SubjectLevel.STACK,
                target_id=target_id,
                library_only=True,
            )
            if row.subject.subject_id == stack_subject_id
        ]
        if not count_rows:
            return (
                None,
                [],
                "the library stack has no stored frame weights (it was stacked before they were "
                "recorded; stack it again)",
            )
        run_id = count_rows[-1].run_id
        weight_rows = [
            row
            for row in store.query(
                metric=STACKING_FRAME_WEIGHT, level=SubjectLevel.FRAME, target_id=target_id, latest_only=False
            )
            if row.run_id == run_id
        ]
    except AstrometricsError as error:
        return None, [], f"the stored frame weights could not be read: {error.message}"
    return count_rows[-1], weight_rows, ""


def expected_noise_ratio(library_count: float | None, subset_count: float | None) -> float | None:
    """Predict a subset's noise against the library stack's from frame counts.

    If every frame were equally noisy, a stack's noise would fall as one over
    the square root of its effective frame count.

    Parameters
    ----------
    library_count : `float` or `None`
        The library stack's effective frame count.
    subset_count : `float` or `None`
        The subset stack's own effective frame count.

    Returns
    -------
    ratio : `float` or `None`
        sqrt(library_count / subset_count), or `None` when either count is
        unknown or not positive.
    """
    if library_count is None or subset_count is None or library_count <= 0 or subset_count <= 0:
        return None
    return math.sqrt(library_count / subset_count)


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
        subset's file name. Its ``variant`` is the label, so a reader can
        tell a subset from a library stack by the field.
    """
    subject = context.stack_subject(f"{SUBSET_FOLDER_NAME}/{label}/{label}.fits")
    update: dict[str, str] = {"variant": label}
    if night_id is not None:
        update["observing_night_id"] = night_id
    return subject.model_copy(update=update)


def subset_frame_subject_id(frame_subject_id: str, label: str) -> str:
    """Name a frame as the subject of a subset stack's own weight row.

    Parameters
    ----------
    frame_subject_id : `str`
        The frame's subject id (`frame_subject_id` of the pipelines' shared
        frame measurement).
    label : `str`
        The subset's label.

    Returns
    -------
    subject_id : `str`
        The frame's id, ``#`` and the label. A reader strips everything from
        the last ``#`` to get the frame's own id back.
    """
    return f"{frame_subject_id}#{label}"


def _subset_frame_subject(context: PipelineRunContext, label: str, frame: FrameRecord) -> QualitySubject:
    """Name a member frame as the subject of the subset's weight row.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    label : `str`
        The subset's label.
    frame : `FrameRecord`
        The member frame.

    Returns
    -------
    subject : `QualitySubject`
        Level ``frame``, in the frame's observing night, with the id from
        `subset_frame_subject_id` and the subset's label as its variant.
    """
    subject = frame_subject(frame.path, context.target_id, frame.timestamp)
    return subject.model_copy(
        update={"subject_id": subset_frame_subject_id(subject.subject_id, label), "variant": label}
    )


def _subset_group_subject(
    context: PipelineRunContext, label: str, library_folder: str, group: ExposureGroupSummary, index: int
) -> QualitySubject:
    """Name one exposure group of a subset stack as the subject of its rows.

    A library stack names a group by the file of its group stack. The subset
    does the same, but under its own ``_subsets/<label>`` folder, so a group of
    the subset never takes the id of a group of the library stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    label : `str`
        The subset's label.
    library_folder : `str`
        The folder of the library stack (the folder ``_subsets`` is in).
    group : `ExposureGroupSummary`
        The group.
    index : `int`
        The group's place in the summary's list.

    Returns
    -------
    subject : `QualitySubject`
        Level ``stack``, with the subset's label as its variant. A group with
        its own file is named by the file's place below the library folder
        (the subset stack itself for a stack of one exposure length, a file in
        its ``groups`` folder otherwise); a group with no file by the subset
        stack, its place in the list and its exposure.
    """
    stack_name = f"{SUBSET_FOLDER_NAME}/{label}/{label}.fits"
    if group.stack_path:
        relative = os.path.relpath(group.stack_path, library_folder).replace(os.sep, "/")
        if relative.startswith(".."):
            relative = f"{SUBSET_FOLDER_NAME}/{label}/groups/{os.path.basename(group.stack_path)}"
        name = relative
    else:
        name = f"{stack_name}#group-{index}-{group.exposure_seconds:g}s"
    return context.stack_subject(name).model_copy(update={"variant": label})


def _forecast_record_measurements(
    context: PipelineRunContext,
    label: str,
    library_folder: str,
    membership: StackMembership | None,
    outcome: Any,
) -> list[QualityMeasurement]:
    """Build the rows of a library stack that the forecast check also reads.

    These are the same rows, built by the same functions as for a library
    stack (`record_quality.frame_weight_measurements`,
    `record_quality.group_summary_measurements` and
    `record_science.group_reference_exposure_measurements`), put on the
    subset's own subjects.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    label : `str`
        The subset's label.
    library_folder : `str`
        The folder of the library stack.
    membership : `StackMembership` or `None`
        The subset's members and weights, or `None` when they are unknown.
    outcome : `SubsetStackOutcome` or `None`
        What stacking the subset produced, or `None` when the stacker failed.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One weight for each member frame; then, for each exposure group of the
        subset, its summary rows and the exposure of its registration
        reference frame.
    """
    rows = frame_weight_measurements(
        membership, "", context, lambda frame: _subset_frame_subject(context, label, frame)
    )
    if outcome is None:
        return rows
    references = membership.group_references if membership is not None else ()
    for index, entry in enumerate(outcome.diagnostics.get("exposure_group_summaries") or []):
        group = ExposureGroupSummary(**entry)
        group_subject = _subset_group_subject(context, label, library_folder, group, index)
        rows.extend(group_summary_measurements(group, context, group_subject))
        rows.extend(group_reference_exposure_measurements(references, index, context, group_subject))
    return rows


def expected_ratio_measurement(
    context: PipelineRunContext,
    subject: QualitySubject,
    library: LibraryRun | None,
    result: SubsetStackResult,
    subset_assumed_reason: str,
) -> QualityMeasurement:
    """Build the expected-noise-ratio row of one subset stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The subset stack.
    library : `LibraryRun` or `None`
        What the library stack stored, or `None` when it is not known.
    result : `SubsetStackResult`
        The subset's result, holding its own effective frame count.
    subset_assumed_reason : `str`
        Why the subset's member list is assumed. Empty when it is not.

    Returns
    -------
    measurement : `QualityMeasurement`
        The ratio, assumed when either member list is; or not measured with
        the reason.
    """
    spec = _SPEC_BY_NAME[STACKING_SUBSET_EXPECTED_NOISE_RATIO]
    ratio = expected_noise_ratio(
        None if library is None else library.effective_count, result.effective_frame_count
    )
    if ratio is None:
        reason = (
            "the library stack's effective frame count is not stored"
            if library is None or library.effective_count is None
            else "the subset's effective frame count is unknown"
        )
        return build_measurement(spec, None, context, subject, reason=reason)
    assumed = " ".join(
        part for part in (library.assumed_reason if library else "", subset_assumed_reason) if part
    )
    return build_measurement(
        spec,
        ratio,
        context,
        subject,
        status=MeasurementStatus.ASSUMED if assumed else MeasurementStatus.MEASURED,
        reason=assumed,
    )


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
    library: LibraryRun | None = None,
    keep_going: bool = False,
    extra_measurements: Callable[[SubsetStackResult, StackNoiseOutcome | None], list[QualityMeasurement]]
    | None = None,
    record_forecast_rows: bool = False,
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
        The folder to write the subset stack to. It must be inside
        ``_subsets`` beside the library stack (see `subset_output_path`).
        Left out, it is ``_subsets/<label>``.
    library : `LibraryRun`, optional
        What the library stack stored, for the expected noise ratio.
    keep_going : `bool`, optional
        When `True` (a series of subsets), a stacker that fails is reported
        in the result, with its rows stored as not measured, and the series
        goes on. When `False`, the error is raised.
    extra_measurements : `Callable`, optional
        Builds more rows from the finished subset result and its noise outcome
        (the night metrics). They are stored with the others.
    record_forecast_rows : `bool`, optional
        When `True`, also store what the forecast check reads from a library
        stack: one weight for each member frame, the exposure-group rows and
        the calibration record (see `_forecast_record_measurements`). Only
        `stack_chosen_subset` can ask for it.

    Returns
    -------
    result : `SubsetStackResult`
        What was stacked and measured. A subset that cannot be measured is
        reported with the reason, not raised.

    Raises
    ------
    InvalidArgumentError
        If the subset's path would be the library stack's path, the output
        folder is not inside ``_subsets``, or the frames are too few.
    """
    library_folder = os.path.dirname(library_stack_path)
    output_path = subset_output_path(library_folder, label, output_folder)
    if os.path.abspath(output_path) == os.path.abspath(library_stack_path):
        raise InvalidArgumentError("A subset stack may never be written to the library stack's path.")
    nights = sorted({observing_night_id(frame.timestamp) for frame in frames if frame.timestamp is not None})
    result = SubsetStackResult(
        targetId=target.id, label=label, framesSubmitted=len(frames), observingNightIds=nights
    )
    outcome = None
    try:
        with stacking_slot():
            outcome = stack_frame_subset(
                target.id,
                list(frames),
                output_path,
                configuration=configuration,
                drivers=drivers,
                job_id=None if context is None else context.job_id,
            )
    except InvalidArgumentError:
        raise
    except Exception as error:
        if not keep_going:
            raise
        logger.exception("The subset stack %s failed; the series goes on", label)
        result.stack_error = f"{type(error).__name__}: {error}"
    result.stack_path = None if outcome is None else outcome.stack_path
    subject = _subset_subject(context, label, night_id) if context is not None else None
    membership: StackMembership | None = None
    membership_reason = ""
    noise: StackNoiseOutcome | None
    if outcome is None or outcome.stack_path is None:
        why = (
            f"the stacker failed ({result.stack_error})"
            if result.stack_error
            else "the stacker made no stack from these frames"
        )
        noise = StackNoiseOutcome(None, why)
        membership_reason = why
    else:
        apertures_path = os.path.join(library_folder, APERTURE_FILE_NAME)
        # A subset never chooses apertures: it reads the ones its library
        # stack chose, and says why when there are none or they are old.
        stored_apertures, apertures_reason = load_apertures_with_reason(apertures_path)
        if stored_apertures is not None:
            noise = measure_noise_safely(
                outcome.stack_path,
                outcome.registration_reference_frame,
                outcome.is_spectral,
                outcome.crop_offset,
                apertures_path,
                may_choose_apertures=False,
            )
        else:
            noise = StackNoiseOutcome(None, apertures_reason)
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
    if library is not None:
        result.expected_noise_ratio = expected_noise_ratio(
            library.effective_count, result.effective_frame_count
        )
    if context is not None and subject is not None:
        measurements = noise_measurements(noise, context, subject)
        measurements.extend(membership_count_measurements(membership, membership_reason, context, subject))
        # The noise the subset's own frames predict, stored beside the noise
        # that was measured, so the two can be read side by side, and the
        # stack's sky level for the unit check.
        expected, scale = scale_expected_noise(
            compute_expected_noise(
                membership,
                membership_reason,
                target.id,
                configuration,
                store=None if library is None else library.store,
                diagnostics=None if outcome is None else outcome.diagnostics,
                is_spectral=bool(outcome is not None and outcome.is_spectral),
            ),
            noise,
            None if outcome is None else outcome.diagnostics,
        )
        measurements.extend(expected_noise_measurements(expected, context, subject))
        measurements.extend(noise_ratio_measurements(noise, expected, context, subject))
        measurements.extend(normalization_scale_measurements(scale, context, subject))
        if outcome is not None and outcome.stack_path is not None:
            measurements.extend(
                stack_sky_level_measurements(
                    resolve_stack_sky_level(noise, outcome.stack_path), context, subject
                )
            )
        measurements.append(
            expected_ratio_measurement(
                context, subject, library, result, membership.membership_reason if membership else ""
            )
        )
        if extra_measurements is not None:
            measurements.extend(extra_measurements(result, noise))
        if record_forecast_rows:
            measurements.extend(
                _forecast_record_measurements(context, label, library_folder, membership, outcome)
            )
        result.rows_written = record_run_measurements(measurements, config=configuration)
        if record_forecast_rows and outcome is not None and outcome.stack_path is not None:
            # The calibration files the subset applied, filed under the
            # subset's own subject so the check can tell whether the two
            # stacks of a pair were calibrated alike. Its own call, so a
            # failure keeps the rows above.
            record_calibration_identities(
                context,
                outcome.stack_path,
                frames,
                outcome.diagnostics,
                config=configuration,
                subject_file_name=f"{SUBSET_FOLDER_NAME}/{label}/{label}.fits",
            )
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
        The library stack whose frames to use.
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
        The subsets, smallest first, and notes about steps left out or failed.
    """
    with subset_job_guard(target.id):
        library = read_library_run(target, library_stack_path, configuration)
        nights, undated = frames_by_night(library.members)
        notes: list[str] = list(library.notes)
        if undated:
            notes.append(f"{len(undated)} frame(s) have no timestamp and were left out of the series.")
        steps = plan_growth_steps(nights, maximum_steps)
        if not steps:
            notes.append(
                f"No subset has at least {MINIMUM_SUBSET_FRAMES} frames, so no subset stack was made."
            )
        stem = stack_stem(library_stack_path)
        subsets = []
        for index, step in enumerate(steps):
            report_progress(
                int(100 * index / len(steps)), f"Stacking subset {step.key} ({len(step.frames)} frames)"
            )
            subset = _stack_and_measure(
                target,
                step.frames,
                f"{stem}.{step.key}",
                library_stack_path,
                context,
                configuration=configuration,
                drivers=drivers,
                stacking_slot=stacking_slot,
                library=library,
                keep_going=True,
            )
            if subset.stack_error:
                notes.append(f"Subset {step.key} failed and was skipped: {subset.stack_error}")
            subsets.append(subset)
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
        The library stack whose frames to use.
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
    with subset_job_guard(target.id):
        return _run_night_contributions(
            target,
            library_stack_path,
            context=context,
            configuration=configuration,
            drivers=drivers,
            stacking_slot=stacking_slot,
        )


def _run_night_contributions(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
) -> NightContributionResult:
    """Do the work of `run_night_contributions` while the guard is held.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library_stack_path : `str`
        The library stack whose frames to use.
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
        One entry for each night.
    """
    library = read_library_run(target, library_stack_path, configuration)
    nights, undated = frames_by_night(library.members)
    notes: list[str] = list(library.notes)
    if undated:
        notes.append(
            f"{len(undated)} frame(s) have no timestamp. They stay in every leave-one-night-out stack."
        )
    stem = stack_stem(library_stack_path)
    shares, share_reason = night_weight_shares(library.weights)
    share_reason = library.weights_reason or share_reason
    contributions = []
    if len(nights) < 2:
        notes.append("The stack has fewer than two observing nights, so there is no night to leave out.")
    for index, night in enumerate(nights if len(nights) >= 2 else []):
        remaining = [frame for other, group in nights.items() if other != night for frame in group] + undated
        label = f"{stem}.without-{night}"
        check_subset_label(label)
        report_progress(
            int(100 * index / len(nights)), f"Stacking without the night {night} ({len(remaining)} frames)"
        )

        def night_rows(
            result: SubsetStackResult,
            noise: StackNoiseOutcome | None,
            night: str = night,
            label: str = label,
        ) -> list[QualityMeasurement]:
            """Build the night rows of one leave-out stack.

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
                The night's share, the noise without the night and its
                difference from the library stack's.
            """
            return night_measurements(
                context,
                label,
                night,
                shares.get(night),
                share_reason,
                result,
                noise,
                share_assumed_reason=library.assumed_reason,
                library_noise_sums=library.noise_sums,
            )

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
                    share_assumed_reason=library.assumed_reason,
                    library_noise_sums=library.noise_sums,
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
                library=library,
                keep_going=True,
                extra_measurements=night_rows,
            )
            if subset.stack_error:
                notes.append(
                    f"The stack without the night {night} failed and was skipped: {subset.stack_error}"
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
    *,
    share_assumed_reason: str = "",
    library_noise_sums: NoiseSums | None = None,
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
    share_assumed_reason : `str`, optional
        Why the weights behind the share are assumed. When it is not empty
        the share is stored as assumed.
    library_noise_sums : `NoiseSums`, optional
        The library stack's per-aperture sums, its reference flux and the
        identity of the apertures they were measured in. Without it the
        difference from the library stack's noise is not measured.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        Three rows on the leave-out stack's subject: the share, the noise
        without the night, and its paired difference from the library stack's.
    """
    if context is None:
        return []
    subject = _subset_subject(context, label, night_id)
    share_known = share is not None
    rows = [
        build_measurement(
            _SPEC_BY_NAME[STACKING_NIGHT_WEIGHT_SHARE],
            share,
            context,
            subject,
            reason=(share_assumed_reason if share_known else share_reason)
            or "the night's share is not known",
            status=MeasurementStatus.ASSUMED
            if share_known and share_assumed_reason
            else MeasurementStatus.MEASURED,
        )
    ]
    outcome = noise.result if noise is not None else None
    missing_reason = (
        (noise.reason if noise is not None else "") or result.noise_reason or "the noise was not measured"
    )
    if outcome is None:
        rows.append(
            build_measurement(
                _SPEC_BY_NAME[STACKING_NOISE_WITHOUT_NIGHT], None, context, subject, reason=missing_reason
            )
        )
        rows.append(
            build_measurement(
                _SPEC_BY_NAME[STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT],
                None,
                context,
                subject,
                reason=missing_reason,
            )
        )
        return rows
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
    difference = None
    difference_reason = ""
    if library_noise_sums is None:
        difference_reason = (
            "the library stack's per-aperture sums were not saved; stack the target again to save them"
        )
    elif (
        not library_noise_sums.aperture_identity
        or library_noise_sums.aperture_identity != outcome.aperture_identity
    ):
        difference_reason = (
            "the library stack was measured in other apertures than this stack "
            f"(identities {library_noise_sums.aperture_identity or 'unknown'} "
            f"and {outcome.aperture_identity}); "
            "stack the target again so the library stack is measured in the current apertures"
        )
    else:
        difference = paired_noise_difference(
            library_noise_sums.sums,
            library_noise_sums.reference_flux,
            library_noise_sums.aperture_identity,
            np.asarray(outcome.aperture_sums, dtype=float),
            outcome.reference_flux_adu,
            outcome.aperture_identity,
        )
        if difference is None:
            difference_reason = "too few apertures are usable on both stacks"
    rows.append(
        build_measurement(
            _SPEC_BY_NAME[STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT],
            None if difference is None else difference[0],
            context,
            subject,
            reason=difference_reason,
            uncertainty=None if difference is None else difference[1],
            sample_count=None if difference is None else difference[2],
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
    record_forecast_rows: bool = False,
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
        The target's library stack (or the path it will have; for spectral
        frames, the spectral library stack). Its folder holds the target's
        stored noise apertures and the ``_subsets`` folder.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    output_folder : `str`, optional
        A folder to write the stack to instead of ``_subsets/<label>``. It
        must lie inside the ``_subsets`` folder beside the library stack.
    record_forecast_rows : `bool`, optional
        Also store what the forecast check reads from a library stack (see
        `_forecast_record_measurements`): one weight for each member frame, the
        exposure-group rows and the calibration record. Defaults to `False`,
        which stores only the rows every subset stores.

    Returns
    -------
    result : `SubsetStackResult`
        The stack's path, its frame counts and its noise.
    """
    with subset_job_guard(target.id):
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
            record_forecast_rows=record_forecast_rows,
        )
