"""Session background-homogeneity check.

Detects when a stacking session mixes two different sky conditions,
like when clouds roll in partway through the night. It looks for
sudden jumps in sky brightness, ignoring normal slow changes (like
the moon rising).
"""

import statistics
from typing import Any

__all__ = [
    "DEFAULT_GAP_RATIO_THRESHOLD",
    "detect_background_split",
    "find_dominant_background_subset",
]

# Validated against three real sessions' per-frame sigma-clipped
# background medians:
# M 13 L-filter (40f, 2026-05-23, homogeneous): gap/spread ratio 0.4.
# M 81 (45f, homogeneous but with wide *gradual* background drift
#   across the session, 244-4512 ADU from changing sky
#   altitude/airmass): gap/spread ratio 0.875 -- the heuristic
#   correctly does not flag gradual drift, only a sharp discontinuous
#   jump.
# NGC 2403 (70f, 2026-02-22, real cloud event): background stepped
#   from ~240-488 ADU to ~2150-2360 ADU at a single transition and
#   never recovered: gap/spread ratio 6.7.
# A threshold of 4.0 sits with wide margin above both clean sessions
# and below the real bad one. See logs/ for the underlying per-frame
# background measurements.
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
        Details about the split, or None if the images are all similar.
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

    measured = [frame for frame in frames if getattr(frame, "background_level", None) is not None]
    split_summary = detect_background_split(
        [frame.background_level for frame in measured], gap_ratio_threshold
    )
    if not split_summary:
        return frames, [], None

    threshold = split_summary["split_threshold"]
    low_group = [frame for frame in measured if frame.background_level < threshold]
    high_group = [frame for frame in measured if frame.background_level >= threshold]

    keep_low = len(low_group) >= len(high_group)
    excluded = high_group if keep_low else low_group
    excluded_ids = {id(frame) for frame in excluded}
    dominant_subset = [frame for frame in frames if id(frame) not in excluded_ids]
    return dominant_subset, excluded, split_summary
