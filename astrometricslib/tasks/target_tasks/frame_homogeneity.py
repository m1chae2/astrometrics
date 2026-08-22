"""Frame and calibration-master metadata homogeneity checks.

Pure comparison logic for two related but distinct homogeneity
concerns in the stacking pipeline: whether a matched calibration
master's own metadata (exposure, gain) is actually compatible with
the light frames it would be applied to, and whether the light frames
being stacked together share a consistent gain setting among
themselves. Both fail closed rather than silently producing a
miscalibrated or noise-inconsistent stack.
"""

from typing import Any


def is_calibration_gain_compatible(light_gain: str, master_gain: str) -> bool:
    """Return whether a calibration master's gain matches the lights'.

    Applies to all three calibration types (dark/bias/flat): dark
    current and read-noise characteristics are gain-setting-specific,
    so any difference is worth flagging regardless of what kind of
    master it is.

    Parameters
    ----------
    light_gain : `str`
        The gain (ISO/GAIN) setting of the light frames.
    master_gain : `str`
        The gain (ISO/GAIN) setting of the calibration master.

    Returns
    -------
    is_compatible : `bool`
        `True` if light_gain and master_gain match exactly, `False`
        otherwise.
    """
    return str(light_gain) == str(master_gain)


def is_dark_calibration_metadata_compatible(
    light_exposure: float,
    light_gain: str,
    master_exposure: float,
    master_gain: str,
    exposure_tolerance_seconds: float = 1.0,
) -> bool:
    """Return whether a matched dark master's metadata is close enough.

    Dark-specific: a dark's exposure time must be close to the light
    frames' exposure time, since dark current accumulates over
    exposure duration. This does NOT apply to bias or flat masters --
    both are expected to have exposure times unrelated to the light
    frames' (bias is near-zero by design, flats are calibrated to the
    flat panel's brightness), so comparing their exposure against the
    light's would flag normal, correct calibration setups as
    mismatched. Use `is_calibration_gain_compatible` for bias/flat
    instead.

    Parameters
    ----------
    light_exposure : `float`
        The exposure time, in seconds, of the light frames.
    light_gain : `str`
        The gain (ISO/GAIN) setting of the light frames.
    master_exposure : `float`
        The exposure time, in seconds, of the dark master.
    master_gain : `str`
        The gain (ISO/GAIN) setting of the dark master.
    exposure_tolerance_seconds : `float`, optional
        The maximum allowed difference between light_exposure and
        master_exposure, in seconds, default 1.0.

    Returns
    -------
    is_compatible : `bool`
        `True` if the gains match exactly and the exposures are
        within exposure_tolerance_seconds of each other, `False`
        otherwise.

    Notes
    -----
    Gain must match exactly for the same reason as
    `is_calibration_gain_compatible`. Exposure allows a tolerance
    because darks are commonly reused across light frames with
    slightly different exposure times. (calibration_library.
    CalibrationLibrary.get_dark_frames already does its own coarser
    0.1s fuzzy match at lookup time; this is a second, independent
    sanity check on the master actually selected, using a looser
    tolerance appropriate for deciding whether to trust it at all
    rather than just whether to consider it a lookup candidate.)
    """
    if not is_calibration_gain_compatible(light_gain, master_gain):
        return False
    return abs(float(light_exposure) - float(master_exposure)) <= exposure_tolerance_seconds


def find_dominant_gain_subset(frames: list[Any]) -> tuple[list[Any], list[Any]]:
    """Split frames into the largest same-gain group and everything else.

    Mixed-gain stacking corrupts noise statistics -- each gain setting
    has its own read-noise and dark-current profile -- so only the
    dominant-gain subset should be stacked; the rest should be
    recorded as excluded rather than silently included.

    Parameters
    ----------
    frames : `List[Any]`
        The frame records to split by gain setting.

    Returns
    -------
    dominant_subset : `List[Any]`
        The largest group of frames sharing the same gain setting.
    excluded : `List[Any]`
        All frames not in the dominant-gain group.

    Notes
    -----
    Only frame.iso (which this codebase uses for both ISO and camera
    GAIN settings, see calibration_library.CalibrationLibrary.
    _get_iso_gain) is compared. A separate sensor OFFSET/black-level
    setting is not currently tracked on FrameRecord, so it can't be
    checked here yet -- this only covers the gain half of
    "gain/offset".
    """
    if not frames:
        return [], []

    groups: dict[str, list[Any]] = {}
    for frame in frames:
        groups.setdefault(str(frame.iso), []).append(frame)

    dominant_gain = max(groups, key=lambda gain: len(groups[gain]))
    dominant_subset = groups[dominant_gain]
    excluded = [f for f in frames if str(f.iso) != dominant_gain]
    return dominant_subset, excluded
