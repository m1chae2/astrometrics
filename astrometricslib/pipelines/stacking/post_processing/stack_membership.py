"""Works out which frames a stack holds and how much each one counted.

A stack's noise depends on its frames and on the weight each frame was given
when they were averaged. Neither is stored by the stacking program in a form
the library can read back, so this module rebuilds them from what the stage
knows:

- Members. The frames of the exposure groups that were combined, without the
  frames whose files could not be read and without the frames Siril dropped
  while registering (its sequence file marks them). A frame Siril's own stack
  filters (star width or roundness) left out afterwards is not marked anywhere.
  When fewer images were stacked than there are candidates, the member list is
  marked uncertain instead of guessed.
- Weights. With equal weights every frame counts 1. With several exposure
  groups, the combine weights each group by frames x exposure^2 / frame noise^2
  (see `stack_runner._stack_exposure_groups`); a frame's share of that is the
  group weight divided by the group's frame count. When Siril weights the
  frames itself it computes the weights inside and does not print them.
  ``-weight=wfwhm`` is rebuilt exactly, because it needs only each frame's
  weighted star width, which Siril does save in its sequence file (see
  `wfwhm_weights`). ``-weight=noise`` needs a scale Siril fits during
  normalization and does not save, and ``nbstars`` is not rebuilt, so their
  weights are unknown. Unknown is reported as unknown. It is never replaced by
  equal weights.

The effective frame count and effective time say how much a weighted stack is
worth in equal frames and equal seconds.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "StackMember",
    "StackMembership",
    "determine_stack_members",
    "effective_exposure_seconds",
    "effective_frame_count",
    "effective_read_noise_time_seconds",
    "normalized_weights",
    "registration_reference_from_diagnostics",
    "wfwhm_weights",
]


@dataclass(frozen=True)
class StackMember:
    """One frame that went into a stack.

    Parameters
    ----------
    frame : `Any`
        The frame record (it has ``path``, ``exposure`` and ``timestamp``).
    raw_weight : `float` or `None`
        The weight in the stacker's own scale, or `None` when it is unknown.
    """

    frame: Any
    raw_weight: float | None


@dataclass(frozen=True)
class StackMembership:
    """The frames of one stack and how sure we are of the list and the weights.

    Parameters
    ----------
    members : `tuple` of `StackMember`
        The frames, in the order they were given to the stage.
    weights_reason : `str`
        Why the weights are unknown. Empty when every member has a weight.
    membership_reason : `str`
        Why the member list may include frames that were not stacked. Empty
        when the list is certain.
    """

    members: tuple[StackMember, ...]
    weights_reason: str = ""
    membership_reason: str = ""


def _dropped_at_registration(diagnostics: dict[str, Any]) -> set[str]:
    """Find the frames Siril marked as not included in its sequence file.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported. ``symlinked_light_paths`` and
        ``registration_included`` are read.

    Returns
    -------
    dropped : `set` of `str`
        The paths of the dropped frames. Empty when the two lists are missing
        or do not line up, because then no frame can be named safely.
    """
    paths = diagnostics.get("symlinked_light_paths") or []
    included = diagnostics.get("registration_included") or []
    if not paths or len(paths) != len(included):
        return set()
    return {path for path, flag in zip(paths, included, strict=True) if flag is False}


def registration_reference_from_diagnostics(diagnostics: dict[str, Any]) -> tuple[str, int | None] | None:
    """Find the frame Siril lined every other frame up with.

    The reference frame is the one whose transform is the identity (it has no
    shift of its own). The answer is given only when it is certain: the frame
    paths and the registration facts must have the same length, so a position
    in one names the same frame in the other, and exactly one frame must have
    no shift. A sequence of frames that were already aligned has a zero shift
    for every frame, so no single frame can be named as "the" reference, and
    naming the first would be made up.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported. ``registration_frames`` (one dict per
        frame with ``dx``, ``dy`` and ``star_count``) and
        ``symlinked_light_paths`` are read.

    Returns
    -------
    reference : `tuple` [`str`, `int` or `None`] or `None`
        The reference frame's path and the number of stars Siril found in it,
        or `None` when no single frame can be named.
    """
    registration_frames = diagnostics.get("registration_frames", [])
    registration_frame_paths = diagnostics.get("symlinked_light_paths", [])
    zero_shift_indices = [
        index for index, facts in enumerate(registration_frames) if not facts["dx"] and not facts["dy"]
    ]
    if len(zero_shift_indices) != 1 or len(registration_frame_paths) != len(registration_frames):
        return None
    reference_index = zero_shift_indices[0]
    return registration_frame_paths[reference_index], registration_frames[reference_index]["star_count"]


def wfwhm_weights(weighted_fwhm_px: Sequence[float]) -> list[float]:
    """Rebuild Siril's star-width weights (``-weight=wfwhm``) for some frames.

    Siril (``compute_wfwhm_weights`` in ``src/stacking/median_and_mean.c``)
    gives each stacked image with a positive weighted width ``w`` the weight
    ``(1/w^2 - 1/w_max^2) / (1/w_min^2 - 1/w_max^2)``, where ``w_min`` and
    ``w_max`` are the smallest and largest widths of the stacked images. The
    sharpest frame gets 1 and the blurriest gets 0. An image whose width is not
    positive gets 0. Each weight is then divided by the mean weight, which is
    the sum over the number of stacked images (zeros included).

    Parameters
    ----------
    weighted_fwhm_px : `Sequence` of `float`
        The weighted FWHM of each stacked image, in pixels, from Siril's
        sequence file.

    Returns
    -------
    weights : `list` of `float`
        One weight per image, with mean 1. When all positive widths are equal
        the formula divides by zero; every image with a positive width then
        gets the same weight, and an input with no positive width gets all
        zeros (mean 0, so the caller treats the weights as unknown).
    """
    positive = [width for width in weighted_fwhm_px if width > 0]
    if not positive:
        return [0.0] * len(weighted_fwhm_px)
    narrowest, widest = min(positive), max(positive)
    if widest == narrowest:
        raw = [1.0 if width > 0 else 0.0 for width in weighted_fwhm_px]
    else:
        denominator = 1.0 / narrowest**2 - 1.0 / widest**2
        raw = [
            (1.0 / width**2 - 1.0 / widest**2) / denominator if width > 0 else 0.0
            for width in weighted_fwhm_px
        ]
    mean = math.fsum(raw) / len(raw)
    return [weight / mean for weight in raw] if mean > 0 else [0.0] * len(raw)


def _star_width_weights(
    candidates: Sequence[Any], diagnostics: dict[str, Any]
) -> tuple[dict[str, float], str]:
    """Rebuild the star-width weights of the candidate frames.

    Parameters
    ----------
    candidates : `Sequence`
        The frames the stack is thought to hold (they have ``path``).
    diagnostics : `dict`
        What the stacking run reported. ``symlinked_light_paths`` and
        ``registration_frames`` are read; they must have the same length so a
        position in one names the same frame in the other.

    Returns
    -------
    weights : `dict` of `str` to `float`
        The weight of each candidate by path. Empty when there is a reason.
    reason : `str`
        Why the weights could not be rebuilt. Empty when they were.
    """
    paths = diagnostics.get("symlinked_light_paths") or []
    facts = diagnostics.get("registration_frames") or []
    if not paths or len(paths) != len(facts):
        return {}, (
            "Siril weighted the frames by star width (-weight=wfwhm) and the sequence file's lines "
            "cannot be matched to the frames"
        )
    width_by_path = {path: fact.get("weighted_fwhm_px") for path, fact in zip(paths, facts, strict=True)}
    widths = [width_by_path.get(frame.path) for frame in candidates]
    if not candidates or any(width is None for width in widths):
        return {}, "Siril weighted the frames by star width (-weight=wfwhm) and a frame has no recorded width"
    weights = wfwhm_weights([float(width) for width in widths])
    if not any(weights):
        return {}, "Siril weighted the frames by star width (-weight=wfwhm) and no frame has a positive width"
    return {frame.path: weight for frame, weight in zip(candidates, weights, strict=True)}, ""


def determine_stack_members(
    target_frames: Sequence[Any], diagnostics: dict[str, Any], stack_weight: str | None
) -> StackMembership:
    """List the frames a stack holds and their weights.

    Parameters
    ----------
    target_frames : `Sequence`
        The frames given to the stacking program.
    diagnostics : `dict`
        What the stacking run reported. Read: ``combined_frame_paths``,
        ``corrupt_frames_skipped``, ``symlinked_light_paths``,
        ``registration_included``, ``images_stacked`` and
        ``exposure_group_weights`` (a list of ``{"paths", "weight",
        "frame_count"}`` for each group that was combined).
    stack_weight : `str` or `None`
        The Siril ``-weight`` mode the stack used, `None` for equal weights.

    Returns
    -------
    membership : `StackMembership`
        The members with raw weights (see the module description).
    """
    combined = diagnostics.get("combined_frame_paths")
    corrupt = set(diagnostics.get("corrupt_frames_skipped", []))
    dropped = _dropped_at_registration(diagnostics)
    candidates = [
        frame
        for frame in target_frames
        if (combined is None or frame.path in combined)
        and frame.path not in corrupt
        and frame.path not in dropped
    ]

    membership_reason = ""
    images_stacked = diagnostics.get("images_stacked")
    if images_stacked is not None and int(images_stacked) < len(candidates):
        membership_reason = (
            f"Siril stacked {int(images_stacked)} of {len(candidates)} candidate frames; its stack "
            "filters left the others out without naming them, so this frame may not be in the stack"
        )

    group_weights = diagnostics.get("exposure_group_weights") or []
    weights_reason = ""
    weight_by_path: dict[str, float] = {}
    if group_weights:
        if stack_weight:
            weights_reason = (
                f"Siril also weights the frames inside each exposure group (-weight={stack_weight}) "
                "and does not report those weights"
            )
        else:
            for group in group_weights:
                count = max(int(group["frame_count"]), 1)
                for path in group["paths"]:
                    weight_by_path[path] = float(group["weight"]) / count
    elif stack_weight == "wfwhm":
        weight_by_path, weights_reason = _star_width_weights(candidates, diagnostics)
    elif stack_weight == "noise":
        weights_reason = (
            "Siril weighted the frames by noise (-weight=noise), 1 / (pscale^2 x bgnoise^2). The "
            "sequence file does not hold the scale Siril fits (pscale) while normalizing, "
            "so the weights cannot be rebuilt"
        )
    elif stack_weight:
        weights_reason = (
            f"Siril weighted the frames itself (-weight={stack_weight}) and does not report the weights"
        )

    members = []
    for frame in candidates:
        if (group_weights or stack_weight == "wfwhm") and not weights_reason:
            raw_weight = weight_by_path.get(frame.path)
        elif weights_reason:
            raw_weight = None
        else:
            raw_weight = 1.0
        members.append(StackMember(frame=frame, raw_weight=raw_weight))
    if any(member.raw_weight is None for member in members) and not weights_reason:
        weights_reason = "the stacker reported no weight for this frame's exposure group"
    return StackMembership(tuple(members), weights_reason, membership_reason)


def normalized_weights(membership: StackMembership) -> list[float | None]:
    """Scale the members' weights so their mean is 1.

    Parameters
    ----------
    membership : `StackMembership`
        The stack's members.

    Returns
    -------
    weights : `list` of `float` or `None`
        One weight per member, in order, with mean 1. Every entry is `None`
        when any weight is unknown, because a mean over some weights would
        misstate the rest.
    """
    raw = [member.raw_weight for member in membership.members]
    if not raw or any(weight is None for weight in raw):
        return [None] * len(raw)
    mean = math.fsum(raw) / len(raw)
    if mean <= 0:
        return [None] * len(raw)
    return [weight / mean for weight in raw]


def effective_frame_count(weights: Sequence[float | None]) -> float | None:
    """Count how many equally weighted frames the weighted frames are worth.

    This is Kish's effective sample size, N_eff = (sum w)^2 / sum(w^2): the
    variance of a weighted mean of equally noisy frames, compared with the
    variance of a plain mean of N_eff frames.

    Parameters
    ----------
    weights : `Sequence` of `float` or `None`
        Each member's weight, in any scale.

    Returns
    -------
    count : `float` or `None`
        N_eff, or `None` when there are no weights or any is unknown.
    """
    if not weights or any(weight is None or weight < 0 for weight in weights):
        return None
    total = math.fsum(weights)
    squares = math.fsum(weight * weight for weight in weights)
    return None if squares <= 0 else total * total / squares


def effective_exposure_seconds(
    weights: Sequence[float | None], exposures: Sequence[float | None]
) -> float | None:
    """Give how many seconds of equal-weight light the stack is worth.

    Each frame gives an estimate of the sky's rate (counts per second). In
    photon-limited light its variance is proportional to 1 / t. A weighted mean
    of the rates then has variance proportional to sum(w^2 / t) / (sum w)^2,
    while an equal-weight stack of T seconds has variance proportional to
    1 / T. Setting them equal gives T_eff = (sum w)^2 / sum(w^2 / t). With
    equal weights and equal exposures it is the total exposure time.

    Parameters
    ----------
    weights : `Sequence` of `float` or `None`
        Each member's weight, in any scale.
    exposures : `Sequence` of `float` or `None`
        Each member's exposure time in seconds, in the same order.

    Returns
    -------
    seconds : `float` or `None`
        T_eff, or `None` when a weight or an exposure is unknown or not
        positive.
    """
    if not weights or len(weights) != len(exposures):
        return None
    if any(weight is None or weight < 0 for weight in weights):
        return None
    if any(exposure is None or exposure <= 0 for exposure in exposures):
        return None
    total = math.fsum(weights)
    denominator = math.fsum(
        weight * weight / exposure for weight, exposure in zip(weights, exposures, strict=True)
    )
    return None if denominator <= 0 else total * total / denominator


def effective_read_noise_time_seconds(
    weights: Sequence[float | None], exposures: Sequence[float | None]
) -> float | None:
    """Give the single-frame exposure with the same read noise as the stack.

    Each frame gives an estimate of the sky's rate (counts per second). Read
    noise adds a variance RN^2 to the counts of every frame whatever its
    length, so in the rate it is RN^2 / t^2. A weighted mean of the rates then
    has a read-noise variance RN^2 x sum(w^2 / t^2) / (sum w)^2. One frame of
    tau seconds has RN^2 / tau^2. Setting them equal gives tau = sum(w) /
    sqrt(sum(w^2 / t^2)). For N equal frames of t seconds it is t x sqrt(N).
    Photon noise is described by `effective_exposure_seconds` instead; a stack
    of frames of different lengths needs both, because the two kinds of noise
    weight the frames differently.

    Parameters
    ----------
    weights : `Sequence` of `float` or `None`
        Each member's weight, in any scale.
    exposures : `Sequence` of `float` or `None`
        Each member's exposure time in seconds, in the same order.

    Returns
    -------
    seconds : `float` or `None`
        tau in seconds, or `None` when a weight or an exposure is unknown or
        not positive.
    """
    if not weights or len(weights) != len(exposures):
        return None
    if any(weight is None or weight < 0 for weight in weights):
        return None
    if any(exposure is None or exposure <= 0 for exposure in exposures):
        return None
    total = math.fsum(weights)
    denominator = math.fsum(
        weight * weight / (exposure * exposure) for weight, exposure in zip(weights, exposures, strict=True)
    )
    return None if denominator <= 0 else total / math.sqrt(denominator)
