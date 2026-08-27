"""Checking the alignment quality of spectroscopy images.

When we stack regular images, we can check how sharp they are (the FWHM).
But spectroscopy images are mostly stretched-out streaks of light, so that
doesn't work. Instead, we check the single round dot (the 'zero-order star')
that creates each streak. This module checks if that star jumps around or
fades out, which means the software might be confused and ruining the stack.
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
    """Find values that are weirdly different from the rest.

    This function calculates the average and then flags any numbers that
    are too far away from that average (measured in standard deviations).

    Parameters
    ----------
    values : `list` of `float` or `None`
        The list of numbers to check. Empty (None) values are ignored.
    sigma_threshold : `float`
        How many standard deviations away a number has to be to get flagged.
    low_is_bad : `bool`, optional
        If True, only flag numbers that are unusually small (like a fading
        star).
        If False, flag numbers that are unusually small or large.

    Returns
    -------
    flags : `list` of `bool`
        A list of True/False flags. True means the number was weird.
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
    """Check each image frame for problems aligning the spectrum.

    Parameters
    ----------
    frame_paths : `list` of `str`
        The file locations for the images being checked.
    seq_frames : `list` of `dict`
        Alignment stats from Siril (like 'nb_stars' and 'rmse').
    zero_order_stars : `list` of `dict` or `None`
        Details about the zero-order star in each frame (x/y position,
        brightness).
        Can be None if the software couldn't find the star.

    Returns
    -------
    flagged_frames : `list` of `dict`
        A list of frames with warnings. Each item has a 'path' and a 'reason'.

    Notes
    -----
    The three input lists must match up perfectly (e.g., the first item in
    each list describes the first image frame).
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
