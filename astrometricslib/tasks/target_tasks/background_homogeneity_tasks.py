"""Session background-homogeneity check.

Detects when a stacking session silently mixes two different sky
conditions -- e.g. thin clouds rolling in partway through a session
and never clearing -- as distinct from calibration/gain mismatch or
ordinary gradual sky-brightness drift. Found via NGC 2403 (2026-02-22):
a real session where background jumped ~5x and stayed there for the
rest of the night, undetected by rejection-fraction or
gain/calibration checks alone.

The per-frame FITS-reading measurement functions
(measure_frame_background_level, measure_frame_saturated_pixel_fraction)
live in data_access/background_homogeneity.py instead -- this module
only operates on already-measured, in-memory values.
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

    Finds the largest gap between consecutive sorted background values
    and compares it to the largest within-group spread on either side
    of that gap. A session with gradual, continuous background drift
    (changing sky altitude, moonrise, twilight) will have a gap
    comparable to its typical spread; a session where conditions
    changed abruptly partway through (thin clouds rolling in) will
    have a gap many times larger than the spread on either side.

    Parameters
    ----------
    background_levels : `List[float]`
        Per-frame sigma-clipped median sky-background levels, in the
        same units as the raw ADU (see `measure_frame_background_level`).
    gap_ratio_threshold : `float`, optional
        The minimum ratio of the largest gap to the typical
        within-group spread required to report a split, default
        `DEFAULT_GAP_RATIO_THRESHOLD` (4.0).

    Returns
    -------
    split_summary : `Optional[Dict[str, Any]]`
        `None` when no split is detected (including when there are
        fewer than 2 frames, or no meaningful spread to compare
        against). Otherwise a dict with keys "low_group_count",
        "high_group_count", "low_group_median", "high_group_median",
        "gap", and "gap_ratio".
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

    Detecting a background split is only half the job. Stacking every
    frame anyway lets a single wildly discrepant frame become Siril's
    registration reference, and a cloud-washed or light-leaked frame
    often has no detectable stars at all -- at which point registration
    aborts and the whole sequence is lost, however many good frames
    were sitting behind it. That is exactly what happened to M 42 on
    2026-08-25: 22 frames at ~236 ADU, one at ~2324, reference found
    0 stars, all 23 frames discarded.

    Mirrors `frame_homogeneity.find_dominant_gain_subset`: keep the
    majority group, hand back the minority to be recorded as excluded
    rather than silently dropped.

    Parameters
    ----------
    frames : `List[Any]`
        Frame records to split. Each is read via its
        ``background_level`` attribute, which the caller is expected to
        have measured already.
    gap_ratio_threshold : `float`, optional
        Passed through to `detect_background_split`, default
        `DEFAULT_GAP_RATIO_THRESHOLD` (4.0).

    Returns
    -------
    dominant_subset : `List[Any]`
        The frames to stack. Equal to `frames` when no split is found.
    excluded : `List[Any]`
        The minority-group frames, empty when no split is found.
    split_summary : `Optional[Dict[str, Any]]`
        The `detect_background_split` result, for logging and quality
        reporting, or `None` when no split was detected.

    Notes
    -----
    Frames whose ``background_level`` is `None` were never successfully
    measured, so they cannot be assigned to either group and are always
    retained -- a failed measurement is not evidence a frame is bad,
    and the existing measurement loop already tolerates such failures.

    The larger group wins, matching the gain check, so a session that
    turned cloudy and stayed that way still stacks its cloudy majority:
    the goal is a homogeneous stack, not the lowest background. Ties go
    to the low-background group, which is the cleaner sky.
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
