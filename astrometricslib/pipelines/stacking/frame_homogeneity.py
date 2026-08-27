"""Checks to make sure a stack's own frames match each other on gain.

When combining (stacking) images, they all need to have been taken with
the same camera gain setting, or the noise math comes out wrong. This
file finds and drops the images that don't match the rest.
"""

from typing import Any


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
