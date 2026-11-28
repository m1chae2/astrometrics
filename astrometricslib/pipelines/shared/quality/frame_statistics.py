"""Functions for checking and counting image frames.

This file contains tools for calculating statistics about the images, such
as counting the frames of each camera and measuring basic quality metrics
like the sky background brightness. It
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
    from astrometricslib.pipelines.shared.quality.background_measurement import BACKGROUND_LEVEL_METHOD
    from astrometricslib.pipelines.shared.quality.quality_metrics import (
        measure_frame_input_quality as measure_one_frame,
    )

    counts = {"measured": 0, "skipped": 0, "failed": 0}

    for frame in target.frames:
        if camera_name and camera_name.lower() not in (frame.camera or "").lower():
            continue
        # A level of an older method is measured again: it must not be
        # compared with a newer one (`BACKGROUND_LEVEL_METHOD`).
        if (
            not remeasure
            and frame.measurements.background_level is not None
            and frame.measurements.background_level_method == BACKGROUND_LEVEL_METHOD
        ):
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
        frame.measurements.background_level_method = BACKGROUND_LEVEL_METHOD
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
