"""Checks to make sure image settings match before stacking.

When combining (stacking) images, they all need to have been taken with
similar camera settings (like gain and exposure). This file checks
that the images match each other, and that the calibration files
(like darks and flats) match the images. If they don't match, the
process stops to avoid creating a bad final image.
"""

from typing import Any


def is_calibration_gain_compatible(light_gain: str, master_gain: str) -> bool:
    """Check if the calibration and light frames share the same gain.

    Gain (or ISO) is how sensitive the camera is set to be. All calibration
    files (darks, bias, and flats) must have the exact same gain as the
    light images for the math to work correctly.

    Parameters
    ----------
    light_gain : `str`
        The gain setting of the light images.
    master_gain : `str`
        The gain setting of the calibration file.

    Returns
    -------
    is_compatible : `bool`
        True if the gain settings match exactly, False if they don't.
    """
    return str(light_gain) == str(master_gain)


def is_dark_calibration_metadata_compatible(
    light_exposure: float,
    light_gain: str,
    master_exposure: float,
    master_gain: str,
    exposure_tolerance_seconds: float = 1.0,
) -> bool:
    """Check if a dark calibration file matches the light images.

    Dark frames remove thermal noise, which builds up over time. Because
    of this, a dark frame must have both the exact same gain AND very
    close to the same exposure time as the light image.

    (Note: Do not use this for bias or flat files, because their exposure
    times are supposed to be different from the light images.)

    Parameters
    ----------
    light_exposure : `float`
        The exposure time of the light images, in seconds.
    light_gain : `str`
        The gain setting of the light images.
    master_exposure : `float`
        The exposure time of the dark file, in seconds.
    master_gain : `str`
        The gain setting of the dark file.
    exposure_tolerance_seconds : `float`, optional
        How much difference in exposure time is allowed (default 1.0 seconds).

    Returns
    -------
    is_compatible : `bool`
        True if the gain matches perfectly and the exposure is close enough.
    """
    if not is_calibration_gain_compatible(light_gain, master_gain):
        return False
    return abs(float(light_exposure) - float(master_exposure)) <= exposure_tolerance_seconds


def find_dominant_gain_subset(frames: list[Any]) -> tuple[list[Any], list[Any]]:
    """Group images by their gain setting and return the largest group.

    Mixing images with different gain settings ruins the noise calculations
    when stacking. This function finds the most common gain setting and
    keeps only those images, throwing out the rest.

    Parameters
    ----------
    frames : `list` [`Any`]
        The list of image records to check.

    Returns
    -------
    dominant_subset : `list` [`Any`]
        The largest group of images that all share the exact same gain.
    excluded : `list` [`Any`]
        The images that were thrown out because their gain was different.
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
