"""Session background-homogeneity check.

Detects when a stacking session mixes two different sky conditions,
like when clouds roll in partway through the night. It looks for
sudden jumps in sky brightness, ignoring normal slow changes (like
the moon rising).
"""

import statistics
from typing import Any

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import describe_background_splits

__all__ = [
    "DEFAULT_GAP_RATIO_THRESHOLD",
    "detect_background_split",
    "find_dominant_background_subset",
]

# We use this threshold to detect sudden changes in sky brightness (like
# when clouds roll in), while ignoring slow, normal changes (like the moon
# rising).
# Tests show that normal conditions produce a ratio below 1.0, while
# sudden cloud cover produces a ratio above 6.0. A threshold of 4.0 easily
# separates the two cases.
DEFAULT_GAP_RATIO_THRESHOLD = 4.0


def detect_background_split(
    background_levels: list[float], gap_ratio_threshold: float = DEFAULT_GAP_RATIO_THRESHOLD
) -> dict[str, Any] | None:
    """Detect a sharp two-group split in per-frame sky-background levels.

    Sorts the background levels and finds the biggest gap between any two.
    If that gap is much larger than the normal spread of values on either
    side, it flags it as a sudden change in conditions.

    Parameters
    ----------
    background_levels : `list` [`float`]
        The measured sky brightness for each image.
    gap_ratio_threshold : `float`, optional
        How many times larger the gap must be compared to normal spread
        to count as a split (default is 4.0).

    Returns
    -------
    split_summary : `dict` [`str`, `Any`] or `None`
        Details about the split, or None if the images are all similar, or
        if neither side of the candidate split has more than one frame (so
        neither can show what its own normal spread looks like).
    """
    if len(background_levels) < 2:
        return None

    sorted_values = sorted(background_levels)
    diffs = [sorted_values[i + 1] - sorted_values[i] for i in range(len(sorted_values) - 1)]
    max_gap = max(diffs)
    split_index = diffs.index(max_gap)

    low_group = sorted_values[: split_index + 1]
    high_group = sorted_values[split_index + 1 :]

    low_spread = low_group[-1] - low_group[0] if len(low_group) > 1 else 0.0
    high_spread = high_group[-1] - high_group[0] if len(high_group) > 1 else 0.0

    # A side with more than one frame shows what "normal" spread looks like
    # on its own, which is enough to judge the gap against, however small
    # the *other* side is (a single washed-out frame among a full night of
    # good ones is exactly the case this check exists to catch). But when
    # *neither* side has more than one frame, nothing here establishes what
    # normal even is: both spreads floor to 0, and dividing by the 1e-9
    # fallback below turns any gap, however small or well explained (e.g.
    # by two frames simply having different exposure times), into an
    # "infinite" ratio. Real case: two ZWO ASI 533MM Pro frames of the same
    # target at 2 s and 10 s exposure, background ~16 and ~92 ADU -- a
    # difference fully explained by exposure time, not sky conditions --
    # produced a gap ratio of 76 billion and had one of the two frames
    # excluded from the stack.
    if max(low_spread, high_spread) <= 0.0:
        return None

    typical_spread = max(low_spread, high_spread, 1e-9)

    if max_gap < gap_ratio_threshold * typical_spread:
        return None

    return {
        "low_group_count": len(low_group),
        "high_group_count": len(high_group),
        "low_group_median": statistics.median(low_group),
        "high_group_median": statistics.median(high_group),
        "gap": max_gap,
        "gap_ratio": max_gap / typical_spread,
        # The boundary between the two groups, so a caller can partition
        # the frames themselves -- the counts and medians above describe
        # the split but don't say which frame fell on which side.
        "split_threshold": high_group[0],
    }


def find_dominant_background_subset(
    frames: list[Any], gap_ratio_threshold: float = DEFAULT_GAP_RATIO_THRESHOLD
) -> tuple[list[Any], list[Any], dict[str, Any] | None]:
    """Split frames into the larger same-conditions group and the rest.

    If the weather changed suddenly during a session (like clouds
    rolling in), this function keeps the largest group of similar
    images and rejects the rest. Stacking completely different images
    together causes errors.

    Parameters
    ----------
    frames : `list` [`Any`]
        The images to check.
    gap_ratio_threshold : `float`, optional
        The threshold to decide if conditions changed (default is 4.0).

    Returns
    -------
    dominant_subset : `list` [`Any`]
        The main group of images to keep.
    excluded : `list` [`Any`]
        The images that were rejected because they look different.
    split_summary : `dict` [`str`, `Any`] or `None`
        Details about the split, or None if no split happened.
    """
    if not frames:
        return [], [], None

    measured = [frame for frame in frames if frame.measurements.background_level is not None]
    split_summary = detect_background_split(
        [frame.measurements.background_level for frame in measured], gap_ratio_threshold
    )
    if not split_summary:
        return frames, [], None

    threshold = split_summary["split_threshold"]
    low_group = [frame for frame in measured if frame.measurements.background_level < threshold]
    high_group = [frame for frame in measured if frame.measurements.background_level >= threshold]

    keep_low = len(low_group) >= len(high_group)
    excluded = high_group if keep_low else low_group
    excluded_ids = {id(frame) for frame in excluded}
    dominant_subset = [frame for frame in frames if id(frame) not in excluded_ids]
    return dominant_subset, excluded, split_summary


def find_dominant_background_subset_by_exposure(
    frames: list[Any], gap_ratio_threshold: float = DEFAULT_GAP_RATIO_THRESHOLD
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    """Check for a sky-background split within each exposure length.

    The sky background of a frame grows with its exposure length, so frames of
    different lengths cannot be compared with each other. Checked together, a
    60 s group and a 300 s group of the NGC 2403 session (backgrounds of 480
    and 2256 counts, a gap ratio of 8.0) looked like a change of sky, and all
    fourteen 60 s frames were excluded (measured 2026-09-21). Each exposure
    length is therefore checked on its own.

    Parameters
    ----------
    frames : `list` [`Any`]
        The images to check.
    gap_ratio_threshold : `float`, optional
        The threshold to decide if conditions changed within a group.

    Returns
    -------
    dominant_subset : `list` [`Any`]
        The images to keep, in their original order.
    excluded : `list` [`Any`]
        The images rejected because they look different from the rest of their
        own exposure length.
    split_summaries : `list` [`dict`]
        One entry per exposure length in which a split was found, as returned
        by `find_dominant_background_subset` with an ``"exposure_seconds"``
        entry added.
    """
    from astrometricslib.pipelines.stacking.processing.exposure_groups import split_frames_by_exposure

    if not frames:
        return [], [], []
    kept_ids: set[int] = set()
    excluded: list[Any] = []
    split_summaries: list[dict[str, Any]] = []
    for group in split_frames_by_exposure(frames):
        dominant, group_excluded, split_summary = find_dominant_background_subset(
            group.frames, gap_ratio_threshold
        )
        kept_ids.update(id(frame) for frame in dominant)
        excluded.extend(group_excluded)
        if split_summary:
            split_summaries.append({**split_summary, "exposure_seconds": group.exposure_seconds})
    return [frame for frame in frames if id(frame) in kept_ids], excluded, split_summaries


# The name the background check goes by in a stack's gate record.
BACKGROUND_GATE_NAME = "background_homogeneity"

# A split needs a side with more than one frame to show what normal spread
# looks like (see `detect_background_split`), so a group of fewer than three
# measured frames cannot be judged at all.
MINIMUM_MEASURED_FRAMES_TO_JUDGE = 3


def background_homogeneity_gate(
    frames: list[Any], split_summaries: list[dict[str, Any]] | None, enabled: bool = True
) -> GateResult:
    """Record whether the background check could judge any exposure length.

    A frame whose background could not be measured is skipped by the check,
    and an exposure length with fewer than three measured frames cannot show a
    split. If no exposure length can be judged, nothing was checked, and the
    gate says so instead of passing.

    Parameters
    ----------
    frames : `list` [`Any`]
        The frames handed to `find_dominant_background_subset_by_exposure`.
    split_summaries : `list` [`dict`] or `None`
        What that function found: one entry per exposure length with a split.
    enabled : `bool`, optional
        Whether the check is switched on in the settings.

    Returns
    -------
    gate : `GateResult`
        ``failed`` when a split was found, ``not_checked`` when the check is
        off or no exposure length could be judged, otherwise ``passed``.
    """
    from astrometricslib.pipelines.stacking.processing.exposure_groups import split_frames_by_exposure

    source = (
        f"gap ratio {DEFAULT_GAP_RATIO_THRESHOLD:g}; "
        f"at least {MINIMUM_MEASURED_FRAMES_TO_JUDGE} measured frames"
    )
    if not enabled:
        return unchecked_gate(
            BACKGROUND_GATE_NAME, "the background check is turned off in the settings", source
        )
    if split_summaries:
        return failed_gate(
            BACKGROUND_GATE_NAME,
            "background split: " + describe_background_splits(split_summaries) or "",
            limit=DEFAULT_GAP_RATIO_THRESHOLD,
            limit_source=source,
        )
    judged = 0
    for group in split_frames_by_exposure(frames) if frames else []:
        measured = [f for f in group.frames if f.measurements.background_level is not None]
        if len(measured) >= MINIMUM_MEASURED_FRAMES_TO_JUDGE:
            judged += 1
    if judged == 0:
        return unchecked_gate(
            BACKGROUND_GATE_NAME,
            "no exposure length had enough frames with a measured background to check for a split",
            source,
        )
    return passed_gate(
        BACKGROUND_GATE_NAME,
        limit=DEFAULT_GAP_RATIO_THRESHOLD,
        limit_source=source,
        detail=f"{judged} exposure length(s) checked, no split",
    )
