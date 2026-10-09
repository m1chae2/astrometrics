"""Checks to make sure a stack's own frames match each other on gain.

When combining (stacking) images, they all need to have been taken with
the same camera gain setting, or the noise math comes out wrong. This
file finds and drops the images that don't match the rest.
"""

from typing import Any

from astrometricslib.models.gate_result import GateResult, passed_gate, unchecked_gate
from astrometricslib.utilities.iso_text import canonical_iso_text

# The name the gain check goes by in a stack's gate record.
GAIN_GATE_NAME = "gain_homogeneity"

# What `canonical_iso_text` writes for a frame that records no gain.
_UNKNOWN_GAIN_TEXTS = frozenset({"", "None"})


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
        The largest group of images that all share the same gain. Gains are
        compared as numbers, so "800" and "800.0" are the same gain.
    excluded : `list` [`Any`]
        The images that were thrown out because their gain was different.
    """
    if not frames:
        return [], []

    groups: dict[str, list[Any]] = {}
    for frame in frames:
        groups.setdefault(canonical_iso_text(frame.iso), []).append(frame)

    dominant_gain = max(groups, key=lambda gain: len(groups[gain]))
    dominant_subset = groups[dominant_gain]
    excluded = [f for f in frames if canonical_iso_text(f.iso) != dominant_gain]
    return dominant_subset, excluded


def gain_homogeneity_gate(frames: list[Any], excluded: list[Any]) -> GateResult:
    """Record whether the gain check had a gain to compare.

    Setting aside the frames of a minority gain corrects the stack, so it
    does not fail the gate. What would make the check meaningless is no frame
    recording its gain at all: every frame then falls in one "unknown" group
    and the check passes by default. That case is ``not_checked``.

    Parameters
    ----------
    frames : `list` [`Any`]
        The frames handed to `find_dominant_gain_subset`.
    excluded : `list` [`Any`]
        The frames it set aside.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` when no frame records a gain, otherwise ``passed``
        with the number of frames set aside.
    """
    source = "frames of the most common gain are kept; the rest are set aside"
    known = [frame for frame in frames if canonical_iso_text(frame.iso) not in _UNKNOWN_GAIN_TEXTS]
    if not known:
        return unchecked_gate(
            GAIN_GATE_NAME, "no frame records its gain setting, so gains could not be compared", source
        )
    return passed_gate(
        GAIN_GATE_NAME,
        measured_value=float(len(excluded)),
        limit_source=source,
        detail=f"{len(excluded)} frame(s) of a minority gain set aside; {len(frames) - len(excluded)} kept",
    )
