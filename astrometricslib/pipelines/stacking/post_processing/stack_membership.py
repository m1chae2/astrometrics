"""Works out which frames a stack holds and how much each one counted.

A stack's noise depends on its frames and on the weight each frame was given
when they were averaged. Neither is stored by the stacking program in a form
the library can read back, so this module rebuilds them from what the stage
knows:

- Members. The frames of the exposure groups that were combined, without the
  frames whose files could not be read and without the frames Siril dropped
  while registering (its sequence file marks them; a group stack reads the
  marks from each group's own entry). The stack's frame count comes from this
  same list (see `count_frames_stacked`). A frame Siril's own stack
  filters (star width or roundness) left out afterwards is not marked anywhere.
  When fewer images were stacked than there are candidates, the member list is
  marked uncertain instead of guessed.
- Weights. With equal weights every frame counts 1. With several exposure
  groups, the combine weights each group by frames x exposure^2 / frame noise^2
  (see `stack_runner._stack_exposure_groups`); a frame's share of that is the
  group weight times the frame's share of its group, which is Siril's weight
  inside the group over the group's total (see `group_frame_weights`). The
  brightness gain of a group is not part of the weight; it is stored on its
  own. When Siril weights the frames itself it computes the weights inside and
  does not print them.
  ``-weight=wfwhm`` is rebuilt exactly, because it needs only each frame's
  weighted star width, which Siril does save in its sequence file (see
  `wfwhm_weights`). ``-weight=noise`` needs a scale Siril fits during
  normalization and does not save, and ``nbstars`` is not rebuilt, so their
  weights are unknown. Unknown is reported as unknown. It is never replaced by
  equal weights.
- Reference frame. A group stack is lined up with its heaviest group, so the
  reference frame is the identity line of that group, leaving out the lines of
  frames Siril dropped (see `registration_reference_from_diagnostics`).
- Folded groups. A group that holds frames of another exposure length (a short
  list of them was folded into it) is stacked like any other group: Siril
  matches every frame to the group's own registration reference frame
  (``-norm=addscale``) and weights it by star width, not by exposure. A
  frame of length ``t_i`` in a group whose reference frame has length
  ``t_ref`` is therefore multiplied by about ``k_i = t_ref / t_i``, and its
  noise in the group's units is ``k_i`` times its own.
  `group_frame_weights` gives the weights of such a group, and
  `group_exposure_scales` gives the ``k_i``. The
  ``k_i`` come from the exposure ratio, which is a design estimate: Siril fits
  the scale from the frame's stars, and that fit also absorbs transparency. A
  group whose reference frame cannot be identified, or that holds a frame with
  no usable exposure length, is refused with a reason.

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
    "GroupFrameWeights",
    "GroupReference",
    "StackMember",
    "StackMembership",
    "count_frames_stacked",
    "determine_stack_members",
    "effective_exposure_seconds",
    "effective_frame_count",
    "effective_read_noise_time_seconds",
    "group_exposure_scales",
    "group_frame_weights",
    "group_references",
    "group_registration_reference",
    "normalized_weights",
    "reference_group_frame_paths",
    "registration_reference_from_diagnostics",
    "stack_candidate_frames",
    "wfwhm_weights",
]


@dataclass(frozen=True)
class GroupReference:
    """The exposure of one combined group's registration reference frame.

    Parameters
    ----------
    summary_index : `int` or `None`
        The position of the group in the stack's ``exposure_group_summaries``
        list (where the group's own quality rows are filed), or `None` when
        the entry does not say.
    reference_path : `str` or `None`
        The registration reference frame of the group. `None` for a group with
        one exposure length, where every frame has the same length and no
        single frame needs naming.
    exposure_seconds : `float` or `None`
        The exposure length of the reference frame, or `None` when it is not
        known.
    reason : `str`
        Why the exposure is not known. Empty when it is.
    """

    summary_index: int | None
    reference_path: str | None
    exposure_seconds: float | None
    reason: str = ""


@dataclass(frozen=True)
class StackMember:
    """One frame that went into a stack.

    Parameters
    ----------
    frame : `Any`
        The frame record (it has ``path``, ``exposure`` and ``timestamp``).
    raw_weight : `float` or `None`
        The weight in the stacker's own scale, or `None` when it is unknown.
    exposure_scale : `float`, optional
        ``k_i``, the factor Siril multiplied the frame by to match the frame
        to its group's registration reference frame, from the exposure ratio
        (see `group_exposure_scales`). 1 for a frame of a group with one
        exposure length and for a stack that is not made of groups.
    """

    frame: Any
    raw_weight: float | None
    exposure_scale: float = 1.0


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
    group_references : `tuple` of `GroupReference`, optional
        For a stack of exposure groups, one entry per combined group (in the
        order of the ``exposure_group_weights`` entries). Empty otherwise.
    """

    members: tuple[StackMember, ...]
    weights_reason: str = ""
    membership_reason: str = ""
    group_references: tuple[GroupReference, ...] = ()


def _paths_marked_not_included(paths: Sequence[Any], included: Sequence[Any]) -> set[str]:
    """Pick the paths whose Siril inclusion flag is `False`.

    Parameters
    ----------
    paths : `Sequence`
        The frame paths, in the order they were given to Siril.
    included : `Sequence`
        Siril's inclusion flag for each frame (``I n 0`` is `False`).

    Returns
    -------
    dropped : `set` of `str`
        The paths of the dropped frames. Empty when either list is missing or
        the two do not line up, because then no frame can be named safely.
    """
    if not paths or len(paths) != len(included):
        return set()
    return {str(path) for path, flag in zip(paths, included, strict=True) if flag is False}


def _dropped_at_registration(diagnostics: dict[str, Any]) -> set[str]:
    """Find the frames Siril marked as not included in its sequence file.

    A stack of exposure groups keeps each group's own path and flag lists in
    its ``exposure_group_weights`` entries, because the joined top-level lists
    do not line up (the flags are those of the first group only). Those entries
    are used when they exist.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported. ``exposure_group_weights`` is read
        when present; otherwise ``symlinked_light_paths`` and
        ``registration_included``.

    Returns
    -------
    dropped : `set` of `str`
        The paths of the dropped frames. Empty when the lists are missing
        or do not line up, because then no frame can be named safely.
    """
    groups = diagnostics.get("exposure_group_weights") or []
    if groups:
        dropped: set[str] = set()
        for group in groups:
            dropped |= _paths_marked_not_included(
                group.get("frame_paths") or [], group.get("registration_included") or []
            )
        return dropped
    return _paths_marked_not_included(
        diagnostics.get("symlinked_light_paths") or [], diagnostics.get("registration_included") or []
    )


def stack_candidate_frames(target_frames: Sequence[Any], diagnostics: dict[str, Any]) -> list[Any]:
    """List the frames that went into a stack.

    These are the frames of the exposure groups that were combined, without the
    frames whose files could not be read and without the frames Siril dropped
    while registering. This is the one list behind both the stack's frame count
    and its member rows, so the two cannot disagree.

    Parameters
    ----------
    target_frames : `Sequence`
        The frames given to the stacking program (they have ``path``).
    diagnostics : `dict`
        What the stacking run reported (``combined_frame_paths``,
        ``corrupt_frames_skipped`` and the registration flags; see
        `_dropped_at_registration`).

    Returns
    -------
    frames : `list`
        The frames, in the order they were given.
    """
    combined = diagnostics.get("combined_frame_paths")
    corrupt = set(diagnostics.get("corrupt_frames_skipped", []))
    dropped = _dropped_at_registration(diagnostics)
    return [
        frame
        for frame in target_frames
        if (combined is None or frame.path in combined)
        and frame.path not in corrupt
        and frame.path not in dropped
    ]


def count_frames_stacked(target_frames: Sequence[Any], diagnostics: dict[str, Any]) -> int:
    """Count the frames that went into a stack.

    The count is the number of frames in `stack_candidate_frames`: submitted,
    less the frames of groups that were not combined, the unreadable files and
    the frames Siril left out while registering. When Siril also reports that
    it stacked fewer images than that (its own star-width or roundness filters
    left some out without naming them), Siril's number is used, because it is
    the count of what the program actually combined.

    Parameters
    ----------
    target_frames : `Sequence`
        The frames given to the stacking program.
    diagnostics : `dict`
        What the stacking run reported.

    Returns
    -------
    count : `int`
        The number of frames in the stack. When the registration flags are
        missing or do not line up with the frame paths, the frames Siril left
        out cannot be named and are not subtracted.
    """
    count = len(stack_candidate_frames(target_frames, diagnostics))
    images_stacked = diagnostics.get("images_stacked")
    if images_stacked is not None and int(images_stacked) < count:
        return int(images_stacked)
    return count


def _is_unregistered_line(facts: dict[str, Any]) -> bool:
    """Tell whether a registration line is Siril's empty entry for a frame.

    Siril keeps a line for a frame it could not register, with every number of
    the transform at zero. Read as a shift, that line looks like "no shift",
    the same as the reference frame's identity transform. The identity has 1 on
    the diagonal; the empty entry does not.

    Parameters
    ----------
    facts : `dict`
        One registration line (``homography`` is read when present).

    Returns
    -------
    empty : `bool`
        `True` when the line has a transform made only of zeros.
    """
    homography = facts.get("homography")
    return bool(homography) and all(not value for value in homography)


def _registration_line_owners(
    paths: Sequence[str], line_count: int, included: Sequence[bool]
) -> list[str | None] | None:
    """Pair each registration line with the frame it describes.

    Siril's sequence file names no frames, so the pairing is by position. The
    pairing is made only when it is safe: either every frame has a line, or
    the lines are exactly as many as the frames Siril kept (then a dropped
    frame has no line and the kept frames take the lines in order).

    Parameters
    ----------
    paths : `Sequence` of `str`
        The frame paths, in the order they were given to Siril.
    line_count : `int`
        The number of registration lines.
    included : `Sequence` of `bool`
        Siril's inclusion flag for each frame, or an empty sequence.

    Returns
    -------
    owners : `list` of (`str` or `None`) or `None`
        The path that owns each line, or `None` for the whole answer when no
        safe pairing exists. A line of a dropped frame is paired with `None`,
        so that it can never be taken as the reference.
    """
    if line_count == len(paths):
        flags = list(included) if len(included) == len(paths) else [True] * len(paths)
        return [path if flag else None for path, flag in zip(paths, flags, strict=True)]
    if len(included) == len(paths) and line_count == sum(1 for flag in included if flag):
        return [path for path, flag in zip(paths, included, strict=True) if flag]
    return None


def registration_reference_from_diagnostics(diagnostics: dict[str, Any]) -> tuple[str, int | None] | None:
    """Find the frame Siril lined every other frame up with.

    The reference frame is the one whose transform is the identity (it has no
    shift of its own). The answer is given only when it is certain. The
    registration lines must be paired with frame paths safely (see
    `_registration_line_owners`). Lines of frames Siril dropped do not count:
    Siril keeps an all-zero line for such a frame, which looks like a zero
    shift. Exactly one remaining frame must have no shift. A sequence of frames
    that were already aligned has a zero shift for every frame, so no single
    frame can be named as "the" reference, and naming the first would be made
    up.

    A stack combined from exposure groups has one identity line per group. It
    is lined up with its heaviest group (the one with the largest combine
    weight, see `stack_runner._stack_exposure_groups`), so the reference is
    the identity line inside that group's own lines.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported. ``registration_frames`` (one dict per
        frame with ``dx``, ``dy`` and ``star_count``),
        ``symlinked_light_paths`` and ``registration_included`` are read; for
        a group stack, the same lists inside the heaviest entry of
        ``exposure_group_weights`` (``registration_frames``, ``frame_paths``
        and ``registration_included``).

    Returns
    -------
    reference : `tuple` [`str`, `int` or `None`] or `None`
        The reference frame's path and the number of stars Siril found in it,
        or `None` when no single frame can be named.
    """
    groups = diagnostics.get("exposure_group_weights") or []
    if groups:
        source = max(groups, key=lambda group: float(group.get("weight") or 0.0))
        return group_registration_reference(source)
    return _identity_reference(
        diagnostics.get("registration_frames", []),
        diagnostics.get("symlinked_light_paths", []),
        diagnostics.get("registration_included") or [],
    )


def group_registration_reference(entry: dict[str, Any]) -> tuple[str, int | None] | None:
    """Find the frame Siril lined up one exposure group's frames with.

    The same rule as `registration_reference_from_diagnostics`, applied to the
    lists of one ``exposure_group_weights`` entry. Each group is registered on
    its own, so each has its own identity line.

    Parameters
    ----------
    entry : `dict`
        One group's entry. ``registration_frames``, ``frame_paths`` and
        ``registration_included`` are read.

    Returns
    -------
    reference : `tuple` [`str`, `int` or `None`] or `None`
        The reference frame's path and its star count, or `None` when no
        single frame can be named.
    """
    return _identity_reference(
        entry.get("registration_frames") or [],
        entry.get("frame_paths") or [],
        entry.get("registration_included") or [],
    )


def _identity_reference(
    registration_frames: Sequence[dict[str, Any]], paths: Sequence[str], included: Sequence[bool]
) -> tuple[str, int | None] | None:
    """Pick the one frame with no shift out of one run's registration lines.

    Parameters
    ----------
    registration_frames : `Sequence` of `dict`
        One registration line per frame (``dx``, ``dy``, ``star_count``).
    paths : `Sequence` of `str`
        The frame paths, in the order they were given to Siril.
    included : `Sequence` of `bool`
        Siril's inclusion flags, or an empty sequence.

    Returns
    -------
    reference : `tuple` [`str`, `int` or `None`] or `None`
        The path and star count of the single identity frame, or `None`.
    """
    owners = _registration_line_owners(paths, len(registration_frames), included)
    if owners is None:
        return None
    candidates = [
        (owner, facts)
        for owner, facts in zip(owners, registration_frames, strict=True)
        if owner is not None and not _is_unregistered_line(facts) and not facts["dx"] and not facts["dy"]
    ]
    if len(candidates) != 1:
        return None
    owner, facts = candidates[0]
    return owner, facts["star_count"]


def reference_group_frame_paths(diagnostics: dict[str, Any]) -> list[str]:
    """List the frames of the group a combined stack was lined up with.

    Used when no single reference frame can be named: any registered frame of
    the heaviest group has a plate solution close to the stack's geometry.
    Frames Siril dropped are left out when its flags can be paired with the
    frames (see `_registration_line_owners`).

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported (``exposure_group_weights``).

    Returns
    -------
    paths : `list` of `str`
        The heaviest group's frame paths, in the order they were given to
        Siril. Empty for a stack of one group or when the entries are missing.
    """
    groups = diagnostics.get("exposure_group_weights") or []
    if not groups:
        return []
    heaviest = max(groups, key=lambda group: float(group.get("weight") or 0.0))
    paths = [str(path) for path in heaviest.get("frame_paths") or heaviest.get("paths") or []]
    included = heaviest.get("registration_included") or []
    if len(included) == len(paths):
        return [path for path, flag in zip(paths, included, strict=True) if flag]
    return paths


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


@dataclass(frozen=True)
class GroupFrameWeights:
    """The frame weights of a stack combined from exposure groups.

    Parameters
    ----------
    weights : `tuple` of (`tuple` of `float`) or `None`
        One entry per group. Each is the raw weight of the group's frames, in
        the order of the group's in-group weights, or `None` when the group's
        weights are unknown.
    reasons : `tuple` of `str`
        One entry per group: why its weights are unknown, or an empty string
        when they are known.
    exposure_scales : `tuple` of (`tuple` of `float`) or `None`
        One entry per group, with ``k_i`` for each frame in the order of the
        group's weights (see `group_exposure_scales`), or `None` when the
        group's weights are unknown. Empty when not worked out.
    """

    weights: tuple[tuple[float, ...] | None, ...]
    reasons: tuple[str, ...]
    exposure_scales: tuple[tuple[float, ...] | None, ...] = ()


def _frame_exposure_seconds(frame: Any) -> float | None:
    """Read a frame record's exposure length.

    Parameters
    ----------
    frame : `Any`
        A frame record with an ``exposure`` attribute.

    Returns
    -------
    exposure_seconds : `float` or `None`
        The length in seconds, or `None` when it is missing, not a number, not
        finite or not above zero.
    """
    try:
        value = float(frame.exposure)
    except AttributeError, TypeError, ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def group_exposure_scales(
    frame_exposures: Sequence[float | None],
    holds_folded_frames: bool,
    reference_exposure_seconds: float | None,
) -> list[float] | None:
    """Give the factor Siril multiplied each frame of a group by.

    Siril normalizes (``-norm=addscale``) every frame of a group to the group's
    registration reference frame, so that a star has the same flux in every
    frame. A frame of ``t_i`` seconds holds about ``t_i / t_ref`` of the
    reference frame's light, so it is multiplied by ``k_i = t_ref / t_i`` and
    its noise grows by the same factor. A group with one exposure length has
    ``k_i = 1`` for every frame: its frames differ only by the small scale
    Siril fits for resampling, which `normalization_scale.py` measures for the
    stack as a whole.

    The ratio is a design estimate. Siril fits the scale from the frame's
    stars, so the fitted number also absorbs the frame's transparency and
    differs from the exposure ratio by that much.

    Parameters
    ----------
    frame_exposures : `Sequence` of (`float` or `None`)
        The exposure length of each frame of the group, in seconds.
    holds_folded_frames : `bool`
        Whether the group holds frames of more than one exposure length.
    reference_exposure_seconds : `float` or `None`
        The exposure length of the group's registration reference frame.

    Returns
    -------
    scales : `list` of `float` or `None`
        ``k_i`` for each frame, all exactly 1 for a group with one exposure
        length. `None` for a folded group when a frame has no usable exposure
        length or the reference exposure is not known.
    """
    if not holds_folded_frames:
        return [1.0] * len(frame_exposures)
    if (
        reference_exposure_seconds is None
        or not math.isfinite(reference_exposure_seconds)
        or reference_exposure_seconds <= 0
    ):
        return None
    scales = []
    for exposure in frame_exposures:
        if exposure is None or not math.isfinite(exposure) or exposure <= 0:
            return None
        scales.append(reference_exposure_seconds / exposure)
    return scales


def group_references(
    candidates: Sequence[Any], group_entries: Sequence[dict[str, Any]]
) -> list[GroupReference]:
    """Find the exposure of each combined group's registration reference frame.

    For a group with one exposure length the answer is the group's exposure,
    because every frame has it and the reference frame needs no name. For a
    folded group the reference frame is read from the group's registration
    lines (`group_registration_reference`). It is not assumed to have the
    group's main length: Siril picks the best frame, and a long frame that
    holds more stars can win.

    Parameters
    ----------
    candidates : `Sequence`
        The frames the stack is thought to hold (they have ``path`` and
        ``exposure``).
    group_entries : `Sequence` of `dict`
        The stack's ``exposure_group_weights`` entries.
        ``holds_folded_frames``, ``exposure_seconds`` and ``summary_index``
        are read, and the lists that `group_registration_reference` reads.

    Returns
    -------
    references : `list` of `GroupReference`
        One entry per group, in the order of `group_entries`.
    """
    frame_by_path = {str(frame.path): frame for frame in candidates}
    references = []
    for entry in group_entries:
        summary_index = entry.get("summary_index")
        label = f"the {float(entry.get('exposure_seconds') or 0.0):g} s exposure group"
        if not entry.get("holds_folded_frames"):
            exposure = entry.get("exposure_seconds")
            if isinstance(exposure, int | float) and math.isfinite(exposure) and exposure > 0:
                references.append(GroupReference(summary_index, None, float(exposure), ""))
            else:
                references.append(GroupReference(summary_index, None, None, f"{label} reported no exposure"))
            continue
        found = group_registration_reference(entry)
        if found is None:
            references.append(
                GroupReference(
                    summary_index,
                    None,
                    None,
                    f"no single registration reference frame can be identified in {label}, "
                    "which holds frames of another exposure length",
                )
            )
            continue
        path = found[0]
        frame = frame_by_path.get(path)
        exposure = None if frame is None else _frame_exposure_seconds(frame)
        if exposure is None:
            references.append(
                GroupReference(
                    summary_index,
                    path,
                    None,
                    f"the registration reference frame of {label} has no usable exposure length",
                )
            )
        else:
            references.append(GroupReference(summary_index, path, exposure, ""))
    return references


def _folded_group_scales(
    label: str,
    lengths: Sequence[float | None],
    reference_exposure_seconds: float | None,
    reference_reason: str,
    group_size: int,
) -> tuple[str, list[float]]:
    """Work out the exposure scales of a group that holds folded frames.

    Parameters
    ----------
    label : `str`
        The group's name for a reason, such as "the 120 s exposure group".
    lengths : `Sequence` of (`float` or `None`)
        The exposure length of each frame of the group.
    reference_exposure_seconds : `float` or `None`
        The exposure length of the group's registration reference frame.
    reference_reason : `str`
        Why the reference exposure is not known (may be empty).
    group_size : `int`
        How many frames the in-group weights cover, or -1 when they are not
        known.

    Returns
    -------
    reason, scales : `tuple` [`str`, `list` of `float`]
        The reason the group is refused (empty when it is not) and ``k_i`` for
        each frame (empty when it is refused).
    """
    if not lengths or any(length is None or not length > 0 for length in lengths):
        return (
            f"{label} holds frames of another exposure length and a frame has no usable exposure "
            "length, so its frames cannot be put on one scale",
            [],
        )
    scales = group_exposure_scales(lengths, True, reference_exposure_seconds)
    if scales is None:
        return reference_reason or f"the registration reference frame of {label} could not be identified", []
    if 0 <= group_size != len(lengths):
        return f"the frame exposure lengths of {label} do not match its frames", []
    return "", scales


def group_frame_weights(
    in_group_weights: Sequence[Sequence[float] | None],
    exposures_seconds: Sequence[float],
    frame_counts: Sequence[int],
    frame_noises: Sequence[float | None],
    holds_folded_frames: Sequence[bool] | None = None,
    gains_measured: Sequence[bool] | None = None,
    weight_modes: Sequence[str | None] | None = None,
    frame_exposures: Sequence[Sequence[float | None]] | None = None,
    reference_exposures: Sequence[float | None] | None = None,
    reference_reasons: Sequence[str] | None = None,
) -> GroupFrameWeights:
    """Give each frame of an exposure-group stack its raw weight.

    The combine averages the groups with the weight ``W = N t^2 / s^2`` (``N``
    frames stacked, exposure ``t`` seconds, frame noise ``s`` counts). Inside
    a group, Siril weighted the frames by ``w``. Frame ``i`` of group ``g``
    therefore counts ``W_g * w_i / sum(w)`` where the sum runs over the
    group's frames, and these raw weights add up to the sum of the group
    weights. The brightness gain of a group is not a factor here. It is kept
    as its own value, and the noise models divide it in later.

    A group that holds frames of another exposure length (folded in because
    that length had too few frames) is weighted like any other group. Siril
    weights its frames by star width, not by exposure, so the raw weights
    above hold. What changes is the noise: Siril multiplies each frame by
    ``k_i = t_ref / t_i`` to match it to the group's reference frame (see
    `group_exposure_scales`), and the predicted noise uses that factor.

    The weights of a group are unknown, with a reason, when they cannot be
    stated honestly. They are never replaced by equal weights:

    - the group holds frames of another exposure length and a frame has no
      usable exposure length (such as a 0 s frame), or the group's
      registration reference frame cannot be identified, so the frames cannot
      be put on one scale;
    - Siril weighted the group by noise (``-weight=noise``) or another mode
      it does not report;
    - the combine could not measure the group's gain;
    - the group's frame noise could not be read;
    - Siril did not report the group's in-group weights.

    Parameters
    ----------
    in_group_weights : `Sequence` of (`Sequence` of `float` or `None`)
        For each group, the weight Siril gave each frame inside the group (all
        equal for equal weighting), over the frames that were stacked. `None`
        when they are not known.
    exposures_seconds : `Sequence` of `float`
        The exposure length of each group, in seconds.
    frame_counts : `Sequence` of `int`
        The number of frames stacked in each group.
    frame_noises : `Sequence` of `float` or `None`
        The noise of one raw frame of each group, in counts, or `None` when it
        could not be read.
    holds_folded_frames : `Sequence` of `bool`, optional
        For each group, whether it holds frames of another exposure length.
        Defaults to no group.
    gains_measured : `Sequence` of `bool`, optional
        For each group, whether the combine measured its gain. Defaults to
        every group.
    weight_modes : `Sequence` of (`str` or `None`), optional
        The Siril ``-weight`` mode each group was stacked with. `None` is
        equal weights. Defaults to equal weights for every group.
    frame_exposures : `Sequence` of `Sequence`, optional
        For each group (a sequence of `float` or `None`), the exposure
        length of each of its frames, in the order of the in-group weights.
        Needed for a group that holds folded frames.
    reference_exposures : `Sequence` of (`float` or `None`), optional
        For each group, the exposure length of its registration reference
        frame, or `None` when it is not known. Needed for a group that holds
        folded frames.
    reference_reasons : `Sequence` of `str`, optional
        For each group, why its reference exposure is not known.

    Returns
    -------
    result : `GroupFrameWeights`
        The raw weights of each group's frames, their exposure scales, and a
        reason for a group whose weights are unknown.
    """
    group_count = len(in_group_weights)
    folded = list(holds_folded_frames) if holds_folded_frames is not None else [False] * group_count
    measured = list(gains_measured) if gains_measured is not None else [True] * group_count
    modes = list(weight_modes) if weight_modes is not None else [None] * group_count
    weights: list[tuple[float, ...] | None] = []
    reasons: list[str] = []
    scales: list[tuple[float, ...] | None] = []
    for index in range(group_count):
        label = f"the {exposures_seconds[index]:g} s exposure group"
        reason = ""
        group_size = -1 if in_group_weights[index] is None else len(in_group_weights[index])
        folded_reason, exposure_scales = (
            _folded_group_scales(
                label,
                frame_exposures[index] if frame_exposures is not None else [],
                reference_exposures[index] if reference_exposures is not None else None,
                reference_reasons[index] if reference_reasons else "",
                group_size,
            )
            if folded[index]
            else ("", [1.0] * max(group_size, 0))
        )
        if folded_reason:
            reason = folded_reason
        elif modes[index] == "noise":
            reason = (
                f"Siril weighted the frames of {label} by noise (-weight=noise), 1 / (pscale^2 x bgnoise^2). "
                "The sequence file does not hold the scale Siril fits (pscale) while normalizing, "
                "so the weights cannot be rebuilt"
            )
        elif modes[index] not in (None, "wfwhm"):
            reason = (
                f"Siril weighted the frames of {label} itself (-weight={modes[index]}) "
                "and does not report the weights"
            )
        elif not measured[index]:
            reason = f"the combine could not measure the brightness gain of {label}"
        elif (
            frame_noises[index] is None or not math.isfinite(frame_noises[index]) or frame_noises[index] <= 0
        ):
            reason = f"the frame noise of {label} could not be read"
        else:
            group_weights = in_group_weights[index]
            total = math.fsum(group_weights) if group_weights else 0.0
            if not group_weights or total <= 0 or any(weight < 0 for weight in group_weights):
                reason = f"Siril did not report the frame weights of {label} and they cannot be rebuilt"
        if reason:
            weights.append(None)
            reasons.append(reason)
            scales.append(None)
            continue
        combine_weight = frame_counts[index] * exposures_seconds[index] ** 2 / frame_noises[index] ** 2
        weights.append(tuple(combine_weight * weight / total for weight in group_weights))
        reasons.append("")
        scales.append(tuple(exposure_scales or ()))
    return GroupFrameWeights(tuple(weights), tuple(reasons), tuple(scales))


def _group_in_group_weights(
    group_frames: Sequence[Any], entry: dict[str, Any], mode: str | None
) -> list[float] | None:
    """Work out Siril's weights inside one exposure group.

    Parameters
    ----------
    group_frames : `Sequence`
        The group's member frames (they have ``path``).
    entry : `dict`
        The group's ``exposure_group_weights`` entry; ``frame_paths`` and
        ``registration_frames`` are read for star-width weights.
    mode : `str` or `None`
        The Siril ``-weight`` mode of the group.

    Returns
    -------
    weights : `list` of `float` or `None`
        One weight per member in order (all 1 for equal weighting), or `None`
        when the star-width weights cannot be rebuilt.
    """
    if mode != "wfwhm":
        return [1.0] * len(group_frames)
    weight_by_path, _ = _star_width_weights(
        group_frames,
        {
            "symlinked_light_paths": entry.get("frame_paths"),
            "registration_frames": entry.get("registration_frames"),
        },
    )
    if not weight_by_path:
        return None
    return [weight_by_path[frame.path] for frame in group_frames]


def _exposure_group_raw_weights(
    candidates: Sequence[Any], group_entries: Sequence[dict[str, Any]], stack_weight: str | None
) -> tuple[dict[str, float], dict[str, float], str, list[GroupReference]]:
    """Give every member of an exposure-group stack its raw weight.

    Parameters
    ----------
    candidates : `Sequence`
        The frames the stack is thought to hold.
    group_entries : `Sequence` of `dict`
        The stack's ``exposure_group_weights`` entries.
    stack_weight : `str` or `None`
        The Siril ``-weight`` mode, used for an entry that names none.

    Returns
    -------
    weight_by_path : `dict` of `str` to `float`
        The raw weight of each member whose group's weights are known.
    scale_by_path : `dict` of `str` to `float`
        The exposure scale ``k_i`` of the same members (see
        `group_exposure_scales`).
    reason : `str`
        Why some weights are unknown, or an empty string.
    references : `list` of `GroupReference`
        The exposure of each group's registration reference frame.
    """
    members_per_group = []
    in_group_weights = []
    modes = []
    for entry in group_entries:
        group_paths = set(entry.get("paths") or [])
        group_frames = [frame for frame in candidates if frame.path in group_paths]
        mode = entry["stack_weight"] if "stack_weight" in entry else stack_weight
        members_per_group.append(group_frames)
        modes.append(mode)
        in_group_weights.append(_group_in_group_weights(group_frames, entry, mode))
    references = group_references(candidates, group_entries)
    # A group whose entry lacks the exposure, noise or gain facts is a group
    # the stacker did not describe: its weights are unknown, not guessed.
    result = group_frame_weights(
        in_group_weights,
        [float(entry.get("exposure_seconds") or 1.0) for entry in group_entries],
        [int(entry["frame_count"]) for entry in group_entries],
        [entry.get("frame_noise_counts") for entry in group_entries],
        holds_folded_frames=[bool(entry.get("holds_folded_frames")) for entry in group_entries],
        gains_measured=[bool(entry.get("gain_measured")) for entry in group_entries],
        weight_modes=modes,
        frame_exposures=[
            [_frame_exposure_seconds(frame) for frame in frames] for frames in members_per_group
        ],
        reference_exposures=[reference.exposure_seconds for reference in references],
        reference_reasons=[reference.reason for reference in references],
    )
    weight_by_path: dict[str, float] = {}
    scale_by_path: dict[str, float] = {}
    for group_frames, group_weights, group_scales in zip(
        members_per_group, result.weights, result.exposure_scales, strict=True
    ):
        if group_weights is None or group_scales is None:
            continue
        for frame, weight, scale in zip(group_frames, group_weights, group_scales, strict=True):
            weight_by_path[frame.path] = weight
            scale_by_path[frame.path] = scale
    return weight_by_path, scale_by_path, "; ".join(reason for reason in result.reasons if reason), references


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


def _single_group_scales(
    candidates: Sequence[Any], diagnostics: dict[str, Any]
) -> tuple[dict[str, float], GroupReference, str]:
    """Work out the exposure scales of a stack that is made of one group.

    A stack of one exposure group goes through Siril once, with no combine. If
    a short list of frames of another length was folded into it (see
    `exposure_groups.fold_exposure_lengths`), Siril still matches every frame
    to the stack's registration reference frame, so the frames carry the same
    scales ``k_i = t_ref / t_i`` as in a group of a combined stack (a design
    estimate, see `group_exposure_scales`).

    Parameters
    ----------
    candidates : `Sequence`
        The frames the stack is thought to hold.
    diagnostics : `dict`
        What the stacking run reported; the registration lists are read.

    Returns
    -------
    scale_by_path, reference, reason : `tuple`
        ``k_i`` by frame path (empty when the group is refused), the exposure
        of the stack's reference frame (``summary_index`` 0, the one group),
        and the reason the weights are unknown (empty when they are known). A
        stack whose frames share one rounded length gets ``k_i = 1`` and the
        mean exposure as its reference exposure.
    """
    from astrometricslib.pipelines.stacking.processing.exposure_groups import exposure_group_key

    lengths = [_frame_exposure_seconds(frame) for frame in candidates]
    readable = [length for length in lengths if length is not None]
    # A 0 s frame is a length of its own for the fold rule, though it has no
    # usable ratio, so the folded test reads the lengths without that check.
    stated = []
    for frame in candidates:
        try:
            stated.append(float(frame.exposure))
        except AttributeError, TypeError, ValueError:
            continue
    folded = len({exposure_group_key(length) for length in stated if math.isfinite(length)}) > 1
    if not folded:
        if not readable:
            return {}, GroupReference(0, None, None, "no frame of the stack has a usable exposure length"), ""
        return {}, GroupReference(0, None, math.fsum(readable) / len(readable), ""), ""
    label = "the stack"
    if len(readable) != len(lengths):
        reason = (
            f"{label} holds frames of more than one exposure length and a frame has no usable exposure "
            "length, so its frames cannot be put on one scale"
        )
        return {}, GroupReference(0, None, None, reason), reason
    found = _identity_reference(
        diagnostics.get("registration_frames", []),
        diagnostics.get("symlinked_light_paths", []),
        diagnostics.get("registration_included") or [],
    )
    frame_by_path = {str(frame.path): frame for frame in candidates}
    reference_frame = None if found is None else frame_by_path.get(found[0])
    reference_exposure = None if reference_frame is None else _frame_exposure_seconds(reference_frame)
    if found is None or reference_exposure is None:
        reason = (
            f"no single registration reference frame can be identified in {label}, "
            "which holds frames of more than one exposure length"
        )
        return {}, GroupReference(0, None if found is None else found[0], None, reason), reason
    scales = group_exposure_scales(lengths, True, reference_exposure) or []
    scale_by_path = {str(frame.path): scale for frame, scale in zip(candidates, scales, strict=True)}
    return scale_by_path, GroupReference(0, found[0], reference_exposure, ""), ""


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
        ``exposure_group_weights`` (one entry for each group that was
        combined; see `stack_runner._stack_exposure_groups` for its keys).
    stack_weight : `str` or `None`
        The Siril ``-weight`` mode the stack used, `None` for equal weights.

    Returns
    -------
    membership : `StackMembership`
        The members with raw weights (see the module description).
    """
    candidates = stack_candidate_frames(target_frames, diagnostics)

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
    scale_by_path: dict[str, float] = {}
    references: list[GroupReference] = []
    if group_weights:
        weight_by_path, scale_by_path, weights_reason, references = _exposure_group_raw_weights(
            candidates, group_weights, stack_weight
        )
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
    if not group_weights:
        scale_by_path, reference, folded_reason = _single_group_scales(candidates, diagnostics)
        references = [reference]
        weights_reason = weights_reason or folded_reason

    members = []
    for frame in candidates:
        if weights_reason:
            raw_weight = None
        elif group_weights or stack_weight == "wfwhm":
            raw_weight = weight_by_path.get(frame.path)
        else:
            raw_weight = 1.0
        members.append(
            StackMember(frame=frame, raw_weight=raw_weight, exposure_scale=scale_by_path.get(frame.path, 1.0))
        )
    if any(member.raw_weight is None for member in members) and not weights_reason:
        weights_reason = "the stacker reported no weight for this frame's exposure group"
    return StackMembership(tuple(members), weights_reason, membership_reason, tuple(references))


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
