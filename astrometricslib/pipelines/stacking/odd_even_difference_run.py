"""Purpose: Measure the noise of the difference of two half stacks.

Description: This job stacks the library stack's frames in two halves and
measures the noise of their difference. It stacks some of the frames of a
target's library stack into the target's ``_subsets`` folder and measures the
result in the same empty sky apertures as the library stack (task E's
`stack_noise.measure_stack_noise`), on the same flux scale. It never changes
the library stack, its previews, its previous-stack copy or its
``_inputs.json`` (see `subset_stack.py`). While it runs, no library stack of
the target and no other subset job of the target can run
(`target_stack_guard.py`).

The noise of the difference of two halves
(`run_odd_even_difference_noise`). The library stack's frames are put in order
of capture time and split into two disjoint halves, the 1st, 3rd, 5th, ...
frame and the 2nd, 4th, 6th, ... frame,
so both halves span the whole night. The library stack's registration
reference frame goes into both halves, so that Siril lines both up with it and
their pixels coincide (`HalfStacks` stacks the halves and checks that
reference; the bias test in `odd_even_bias.py` uses it too). The two half
stacks are subtracted, and the noise of the difference is measured in the same
empty apertures as the library stack's noise. The difference cancels what the
frames share (sky structure, faint stars, nebula) and keeps the random noise,
so it is stored beside the empty-aperture noise and never replaces it. The
rows sit on the library stack's own subject, with the reference flux the
normalized value divides by and the shared frame's two weight shares. The same
two half stacks also give what the halves share at the scale of a few pixels
and how that differs along and across the pointing drift
(`half_pattern.measure_half_pattern`, eight more rows): the other side of the
difference, which keeps what the difference cancels. The two half stacks are
deleted after the measurement. It costs two stacks of half the frames each.
The growth job (`subset_noise_run.run_noise_growth`) can run it too, in the
same lock; no job starts by itself after a stack.

What the run found is gathered in an `OddEvenResult`, which
`odd_even_difference_measurements` turns into quality rows. A run that stops
early builds one with a reason only. A storage failure is logged and never
fails the job. Nothing here reads the clock: the caller supplies the
measurement time.
"""

import logging
import math
import os
import shutil
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject
from astrometricslib.models.quality.subset_stacks import OddEvenDifferenceNoiseResult
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    record_run_measurements,
)
from astrometricslib.pipelines.shared.registration_record import build_registration_record
from astrometricslib.pipelines.shared.target_position import parse_target_position
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_METRICS,
    STACKING_ODD_EVEN_DIFFERENCE_APERTURE_RECORD_COUNT,
    STACKING_ODD_EVEN_DIFFERENCE_APERTURE_SET_NUMBER,
    STACKING_ODD_EVEN_DIFFERENCE_COLUMN_AVERAGE_SCATTER_STACK_UNIT,
    STACKING_ODD_EVEN_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
    STACKING_ODD_EVEN_DIFFERENCE_MODEL_VARIANCE_RATIO,
    STACKING_ODD_EVEN_DIFFERENCE_NOISE_NORMALIZED,
    STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT,
    STACKING_ODD_EVEN_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
    STACKING_ODD_EVEN_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
    STACKING_ODD_EVEN_DIFFERENCE_ROW_AVERAGE_SCATTER_STACK_UNIT,
    STACKING_ODD_EVEN_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
    STACKING_ODD_EVEN_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
    STACKING_ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR,
    STACKING_ODD_EVEN_SUM_COLUMN_AVERAGE_SCATTER_STACK_UNIT,
    STACKING_ODD_EVEN_SUM_ROW_AVERAGE_SCATTER_STACK_UNIT,
)
from astrometricslib.pipelines.stacking.post_processing.difference_noise import measure_difference_noise
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
from astrometricslib.pipelines.stacking.post_processing.odd_even_apertures import (
    LinePatternOutcome,
    build_aperture_records,
    measure_line_pattern,
    save_odd_even_aperture_record,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import aperture_set_measurement
from astrometricslib.pipelines.stacking.post_processing.record_science import frame_noise_inputs_from_store
from astrometricslib.pipelines.stacking.post_processing.registration_shifts import drift_of_runs
from astrometricslib.pipelines.stacking.post_processing.stack_image import read_stack_plane
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMembership,
    determine_stack_members,
    effective_frame_count,
    normalized_weights,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import StackNoiseOutcome
from astrometricslib.pipelines.stacking.subset_noise_run import (
    LibraryRun,
    read_library_run,
    report_progress,
)
from astrometricslib.pipelines.stacking.subset_stack import (
    SUBSET_FOLDER_NAME,
    check_subset_label,
    stack_frame_subset,
    stack_stem,
    subset_output_path,
)
from astrometricslib.pipelines.stacking.target_stack_guard import subset_job_guard

logger = logging.getLogger(__name__)

__all__ = [
    "ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF",
    "HalfSplit",
    "HalfStacks",
    "OddEvenDetail",
    "OddEvenResult",
    "delete_half_folder",
    "detail_measurements",
    "difference_variance_factor",
    "full_stack_noise_equivalent",
    "full_stack_noise_equivalent_from_model",
    "model_variance_ratio",
    "odd_even_difference_measurements",
    "run_odd_even_difference_noise",
    "split_into_halves",
    "weight_shares",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

# The fewest frames in each half of the odd–even difference noise, counting the
# registration reference frame that both halves hold. Sigma-clipping rejection
# needs at least `MINIMUM_SUBSET_FRAMES` (4) frames to leave a spread to judge
# by. With 8 or more, the one shared frame is at most an eighth of either
# half's weight when the weights are equal, a small effect that
# `full_stack_noise_equivalent` takes into account. A design choice; no data
# set decided it.
ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF = 8


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


def weight_shares(membership: StackMembership | None) -> dict[str, float] | None:
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


@dataclass(frozen=True)
class OddEvenDetail:
    """Where the odd–even difference's values sit, and its line pattern.

    Parameters
    ----------
    record_count : `int` or `None`
        How many apertures the per-aperture record holds, or `None` when none
        was written.
    record_path : `str` or `None`
        The per-aperture record file.
    record_reason : `str`
        Why there is no record, when there is none.
    line_pattern : `LinePatternOutcome` or `None`
        The row and column average scatters, or the reason there are none.
        `None` when the measurement was not tried.
    """

    record_count: int | None = None
    record_path: str | None = None
    record_reason: str = ""
    line_pattern: LinePatternOutcome | None = None


def _target_sky_degrees(target: Target) -> tuple[float, float] | None:
    """Give the target's stored position in degrees, if it has one.

    Parameters
    ----------
    target : `Target`
        The target.

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        (RA, Dec) in degrees, or `None` when the target has no usable
        position. A position taken from the center of an image
        (``position_unverified``) is still returned: it is where the camera
        looked.
    """
    try:
        coordinate = parse_target_position(target)
    except Exception:  # a by-product: no position only drops one column of the record
        logger.exception("The position of %s could not be read", target.id)
        return None
    if coordinate is None:
        return None
    return float(coordinate.ra.deg), float(coordinate.dec.deg)


def _aperture_detail_safely(
    library_stack_path: str,
    noise: StackNoiseOutcome,
    odd_plane: np.ndarray | None,
    even_plane: np.ndarray | None,
) -> OddEvenDetail:
    """Write the per-aperture record and measure the line pattern safely.

    Both are by-products of the odd–even difference (decision D21): a failure
    here must not lose the difference's own rows.

    Parameters
    ----------
    library_stack_path : `str`
        The library stack the record goes beside.
    noise : `StackNoiseOutcome`
        The difference's noise outcome, holding the apertures' positions and
        their sum and difference values.
    odd_plane, even_plane : `numpy.ndarray` or `None`
        The two half stacks' images.

    Returns
    -------
    detail : `OddEvenDetail`
        The record's count and path, and the line pattern, or the reasons.
    """
    result = noise.result
    count: int | None = None
    path: str | None = None
    record_reason = ""
    line_pattern: LinePatternOutcome | None = None
    try:
        if result is None or odd_plane is None:
            record_reason = "the difference was not measured"
        elif not result.added_aperture_sums:
            record_reason = "the sum image was not measured"
        else:
            records = build_aperture_records(
                result.aperture_pixel_positions,
                result.added_aperture_sums,
                result.aperture_sums,
                odd_plane.shape,
                result.target_pixel_position,
            )
            note = (
                "Values are sky-subtracted aperture sums in the stack's units. 'sum' is taken on A + B "
                "and 'difference' on A - B of the odd and even half stacks (not halved). Distances are in "
                "pixels; the target distance is null when the target's position is not known."
            )
            path = save_odd_even_aperture_record(library_stack_path, records, result.aperture_identity, note)
            if path is None:
                record_reason = "the record file could not be written"
            else:
                count = len(records)
    except Exception as error:
        logger.exception("The odd–even aperture record of %s could not be built", library_stack_path)
        record_reason = f"the record could not be built ({type(error).__name__})"
        path, count = None, None
    try:
        if odd_plane is None or even_plane is None:
            line_pattern = LinePatternOutcome(None, "a half stack file holds no pixels")
        else:
            line_pattern = measure_line_pattern(odd_plane, even_plane)
    except Exception as error:
        logger.exception(
            "The line pattern of the half stacks of %s could not be measured", library_stack_path
        )
        line_pattern = LinePatternOutcome(None, f"the measurement failed: {type(error).__name__}")
    return OddEvenDetail(count, path, record_reason, line_pattern)


def detail_measurements(
    detail: OddEvenDetail | None,
    context: PipelineRunContext,
    subject: QualitySubject,
    reason: str,
    note: str,
) -> list[QualityMeasurement]:
    """Build the record-count row and the four line-pattern rows.

    Parameters
    ----------
    detail : `OddEvenDetail` or `None`
        What `_aperture_detail_safely` gave, or `None` when it was not tried.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The library stack.
    reason : `str`
        Why the difference has no values, used when ``detail`` is `None`.
    note : `str`
        What was done, kept on each measured row.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        Five rows, each not measured with the reason when its value is missing.
    """
    detail = detail or OddEvenDetail(record_reason=reason, line_pattern=LinePatternOutcome(None, reason))
    record_row = build_measurement(
        _SPEC_BY_NAME[STACKING_ODD_EVEN_DIFFERENCE_APERTURE_RECORD_COUNT],
        None if detail.record_count is None else float(detail.record_count),
        context,
        subject,
        reason=detail.record_reason or reason or "the per-aperture record was not written",
        sample_count=detail.record_count,
        provenance=f"Per-aperture record: {detail.record_path}" if detail.record_path else "",
    )
    pattern = detail.line_pattern or LinePatternOutcome(None, reason or "the line pattern was not measured")
    line = pattern.result
    names = (
        (STACKING_ODD_EVEN_DIFFERENCE_ROW_AVERAGE_SCATTER_STACK_UNIT, "difference_row", "row_count"),
        (STACKING_ODD_EVEN_DIFFERENCE_COLUMN_AVERAGE_SCATTER_STACK_UNIT, "difference_column", "column_count"),
        (STACKING_ODD_EVEN_SUM_ROW_AVERAGE_SCATTER_STACK_UNIT, "sum_row", "row_count"),
        (STACKING_ODD_EVEN_SUM_COLUMN_AVERAGE_SCATTER_STACK_UNIT, "sum_column", "column_count"),
    )
    rows = [record_row]
    for name, attribute, count_attribute in names:
        value = None if line is None else getattr(line, attribute)
        count = None if line is None else getattr(line, count_attribute)
        rows.append(
            build_measurement(
                _SPEC_BY_NAME[name],
                value,
                context,
                subject,
                reason=pattern.reason or reason or "the line pattern was not measured",
                uncertainty=None if line is None else line.error(value, count),
                sample_count=count,
                provenance=note,
            )
        )
    return rows


@dataclass
class OddEvenResult:
    """What the odd–even difference found, ready to turn into quality rows.

    This gathers everything `odd_even_difference_measurements` needs besides
    the run's context and the library stack's subject. A run that stops early
    builds one with only a ``reason``. It is not the public
    `OddEvenDifferenceNoiseResult` that the job returns; that one is built up
    alongside it.

    Parameters
    ----------
    reason : `str`, optional
        Why there is no noise value. Empty when there is one.
    noise : `StackNoiseOutcome`, optional
        The noise of the difference, or `None` when no difference was made.
    note : `str`, optional
        What was done, kept on each measured row.
    counts : `tuple` [`float` or `None`, `float` or `None`], optional
        The odd and even halves' effective frame counts.
    counts_reason : `str`, optional
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
    detail : `OddEvenDetail`, optional
        The per-aperture record and the line pattern.
    """

    reason: str = ""
    noise: StackNoiseOutcome | None = None
    note: str = ""
    counts: tuple[float | None, float | None] = (None, None)
    counts_reason: str = ""
    variance_factor: float | None = None
    shared_fractions: tuple[float | None, float | None] = (None, None)
    model_ratio: float | None = None
    model_ratio_reason: str = ""
    pattern: HalfPatternOutcome | None = None
    detail: OddEvenDetail | None = None


def odd_even_difference_measurements(
    context: PipelineRunContext,
    subject: QualitySubject,
    outcome: OddEvenResult,
) -> list[QualityMeasurement]:
    """Build the odd–even difference rows of the library stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The library stack.
    outcome : `OddEvenResult`
        What the difference found, or why it found nothing.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        The noise in stack units, the normalized noise, the reference flux it
        is divided by, the two halves' effective frame counts, the variance
        factor, the model variance ratio and the shared frame's two weight
        shares, then the eight pattern rows. Each is not measured, with the
        reason, when its value is missing.
    """
    noise = outcome.noise
    reason = outcome.reason
    note = outcome.note
    result = noise.result if noise is not None else None
    missing = reason or (noise.reason if noise is not None else "") or "the difference was not measured"
    if result is None:
        noise_rows = [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=missing)
            for name in (
                STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT,
                STACKING_ODD_EVEN_DIFFERENCE_NOISE_NORMALIZED,
                STACKING_ODD_EVEN_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
            )
        ]
        noise_rows.append(
            aperture_set_measurement(
                STACKING_ODD_EVEN_DIFFERENCE_APERTURE_SET_NUMBER, None, context, subject, missing
            )
        )
    else:
        noise_rows = [
            build_measurement(
                _SPEC_BY_NAME[STACKING_ODD_EVEN_DIFFERENCE_NOISE_STACK_UNIT],
                result.noise_adu,
                context,
                subject,
                uncertainty=result.noise_uncertainty_adu,
                sample_count=result.aperture_count,
            ),
            build_measurement(
                _SPEC_BY_NAME[STACKING_ODD_EVEN_DIFFERENCE_NOISE_NORMALIZED],
                result.normalized_noise,
                context,
                subject,
                uncertainty=result.normalized_uncertainty,
                sample_count=result.aperture_count,
            ),
            build_measurement(
                _SPEC_BY_NAME[STACKING_ODD_EVEN_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT],
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
                STACKING_ODD_EVEN_DIFFERENCE_APERTURE_SET_NUMBER, result.aperture_identity, context, subject
            )
        )
    count_rows = [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=outcome.counts_reason or missing or "the half's effective frame count is unknown",
        )
        for name, value in zip(
            (
                STACKING_ODD_EVEN_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
                STACKING_ODD_EVEN_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
            ),
            outcome.counts,
            strict=True,
        )
    ]
    factor_row = build_measurement(
        _SPEC_BY_NAME[STACKING_ODD_EVEN_DIFFERENCE_VARIANCE_FACTOR],
        outcome.variance_factor,
        context,
        subject,
        reason=outcome.counts_reason or missing or "the halves' weights are unknown",
    )
    model_row = build_measurement(
        _SPEC_BY_NAME[STACKING_ODD_EVEN_DIFFERENCE_MODEL_VARIANCE_RATIO],
        outcome.model_ratio,
        context,
        subject,
        reason=outcome.model_ratio_reason or missing or "the modeled noise of the frames is unknown",
    )
    share_rows = [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=outcome.counts_reason
            or (reason if result is None else "the shared frame is not among the frames the half stacked"),
        )
        for name, value in zip(
            (
                STACKING_ODD_EVEN_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
                STACKING_ODD_EVEN_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
            ),
            outcome.shared_fractions,
            strict=True,
        )
    ]
    pattern_rows = _pattern_rows_safely(
        context, subject, outcome.pattern, missing if result is None else "the pattern was not measured", note
    )
    detail_rows = detail_measurements(
        outcome.detail, context, subject, missing if result is None else "the detail was not measured", note
    )
    return [*noise_rows, *count_rows, factor_row, model_row, *share_rows, *pattern_rows, *detail_rows]


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


def delete_half_folder(library_stack_path: str, label: str) -> None:
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


@dataclass
class HalfStacks:
    """Stack the halves of one library stack under one shared reference.

    Both halves hold the same registration reference frame, so that Siril
    lines them up with it and their pixels coincide. This class stacks a set
    of frames under a stacking slot into the target's ``_subsets`` folder
    (without the edge trim), remembers each result, and checks that the
    stacker really used the shared frame as its reference. The odd–even
    difference job and the bias test both use it. The bias test adds a third
    set, the whole stack, under the name ``full``.

    Parameters
    ----------
    target_id : `str`
        The target, which names Siril's work folder.
    library_stack_path : `str`
        The library stack. Only folders inside its ``_subsets`` folder are
        ever written or deleted.
    shared_path : `str`
        The path of the frame that is the registration reference of every
        stack.
    labels : `dict` [`str`, `str`]
        The label of each stack by name, which names its folder.
    configuration : `AppConfiguration`
        The settings (weighting and rejection).
    drivers : `Drivers` or `None`
        The stacking program. Siril when `None`.
    stacking_slot : `Callable`
        Gives the context manager that holds a stacking slot.
    job_id : `str`, optional
        The running job, for Siril's progress.
    stacked : `dict`, optional
        The stacker's outcome by name, filled by `stack`.
    """

    target_id: str
    library_stack_path: str
    shared_path: str
    labels: dict[str, str]
    configuration: Any
    drivers: Drivers | None
    stacking_slot: Callable[[], AbstractContextManager]
    job_id: str | None = None
    stacked: dict[str, Any] = field(default_factory=dict)

    @property
    def library_folder(self) -> str:
        """Give the folder of the library stack.

        Returns
        -------
        folder : `str`
            The folder of the library stack and its ``_subsets`` folder.
        """
        return os.path.dirname(self.library_stack_path)

    def output_path(self, name: str) -> str:
        """Give the file a named stack is written to.

        Parameters
        ----------
        name : `str`
            The stack's name, a key of ``labels``.

        Returns
        -------
        path : `str`
            The stack's path inside ``_subsets``.
        """
        return subset_output_path(self.library_folder, self.labels[name], None)

    def stack(self, name: str, frames: Sequence[FrameRecord], output_path: str | None = None) -> Any:
        """Stack frames under a stacking slot and remember the outcome.

        Parameters
        ----------
        name : `str`
            The stack's name, a key of ``labels``.
        frames : `Sequence` [`FrameRecord`]
            The frames to stack.
        output_path : `str`, optional
            Where to write the stack. The path from `output_path` when left
            out.

        Returns
        -------
        outcome : `SubsetStackOutcome`
            What the stacker gave back. Any error it raises is not caught.
        """
        with self.stacking_slot():
            path = output_path if output_path is not None else self.output_path(name)
            self.stacked[name] = stack_frame_subset(
                self.target_id,
                list(frames),
                path,
                configuration=self.configuration,
                drivers=self.drivers,
                job_id=self.job_id,
                trim_edges=False,
                reference_frame_path=self.shared_path,
            )
        return self.stacked[name]

    def made_stack(self, name: str) -> bool:
        """Say whether a named stack produced a file.

        Parameters
        ----------
        name : `str`
            The stack's name.

        Returns
        -------
        made : `bool`
            `False` when the stacker gave no stack path.
        """
        return self.stacked[name].stack_path is not None

    def lined_up_on_shared(self, name: str) -> bool:
        """Say whether a named stack used the shared frame as its reference.

        Parameters
        ----------
        name : `str`
            The stack's name.

        Returns
        -------
        lined_up : `bool`
            `True` when the stacker reports exactly the shared frame.
        """
        return self.stacked[name].registration_reference_frame == self.shared_path

    def reference_problem(self) -> str:
        """Say why the odd and even halves are not on the shared reference.

        Returns
        -------
        reason : `str`
            Empty when both halves were lined up with the shared frame.
            Otherwise a sentence naming what went wrong.
        """
        odd = self.stacked["odd"].registration_reference_frame
        even = self.stacked["even"].registration_reference_frame
        if odd is None or even is None:
            return "the halves cannot be registered on one reference: Siril's reference frame is unknown"
        if not (odd == even == self.shared_path):
            return (
                "the halves cannot be registered on one reference: they were lined up with "
                f"{os.path.basename(odd)} and {os.path.basename(even)}, "
                f"not both with the library stack's reference {os.path.basename(self.shared_path)}"
            )
        return ""

    def delete_folders(self) -> None:
        """Delete every stack's folder from ``_subsets``."""
        for label in self.labels.values():
            delete_half_folder(self.library_stack_path, label)


def run_odd_even_difference_noise(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    keep_halves: bool = False,
) -> OddEvenDifferenceNoiseResult:
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
    `ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF` frames; Siril fails; or the
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
    result : `OddEvenDifferenceNoiseResult`
        The numbers, or the reason there are none.
    """
    with subset_job_guard(target.id):
        return _run_odd_even_difference_noise(
            target,
            library_stack_path,
            context=context,
            configuration=configuration,
            drivers=drivers,
            stacking_slot=stacking_slot,
            keep_halves=keep_halves,
        )


@dataclass
class _PreparedHalves:
    """What the checks before any stacking found.

    Parameters
    ----------
    reason : `str`
        Why the job cannot go on. Empty when it can.
    split : `HalfSplit` or `None`
        The two halves, when they were worked out.
    apertures_path : `str`
        The target's stored apertures file.
    """

    reason: str = ""
    split: HalfSplit | None = None
    apertures_path: str = ""


@dataclass
class _HalfWeights:
    """The weights of the two half stacks.

    Parameters
    ----------
    memberships : `dict` [`str`, `StackMembership` or `None`]
        The members of each half stack by name (``odd``, ``even``).
    shares : `dict` [`str`, `dict` or `None`]
        Each member's weight share by frame path, for each half.
    counts : `tuple` [`float` or `None`, `float` or `None`]
        The halves' effective frame counts.
    counts_reason : `str`
        Why a count is missing, when one is.
    """

    memberships: dict[str, StackMembership | None]
    shares: dict[str, dict[str, float] | None]
    counts: tuple[float | None, float | None]
    counts_reason: str


def _store_rows(
    result: OddEvenDifferenceNoiseResult,
    outcome: OddEvenResult,
    context: PipelineRunContext | None,
    subject: QualitySubject | None,
    configuration: Any,
) -> OddEvenDifferenceNoiseResult:
    """Store the rows and give back the job's result.

    Parameters
    ----------
    result : `OddEvenDifferenceNoiseResult`
        The job's result so far.
    outcome : `OddEvenResult`
        What the difference found, or why it found nothing.
    context : `PipelineRunContext` or `None`
        The run's shared facts, or `None` to store nothing.
    subject : `QualitySubject` or `None`
        The library stack.
    configuration : `AppConfiguration`
        The settings.

    Returns
    -------
    result : `OddEvenDifferenceNoiseResult`
        The finished result.
    """
    result.reason = outcome.reason
    if context is not None and subject is not None:
        rows = odd_even_difference_measurements(context, subject, outcome)
        result.rows_written = record_run_measurements(rows, config=configuration)
    return result


def _prepare_halves(
    target: Target,
    library: LibraryRun,
    result: OddEvenDifferenceNoiseResult,
    library_stack_path: str,
) -> _PreparedHalves:
    """Check the library stack can be split and measured, and split it.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library : `LibraryRun`
        What the library stack stored.
    result : `OddEvenDifferenceNoiseResult`
        The job's result; the halves' sizes and notes are put on it.
    library_stack_path : `str`
        The library stack.

    Returns
    -------
    prepared : `_PreparedHalves`
        The halves and the apertures file, or the reason the job cannot go on.
    """
    if any(frame_is_spectral(frame) for frame in library.members):
        return _PreparedHalves("the odd–even difference noise is measured for imaging stacks only")
    if library.noise_sums is None:
        return _PreparedHalves(
            "the library stack's per-aperture noise sums were not saved, so the apertures it was measured "
            "in cannot be checked; stack the target again"
        )
    summary = getattr(target.stacking, "quality_summary", None)
    reference_path = getattr(getattr(summary, "stacking_metrics", None), "registration_reference_frame", None)
    if not reference_path:
        return _PreparedHalves(
            "the library stack's registration reference frame is not recorded, so the two halves cannot "
            "be lined up on one reference; stack the target again"
        )
    try:
        split = split_into_halves(library.members, reference_path)
    except InvalidArgumentError as error:
        return _PreparedHalves(error.message)
    result.shared_frame = split.shared_path
    result.odd_half_frames = len(split.odd)
    result.even_half_frames = len(split.even)
    if split.left_out:
        result.notes.append(f"{split.left_out} frame(s) have no timestamp and were left out of both halves.")
    smaller = min(len(split.odd), len(split.even))
    if smaller < ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF:
        return _PreparedHalves(
            f"a half would have {smaller} frames, fewer than the "
            f"{ODD_EVEN_DIFFERENCE_MINIMUM_FRAMES_PER_HALF} "
            "the odd–even difference noise needs"
        )
    apertures_path = os.path.join(os.path.dirname(library_stack_path), APERTURE_FILE_NAME)
    stored_apertures, apertures_reason = load_apertures_with_reason(apertures_path)
    if stored_apertures is None:
        return _PreparedHalves(apertures_reason)
    return _PreparedHalves("", split, apertures_path)


def _stack_both_halves(target_id: str, split: HalfSplit, half_stacks: HalfStacks) -> str:
    """Stack the odd and the even half and check their shared reference.

    Parameters
    ----------
    target_id : `str`
        The target, for the log.
    split : `HalfSplit`
        The two halves.
    half_stacks : `HalfStacks`
        Stacks the halves and remembers the outcomes.

    Returns
    -------
    reason : `str`
        Why the halves cannot be compared. Empty when both were stacked on
        the shared reference frame.
    """
    for index, (name, half_frames) in enumerate((("odd", split.odd), ("even", split.even))):
        report_progress(int(40 * index), f"Stacking the {name} half ({len(half_frames)} frames)")
        output_path = half_stacks.output_path(name)
        try:
            half_stacks.stack(name, half_frames, output_path)
        except Exception as error:
            logger.exception("The %s half stack of %s failed", name, target_id)
            return f"the stacker failed on the {name} half ({type(error).__name__}: {error})"
        if not half_stacks.made_stack(name):
            return f"the stacker made no stack from the {name} half"
    return half_stacks.reference_problem()


def _measure_difference(
    target: Target, split: HalfSplit, half_stacks: HalfStacks, apertures_path: str
) -> tuple[StackNoiseOutcome | None, np.ndarray | None, np.ndarray | None, str]:
    """Read the two half stacks once and measure the noise of their difference.

    Parameters
    ----------
    target : `Target`
        The target, for its position and the log.
    split : `HalfSplit`
        The two halves; the shared frame is the flux reference.
    half_stacks : `HalfStacks`
        Holds the two stacked halves.
    apertures_path : `str`
        The target's stored apertures.

    Returns
    -------
    noise, odd_plane, even_plane, reason : `tuple`
        The noise outcome and the two half images, or `None` for all three
        and the reason when the measurement raised.
    """
    try:
        # Each half is read once, as 32-bit, and shared by the difference
        # noise and the pattern measurement.
        odd_plane = read_stack_plane(half_stacks.stacked["odd"].stack_path, np.float32)
        even_plane = read_stack_plane(half_stacks.stacked["even"].stack_path, np.float32)
        noise = measure_difference_noise(
            half_stacks.stacked["odd"].stack_path,
            half_stacks.stacked["even"].stack_path,
            split.shared_path,
            apertures_path,
            first_plane=odd_plane,
            second_plane=even_plane,
            target_sky_deg=_target_sky_degrees(target),
        )
    except Exception as error:  # a measurement failure is stored as a reason, never raised
        logger.exception("The noise of the odd–even difference of %s could not be measured", target.id)
        return None, None, None, f"the difference could not be measured ({type(error).__name__}: {error})"
    return noise, odd_plane, even_plane, ""


def _half_weights(split: HalfSplit, half_stacks: HalfStacks) -> _HalfWeights:
    """Work out the members, weight shares and effective counts of the halves.

    Parameters
    ----------
    split : `HalfSplit`
        The two halves.
    half_stacks : `HalfStacks`
        Holds the two stacked halves.

    Returns
    -------
    weights : `_HalfWeights`
        The halves' weights. A half whose members cannot be worked out has
        no shares, and the reason is given.
    """
    stacked = half_stacks.stacked
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
    shares = {name: weight_shares(memberships[name]) for name in ("odd", "even")}
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
    return _HalfWeights(memberships, shares, counts, counts_reason)


def _model_ratio_safely(
    library: LibraryRun, target_id: str, configuration: Any, weights: _HalfWeights
) -> tuple[float | None, str]:
    """Work out the model variance ratio, never raising.

    Parameters
    ----------
    library : `LibraryRun`
        What the library stack stored.
    target_id : `str`
        The target.
    configuration : `AppConfiguration`
        Where the camera profiles live.
    weights : `_HalfWeights`
        The halves' weights.

    Returns
    -------
    ratio, reason : `tuple` [`float` or `None`, `str`]
        The ratio, or `None` and why there is none.
    """
    try:
        half_scales = {
            member.frame.path: member.exposure_scale
            for membership in weights.memberships.values()
            if membership is not None
            for member in membership.members
        }
        return _model_ratio_for_library(
            library, target_id, configuration, weights.shares["odd"], weights.shares["even"], half_scales
        )
    except Exception as error:  # a by-product: its failure must not lose the noise
        logger.exception("The modeled variance ratio of %s could not be worked out", target_id)
        return None, f"the modeled noise failed: {type(error).__name__}"


def _record_pattern_values(
    result: OddEvenDifferenceNoiseResult, detail: OddEvenDetail, pattern: HalfPatternOutcome
) -> None:
    """Copy the pattern and line-pattern numbers onto the job's result.

    Parameters
    ----------
    result : `OddEvenDifferenceNoiseResult`
        The job's result, changed in place.
    detail : `OddEvenDetail`
        The per-aperture record and the line pattern.
    pattern : `HalfPatternOutcome`
        What the two halves share at a few pixels.
    """
    result.aperture_record_path = detail.record_path
    result.aperture_record_count = detail.record_count
    line = None if detail.line_pattern is None else detail.line_pattern.result
    if line is not None and line.difference_row > 0 and line.difference_column > 0:
        result.line_pattern_ratios = {
            "row": line.sum_row / line.difference_row,
            "column": line.sum_column / line.difference_column,
        }
    if pattern.result is not None:
        result.shared_fine_scale_variance_ratio = pattern.result.variance_ratio
        result.walking_noise_correlation_excess = pattern.result.excess
        result.walking_noise_correlation_excess_error = pattern.result.excess_error


def _measure_halves(
    target: Target,
    library: LibraryRun,
    split: HalfSplit,
    apertures_path: str,
    half_stacks: HalfStacks,
    result: OddEvenDifferenceNoiseResult,
    configuration: Any,
) -> OddEvenResult:
    """Stack the halves, measure their difference and gather what it shows.

    Parameters
    ----------
    target : `Target`
        The target. It is only read.
    library : `LibraryRun`
        What the library stack stored.
    split : `HalfSplit`
        The two halves.
    apertures_path : `str`
        The target's stored apertures.
    half_stacks : `HalfStacks`
        Stacks the halves.
    result : `OddEvenDifferenceNoiseResult`
        The job's result; its numbers are filled in.
    configuration : `AppConfiguration`
        The settings.

    Returns
    -------
    outcome : `OddEvenResult`
        What the difference found, or why it found nothing.
    """
    reason = _stack_both_halves(target.id, split, half_stacks)
    if reason:
        return OddEvenResult(reason=reason)
    report_progress(80, "Measuring the noise of the difference")
    noise, odd_plane, even_plane, reason = _measure_difference(target, split, half_stacks, apertures_path)
    if reason:
        return OddEvenResult(reason=reason)
    if noise.result is None:
        return OddEvenResult(reason=noise.reason or "the difference was not measured")
    noise_sums = library.noise_sums
    if not noise_sums.aperture_identity or noise_sums.aperture_identity != noise.result.aperture_identity:
        return OddEvenResult(
            reason="the library stack was measured in other apertures than the halves "
            f"(identities {noise_sums.aperture_identity or 'unknown'} "
            f"and {noise.result.aperture_identity}); "
            "stack the target again so the library stack is measured in the current apertures"
        )
    weights = _half_weights(split, half_stacks)
    shares = weights.shares
    note = (
        f"The odd half holds {len(split.odd)} frames and the even half {len(split.even)}; both hold "
        f"{os.path.basename(split.shared_path)}, the library stack's registration reference frame."
    )
    result.odd_half_effective_frame_count, result.even_half_effective_frame_count = weights.counts
    result.difference_noise_stack_unit = noise.result.noise_adu
    result.difference_noise_normalized = noise.result.normalized_noise
    result.difference_noise_normalized_uncertainty = noise.result.normalized_uncertainty
    result.reference_flux_stack_unit = noise.result.reference_flux_adu
    result.aperture_count = noise.result.aperture_count
    variance_factor = difference_variance_factor(shares["odd"], shares["even"])
    result.variance_factor = variance_factor
    result.full_stack_equivalent_normalized = full_stack_noise_equivalent(
        noise.result.normalized_noise, library.effective_count, shares["odd"], shares["even"]
    )
    shared_fractions = tuple(
        shares[name].get(split.shared_path) if shares[name] is not None else None for name in ("odd", "even")
    )
    result.shared_frame_odd_weight_fraction, result.shared_frame_even_weight_fraction = shared_fractions
    model_ratio, model_ratio_reason = _model_ratio_safely(library, target.id, configuration, weights)
    result.model_variance_ratio = model_ratio
    result.full_stack_equivalent_model_normalized = full_stack_noise_equivalent_from_model(
        noise.result.normalized_noise, model_ratio
    )
    result.notes.append(note)
    pattern = _half_pattern_safely(
        half_stacks.stacked["odd"],
        half_stacks.stacked["even"],
        odd_plane,
        even_plane,
        library,
        variance_factor,
        shared_fractions,
    )
    detail = _aperture_detail_safely(half_stacks.library_stack_path, noise, odd_plane, even_plane)
    del odd_plane, even_plane
    _record_pattern_values(result, detail, pattern)
    return OddEvenResult(
        reason="",
        noise=noise,
        note=note,
        counts=weights.counts,
        counts_reason=weights.counts_reason,
        variance_factor=variance_factor,
        shared_fractions=shared_fractions,
        model_ratio=model_ratio,
        model_ratio_reason=model_ratio_reason,
        pattern=pattern,
        detail=detail,
    )


def _run_odd_even_difference_noise(
    target: Target,
    library_stack_path: str,
    *,
    context: PipelineRunContext | None,
    configuration: Any,
    drivers: Drivers | None,
    stacking_slot: Callable[[], AbstractContextManager],
    keep_halves: bool,
) -> OddEvenDifferenceNoiseResult:
    """Do the work of `run_odd_even_difference_noise` while the guard is held.

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
    result : `OddEvenDifferenceNoiseResult`
        The numbers, or the reason there are none.
    """
    result = OddEvenDifferenceNoiseResult(targetId=target.id, libraryStackPath=library_stack_path)
    subject = context.stack_subject(os.path.basename(library_stack_path)) if context is not None else None
    library = read_library_run(target, library_stack_path, configuration)
    result.notes.extend(library.notes)
    prepared = _prepare_halves(target, library, result, library_stack_path)
    if prepared.reason:
        return _store_rows(result, OddEvenResult(reason=prepared.reason), context, subject, configuration)
    split = prepared.split
    stem = stack_stem(library_stack_path)
    half_stacks = HalfStacks(
        target_id=target.id,
        library_stack_path=library_stack_path,
        shared_path=split.shared_path,
        labels={"odd": f"{stem}.half-odd", "even": f"{stem}.half-even"},
        configuration=configuration,
        drivers=drivers,
        stacking_slot=stacking_slot,
        job_id=None if context is None else context.job_id,
    )
    try:
        outcome = _measure_halves(
            target, library, split, prepared.apertures_path, half_stacks, result, configuration
        )
        return _store_rows(result, outcome, context, subject, configuration)
    finally:
        if not keep_halves:
            half_stacks.delete_folders()
