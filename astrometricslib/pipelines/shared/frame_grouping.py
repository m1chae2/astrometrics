"""Sorting a target's image frames by camera, optic, and role.

Every pipeline needs to answer some version of "which of this target's
frames actually belong together?" -- stacking can only combine frames
shot with the same camera and the same focal length, and a frame with
no recorded focal length can't be sorted at all. These functions are
the one place that logic lives, so every pipeline answers the question
the same way.
"""

from typing import Any

from astrometricslib.models.target import FrameRecord
from astrometricslib.utilities.enums import FilterType


def select_frames_for_camera(target: Any, camera_name: str) -> list:
    """Find all images taken with a specific camera.

    You can provide either the full camera name or just a part of it
    (like 'ZWO' or 'Canon'). It ignores capitalization.

    Parameters
    ----------
    target : `Any`
        The target object containing the images.
    camera_name : `str`
        The name (or part of the name) of the camera to look for.

    Returns
    -------
    camera_frames : `list`
        A list of images taken by that camera.
    """
    return [frame for frame in target.frames if camera_name.lower() in (frame.camera or "").lower()]


def frame_configuration_key(frame: Any) -> str | None:
    """Create a label identifying the camera and telescope combination.

    We can only combine (stack) images taken with the exact same
    camera and telescope (focal length). Mixing different ones creates
    a bad image that can't be measured properly. This function creates
    a label like 'Canon@300mm' to help group matching images together.

    Parameters
    ----------
    frame : `Any`
        The image record to look at.

    Returns
    -------
    configuration_key : `str` or `None`
        The label (like "<camera>@<focal_length>mm"), or None if the
        focal length is missing.
    """
    focal_length = getattr(frame, "focal_length_mm", None)
    if not focal_length or focal_length <= 0:
        return None
    camera = (getattr(frame, "camera", None) or "Unknown").strip()
    # Rounded to whole millimetres so 405.0 and 405 key identically; no
    # real optic is distinguished by a fraction of a millimetre.
    return f"{camera}@{round(float(focal_length))}mm"


def group_frames_by_configuration(target: Any, camera_name: str | None = None) -> dict[str, list]:
    """Sort a target's images into groups that can be stacked together.

    Parameters
    ----------
    target : `Any`
        The target containing the images.
    camera_name : `str`, optional
        Only include images from this camera (ignores capitalization).

    Returns
    -------
    frames_by_configuration : `dict` [`str`, `list`]
        A dictionary where the keys are the camera/telescope combo labels
        and the values are lists of images. The largest group is first.
        Images missing focal length data are skipped.
    """
    grouped: dict[str, list] = {}
    for frame in target.frames or []:
        if camera_name and camera_name.lower() not in (frame.camera or "").lower():
            continue
        key = frame_configuration_key(frame)
        if key is None:
            continue
        grouped.setdefault(key, []).append(frame)
    return dict(sorted(grouped.items(), key=lambda item: -len(item[1])))


def frames_missing_focal_length(target: Any, camera_name: str | None = None) -> list:
    """Find images that are missing focal length information.

    Images without a focal length can't be safely stacked because we don't
    know how zoomed in they are. This function finds them so we can warn
    the user, instead of just silently ignoring them.

    Parameters
    ----------
    target : `Any`
        The target to check.
    camera_name : `str`, optional
        Only check images from this specific camera.

    Returns
    -------
    unassignable_frames : `list`
        A list of images that are missing focal length data.
    """
    return [
        frame
        for frame in target.frames or []
        if (not camera_name or camera_name.lower() in (frame.camera or "").lower())
        and frame_configuration_key(frame) is None
    ]


def select_frames_for_configuration(target: Any, configuration_key: str) -> list:
    """Select the frames belonging to one camera-and-optic configuration.

    Parameters
    ----------
    target : `Any`
        The target whose `frames` are filtered.
    configuration_key : `str`
        A key as produced by `frame_configuration_key`.

    Returns
    -------
    configuration_frames : `list`
        The matching frames, in their original order.
    """
    return [frame for frame in target.frames or [] if frame_configuration_key(frame) == configuration_key]


def add_frame(  # ruff: ignore[missing-return-type-undocumented-public-function]
    target,  # ruff: ignore[missing-type-function-argument]
    path: str,
    role: str = "LIGHT",
    filter_type: str | None = None,
    camera: str | None = None,
):
    """Add an image to a target, or update it if it's already there.

    This function reads the metadata from the image file and updates the
    target's total exposure time.

    Returns
    -------
    frame_record : `FrameRecord`
        The new or updated image record.

    Raises
    ------
    ValueError
        If adding this frame would mix spectral ('SPEC') and standard
        imaging frames on the same target.
    """
    from astrometricslib.data_access.frame_scanning import create_frame_record_from_fits

    record = create_frame_record_from_fits(path, camera)
    record.role = role
    if filter_type is not None:
        record.filter = FrameRecord.normalize_filter(filter_type)

    is_spectral = record.filter == FilterType.SPEC
    has_spectral = any(f.filter == FilterType.SPEC for f in target.frames)
    has_standard = any(f.filter != FilterType.SPEC for f in target.frames)

    if is_spectral and has_standard:
        raise ValueError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )
    if not is_spectral and has_spectral:
        raise ValueError(
            "Target contains a mixed set of spectral ('SPEC') and standard imaging frames. "
            "Stacking mixed frame types is not permitted."
        )

    for f in target.frames:
        if f.path == path:
            f.role = role
            if filter_type is not None:
                f.filter = record.filter
            target.recalculate_total_exposure()
            return f

    target.frames.append(record)
    target.recalculate_total_exposure()
    return record
