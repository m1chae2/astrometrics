"""Frame statistics and scanning.

Extracted astronomical target stats calculations, grouping of light
frame logs, FITS header queries, and recursive folder scanners.
"""

from typing import Any


def get_frame_stats(target: Any) -> dict[str, Any]:
    """Return aggregated frame counts grouped by acquisition specs.

    Parameters
    ----------
    target : `Any`
        The target whose frames are summarized.

    Returns
    -------
    frame_stats : `Dict[str, Any]`
        A dict with key "lights", a list of per-acquisition-spec
        entries each containing "telescope", "camera", "iso",
        "exposure", "filter", and "count". Empty when the target has
        no light frames.
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
    """Count frames per distinct camera name across a list of targets.

    Intended for discoverability: callers of pipeline entry points
    that filter by camera (e.g. `run_full_pipeline`'s `camera_name`)
    have no other way to know which camera names are actually present
    in the catalog's frame data before choosing one.

    Parameters
    ----------
    targets : `list`
        The targets whose frames are counted.

    Returns
    -------
    counts_by_camera : `Dict[str, int]`
        Each distinct camera name found (the frame's raw `camera`
        value, unset frames reported as ``"Unknown"``) mapped to how
        many frames across all given targets used it, sorted by count
        descending.
    """
    counts: dict[str, int] = {}
    for target in targets:
        for frame in target.frames:
            camera_name = frame.camera or "Unknown"
            counts[camera_name] = counts.get(camera_name, 0) + 1

    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def get_frame_stats_grouped(target: Any, calibration: Any, camera: str | None = None) -> list[dict[str, Any]]:
    """Return frame statistics grouped by filter and exposure.

    Parameters
    ----------
    target : `Any`
        The target whose frames are summarized.
    calibration : `Any`
        A `CalibrationCatalog`-like object (``.get(kind, **kwargs)``)
        used to look up matching dark frames.
    camera : `str`, optional
        If given, restrict the summary to light frames captured with
        this camera; default `None` (no restriction).

    Returns
    -------
    grouped_stats : `List[Dict[str, Any]]`
        One entry per (filter, iso, exposure) group, sorted by filter
        then iso, each containing "filter", "iso", "exposure" (as a
        formatted string), "count", "darks" (matched dark frame count
        or "Missing"), and "camera". Empty when the target is not
        found.
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
