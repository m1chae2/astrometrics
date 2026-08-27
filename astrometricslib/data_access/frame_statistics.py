"""Functions for checking and counting image frames.

This file contains tools for calculating statistics about the images,
such as counting how many images there are for different camera settings,
and measuring basic quality metrics like the sky background brightness.
"""

from typing import Any


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
    include_fwhm: bool = False,
    remeasure: bool = False,
    camera_name: str | None = None,
) -> dict[str, int]:
    """Measure basic quality stats for every image in a target.

    This checks things like sky background brightness and how many pixels
    are maxed out (saturated). This helps us throw out bad images before
    we waste time trying to stack them. It updates the target object directly.

    Parameters
    ----------
    target : `Any`
        The target whose images need measuring.
    include_fwhm : `bool`, optional
        If True, also measures sharpness (FWHM). This takes a long time!
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
    from astrometricslib.image_processing.quality_metrics import (
        measure_frame_input_quality as measure_one_frame,
    )

    counts = {"measured": 0, "skipped": 0, "failed": 0}

    for frame in target.frames:
        if camera_name and camera_name.lower() not in (frame.camera or "").lower():
            continue
        if not remeasure and frame.background_level is not None:
            counts["skipped"] += 1
            continue

        metrics = measure_one_frame(frame.path, include_fwhm=include_fwhm)
        if metrics["background_level"] is None:
            counts["failed"] += 1
            continue

        frame.background_level = metrics["background_level"]
        if metrics["saturated_pixel_fraction"] is not None:
            frame.saturated_pixel_fraction = metrics["saturated_pixel_fraction"]
        if metrics["fwhm_px"] is not None:
            # Deliberately NOT written into registration_fwhm_x/y_px.
            # Siril's registration PSF fit and this photutils measurement
            # are not on the same absolute scale -- measured on identical
            # NGC 4438 frames, photutils reports ~1.53x Siril's value
            # (5.1px vs 3.2px), and stacking_tasks.py already documents
            # the same mismatch from an independent M 13 comparison.
            # Mixing them in one field would make a frame appear to jump
            # ~50% in seeing purely from which stage measured it, which
            # is precisely the false signal any trend analysis over these
            # numbers must not see. Siril's pair is also genuinely
            # per-axis (fwhm_x != fwhm_y on 228 of 238 measured frames),
            # carrying elongation information a single circularized
            # median cannot represent.
            frame.measured_fwhm_px = metrics["fwhm_px"]
        counts["measured"] += 1

    return counts


def get_frame_stats_grouped(target: Any, calibration: Any, camera: str | None = None) -> list[dict[str, Any]]:
    """Group image statistics and check if we have matching dark frames.

    This groups the images by filter, ISO, and exposure time. For each group,
    it also checks the calibration library to see if we have dark frames
    that match those exact settings.

    Parameters
    ----------
    target : `Any`
        The target to summarize.
    calibration : `Any`
        The library of calibration frames (dark frames, flat frames, etc).
    camera : `str`, optional
        Only check images from this camera.

    Returns
    -------
    grouped_stats : `list` of `dict`
        A list where each dictionary represents a group of images with the
        same settings, and includes a count of matching dark frames.
    """
    if not target:
        return []

    aggregates = {}
    cameras_by_group = {}

    for frame in target.frames:
        if frame.role != "LIGHT":
            continue
        if camera and frame.camera != camera:
            continue

        filter_str = frame.filter.name if hasattr(frame.filter, "name") else str(frame.filter)
        if filter_str == "NONE":
            filter_str = "None"
        iso_str = str(frame.iso)

        try:
            exp_float = float(frame.exposure)
        except ValueError, TypeError:
            exp_float = 1.0

        key = (filter_str, iso_str, exp_float)
        aggregates[key] = aggregates.get(key, 0) + 1

        if key not in cameras_by_group:
            cameras_by_group[key] = frame.camera

    results = []
    sorted_keys = sorted(aggregates.keys(), key=lambda x: (x[0], x[1]))

    for key in sorted_keys:
        f, iso, e = key
        count = aggregates[key]
        cam = cameras_by_group.get(key, camera or "Unknown")

        darks = calibration.get("dark", camera=cam, exposure=e)

        results.append({
            "filter": f,
            "iso": iso,
            "exposure": f"{e:g}",
            "count": count,
            "darks": f"{len(darks)}" if darks else "Missing",
            "camera": cam,
        })

    return results
