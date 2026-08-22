"""Spectral (SA200) registration quality evaluation.

Wires spectral_registration_quality_analysis.py's per-frame
registration diagnostics (matched star count, fit RMSE, zero-order
star position/brightness stability) into a reusable function for
StackQualitySummary, rather than only existing as a standalone
analysis script. Validated against a real M 13 SA200 session (40
frames): found 2 frames where the zero-order star was briefly
misidentified, correlated with a drop in matched star count --
exactly the class of problem this is meant to catch during a real
production stack, not just an offline sweep.

Standard imaging and spectral stacks need different quality-check
shapes: a whole-field FWHM check
(image_quality_metrics.measure_image_fwhm) doesn't capture what
matters for a spectral trace, which only has one star (the zero
order) that matters. See stack_quality.py for the standard-imaging
equivalent.
"""

import itertools
import math
from typing import Any

import numpy as np

MIN_MATCHED_STAR_PAIRS = 10
RMSE_OUTLIER_SIGMA = 2.5
ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA = 3.0
ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA = 2.5


def flag_outliers(values: list[float | None], sigma_threshold: float, low_is_bad: bool = False) -> list[bool]:
    """Flag entries more than sigma_threshold standard deviations away.

    Parameters
    ----------
    values : `List[Optional[float]]`
        The per-frame values to check for outliers. `None` entries
        are excluded from the mean/std computation and are never
        flagged.
    sigma_threshold : `float`
        The number of standard deviations from the mean beyond which
        a value is flagged.
    low_is_bad : `bool`, optional
        If `True`, flags only values below the mean (e.g. dim
        zero-order star); if `False` (default), flags deviation in
        either direction (e.g. elevated RMSE).

    Returns
    -------
    flags : `List[bool]`
        A list the same length as values, `True` where the
        corresponding entry is an outlier.
    """
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [False] * len(values)
    mean, std = float(np.mean(present)), float(np.std(present))
    if std == 0:
        return [False] * len(values)

    flags = []
    for v in values:
        if v is None:
            flags.append(False)
            continue
        z = (v - mean) / std
        flags.append(z < -sigma_threshold if low_is_bad else abs(z) > sigma_threshold)
    return flags


def evaluate_spectral_registration_quality(
    frame_paths: list[str],
    seq_frames: list[dict[str, float]],
    zero_order_stars: list[dict[str, float] | None],
) -> list[dict[str, Any]]:
    """Return frames with a registration concern.

    Parameters
    ----------
    frame_paths : `List[str]`
        The filesystem paths of the frames being evaluated.
    seq_frames : `List[Dict[str, float]]`
        Per-frame registration stats parsed from Siril's .seq output,
        expected to contain "nb_stars" and "rmse" keys.
    zero_order_stars : `List[Optional[Dict[str, float]]]`
        Per-frame zero-order star measurements (keys "x", "y", and
        "peak_to_background_ratio"), or `None` where the zero-order
        star could not be identified in that frame.

    Returns
    -------
    flagged_frames : `List[Dict[str, Any]]`
        A list of {"path", "reason"} entries for frames with a
        registration concern.

    Notes
    -----
    frame_paths, seq_frames, and zero_order_stars must be
    index-aligned (one entry per input frame, in original submission
    order) -- callers are responsible for that alignment since it
    depends on how the caller parsed Siril's .seq/.lst output.
    """
    n = len(frame_paths)
    if len(seq_frames) != n or len(zero_order_stars) != n:
        return []

    nb_stars = [f.get("nb_stars") for f in seq_frames]
    rmse_values = [f.get("rmse") for f in seq_frames]
    peak_to_background_ratios = [s["peak_to_background_ratio"] if s else None for s in zero_order_stars]

    position_jumps: list[float | None] = [None]
    for prev, curr in itertools.pairwise(zero_order_stars):
        if prev is None or curr is None:
            position_jumps.append(None)
        else:
            position_jumps.append(math.hypot(curr["x"] - prev["x"], curr["y"] - prev["y"]))

    low_star_count_flags = [count is not None and count < MIN_MATCHED_STAR_PAIRS for count in nb_stars]
    high_rmse_flags = flag_outliers(rmse_values, RMSE_OUTLIER_SIGMA)
    dim_zero_order_flags = flag_outliers(
        peak_to_background_ratios, ZERO_ORDER_AMPLITUDE_OUTLIER_SIGMA, low_is_bad=True
    )
    position_jump_flags = flag_outliers(position_jumps, ZERO_ORDER_POSITION_JUMP_OUTLIER_SIGMA)

    flagged_frames = []
    for i, path in enumerate(frame_paths):
        reasons = []
        if low_star_count_flags[i]:
            reasons.append(f"low matched star count ({nb_stars[i]})")
        if high_rmse_flags[i]:
            reasons.append("elevated registration fit RMSE")
        if dim_zero_order_flags[i]:
            reasons.append("dim zero-order star")
        if position_jump_flags[i]:
            reasons.append("zero-order star position jump")
        if reasons:
            flagged_frames.append({"path": path, "reason": "; ".join(reasons)})

    return flagged_frames
