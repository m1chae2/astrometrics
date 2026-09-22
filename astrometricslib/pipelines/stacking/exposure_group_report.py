"""Describe the exposure groups of a stack, and keep a record of them.

For every stack this builds one entry per exposure length (how many frames it
held, whether a dark was applied, whether a star is clipped at that length) and
the exposure that would keep the brightest star below the ceiling. For a stack
made from several exposure lengths it also writes those entries, and how each
group stack was lined up and weighted, to a manifest file in the ``groups``
folder next to the combined stack.

The entries are plain dictionaries with the field names of
`astrometricslib.models.quality_summary.ExposureGroupSummary`, so the stage can
put them straight into the quality summary.
"""

import json
import logging
import os
from typing import Any

import numpy as np

from astrometricslib.drivers.fits_access import read_data
from astrometricslib.pipelines.stacking.exposure_groups import FRAMES_SAMPLED_PER_GROUP
from astrometricslib.pipelines.stacking.exposure_saturation import (
    DEFAULT_STAR_FWHM_PIXELS,
    FrameSaturation,
    group_is_saturated,
    measure_frame_saturation,
    recommend_stack_exposure_seconds,
)

logger = logging.getLogger(__name__)

# The folder, next to a combined stack, that keeps each exposure group's own
# stack and the manifest describing them.
GROUPS_FOLDER_NAME = "groups"


def _attribute(frame: Any, name: str) -> Any:
    """Read one field of a frame record or a dictionary of one.

    Returns
    -------
    value : `Any`
        The field's value, or `None` when it is missing.
    """
    return frame.get(name) if isinstance(frame, dict) else getattr(frame, name, None)


def measure_group_saturation(
    frames: list[Any], sample_count: int = FRAMES_SAMPLED_PER_GROUP
) -> list[FrameSaturation]:
    """Measure saturation in a few raw frames spread through an exposure group.

    Parameters
    ----------
    frames : `list`
        The frame records (or dictionaries of them) of one exposure group.
    sample_count : `int`, optional
        How many frames to read, spread evenly through the group.

    Returns
    -------
    measurements : `list` [`FrameSaturation`]
        One per frame that could be read. Frames that cannot be read are
        skipped, so this may be empty.
    """
    if not frames:
        return []
    positions = sorted({
        round(position) for position in np.linspace(0, len(frames) - 1, min(sample_count, len(frames)))
    })
    measurements = []
    for position in positions:
        frame = frames[position]
        path = _attribute(frame, "path")
        if not path:
            continue
        fwhm = _attribute(frame, "measured_fwhm_px") or DEFAULT_STAR_FWHM_PIXELS
        try:
            data = np.asarray(read_data(str(path)))
        except OSError as read_error:
            logger.warning("Could not read %s to measure saturation: %s", path, read_error)
            continue
        measurements.append(measure_frame_saturation(data, _attribute(frame, "camera"), float(fwhm)))
    return measurements


def build_group_summary(
    exposure_seconds: float,
    frames: list[Any],
    diagnostics: dict[str, Any],
    saturation: list[FrameSaturation],
    stack_path: str | None = None,
    clipped_at_zero: bool = False,
    alignment_shift_pixels: list[float] | None = None,
    left_out_reason: str | None = None,
) -> dict[str, Any]:
    """Build the entry describing one exposure group.

    Parameters
    ----------
    exposure_seconds : `float`
        The group's exposure length.
    frames : `list`
        The frames submitted to the group.
    diagnostics : `dict`
        What the driver reported for the group's run.
    saturation : `list` [`FrameSaturation`]
        The sampled frames' saturation.
    stack_path : `str`, optional
        Where the group's own stack is kept.
    clipped_at_zero : `bool`, optional
        Whether the group's raw frames are clipped at zero.
    alignment_shift_pixels : `list` [`float`], optional
        The (rows, columns) shift that lined the group up with the reference.
    left_out_reason : `str`, optional
        Why the group is not in the combined image.

    Returns
    -------
    summary : `dict`
        The fields of `ExposureGroupSummary`.
    """
    calibration = diagnostics.get("calibration_applied") or {}
    return {
        "exposure_seconds": float(exposure_seconds),
        "frames_submitted": len(frames),
        "frames_stacked": int(diagnostics.get("images_stacked") or 0),
        "dark_applied": bool(calibration.get("dark", False)),
        "saturated": group_is_saturated(saturation),
        "clipped_at_zero": bool(clipped_at_zero),
        "stack_path": stack_path,
        "alignment_shift_pixels": alignment_shift_pixels,
        "left_out_reason": left_out_reason,
    }


def recommended_exposure_for_groups(
    exposure_seconds: list[float], saturation_by_group: list[list[FrameSaturation]]
) -> float | None:
    """Recommend one exposure for a stack.

    Returns
    -------
    recommended : `float` or `None`
        The recommended exposure in seconds, rounded to two decimals, or
        `None` when it cannot be worked out.
    """
    recommendation = recommend_stack_exposure_seconds(exposure_seconds, saturation_by_group)
    return None if recommendation is None else round(float(recommendation), 2)


def write_group_manifest(
    final_path: str, entries: list[dict[str, Any]], details: list[dict[str, Any]]
) -> str:
    """Write the manifest of a combined stack's exposure groups.

    Parameters
    ----------
    final_path : `str`
        The combined stack's path. The manifest goes in the ``groups`` folder
        next to it, named after the stack.
    entries : `list` [`dict`]
        The group entries (see `build_group_summary`).
    details : `list` [`dict`]
        Per-group numbers that are not part of the public summary: the weight,
        brightness gain and frame noise used to combine it, in the same order.

    Returns
    -------
    manifest_path : `str`
        Where the manifest was written.
    """
    directory = os.path.join(os.path.dirname(final_path), GROUPS_FOLDER_NAME)
    os.makedirs(directory, exist_ok=True)
    stem = os.path.splitext(os.path.basename(final_path))[0]
    manifest_path = os.path.join(directory, f"{stem}_manifest.json")
    with open(manifest_path, "w") as manifest_file:
        json.dump(
            {
                "combined_stack": os.path.basename(final_path),
                "groups": [{**entry, **detail} for entry, detail in zip(entries, details, strict=True)],
            },
            manifest_file,
            indent=2,
        )
    return manifest_path


def groups_directory(final_path: str) -> str:
    """Give the folder that keeps a combined stack's group stacks.

    Returns
    -------
    directory : `str`
        The ``groups`` folder next to `final_path`, created if it is missing.
    """
    directory = os.path.join(os.path.dirname(final_path), GROUPS_FOLDER_NAME)
    os.makedirs(directory, exist_ok=True)
    return directory
