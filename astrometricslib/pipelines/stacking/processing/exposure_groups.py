"""Stacking frames of different exposure lengths without wasting the light.

Some spectroscopy sessions are shot as a bracketed set: many exposure
lengths of the same target, for example 0.5 s, 1 s, 2 s, 3 s and 5 s, so
that a very bright star is not saturated in every frame. Frames like that
must not be stacked as one batch by the usual recipe.

Why not: the recipe first scales every frame to the same noise level
("additive + scaling" normalization) and then throws away pixel values
that stand too far from the rest ("sigma clipping"). With one exposure
length, that is safe. With several, a 5 s frame and a 0.5 s frame hold
very different amounts of light in the same pixel, and the clipping then
discards the best frames' values as if they were outliers. On the Vega
session (140 frames, 0.5-5 s) this made a faint star's spectrum about 8
times noisier than a plain sum of the same frames (measured 2026-09-20,
see documentation/evidence/stack_exposure_groups_20260920.json).

The fix here has three parts:

1. Split the frames into groups that share one exposure length and one
   ISO (`split_frames_by_exposure`). Each group is stacked on its own, where
   the usual recipe is safe. A length with only a few frames joins the
   nearest group of its own ISO, but only within
   `MAXIMUM_EXPOSURE_FOLD_RATIO`: a folded
   frame is scaled by the exposure ratio, so its noise grows by that
   factor. A length with no group that near is left out of the stack
   (`plan_exposure_folding`), and the frame screening records it.
2. Turn each group's stack into counts per second, so groups of different
   exposure length can be compared, and put the groups on one brightness
   scale (Siril leaves each stack with its own) after leaving out each
   group's saturated pixels (`estimate_saturation_mask_level`,
   `measure_exposure_group_gains`). For an imaging stack the scale is the
   median ratio of the aperture fluxes of isolated, unsaturated stars
   (`measure_group_gains_with_stars`), and a group whose faintest or
   brightest quarter of stars disagrees with it is left out. A group with
   too few such stars, and every group of a spectral stack, is measured on
   mid-range pixels instead, and left out when its brightest pixels
   disagree (`measure_group_gains`). A group whose sky is far brighter
   than the reference's, per unit ISO, is also left out (`SKY_RATIO_LIMIT`).
3. Combine the groups with inverse-variance weights, measured from each
   stack's own background noise (`combine_exposure_group_images`). A group
   that is quieter per second counts for more, so long exposures and
   frames with low read noise are favoured automatically, without
   assuming whether the noise comes from the sky or from the camera.

The work is spread over five modules in this folder:

- `exposure_groups` (this file): sorting frames into groups, by exposure
  length and by night.
- `group_gains`: the brightness scale of each group, measured on
  mid-range pixels, and the saturation mask.
- `group_star_gains`: the same scale measured on isolated stars.
- `group_combination`: the weights, and combining the groups into one
  image.
- `group_file_merging`: joining the per-group rejection maps and
  registration sequences into one file each.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.frame_grouping import frame_exposure_seconds, frame_iso_value
from astrometricslib.pipelines.shared.target_sessions import compute_session_night

# The fewest frames a group of one exposure length may have and still be
# stacked on its own. A group smaller than this cannot be registered or
# clipped reliably (registration needs a reference and other frames to match
# it against, and clipping needs several values per pixel), so its frames are
# added to the group with the nearest exposure length instead. Unvalidated:
# on the Vega session every group has at least 20 frames, and the smallest
# groups in the library (1-2 frames each, from short calibration sets) only
# need to be small enough to be folded away. It has not been tested at 5.
MINIMUM_FRAMES_PER_EXPOSURE_GROUP = TunedParameter(
    name="stacking.minimum_frames_per_exposure_group",
    default=5,
    unit="frames",
    description="Fewest frames a group of one exposure length may have and still be stacked on its own.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-11",
)


# The largest exposure ratio over which a short list of frames may be folded
# into a group. A folded frame is scaled by the exposure ratio (Siril matches
# it to the group's reference frame), so its noise grows by the same factor:
# at 2 a folded frame carries at most twice its own noise, and one doubling of
# the exposure keeps the star profile and the sky level close enough to what
# the group's calibration frames cover. A length farther than this from every
# group is left out of the stack (and recorded), not folded. Design estimate,
# not yet measured: the real cases that motivate it are M 31 (117 frames of
# 60 s, and single frames of 20 s, 30 s and 120 s), M 45, M 81 and M 42 (a
# single 300 s frame beside 30 s frames, 10 times away); see STK-D79.
MAXIMUM_EXPOSURE_FOLD_RATIO = TunedParameter(
    name="stacking.maximum_exposure_fold_ratio",
    default=2.0,
    unit="ratio",
    description="Largest exposure ratio over which a short list of frames is folded into a group.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D79",
)


@dataclass
class ExposureGroup:
    """Frames that share (nearly) one exposure length.

    Attributes
    ----------
    exposure_seconds : `float`
        The mean exposure length of the frames in the group, in seconds.
    frames : `list`
        The frame records in the group, in their original order.
    night : `str` or `None`
        The observing night of the group's frames (``YYYY-MM-DD``, see
        `split_groups_by_night`), or `None` when the group was not split by
        night.
    iso : `float` or `None`
        The ISO (or gain setting) the group's frames share, or `None` when it
        is not known or the frames differ. Frames at different ISOs never
        share a group, so the sky of two groups can be compared per unit ISO
        (`normalize_sky_ratios_by_iso`).
    """

    exposure_seconds: float
    frames: list[Any] = field(default_factory=list)
    night: str | None = None
    iso: float | None = None


def exposure_group_key(exposure_seconds: float) -> float:
    """Round an exposure length to the value frames are grouped by.

    Three significant figures, not three decimal places: the camera's
    shortest exposure is 0.000032 s, which rounds to 0.0 at three decimals and
    made the nearest-group ratio of `fold_exposure_lengths` divide by zero.

    Parameters
    ----------
    exposure_seconds : `float`
        An exposure length in seconds.

    Returns
    -------
    key : `float`
        The length rounded to three significant figures.
    """
    return float(f"{exposure_seconds:.3g}")


@dataclass(frozen=True)
class ExposureFolding:
    """Where each frame of a list lands among the exposure groups.

    Attributes
    ----------
    group_lengths : `list` [`float` or `None`]
        For each frame, the rounded exposure length of the group it lands in.
        Every entry is `None` when all the frames stay in one group (no
        frames, a frame with no readable length, or no length with enough
        frames). A frame that is left out has its own rounded length here,
        which means nothing: read `left_out_reasons`.
    group_isos : `list` [`float` or `None`]
        For each frame, the ISO of the group it lands in, or `None` when the
        frames' ISO is not used (no ISO was given). Empty when the plan keeps
        everything in one group.
    left_out_reasons : `list` [`str` or `None`]
        For each frame, `None` when it is stacked, or a plain sentence when it
        is left out of the stack: its length has too few frames to stack on
        its own and no group is near enough to fold it into.
    """

    group_lengths: list[float | None]
    left_out_reasons: list[str | None]
    group_isos: list[float | None] = field(default_factory=list)


def _ratio_between(first: float, second: float) -> float:
    """Give the ratio of the longer of two exposure lengths to the shorter.

    An exposure of exactly 0 has no ratio, so a tiny stand-in keeps it
    farthest from every real length instead of raising.

    Returns
    -------
    ratio : `float`
        At least 1.
    """
    longer = max(first, second, 1e-9)
    shorter = max(min(first, second), 1e-9)
    return longer / shorter


def plan_exposure_folding(
    exposures_seconds: Sequence[float | None],
    minimum_frames_per_group: int | None = None,
    maximum_fold_ratio: float | None = None,
    isos: Sequence[float | None] | None = None,
) -> ExposureFolding:
    """Say which group each frame lands in, and which frames are left out.

    Frames whose exposure lengths match (to three significant figures, see
    `exposure_group_key`) and whose ISO matches share a group. Frames at
    different ISOs are never put in one group, and a short list is folded
    only into a group of its own ISO: counts per second cannot be compared
    across ISOs without a measured gain, and the raw sky differs by the ISO
    ratio alone. A frame with no readable ISO counts as its own ISO, so a set
    of frames with no ISO at all is grouped by exposure length only. A length
    that has fewer than
    `minimum_frames_per_group` frames is folded into the group with the
    nearest length (nearest in ratio, so 2 s is nearer to 1 s than to 5 s),
    but only when that group is within `maximum_fold_ratio` of it: a folded
    frame is scaled by the exposure ratio and its noise grows by that factor.
    A short length with no group that near is left out of the stack, with a
    reason. This one rule is used by `split_frames_by_exposure` to build the
    groups, by the frame screening to record the left-out frames, and by the
    stack records to find a group that holds frames of another length, whose
    weights are therefore unknown.

    Parameters
    ----------
    exposures_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame, in seconds, or `None` for a frame
        whose length cannot be read.
    minimum_frames_per_group : `int`, optional
        The fewest frames a length needs to be stacked on its own.
    maximum_fold_ratio : `float`, optional
        The largest ratio of two exposure lengths over which frames may be
        folded together. The ratio is at least 1, and a ratio equal to the
        limit still folds. Defaults to `MAXIMUM_EXPOSURE_FOLD_RATIO`.
    isos : `Sequence` [`float` or `None`], optional
        The ISO of each frame, or `None` for a frame whose ISO cannot be
        read. Left out, every frame counts as having no ISO.

    Returns
    -------
    folding : `ExposureFolding`
        The group of each frame, and the reason for each frame that is left
        out. All the frames stay in one group, with none left out, when there
        are no frames, when any frame has no readable length, or when no
        length has enough frames. A set of frames can never be left with no
        group at all, because a left-out length needs a large group to be
        measured against.
    """
    if minimum_frames_per_group is None:
        minimum_frames_per_group = resolve(MINIMUM_FRAMES_PER_EXPOSURE_GROUP).value
    if maximum_fold_ratio is None:
        maximum_fold_ratio = resolve(MAXIMUM_EXPOSURE_FOLD_RATIO).value
    count = len(exposures_seconds)
    if not count or any(exposure is None for exposure in exposures_seconds):
        return ExposureFolding([None] * count, [None] * count)
    frame_isos: list[float | None] = [None] * count if isos is None else list(isos)
    if len(frame_isos) != count:
        frame_isos = [None] * count
    keys = [
        (exposure_group_key(exposure), iso)
        for exposure, iso in zip(exposures_seconds, frame_isos, strict=True)
        if exposure is not None
    ]
    frames_per_key: dict[tuple[float, float | None], int] = defaultdict(int)
    for key in keys:
        frames_per_key[key] += 1
    if len(frames_per_key) == 1:
        return ExposureFolding([key[0] for key in keys], [None] * count, [key[1] for key in keys])
    large = [key for key, frames in frames_per_key.items() if frames >= minimum_frames_per_group]
    if not large:
        return ExposureFolding([None] * count, [None] * count)
    mixed_isos = len({key[1] for key in frames_per_key}) > 1
    destination: dict[tuple[float, float | None], tuple[float, float | None]] = {}
    left_out_reason: dict[tuple[float, float | None], str] = {}
    for key in frames_per_key:
        if key in large:
            destination[key] = key
            continue
        same_iso = [large_key for large_key in large if large_key[1] == key[1]]
        iso_text = f" at ISO {key[1]:g}" if mixed_isos and key[1] is not None else ""
        if not same_iso:
            destination[key] = key
            left_out_reason[key] = (
                f"exposure {key[0]:g} s{iso_text} has too few frames ({frames_per_key[key]}) to stack on "
                "its own and no group of that ISO to fold into"
            )
            continue
        nearest = min(
            same_iso,
            key=lambda large_key, small_key=key: math.log(_ratio_between(large_key[0], small_key[0])),
        )
        # A small tolerance keeps a ratio of exactly the limit (30 s against
        # 60 s) from being lost to rounding.
        if math.log(_ratio_between(nearest[0], key[0])) <= math.log(maximum_fold_ratio) + 1e-9:
            destination[key] = nearest
        else:
            destination[key] = key
            left_out_reason[key] = (
                f"exposure {key[0]:g} s{iso_text} is more than {maximum_fold_ratio:g}x from the nearest "
                f"group ({nearest[0]:g} s) and has too few frames ({frames_per_key[key]}) to stack on its own"
            )
    return ExposureFolding(
        [destination[key][0] for key in keys],
        [left_out_reason.get(key) for key in keys],
        [destination[key][1] for key in keys],
    )


def fold_exposure_lengths(
    exposures_seconds: Sequence[float | None],
    minimum_frames_per_group: int | None = None,
    maximum_fold_ratio: float | None = None,
) -> list[float | None]:
    """Say which exposure group each frame lands in.

    This is the group half of `plan_exposure_folding`, for callers that hold
    only frames the stack uses (the members of a finished stack). A frame
    that the plan leaves out is given its own rounded length here, so it
    looks like a group of its own. A caller that holds frames the stack may
    not use asks `plan_exposure_folding` for the left-out frames.

    Parameters
    ----------
    exposures_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame, in seconds, or `None` for a frame
        whose length cannot be read.
    minimum_frames_per_group : `int`, optional
        The fewest frames a length needs to be stacked on its own.
    maximum_fold_ratio : `float`, optional
        The largest exposure ratio over which frames may be folded.

    Returns
    -------
    group_lengths : `list` [`float` or `None`]
        For each frame, the rounded exposure length of the group it lands in.
        Every entry is `None` when there are no frames, when any frame has no
        readable length, or when no length has enough frames. All the frames
        then stay in one group, which is the same as stacking them together.
    """
    return plan_exposure_folding(
        exposures_seconds, minimum_frames_per_group, maximum_fold_ratio
    ).group_lengths


def exposure_group_holds_folded_frames(frames: list[Any]) -> bool:
    """Tell whether a group holds frames of more than one exposure length.

    A group built by `split_frames_by_exposure` holds frames of another length
    when a short list of them was folded into it (see `fold_exposure_lengths`).

    Parameters
    ----------
    frames : `list`
        The frame records (or dictionaries of them) of one group.

    Returns
    -------
    holds_folded : `bool`
        `True` when the frames with a readable length do not all share one
        rounded exposure length. A frame whose length cannot be read is
        ignored: a split into several groups never happens with one.
    """
    keys = {
        exposure_group_key(exposure)
        for frame in frames
        if (exposure := frame_exposure_seconds(frame)) is not None
    }
    return len(keys) > 1


def split_frames_by_exposure(
    frames: list[Any],
    minimum_frames_per_group: int | None = None,
    maximum_fold_ratio: float | None = None,
) -> list[ExposureGroup]:
    """Split frames into groups that share one exposure length.

    Frames whose exposure lengths match (to three significant figures) go in
    one group, provided their ISO also matches: frames at different ISOs are
    never grouped together (see `plan_exposure_folding`). A group with fewer
    than `minimum_frames_per_group` frames is added to the group of its ISO
    with the nearest exposure length (nearest in ratio,
    so 2 s is nearer to 1 s than to 5 s), unless that group is more than
    `maximum_fold_ratio` away; those frames belong to no group and are not in
    the result (`find_frames_left_out_of_exposure_groups` lists them). If any
    frame has no readable exposure length, or no group is large enough,
    everything stays in one group, which is the same as stacking the frames
    together as before. The folding itself is `plan_exposure_folding`.

    Parameters
    ----------
    frames : `list`
        The frame records to split.
    minimum_frames_per_group : `int`, optional
        The fewest frames a group needs to be stacked on its own.
    maximum_fold_ratio : `float`, optional
        The largest exposure ratio over which frames may be folded.

    Returns
    -------
    groups : `list` [`ExposureGroup`]
        The groups, shortest exposure first. Always at least one.
    """
    exposures = [frame_exposure_seconds(frame) for frame in frames]
    isos = [frame_iso_value(frame) for frame in frames]
    folding = plan_exposure_folding(exposures, minimum_frames_per_group, maximum_fold_ratio, isos)
    group_lengths = folding.group_lengths
    if not frames or group_lengths[0] is None:
        return [ExposureGroup(_mean_exposure(exposures), list(frames), iso=exposure_group_iso(frames))]
    kept = [index for index, reason in enumerate(folding.left_out_reasons) if reason is None]
    group_keys = {(group_lengths[index], folding.group_isos[index]) for index in kept}
    if len(group_keys) == 1:
        members = [frames[index] for index in kept]
        return [ExposureGroup(group_lengths[kept[0]], members, iso=folding.group_isos[kept[0]])]

    members_by_key: dict[tuple[float, float | None], list[Any]] = defaultdict(list)
    for index in kept:
        members_by_key[group_lengths[index], folding.group_isos[index]].append(frames[index])
    groups = []
    for group_key in sorted(members_by_key, key=lambda key: (key[0], key[1] or 0.0)):
        members = members_by_key[group_key]
        groups.append(
            ExposureGroup(
                _mean_exposure([frame_exposure_seconds(frame) for frame in members]),
                members,
                iso=group_key[1],
            )
        )
    return groups


def find_frames_left_out_of_exposure_groups(
    frames: list[Any],
    minimum_frames_per_group: int | None = None,
    maximum_fold_ratio: float | None = None,
) -> list[tuple[Any, str]]:
    """List the frames that no exposure group would take, with the reason.

    These are the frames `split_frames_by_exposure` leaves out of every
    group: their exposure length and ISO have too few frames to stack on
    their own and no group of that ISO is within the fold ratio limit
    (`MAXIMUM_EXPOSURE_FOLD_RATIO`).
    The stacking stage records each one with its reason, so a left-out frame
    is never lost without a trace. The frame files are not touched.

    Parameters
    ----------
    frames : `list`
        The frame records offered to the stack.
    minimum_frames_per_group : `int`, optional
        The fewest frames a group needs to be stacked on its own.
    maximum_fold_ratio : `float`, optional
        The largest exposure ratio over which frames may be folded.

    Returns
    -------
    left_out : `list` [`tuple` [`Any`, `str`]]
        Each left-out frame and the sentence that says why, in the order of
        the frames. Empty when every frame has a group.
    """
    folding = plan_exposure_folding(
        [frame_exposure_seconds(frame) for frame in frames],
        minimum_frames_per_group,
        maximum_fold_ratio,
        [frame_iso_value(frame) for frame in frames],
    )
    return [
        (frame, reason)
        for frame, reason in zip(frames, folding.left_out_reasons, strict=True)
        if reason is not None
    ]


def _frame_night(frame: Any) -> date | None:
    """Read which observing night a frame belongs to.

    Returns
    -------
    night : `datetime.date` or `None`
        The night (noon to noon, see `compute_session_night`), or `None` when
        the frame has no readable timestamp.
    """
    raw = frame.get("timestamp") if isinstance(frame, dict) else getattr(frame, "timestamp", None)
    try:
        return compute_session_night(float(raw))
    except TypeError, ValueError, OverflowError, OSError:
        return None


def split_groups_by_night(
    groups: list[ExposureGroup],
    minimum_frames_per_group: int | None = None,
) -> list[ExposureGroup]:
    """Split each exposure group into one group per observing night.

    The star sits on a slightly different pixel each night (a few to tens of
    pixels for a spectroscopy target), and registering frames from two nights
    as one batch can fail for most of them: on the Vega session the 138 frames
    of the 0.5 s group, from two nights, registered only 43% together, while
    each night's frames stacked cleanly alone. Stacking each night on its own
    and letting the group alignment line the nights up (see
    `group_alignment`) keeps every frame.

    A night with fewer than `minimum_frames_per_group` frames of an exposure
    is added to the nearest night's group of that exposure. A group whose
    frames are all from one night, or that has a frame without a readable
    timestamp, is left as it is.

    Parameters
    ----------
    groups : `list` [`ExposureGroup`]
        The groups from `split_frames_by_exposure`.
    minimum_frames_per_group : `int`, optional
        The fewest frames a night needs to be stacked on its own.

    Returns
    -------
    groups : `list` [`ExposureGroup`]
        The groups, shortest exposure first and then earliest night first.
    """
    if minimum_frames_per_group is None:
        minimum_frames_per_group = resolve(MINIMUM_FRAMES_PER_EXPOSURE_GROUP).value
    result: list[ExposureGroup] = []
    for group in groups:
        nights = [_frame_night(frame) for frame in group.frames]
        if any(night is None for night in nights) or len(set(nights)) < 2:
            result.append(group)
            continue
        by_night: dict[date, list[Any]] = defaultdict(list)
        for frame, night in zip(group.frames, nights, strict=True):
            by_night[night].append(frame)
        large = {
            night: members for night, members in by_night.items() if len(members) >= minimum_frames_per_group
        }
        if len(large) < 2:
            result.append(group)
            continue
        for night, members in by_night.items():
            if night not in large:
                nearest = min(
                    large, key=lambda large_night, small_night=night: abs((large_night - small_night).days)
                )
                large[nearest].extend(members)
        position = {id(frame): index for index, frame in enumerate(group.frames)}
        for night in sorted(large):
            members = sorted(large[night], key=lambda frame: position[id(frame)])
            result.append(ExposureGroup(group.exposure_seconds, members, night.isoformat(), group.iso))
    return result


def exposure_group_iso(frames: list[Any]) -> float | None:
    """Name the ISO that all the frames of a group share.

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    iso : `float` or `None`
        The one ISO every frame has, or `None` when a frame has no readable
        ISO or the frames differ.
    """
    isos = {frame_iso_value(frame) for frame in frames}
    return isos.pop() if len(isos) == 1 else None


def _mean_exposure(exposures: list[float | None]) -> float:
    """Average the readable exposure lengths.

    Returns
    -------
    mean_exposure : `float`
        The mean in seconds, or 0.0 when none of the lengths is readable.
    """
    readable = [exposure for exposure in exposures if exposure is not None]
    return float(np.mean(readable)) if readable else 0.0


def _frames_of_representative_exposure(frames: list[Any]) -> list[Any]:
    """Pick the frames that share the most common exposure length.

    Exposure lengths are compared to three significant figures, the same way
    `split_frames_by_exposure` compares them. When two lengths are equally
    common, the one nearest the median exposure of all the frames wins (the
    shorter one if both are equally near).

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    members : `list`
        The frames of the winning length, in their original order. Empty
        when no frame has a readable exposure length.
    """
    readable = [
        (float(f"{exposure:.3g}"), frame)
        for frame in frames
        if (exposure := frame_exposure_seconds(frame)) is not None
    ]
    if not readable:
        return []
    counts: dict[float, int] = defaultdict(int)
    for key, _ in readable:
        counts[key] += 1
    most_frames = max(counts.values())
    median_exposure = float(np.median([key for key, _ in readable]))
    winner = min(
        (key for key, count in counts.items() if count == most_frames),
        key=lambda key: (abs(key - median_exposure), key),
    )
    return [frame for key, frame in readable if key == winner]


def representative_exposure_seconds(frames: list[Any]) -> float:
    """Name the exposure length that best describes a set of frames.

    A group that `split_frames_by_exposure` could not split can still hold a
    few frames of another length: a lone 5 s frame is added to a group of 59
    frames of 0.5 s because a group needs at least
    `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` frames. Reading the first frame would
    then name the group after that stray frame, so the most common length is
    used instead (see `_frames_of_representative_exposure` for ties).

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    exposure_seconds : `float`
        The mean exposure of the frames of the most common length, in
        seconds, or 0.0 when no frame has a readable exposure length.
    """
    members = _frames_of_representative_exposure(frames)
    return _mean_exposure([frame_exposure_seconds(frame) for frame in members])


def representative_exposure_frames(frames: list[Any]) -> list[Any]:
    """Return the frames that have the representative exposure length.

    Used to measure raw-frame clipping on the frames that make up the group,
    not on a stray frame of another length that was added to it.

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    members : `list`
        The frames of the most common length, or all the frames when none
        has a readable exposure length.
    """
    return _frames_of_representative_exposure(frames) or list(frames)
