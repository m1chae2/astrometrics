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

The noise of the difference of two halves (`run_half_difference_noise`). The
library stack's frames are put in order of capture time and split into two
disjoint halves, the 1st, 3rd, 5th, ... frame and the 2nd, 4th, 6th, ... frame,
so both halves span the whole night. The library stack's registration
reference frame goes into both halves, so that Siril lines both up with it and
their pixels coincide. The two half stacks are subtracted, and the noise of
the difference is measured in the same empty apertures as the library stack's
noise. The difference cancels what the frames share (sky structure, faint
stars, nebula) and keeps the random noise, so it is stored beside the
empty-aperture noise and never replaces it. The rows sit on the library
stack's own subject, with the reference flux the normalized value divides by
and the shared frame's two weight shares. The same two half stacks also give
what the halves share at the scale of a few pixels and how that differs along
and across the pointing drift (`half_pattern.measure_half_pattern`, eight more
rows): the other side of the difference, which keeps what the difference
cancels. The two half stacks are deleted after the measurement. It costs two
stacks of half the frames each. The growth job (`run_noise_growth`) can run
it too, in the same lock; no job starts by itself after a stack.

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
import shutil
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
    HalfDifferenceNoiseResult,
    NightContribution,
    NightContributionResult,
    NoiseGrowthResult,
    SubsetStackResult,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    record_run_measurements,
)
from astrometricslib.pipelines.shared.registration_record import build_registration_record
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_EFFECTIVE_FRAME_COUNT,
    STACKING_FRAME_WEIGHT,
    STACKING_HALF_DIFFERENCE_APERTURE_SET_NUMBER,
    STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
    STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO,
    STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED,
    STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT,
    STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
    STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
    STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
    STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
    STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR,
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
    load_noise_sums,
    measure_difference_noise,
    paired_noise_difference,
)
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    APERTURE_FILE_NAME,
    load_apertures_with_reason,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import ExpectedNoiseOutcome
from astrometricslib.pipelines.stacking.post_processing.half_pattern import (
    PATTERN_ROW_NAMES,
    HalfPatternOutcome,
    half_pattern_measurements,
    measure_half_pattern,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import (
    aperture_set_measurement,
    frame_weight_measurements,
    group_summary_measurements,
    measure_noise_safely,
    membership_count_measurements,
    noise_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import (
    compute_expected_noise,
    expected_noise_measurements,
    frame_noise_inputs_from_store,
    group_reference_exposure_measurements,
    normalization_scale_measurements,
    resolve_stack_sky_level,
    scale_expected_noise,
    stack_sky_level_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.registration_shifts import drift_of_runs
from astrometricslib.pipelines.stacking.post_processing.stack_image import read_stack_plane
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
    subset_output_path,
)
from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_GROWTH_STEPS",
    "HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF",
    "GrowthStep",
    "HalfSplit",
    "LibraryRun",
    "difference_variance_factor",
    "expected_noise_ratio",
    "expected_ratio_measurement",
    "frames_by_night",
    "full_stack_noise_equivalent",
    "full_stack_noise_equivalent_from_model",
    "half_difference_measurements",
    "library_member_frames",
    "model_variance_ratio",
    "night_measurements",
    "night_weight_shares",
    "plan_growth_steps",
    "read_library_run",
    "run_half_difference_noise",
    "run_night_contributions",
    "run_noise_growth",
    "split_into_halves",
    "stack_chosen_subset",
    "subset_frame_subject_id",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

# How many subset stacks the growth series makes by default. Four points, with
# the library stack as the fifth, are enough to see whether the noise still
# falls like 1 / sqrt(frames) or has flattened, and each point costs one stack.
# A design choice; no data set decided it.
DEFAULT_GROWTH_STEPS = 4

# The fewest frames in each half of the half-difference noise, counting the
# registration reference frame that both halves hold. Sigma-clipping rejection
# needs at least `MINIMUM_SUBSET_FRAMES` (4) frames to leave a spread to judge
# by. With 8 or more, the one shared frame is at most an eighth of either
# half's weight when the weights are equal, a small effect that
# `full_stack_noise_equivalent` takes into account. A design choice; no data
# set decided it.
HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF = 8

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
    noise_sums : `tuple` or `None`
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
    noise_sums: tuple[np.ndarray, float, str] | None = None
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
        stem = os.path.splitext(os.path.basename(library_stack_path))[0]
        subsets = []
        for index, step in enumerate(steps):
            _report(int(100 * index / len(steps)), f"Stacking subset {step.key} ({len(step.frames)} frames)")
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
    stem = os.path.splitext(os.path.basename(library_stack_path))[0]
    shares, share_reason = night_weight_shares(library.weights)
    share_reason = library.weights_reason or share_reason
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
    library_noise_sums: tuple[np.ndarray, float, str] | None = None,
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
    library_noise_sums : `tuple`, optional
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
    elif not library_noise_sums[2] or library_noise_sums[2] != outcome.aperture_identity:
        difference_reason = (
            "the library stack was measured in other apertures than this stack "
            f"(identities {library_noise_sums[2] or 'unknown'} and {outcome.aperture_identity}); "
            "stack the target again so the library stack is measured in the current apertures"
        )
    else:
        difference = paired_noise_difference(
            library_noise_sums[0],
            library_noise_sums[1],
            library_noise_sums[2],
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


@dataclass(frozen=True)
class HalfSplit:
    """Two disjoint halves of a stack's frames and the frame both share.

    Parameters
    ----------
    odd : `tuple` [`FrameRecord`, ...]
        The 1st, 3rd, 5th, ... frame by capture time, and the shared frame
        when it is not one of them. In time order.
    even : `tuple` [`FrameRecord`, ...]
        The 2nd, 4th, 6th, ... frame, and the shared frame when it is not one
        of them.
    shared_path : `str`
        The path of the one frame that is in both halves.
    left_out : `int`
        How many frames had no timestamp and so were left out of both halves.
    """

    odd: tuple[FrameRecord, ...]
    even: tuple[FrameRecord, ...]
    shared_path: str
    left_out: int = 0


def split_into_halves(frames: Sequence[FrameRecord], shared_path: str) -> HalfSplit:
    """Split frames into two disjoint halves that alternate in time.

    The frames are put in order of capture time (ties broken by path). The
    1st, 3rd, 5th, ... go in the odd half and the 2nd, 4th, 6th, ... in the
    even half, so both halves span the whole night and a slow change in the
    sky or the seeing falls on both. With an odd number of frames the odd
    half has one more. The shared frame is then put in the half that lacks
    it, so it is in both. A frame with no timestamp cannot be placed in time
    and is left out of both halves.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames of the library stack.
    shared_path : `str`
        The path of the frame to put in both halves: the registration
        reference frame of the library stack.

    Returns
    -------
    split : `HalfSplit`
        The two halves, each in time order.

    Raises
    ------
    InvalidArgumentError
        If the shared frame is not among the dated frames.
    """
    dated = sorted(
        (frame for frame in frames if frame.timestamp is not None),
        key=lambda frame: (frame.timestamp or 0.0, frame.path),
    )
    shared = next((frame for frame in dated if frame.path == shared_path), None)
    if shared is None:
        raise InvalidArgumentError(
            f"The shared frame {shared_path!r} is not among the stack's frames that have a timestamp."
        )
    odd = dated[0::2]
    even = dated[1::2]
    if shared not in odd:
        odd = sorted([*odd, shared], key=lambda frame: (frame.timestamp or 0.0, frame.path))
    if shared not in even:
        even = sorted([*even, shared], key=lambda frame: (frame.timestamp or 0.0, frame.path))
    return HalfSplit(
        odd=tuple(odd), even=tuple(even), shared_path=shared_path, left_out=len(frames) - len(dated)
    )


def difference_variance_factor(
    odd_shares: dict[str, float] | None, even_shares: dict[str, float] | None
) -> float | None:
    """Give the variance of the halves' difference in one frame's variances.

    If every frame had the same noise sigma, a half with weight shares ``a``
    (adding up to 1 over its frames) would have the variance
    ``sigma^2 x sum(a^2)``, and the difference of the odd half (shares ``a``)
    and the even half (shares ``b``) would have ``sigma^2 x F``, with
    ``F = sum((a - b)^2)`` over all the frames of both halves. A frame in one
    half only has 0 in the other. A frame in both (the registration reference
    frame) lowers F by twice the product of its two shares, because its noise
    cancels in the difference.

    Parameters
    ----------
    odd_shares : `dict` [`str`, `float`] or `None`
        The odd half's weight share of each frame by path, or `None` when its
        weights are unknown.
    even_shares : `dict` [`str`, `float`] or `None`
        The even half's weight shares.

    Returns
    -------
    factor : `float` or `None`
        F, or `None` when a half's weights are unknown or F is not positive.
    """
    if odd_shares is None or even_shares is None:
        return None
    paths = set(odd_shares) | set(even_shares)
    factor = math.fsum((odd_shares.get(path, 0.0) - even_shares.get(path, 0.0)) ** 2 for path in paths)
    return factor if factor > 0 else None


def full_stack_noise_equivalent(
    difference_noise: float,
    library_effective_count: float | None,
    odd_shares: dict[str, float] | None,
    even_shares: dict[str, float] | None,
) -> float | None:
    """Estimate the library stack's random noise from the halves' difference.

    If every frame had the same noise sigma, a stack with weight shares ``s``
    (adding up to 1 over its frames) would have the variance
    ``sigma^2 x sum(s^2)``. The odd half has shares ``a``, the even half
    ``b``, and the library stack has ``sum(s^2) = 1 / N_eff,library`` (Kish's
    effective frame count). The difference of the halves has the variance
    ``sigma^2 x sum((a - b)^2)`` over all the frames of both halves (a frame
    in one half only has 0 in the other). So the library stack's random noise
    is the difference times ``1 / sqrt(N_eff,library x sum((a - b)^2))``. For
    two disjoint halves of N equal frames this is the difference divided by
    2. Frames differ in noise (the sky changes, the star-width weights are not
    noise weights), so this is an estimate and not an exact value.

    Parameters
    ----------
    difference_noise : `float`
        The noise of the difference, in any unit.
    library_effective_count : `float` or `None`
        The library stack's effective frame count.
    odd_shares : `dict` [`str`, `float`] or `None`
        The odd half's weight share of each frame by path, adding up to 1, or
        `None` when its weights are unknown.
    even_shares : `dict` [`str`, `float`] or `None`
        The even half's weight shares.

    Returns
    -------
    noise : `float` or `None`
        The estimate in the unit of ``difference_noise``, or `None` when a
        weight or the effective count is unknown.
    """
    if library_effective_count is None or library_effective_count <= 0:
        return None
    variance_factor = difference_variance_factor(odd_shares, even_shares)
    if variance_factor is None:
        return None
    return difference_noise / math.sqrt(library_effective_count * variance_factor)


def full_stack_noise_equivalent_from_model(difference_noise: float, ratio: float | None) -> float | None:
    """Estimate the library stack's noise with per-frame model variances.

    Parameters
    ----------
    difference_noise : `float`
        The noise of the difference, in any unit.
    ratio : `float` or `None`
        The difference's variance in units of the library stack's variance
        (see `model_variance_ratio`).

    Returns
    -------
    noise : `float` or `None`
        ``difference_noise / sqrt(ratio)``, in the unit of
        ``difference_noise``, or `None` when the ratio is unknown or not
        positive.
    """
    if ratio is None or ratio <= 0:
        return None
    return difference_noise / math.sqrt(ratio)


def model_variance_ratio(
    odd_shares: dict[str, float] | None,
    even_shares: dict[str, float] | None,
    library_shares: dict[str, float] | None,
    frame_variances: dict[str, float],
) -> float | None:
    """Give the difference's variance in units of the library stack's variance.

    Each frame ``i`` has its own noise variance ``v_i`` (from its sky and the
    camera's read noise). With the weight shares ``a`` (odd half), ``b``
    (even half) and ``s`` (library stack), all adding up to 1 over their
    frames, the difference of the halves has the variance
    ``sum((a_i - b_i)^2 v_i)`` and the library stack ``sum(s_i^2 v_i)``. A
    frame missing from a set has share 0 in it. The ratio is the first
    divided by the second. The library stack's noise is the difference's
    noise divided by the square root of the ratio. When every ``v_i`` is the
    same, the ratio is ``N_eff,library x F``, the number
    `full_stack_noise_equivalent` divides by.

    Parameters
    ----------
    odd_shares, even_shares : `dict` [`str`, `float`] or `None`
        The halves' weight shares by frame path.
    library_shares : `dict` [`str`, `float`] or `None`
        The library stack's weight shares by frame path.
    frame_variances : `dict` [`str`, `float`]
        Each frame's noise variance by path, in one unit.

    Returns
    -------
    ratio : `float` or `None`
        The ratio, or `None` when a set of shares is unknown, a frame has no
        variance, or a variance sum is not positive.
    """
    if odd_shares is None or even_shares is None or library_shares is None:
        return None
    paths = set(odd_shares) | set(even_shares) | set(library_shares)
    if any(path not in frame_variances for path in paths):
        return None
    difference_variance = math.fsum(
        (odd_shares.get(path, 0.0) - even_shares.get(path, 0.0)) ** 2 * frame_variances[path]
        for path in paths
    )
    library_variance = math.fsum(library_shares.get(path, 0.0) ** 2 * frame_variances[path] for path in paths)
    if difference_variance <= 0 or library_variance <= 0:
        return None
    return difference_variance / library_variance


def _normalized_shares(weights: dict[str, float] | None) -> dict[str, float] | None:
    """Scale a dictionary of weights so they add up to 1.

    Parameters
    ----------
    weights : `dict` [`str`, `float`] or `None`
        Weights by frame path.

    Returns
    -------
    shares : `dict` [`str`, `float`] or `None`
        The shares, or `None` when there are no weights or they add up to
        nothing.
    """
    if not weights:
        return None
    total = math.fsum(weights.values())
    if total <= 0:
        return None
    return {path: weight / total for path, weight in weights.items()}


def _model_ratio_for_library(
    library: LibraryRun,
    target_id: str,
    configuration: Any,
    odd_shares: dict[str, float] | None,
    even_shares: dict[str, float] | None,
    exposure_scales: dict[str, float] | None = None,
) -> tuple[float | None, str]:
    """Work out `model_variance_ratio` from stored sky rates and profiles.

    A frame folded into an exposure group of another length is multiplied by
    ``k_i`` (about the group's reference exposure over the frame's exposure)
    before it is averaged, so its variance in the stack's units is ``k_i``
    squared times its own (see `FrameNoiseInput.exposure_scale`). Without the
    scales every frame would be treated as unfolded.

    Parameters
    ----------
    library : `LibraryRun`
        What the library stack stored (members, weights, the store).
    target_id : `str`
        The target.
    configuration : `AppConfiguration`
        Where the camera profiles live.
    odd_shares, even_shares : `dict` [`str`, `float`] or `None`
        The halves' weight shares by frame path.
    exposure_scales : `dict` [`str`, `float`], optional
        The ``k_i`` of each frame by path, read from the half stack that
        holds the frame (a half works out its own exposure groups, and the
        library stack's own scales are not stored for each frame). A frame
        with no entry gets 1. For a frame of an unfolded group the half's
        scale and the library stack's are both exactly 1.

    Returns
    -------
    ratio, reason : `tuple` [`float` or `None`, `str`]
        The ratio, or `None` and why there is none.
    """
    library_shares = _normalized_shares(library.weight_by_path)
    if library_shares is None:
        return None, library.weights_reason or "the library stack's frame weights are unknown"
    if odd_shares is None or even_shares is None:
        return None, "a half's weights are unknown"
    scales = exposure_scales or {}
    inputs = frame_noise_inputs_from_store(
        library.members,
        [1.0] * len(library.members),
        target_id,
        configuration,
        library.store,
        exposure_scales=[scales.get(frame.path, 1.0) for frame in library.members],
    )
    if isinstance(inputs, ExpectedNoiseOutcome):
        return None, inputs.reason
    variances = {
        frame.path: item.exposure_scale**2
        * (item.sky_electrons + item.read_noise_electrons**2)
        / item.gain_electrons_per_adu**2
        for frame, item in zip(library.members, inputs, strict=True)
    }
    ratio = model_variance_ratio(odd_shares, even_shares, library_shares, variances)
    if ratio is None:
        return None, "a frame of the halves or the library stack has no modeled noise, or a variance is zero"
    return ratio, ""


def _weight_shares(membership: StackMembership | None) -> dict[str, float] | None:
    """Give each member's share of a stack's total weight.

    Parameters
    ----------
    membership : `StackMembership` or `None`
        The stack's members, or `None`.

    Returns
    -------
    shares : `dict` [`str`, `float`] or `None`
        The weight share of each member frame by path, adding up to 1, or
        `None` when there are no members or any weight is unknown.
    """
    if membership is None or not membership.members:
        return None
    weights = normalized_weights(membership)
    if any(weight is None for weight in weights):
        return None
    total = math.fsum(weight for weight in weights if weight is not None)
    if total <= 0:
        return None
    return {
        member.frame.path: weight / total
        for member, weight in zip(membership.members, weights, strict=True)
        if weight is not None
    }


def half_difference_measurements(
    context: PipelineRunContext,
    subject: QualitySubject,
    noise: StackNoiseOutcome | None,
    reason: str,
    note: str,
    counts: tuple[float | None, float | None],
    counts_reason: str,
    variance_factor: float | None = None,
    shared_fractions: tuple[float | None, float | None] = (None, None),
    model_ratio: float | None = None,
    model_ratio_reason: str = "",
    pattern: HalfPatternOutcome | None = None,
) -> list[QualityMeasurement]:
    """Build the half-difference rows of the library stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The library stack.
    noise : `StackNoiseOutcome` or `None`
        The noise of the difference, or `None` when no difference was made.
    reason : `str`
        Why there is no noise value. Used when ``noise`` holds no result.
    note : `str`
        What was done, kept on each measured row.
    counts : `tuple` [`float` or `None`, `float` or `None`]
        The odd and even halves' effective frame counts.
    counts_reason : `str`
        Why a count is missing, when one is.
    variance_factor : `float`, optional
        The difference's variance in units of one frame's variance (see
        `difference_variance_factor`).
    shared_fractions : `tuple` [`float` or `None`, `float` or `None`], optional
        The shared frame's share of the odd and of the even half's weight.
    model_ratio : `float`, optional
        The difference's variance in units of the library stack's variance,
        with each frame's own modeled noise (see `model_variance_ratio`).
    model_ratio_reason : `str`, optional
        Why there is no ``model_ratio``.
    pattern : `HalfPatternOutcome`, optional
        What the two halves share at a few pixels (see
        `half_pattern.measure_half_pattern`). Left out, its eight rows are
        not measured, with the reason of the difference.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The noise in stack units, the normalized noise, the reference flux it
        is divided by, the two halves' effective frame counts, the variance
        factor, the model variance ratio and the shared frame's two weight
        shares, then the eight pattern rows. Each is not measured, with the
        reason, when its value is missing.
    """
    result = noise.result if noise is not None else None
    missing = reason or (noise.reason if noise is not None else "") or "the difference was not measured"
    if result is None:
        noise_rows = [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=missing)
            for name in (
                STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT,
                STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED,
                STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
            )
        ]
        noise_rows.append(
            aperture_set_measurement(
                STACKING_HALF_DIFFERENCE_APERTURE_SET_NUMBER, None, context, subject, missing
            )
        )
    else:
        noise_rows = [
            build_measurement(
                _SPEC_BY_NAME[STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT],
                result.noise_adu,
                context,
                subject,
                uncertainty=result.noise_uncertainty_adu,
                sample_count=result.aperture_count,
            ),
            build_measurement(
                _SPEC_BY_NAME[STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED],
                result.normalized_noise,
                context,
                subject,
                uncertainty=result.normalized_uncertainty,
                sample_count=result.aperture_count,
            ),
            build_measurement(
                _SPEC_BY_NAME[STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT],
                result.reference_flux_adu,
                context,
                subject,
                uncertainty=result.reference_uncertainty_adu,
                sample_count=result.reference_star_count,
            ),
        ]
        kept_note = " ".join(
            part for part in (note, result.position_note, result.aperture_definition_note) if part
        )
        if kept_note:
            noise_rows = [row.model_copy(update={"reason": kept_note}) for row in noise_rows]
        noise_rows.append(
            aperture_set_measurement(
                STACKING_HALF_DIFFERENCE_APERTURE_SET_NUMBER, result.aperture_identity, context, subject
            )
        )
    count_rows = [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=counts_reason or missing or "the half's effective frame count is unknown",
        )
        for name, value in zip(
            (
                STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
                STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
            ),
            counts,
            strict=True,
        )
    ]
    factor_row = build_measurement(
        _SPEC_BY_NAME[STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR],
        variance_factor,
        context,
        subject,
        reason=counts_reason or missing or "the halves' weights are unknown",
    )
    model_row = build_measurement(
        _SPEC_BY_NAME[STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO],
        model_ratio,
        context,
        subject,
        reason=model_ratio_reason or missing or "the modeled noise of the frames is unknown",
    )
    share_rows = [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=counts_reason
            or (reason if result is None else "the shared frame is not among the frames the half stacked"),
        )
        for name, value in zip(
            (
                STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
                STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
            ),
            shared_fractions,
            strict=True,
        )
    ]
    pattern_rows = _pattern_rows_safely(
        context, subject, pattern, missing if result is None else "the pattern was not measured", note
    )
    return [*noise_rows, *count_rows, factor_row, model_row, *share_rows, *pattern_rows]


def _pattern_rows_safely(
    context: PipelineRunContext,
    subject: QualitySubject,
    pattern: HalfPatternOutcome | None,
    reason: str,
    note: str,
) -> list[QualityMeasurement]:
    """Build the half-stack pattern rows, never raising.

    The pattern rows are a by-product of the half-stack difference: an error
    in building them must not lose the nine rows of the difference itself.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The library stack.
    pattern : `HalfPatternOutcome` or `None`
        What the pattern measurement gave, or `None` when it was not tried.
    reason : `str`
        Why there is no pattern, when there is none.
    note : `str`
        What was done, kept on each measured row.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        The pattern rows, or one not-measured row for each pattern metric with
        the reason that the rows could not be built.
    """
    try:
        return half_pattern_measurements(context, subject, pattern, reason, note)
    except Exception as error:  # a by-product: its failure must not lose the noise rows
        logger.exception("The shared fine-scale structure rows of the half stacks could not be built")
        failed = f"the pattern rows could not be built ({type(error).__name__})"
        return [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=failed)
            for name in PATTERN_ROW_NAMES
        ]


def _half_pattern_safely(
    odd_stack: Any,
    even_stack: Any,
    odd_plane: np.ndarray | None,
    even_plane: np.ndarray | None,
    library: LibraryRun,
    variance_factor: float | None,
    shared_fractions: tuple[float | None, float | None],
) -> HalfPatternOutcome:
    """Measure what the two half stacks share, never raising.

    The drift of the frames comes from the registration results of the two
    half-stack runs, which share one reference frame, and the capture times
    of the library stack's frames.

    Parameters
    ----------
    odd_stack, even_stack : `Any`
        The two half-stack outcomes (their diagnostics).
    odd_plane, even_plane : `numpy.ndarray` or `None`
        The two half stacks' images, read once by the caller and shared with
        the difference noise, or `None` when a file held no pixels.
    library : `LibraryRun`
        The library stack's frames and effective frame count.
    variance_factor : `float` or `None`
        The difference's variance factor.
    shared_fractions : `tuple` [`float` or `None`, `float` or `None`]
        The shared frame's weight shares in the two halves.

    Returns
    -------
    outcome : `HalfPatternOutcome`
        The numbers, or the reason there are none. A failure to read the
        stacks or to measure is returned as a reason.
    """
    try:
        if odd_plane is None or even_plane is None:
            return HalfPatternOutcome(None, "a half stack file holds no pixels")
        drift = drift_of_runs(
            [build_registration_record(stack.diagnostics, (0, 0), None) for stack in (odd_stack, even_stack)],
            {frame.path: frame.timestamp for frame in library.members},
            (odd_plane.shape[1], odd_plane.shape[0]),
        )
        return measure_half_pattern(
            odd_plane,
            even_plane,
            variance_factor=variance_factor,
            shared_fractions=shared_fractions,
            effective_count=library.effective_count,
            drift_axis_deg=drift.direction_deg,
            drift_extent_px=drift.extent_px,
            drift_reason=drift.direction_reason,
        )
    except Exception as error:  # a by-product: its failure must not lose the noise
        logger.exception("The shared fine-scale structure of the half stacks could not be measured")
        return HalfPatternOutcome(None, f"the measurement failed: {type(error).__name__}")


def _delete_half_folder(library_stack_path: str, label: str) -> None:
    """Delete a half stack's folder from ``_subsets``.

    Parameters
    ----------
    library_stack_path : `str`
        The library stack. Only a folder inside its ``_subsets`` folder is
        ever deleted.
    label : `str`
        The half's label, which names its folder.
    """
    check_subset_label(label)
    folder = os.path.join(os.path.dirname(library_stack_path), SUBSET_FOLDER_NAME, label)
    try:
        shutil.rmtree(folder, ignore_errors=False)
    except FileNotFoundError:
        return
    except OSError as error:
        logger.warning("The half stack folder %s could not be deleted: %s", folder, error)


def run_half_difference_noise(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    keep_halves: bool = False,
) -> HalfDifferenceNoiseResult:
    """Measure the noise of the difference of two halves of the library stack.

    The frames of the library stack are split into two disjoint halves that
    alternate in time (`split_into_halves`), with the library stack's
    registration reference frame in both. Each half is stacked into the
    target's ``_subsets`` folder with the library stack's weighting and
    rejection, without the edge trim and the frame quarantine. The noise of
    the difference of the two stacks is measured in the target's stored empty
    apertures (`difference_noise.measure_difference_noise`), and the rows are
    stored on the library stack's own subject: the difference noise in stack
    units and normalized, the reference flux the normalized value divides by,
    each half's effective frame count, the variance factor, the variance
    ratio with each frame's modeled noise, and the two shares the shared
    frame has in the halves' weights (so the factor can be split back). The
    registration reference is handed to the stacker for both halves, so a
    half cannot choose its own; a half that reports another reference is
    still refused. The halves are deleted afterwards unless ``keep_halves``
    is set. Neither this job nor the growth job starts by itself after a
    stack.

    Nothing is measured, and each row says why, when: the stack is
    spectral; the library stack has no recorded registration reference frame
    or noise sums; the apertures are missing or of another identity than the
    library stack's; a half would have fewer than
    `HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF` frames; Siril fails; or the
    halves were lined up with different reference frames. No other number is
    put in the place of a missing one.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library_stack_path : `str`
        The library stack whose frames to split.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    keep_halves : `bool`, optional
        Keep the two half stacks in ``_subsets`` after the measurement.

    Returns
    -------
    result : `HalfDifferenceNoiseResult`
        The numbers, or the reason there are none.
    """
    with subset_job_guard(target.id):
        return _run_half_difference_noise(
            target,
            library_stack_path,
            context=context,
            configuration=configuration,
            drivers=drivers,
            stacking_slot=stacking_slot,
            keep_halves=keep_halves,
        )


def _run_half_difference_noise(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    keep_halves: bool,
) -> HalfDifferenceNoiseResult:
    """Do the work of `run_half_difference_noise` while the guard is held.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library_stack_path : `str`
        The library stack whose frames to split.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    configuration : `AppConfiguration`
        The settings.
    drivers : `Drivers`, optional
        The stacking program.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    keep_halves : `bool`
        Keep the two half stacks after the measurement.

    Returns
    -------
    result : `HalfDifferenceNoiseResult`
        The numbers, or the reason there are none.
    """
    result = HalfDifferenceNoiseResult(targetId=target.id, libraryStackPath=library_stack_path)
    subject = context.stack_subject(os.path.basename(library_stack_path)) if context is not None else None

    def finish(
        reason: str,
        noise: StackNoiseOutcome | None = None,
        note: str = "",
        counts: tuple[float | None, float | None] = (None, None),
        counts_reason: str = "",
        variance_factor: float | None = None,
        shared_fractions: tuple[float | None, float | None] = (None, None),
        model_ratio: float | None = None,
        model_ratio_reason: str = "",
        pattern: HalfPatternOutcome | None = None,
    ) -> HalfDifferenceNoiseResult:
        """Store the rows and give back the result.

        Parameters
        ----------
        reason : `str`
            Why there is no noise value. Empty when there is one.
        noise : `StackNoiseOutcome`, optional
            The noise of the difference.
        note : `str`, optional
            What was done, kept on each measured row.
        counts : `tuple`, optional
            The halves' effective frame counts.
        counts_reason : `str`, optional
            Why a count is missing.
        variance_factor : `float`, optional
            The difference's variance factor.
        shared_fractions : `tuple`, optional
            The shared frame's share of each half's weight.
        model_ratio : `float`, optional
            The difference's variance in the library stack's variance, with
            each frame's modeled noise.
        model_ratio_reason : `str`, optional
            Why there is no ``model_ratio``.
        pattern : `HalfPatternOutcome`, optional
            What the halves share at a few pixels.

        Returns
        -------
        result : `HalfDifferenceNoiseResult`
            The finished result.
        """
        result.reason = reason
        if context is not None and subject is not None:
            rows = half_difference_measurements(
                context,
                subject,
                noise,
                reason,
                note,
                counts,
                counts_reason,
                variance_factor,
                shared_fractions,
                model_ratio,
                model_ratio_reason,
                pattern,
            )
            result.rows_written = record_run_measurements(rows, config=configuration)
        return result

    library = read_library_run(target, library_stack_path, configuration)
    result.notes.extend(library.notes)
    if any(frame_is_spectral(frame) for frame in library.members):
        return finish("the half-difference noise is measured for imaging stacks only")
    if library.noise_sums is None:
        return finish(
            "the library stack's per-aperture noise sums were not saved, so the apertures it was measured "
            "in cannot be checked; stack the target again"
        )
    summary = getattr(target.stacking, "quality_summary", None)
    reference_path = getattr(getattr(summary, "stacking_metrics", None), "registration_reference_frame", None)
    if not reference_path:
        return finish(
            "the library stack's registration reference frame is not recorded, so the two halves cannot "
            "be lined up on one reference; stack the target again"
        )
    try:
        split = split_into_halves(library.members, reference_path)
    except InvalidArgumentError as error:
        return finish(error.message)
    result.shared_frame = split.shared_path
    result.odd_half_frames = len(split.odd)
    result.even_half_frames = len(split.even)
    if split.left_out:
        result.notes.append(f"{split.left_out} frame(s) have no timestamp and were left out of both halves.")
    smaller = min(len(split.odd), len(split.even))
    if smaller < HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF:
        return finish(
            f"a half would have {smaller} frames, fewer than the {HALF_DIFFERENCE_MINIMUM_FRAMES_PER_HALF} "
            "the half-difference noise needs"
        )
    library_folder = os.path.dirname(library_stack_path)
    apertures_path = os.path.join(library_folder, APERTURE_FILE_NAME)
    stored_apertures, apertures_reason = load_apertures_with_reason(apertures_path)
    if stored_apertures is None:
        return finish(apertures_reason)

    stem = os.path.splitext(os.path.basename(library_stack_path))[0]
    stacked: dict[str, Any] = {}
    labels = {"odd": f"{stem}.half-odd", "even": f"{stem}.half-even"}
    try:
        for index, (name, half_frames) in enumerate((("odd", split.odd), ("even", split.even))):
            _report(int(40 * index), f"Stacking the {name} half ({len(half_frames)} frames)")
            output_path = subset_output_path(library_folder, labels[name], None)
            try:
                with stacking_slot():
                    stacked[name] = stack_frame_subset(
                        target.id,
                        list(half_frames),
                        output_path,
                        configuration=configuration,
                        drivers=drivers,
                        job_id=None if context is None else context.job_id,
                        trim_edges=False,
                        reference_frame_path=split.shared_path,
                    )
            except Exception as error:
                logger.exception("The %s half stack of %s failed", name, target.id)
                return finish(f"the stacker failed on the {name} half ({type(error).__name__}: {error})")
            if stacked[name].stack_path is None:
                return finish(f"the stacker made no stack from the {name} half")
        references = {name: outcome.registration_reference_frame for name, outcome in stacked.items()}
        if references["odd"] is None or references["even"] is None:
            return finish(
                "the halves cannot be registered on one reference: Siril's reference frame is unknown"
            )
        if not (references["odd"] == references["even"] == split.shared_path):
            return finish(
                "the halves cannot be registered on one reference: they were lined up with "
                f"{os.path.basename(references['odd'])} and {os.path.basename(references['even'])}, "
                f"not both with the library stack's reference {os.path.basename(split.shared_path)}"
            )
        _report(80, "Measuring the noise of the difference")
        try:
            # Each half is read once, as 32-bit, and shared by the difference
            # noise and the pattern measurement.
            odd_plane = read_stack_plane(stacked["odd"].stack_path, np.float32)
            even_plane = read_stack_plane(stacked["even"].stack_path, np.float32)
            noise = measure_difference_noise(
                stacked["odd"].stack_path,
                stacked["even"].stack_path,
                split.shared_path,
                apertures_path,
                first_plane=odd_plane,
                second_plane=even_plane,
            )
        except Exception as error:  # a measurement failure is stored as a reason, never raised
            logger.exception("The noise of the half difference of %s could not be measured", target.id)
            return finish(f"the difference could not be measured ({type(error).__name__}: {error})")
        if noise.result is None:
            return finish(noise.reason or "the difference was not measured")
        if not library.noise_sums[2] or library.noise_sums[2] != noise.result.aperture_identity:
            return finish(
                "the library stack was measured in other apertures than the halves "
                f"(identities {library.noise_sums[2] or 'unknown'} and {noise.result.aperture_identity}); "
                "stack the target again so the library stack is measured in the current apertures"
            )
        memberships: dict[str, StackMembership | None] = {}
        counts_reason = ""
        for name, half_frames in (("odd", split.odd), ("even", split.even)):
            try:
                memberships[name] = determine_stack_members(
                    half_frames, stacked[name].diagnostics, stacked[name].stack_weight
                )
            except Exception as error:  # a by-product: its failure must not lose the noise
                logger.exception("The member frames of the %s half stack could not be worked out", name)
                memberships[name] = None
                counts_reason = f"the member frames could not be worked out: {type(error).__name__}"
        shares = {name: _weight_shares(memberships[name]) for name in ("odd", "even")}
        counts = tuple(
            effective_frame_count(list(shares[name].values())) if shares[name] is not None else None
            for name in ("odd", "even")
        )
        if not counts_reason and any(count is None for count in counts):
            counts_reason = next(
                (
                    memberships[name].membership_reason
                    for name in ("odd", "even")
                    if memberships[name] is not None and memberships[name].membership_reason
                ),
                "the half's weights are unknown (Siril weighted the frames itself)",
            )
        note = (
            f"The odd half holds {len(split.odd)} frames and the even half {len(split.even)}; both hold "
            f"{os.path.basename(split.shared_path)}, the library stack's registration reference frame."
        )
        result.odd_half_effective_frame_count, result.even_half_effective_frame_count = counts
        result.difference_noise_stack_unit = noise.result.noise_adu
        result.difference_noise_normalized = noise.result.normalized_noise
        result.difference_noise_normalized_uncertainty = noise.result.normalized_uncertainty
        result.reference_flux_stack_unit = noise.result.reference_flux_adu
        result.aperture_count = noise.result.aperture_count
        variance_factor = difference_variance_factor(shares["odd"], shares["even"])
        result.variance_factor = variance_factor
        equivalent = full_stack_noise_equivalent(
            noise.result.normalized_noise, library.effective_count, shares["odd"], shares["even"]
        )
        result.full_stack_equivalent_normalized = equivalent
        shared_fractions = tuple(
            shares[name].get(split.shared_path) if shares[name] is not None else None
            for name in ("odd", "even")
        )
        result.shared_frame_odd_weight_fraction, result.shared_frame_even_weight_fraction = shared_fractions
        try:
            half_scales = {
                member.frame.path: member.exposure_scale
                for membership in memberships.values()
                if membership is not None
                for member in membership.members
            }
            model_ratio, model_ratio_reason = _model_ratio_for_library(
                library, target.id, configuration, shares["odd"], shares["even"], half_scales
            )
        except Exception as error:  # a by-product: its failure must not lose the noise
            logger.exception("The modeled variance ratio of %s could not be worked out", target.id)
            model_ratio, model_ratio_reason = None, f"the modeled noise failed: {type(error).__name__}"
        result.model_variance_ratio = model_ratio
        result.full_stack_equivalent_model_normalized = full_stack_noise_equivalent_from_model(
            noise.result.normalized_noise, model_ratio
        )
        result.notes.append(note)
        pattern = _half_pattern_safely(
            stacked["odd"],
            stacked["even"],
            odd_plane,
            even_plane,
            library,
            variance_factor,
            shared_fractions,
        )
        del odd_plane, even_plane
        if pattern.result is not None:
            result.shared_fine_scale_variance_ratio = pattern.result.variance_ratio
            result.walking_noise_correlation_excess = pattern.result.excess
            result.walking_noise_correlation_excess_error = pattern.result.excess_error
        return finish(
            "",
            noise,
            note,
            counts,
            counts_reason,
            variance_factor,
            shared_fractions,
            model_ratio,
            model_ratio_reason,
            pattern,
        )
    finally:
        if not keep_halves:
            for label in labels.values():
                _delete_half_folder(library_stack_path, label)
