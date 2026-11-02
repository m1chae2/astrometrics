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

1. Split the frames into groups that share one exposure length
   (`split_frames_by_exposure`). Each group is stacked on its own, where
   the usual recipe is safe.
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
   than the reference's is also left out (`SKY_RATIO_LIMIT`).
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

from astrometricslib.pipelines.shared.frame_grouping import frame_exposure_seconds
from astrometricslib.pipelines.shared.target_sessions import compute_session_night

# The fewest frames a group of one exposure length may have and still be
# stacked on its own. A group smaller than this cannot be registered or
# clipped reliably (registration needs a reference and other frames to match
# it against, and clipping needs several values per pixel), so its frames are
# added to the group with the nearest exposure length instead. Unvalidated:
# on the Vega session every group has at least 20 frames, and the smallest
# groups in the library (1-2 frames each, from short calibration sets) only
# need to be small enough to be folded away. It has not been tested at 5.
MINIMUM_FRAMES_PER_EXPOSURE_GROUP = 5


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
    """

    exposure_seconds: float
    frames: list[Any] = field(default_factory=list)
    night: str | None = None


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


def fold_exposure_lengths(
    exposures_seconds: Sequence[float | None],
    minimum_frames_per_group: int = MINIMUM_FRAMES_PER_EXPOSURE_GROUP,
) -> list[float | None]:
    """Say which exposure group each frame lands in.

    Frames whose exposure lengths match (to three significant figures, see
    `exposure_group_key`) share a group. A length that has fewer than
    `minimum_frames_per_group` frames is folded into the group with the
    nearest length (nearest in ratio, so 2 s is nearer to 1 s than to 5 s).
    This one rule is used by `split_frames_by_exposure` to build the groups
    and by the stack records to find a group that holds frames of another
    length, whose weights are therefore unknown.

    Parameters
    ----------
    exposures_seconds : `Sequence` [`float` or `None`]
        The exposure length of each frame, in seconds, or `None` for a frame
        whose length cannot be read.
    minimum_frames_per_group : `int`, optional
        The fewest frames a length needs to be stacked on its own.

    Returns
    -------
    group_lengths : `list` [`float` or `None`]
        For each frame, the rounded exposure length of the group it lands in.
        Every entry is `None` when there are no frames, when any frame has no
        readable length, or when no length has enough frames. All the frames
        then stay in one group, which is the same as stacking them together.
    """
    count = len(exposures_seconds)
    if not count or any(exposure is None for exposure in exposures_seconds):
        return [None] * count
    keys = [exposure_group_key(exposure) for exposure in exposures_seconds if exposure is not None]
    frames_per_key: dict[float, int] = defaultdict(int)
    for key in keys:
        frames_per_key[key] += 1
    if len(frames_per_key) == 1:
        return list(keys)
    large = [key for key, frames in frames_per_key.items() if frames >= minimum_frames_per_group]
    if not large:
        return [None] * count
    destination: dict[float, float] = {}
    for key in frames_per_key:
        if key in large:
            destination[key] = key
            continue
        # An exposure of exactly 0 has no ratio; a tiny stand-in keeps it
        # nearest to the shortest group instead of raising.
        destination[key] = min(
            large,
            key=lambda large_key, small_key=key: abs(math.log(max(large_key, 1e-9) / max(small_key, 1e-9))),
        )
    return [destination[key] for key in keys]


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
    frames: list[Any], minimum_frames_per_group: int = MINIMUM_FRAMES_PER_EXPOSURE_GROUP
) -> list[ExposureGroup]:
    """Split frames into groups that share one exposure length.

    Frames whose exposure lengths match (to three decimal places) go in one
    group. A group with fewer than `minimum_frames_per_group` frames is
    added to the group with the nearest exposure length (nearest in ratio,
    so 2 s is nearer to 1 s than to 5 s). If any frame has no readable
    exposure length, or no group is large enough, everything stays in one
    group, which is the same as stacking the frames together as before. The
    folding itself is `fold_exposure_lengths`.

    Parameters
    ----------
    frames : `list`
        The frame records to split.
    minimum_frames_per_group : `int`, optional
        The fewest frames a group needs to be stacked on its own.

    Returns
    -------
    groups : `list` [`ExposureGroup`]
        The groups, shortest exposure first. Always at least one.
    """
    exposures = [frame_exposure_seconds(frame) for frame in frames]
    group_lengths = fold_exposure_lengths(exposures, minimum_frames_per_group)
    if not frames or group_lengths[0] is None:
        return [ExposureGroup(_mean_exposure(exposures), list(frames))]
    if len(set(group_lengths)) == 1:
        return [ExposureGroup(group_lengths[0], list(frames))]

    members_by_length: dict[float, list[Any]] = defaultdict(list)
    for frame, group_length in zip(frames, group_lengths, strict=True):
        members_by_length[group_length].append(frame)
    groups = []
    for group_length in sorted(members_by_length):
        members = members_by_length[group_length]
        groups.append(
            ExposureGroup(_mean_exposure([frame_exposure_seconds(frame) for frame in members]), members)
        )
    return groups


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
    groups: list[ExposureGroup], minimum_frames_per_group: int = MINIMUM_FRAMES_PER_EXPOSURE_GROUP
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
            result.append(ExposureGroup(group.exposure_seconds, members, night.isoformat()))
    return result


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
