"""Summarize which configured camera took each target's light frames, and when.

The target list in the user interface can be filtered by camera and sorted by
the most recent image. Sending every frame of every target to the browser
would be slow, so this module boils the frames down to a small table: for each
target, how many light frames each camera took and the time of the newest one.

Cameras write their name into FITS headers in their own spelling (for example
``Nikon DSLR DSC D5300``), while the config lists the same camera under a
shorter name (``Nikon D5300``). The two are matched with ``camera_identity``,
which knows each camera profile's alternate spellings.
"""

from collections.abc import Callable, Iterable
from typing import Any


def build_target_camera_index(
    targets: Iterable[Any],
    camera_names: list[str],
    camera_identity: Callable[[str], str],
) -> dict[str, Any]:
    """Count light frames and find the newest frame per target and camera.

    Parameters
    ----------
    targets : `Iterable`
        Target objects, each with an ``id`` and a ``frames`` list. Every frame
        has ``role``, ``camera`` and ``timestamp`` attributes.
    camera_names : `list` [`str`]
        The configured camera names to report on, in the order to list them.
    camera_identity : `Callable` [[`str`], `str`]
        Reduces a camera name to text that is the same for every spelling of
        that camera.

    Returns
    -------
    index : `dict`
        ``cameras`` lists each configured camera as
        ``{"name", "targetCount"}``.
        ``targets`` maps a target id to ``{"lastFrameTime", "cameras"}``, where
        ``lastFrameTime`` is the newest light-frame time (seconds since 1970,
        or `None` when no frame has a time) and ``cameras`` maps a camera name
        to ``{"frameCount", "lastFrameTime"}``. Frames from a camera that is
        not configured count toward ``lastFrameTime`` but not ``cameras``.
    """
    configured_name_by_identity = {camera_identity(name): name for name in camera_names}
    target_count_by_camera = dict.fromkeys(camera_names, 0)
    targets_summary: dict[str, Any] = {}

    for target in targets:
        newest_time: float | None = None
        per_camera: dict[str, dict[str, Any]] = {}
        for frame in target.frames:
            if str(frame.role).upper() != "LIGHT":
                continue
            frame_time = frame.timestamp
            if frame_time is not None and (newest_time is None or frame_time > newest_time):
                newest_time = frame_time
            if not frame.camera or frame.camera == "Unknown":
                continue
            configured_name = configured_name_by_identity.get(camera_identity(frame.camera))
            if configured_name is None:
                continue
            camera_entry = per_camera.setdefault(configured_name, {"frameCount": 0, "lastFrameTime": None})
            camera_entry["frameCount"] += 1
            previous_time = camera_entry["lastFrameTime"]
            if frame_time is not None and (previous_time is None or frame_time > previous_time):
                camera_entry["lastFrameTime"] = frame_time
        for configured_name in per_camera:
            target_count_by_camera[configured_name] += 1
        targets_summary[str(target.id)] = {"lastFrameTime": newest_time, "cameras": per_camera}

    return {
        "cameras": [{"name": name, "targetCount": target_count_by_camera[name]} for name in camera_names],
        "targets": targets_summary,
    }
