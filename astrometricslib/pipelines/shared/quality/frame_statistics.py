"""Functions for checking and counting image frames.

This file contains tools for calculating statistics about the images,
such as counting how many images there are for different camera settings,
and measuring basic quality metrics like the sky background brightness. It
also holds the seeded bootstrap error of a median, which the frame
measurement and the photometry zero point both use.
"""

from collections.abc import Callable
from typing import Any

import numpy as np

# The number of resamples for the bootstrap of a median. 1000 puts the error
# of the uncertainty itself near 2 percent.
BOOTSTRAP_RESAMPLE_COUNT = 1000

# Seed of the bootstrap, so one frame always gives the same uncertainty. The
# value itself is arbitrary.
BOOTSTRAP_SEED = 20261022


def bootstrap_median_error(values: np.ndarray, generator: np.random.Generator) -> float:
    """Estimate the standard error of a median by resampling.

    Parameters
    ----------
    values : `numpy.ndarray`
        The sample.
    generator : `numpy.random.Generator`
        The seeded random generator.

    Returns
    -------
    error : `float`
        The standard deviation of the medians of
        `BOOTSTRAP_RESAMPLE_COUNT` resamples drawn with replacement.
    """
    draws = generator.integers(0, values.size, size=(BOOTSTRAP_RESAMPLE_COUNT, values.size))
    return float(np.std(np.median(values[draws], axis=1)))


def get_frame_stats(target: Any) -> dict[str, Any]:
    """Count how many images share the same camera settings.

    Parameters
    ----------
    target : `Any`
        The target to check.

    Returns
    -------
    frame_stats : `dict`
        A dictionary with a "lights" list. Each item in the list shows
        the camera, telescope, ISO, exposure time, filter, and how many
        images match those exact settings.
    """
    if not target:
        return {"lights": []}

    stats: list[dict[str, Any]] = []
    if target.frames:
        aggregates = {}
        for frame in target.frames:
            if frame.role != "LIGHT":
                continue
            filter_string = frame.filter.value if hasattr(frame.filter, "value") else str(frame.filter)
            key = (frame.telescope, frame.camera, frame.iso, frame.exposure, filter_string)
            aggregates[key] = aggregates.get(key, 0) + 1

        for (telescope, camera, iso, exposure, filter_value), count in aggregates.items():
            stats.append({
                "telescope": telescope,
                "camera": camera,
                "iso": iso,
                "exposure": exposure,
                "filter": filter_value,
                "count": count,
            })

    return {"lights": stats}


def list_camera_names(targets: list) -> dict[str, int]:
    """Find all the different cameras used across a bunch of targets.

    This helps the user interface show a list of available cameras to
    filter by.

    Parameters
    ----------
    targets : `list`
        A list of target objects to look through.

    Returns
    -------
    counts_by_camera : `dict`
        A dictionary where the key is the camera name and the value is
        how many images were taken with it. Sorted with the most used
        camera first.
    """
    counts: dict[str, int] = {}
    for target in targets:
        for frame in target.frames:
            camera_name = frame.camera or "Unknown"
            counts[camera_name] = counts.get(camera_name, 0) + 1

    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def measure_frame_input_quality(
    target: Any,
    measure_fwhm: Callable[[Any], float | None] | None = None,
    remeasure: bool = False,
    camera_name: str | None = None,
) -> dict[str, int]:
    """Measure basic quality stats for every image in a target.

    This checks things like sky background brightness and how many pixels
    are maxed out (saturated). This helps us throw out bad images before
    time is wasted trying to stack them. It updates the target object directly.

    Parameters
    ----------
    target : `Any`
        The target whose images need measuring.
    measure_fwhm : `Callable`, optional
        A function that measures sharpness (FWHM) from an image's pixels,
        such as the astrometry pipeline's `measure_fwhm_from_data`. When
        given, sharpness is measured too, which takes a long time. Defaults
        to `None`, which skips it.
    remeasure : `bool`, optional
        If True, forces it to re-check images it already measured before.
    camera_name : `str`, optional
        Only measure images from this specific camera.

    Returns
    -------
    counts : `dict`
        A dictionary showing how many images were "measured", "skipped",
        or "failed" (because they couldn't be read).
    """
    from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
    from astrometricslib.pipelines.shared.quality.quality_metrics import (
        measure_frame_input_quality as measure_one_frame,
    )

    counts = {"measured": 0, "skipped": 0, "failed": 0}

    for frame in target.frames:
        if camera_name and camera_name.lower() not in (frame.camera or "").lower():
            continue
        if not remeasure and frame.measurements.background_level is not None:
            counts["skipped"] += 1
            continue

        saturation_threshold_adu = resolve_camera_profile(frame.camera).pixel_saturation_level_adu.value
        metrics = measure_one_frame(
            frame.path, measure_fwhm=measure_fwhm, saturation_threshold_adu=saturation_threshold_adu
        )
        if metrics["background_level"] is None:
            counts["failed"] += 1
            continue

        frame.measurements.background_level = metrics["background_level"]
        if metrics["saturated_pixel_fraction"] is not None:
            frame.measurements.saturated_pixel_fraction = metrics["saturated_pixel_fraction"]
        if metrics["fwhm_px"] is not None:
            # Deliberately NOT written into registration_fwhm_x_px.
            # Siril's registration PSF fit and this photutils measurement
            # are not on the same absolute scale -- measured on identical
            # NGC 4438 frames, photutils reports ~1.53x Siril's value
            # (5.1px vs 3.2px), and stage.py already documents
            # the same mismatch from an independent M 13 comparison.
            # Mixing them in one field would make a frame appear to jump
            # ~50% in seeing purely from which stage measured it, which
            # is precisely the false signal any trend analysis over these
            # numbers must not see. (Siril's second number was once read as
            # a width along y, from a count of 228 of 238 frames where the
            # two differed. It is the weighted FWHM, so that count did not
            # show elongation.)
            frame.measurements.measured_fwhm_px = metrics["fwhm_px"]
        counts["measured"] += 1

    return counts


def _selection_to_match(selection: Any, kind: str, setting_text: str) -> dict[str, Any]:
    """Turn one calibration selection into the match record a screen shows.

    Parameters
    ----------
    selection : `CalibrationSelection`
        What the library chose for the lights (frames and flags).
    kind : `str`
        ``"dark"``, ``"bias"`` or ``"flat"``.
    setting_text : `str`
        The light settings in words, such as ``"gain 100, offset 10"``.

    Returns
    -------
    match : `dict`
        The `CalibrationGroupMatch` fields (``count``, ``fallbacks``,
        ``problem``). A blocking flag or an empty choice gives a ``problem``.
    """
    count = len(selection.frames)
    blocking = list(selection.blocking_flags)
    problem = ""
    if blocking:
        problem = "; ".join(blocking)
    elif count == 0:
        problem = f"no {kind} frames at {setting_text}"
    return {"count": count, "fallbacks": [] if problem else list(selection.flags), "problem": problem}


def _group_match(
    calibration: Any, frames: list[Any], camera: str, exposure: float
) -> dict[str, dict[str, Any]]:
    """Run the stacker's own calibration selection for one group of lights.

    This calls the same library selections as the stacking stage
    (``_stack_one_batch`` in the Siril staging code), with the same inputs:
    the camera, telescope, filter, exposure, gain, offset, binning and mean
    sensor temperature of the lights. No matching rule is repeated here.

    Parameters
    ----------
    calibration : `CalibrationLibrary`
        The library of calibration frames.
    frames : `list` [`FrameRecord`]
        The light frames of one group.
    camera : `str`
        The camera name.
    exposure : `float`
        The exposure of the group in seconds.

    Returns
    -------
    matches : `dict` [`str`, `dict`]
        ``darkMatch``, ``flatMatch`` and ``biasMatch``, each a
        `CalibrationGroupMatch` as a dictionary.
    """
    from astrometricslib._internal.drivers.batch_calibration import choose_batch_calibration

    chosen = choose_batch_calibration(calibration, frames, [])
    gain, offset, binning = chosen.gain, chosen.offset, chosen.binning
    filter_name = chosen.filter_name
    setting_text = f"gain {gain}, offset {offset}, binning {binning}"
    darks, biases, flats = chosen.dark, chosen.bias, chosen.flat
    return {
        "darkMatch": _selection_to_match(darks, "dark", f"{exposure:g} s, {setting_text}"),
        "flatMatch": _selection_to_match(flats, "flat", f"filter {filter_name}, {setting_text}"),
        "biasMatch": _selection_to_match(biases, "bias", setting_text),
        "binning": binning,
    }


def get_frame_stats_grouped(target: Any, calibration: Any, camera: str | None = None) -> list[dict[str, Any]]:
    """Group the light frames and say which calibration the stacker would use.

    The lights are grouped by camera, optic, filter, gain, offset and exposure.
    For each group, the calibration library runs the same dark, flat and bias
    selection that the stacking stage runs, so a match here is a match when
    stacking. A group says why when nothing matches (for example "no bias
    frames at gain 100, offset 10, binning 1x1").

    Parameters
    ----------
    target : `Any`
        The target to summarize.
    calibration : `CalibrationLibrary`
        The library of calibration frames (dark frames, flat frames, etc).
    camera : `str`, optional
        Only check images from this camera.

    Returns
    -------
    grouped_stats : `list` of `dict`
        One dictionary per group: ``filter``, ``iso``, ``offset``,
        ``exposure``, ``count``, ``camera``, ``focalLengthMm`` (the optic in
        whole millimeters, or ``None``), ``binning``, ``darks`` (the dark
        count as text, or ``"Missing"``) and the ``darkMatch``, ``flatMatch``
        and ``biasMatch`` records.
    """
    if not target:
        return []

    groups: dict[tuple[str, str, str, str, float, int | None], list[Any]] = {}

    for frame in target.frames:
        if frame.role != "LIGHT":
            continue
        if camera and frame.camera != camera:
            continue

        filter_str = frame.filter.name if hasattr(frame.filter, "name") else str(frame.filter)
        if filter_str == "NONE":
            filter_str = "None"

        try:
            exp_float = float(frame.exposure)
        except ValueError, TypeError:
            exp_float = 1.0

        focal_length = getattr(frame, "focal_length_mm", None)
        optic = None if focal_length is None else round(float(focal_length))
        key = (frame.camera, filter_str, str(frame.iso), str(frame.offset), exp_float, optic)
        groups.setdefault(key, []).append(frame)

    results = []
    for key in sorted(groups, key=lambda item: (item[1], item[2], item[3], item[4], item[5] or 0)):
        cam, filter_str, iso, offset, exposure, optic = key
        frames = groups[key]
        matches = _group_match(calibration, frames, cam or camera or "Unknown", exposure)
        dark_count = matches["darkMatch"]["count"]
        results.append({
            "filter": filter_str,
            "iso": iso,
            "offset": offset,
            "exposure": f"{exposure:g}",
            "count": len(frames),
            "darks": f"{dark_count}" if dark_count else "Missing",
            "camera": cam or camera or "Unknown",
            "focalLengthMm": optic,
            **matches,
        })

    return results
