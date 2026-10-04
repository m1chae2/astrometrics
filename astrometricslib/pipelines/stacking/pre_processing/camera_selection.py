"""Keeps a stack to the frames of one camera.

Two cameras do not make frames that can be stacked together. Their pixel
sizes, gain scales, noise and calibration frames all differ, so a stack that
mixes them has no single meaning. The stacker used to take every frame it was
given. The gain check then kept the most common gain across both cameras, so
which camera's frames made the stack depended on which camera had more of
them: the right one by luck for one target, the wrong one for another.

This module does two things:

- `choose_camera_frames` picks one camera's frames from a list, when a person
  names the camera they want.
- `ensure_single_camera` refuses to go on if the frames still come from more
  than one camera.

Frames with no camera recorded are not counted, because nothing says they
differ from the others.
"""

from collections.abc import Sequence
from typing import Any

from astrometricslib.drivers.camera_profile_store import camera_identity

__all__ = ["choose_camera_frames", "ensure_single_camera", "split_frames_by_camera"]

# What a frame record holds for its camera when none was recorded.
_UNKNOWN_CAMERA_NAMES = frozenset({"", "unknown"})


def _is_known_camera(camera_name: str | None) -> bool:
    """Say whether a frame's camera name names a camera.

    Returns
    -------
    known : `bool`
        `False` for a missing or "Unknown" name.
    """
    return bool(camera_name) and str(camera_name).strip().lower() not in _UNKNOWN_CAMERA_NAMES


def split_frames_by_camera(frames: Sequence[Any]) -> dict[str, list[Any]]:
    """Group frames by the camera that took them.

    Names that mean the same camera (different spelling, case or a listed
    alias) go in one group. Frames with no camera recorded are left out.

    Parameters
    ----------
    frames : `Sequence`
        Frame records, each with a ``camera`` name.

    Returns
    -------
    frames_by_camera : `dict` [`str`, `list`]
        The camera's name, as the first of its frames spells it, mapped to its
        frames in the order given.
    """
    groups: dict[str, list[Any]] = {}
    name_by_identity: dict[str, str] = {}
    for frame in frames:
        if not _is_known_camera(frame.camera):
            continue
        identity = camera_identity(frame.camera)
        display_name = name_by_identity.setdefault(identity, str(frame.camera))
        groups.setdefault(display_name, []).append(frame)
    return groups


def describe_cameras(frames_by_camera: dict[str, list[Any]]) -> str:
    """Write the cameras and their frame counts as one line.

    Returns
    -------
    description : `str`
        Such as ``'ZWO ASI 533MM Pro' (2 frames), 'Nikon D5300' (3 frames)``.
    """
    return ", ".join(f"'{name}' ({len(group)} frames)" for name, group in frames_by_camera.items())


def ensure_single_camera(frames: Sequence[Any]) -> None:
    """Refuse to go on if the frames come from more than one camera.

    Parameters
    ----------
    frames : `Sequence`
        The frames about to be stacked.

    Raises
    ------
    ValueError
        If the frames come from more than one camera. The message names the
        cameras and how many frames each took.
    """
    frames_by_camera = split_frames_by_camera(frames)
    if len(frames_by_camera) > 1:
        raise ValueError(
            "These frames come from more than one camera, and frames from different cameras "
            f"cannot be stacked together: {describe_cameras(frames_by_camera)}. "
            "Choose the frames of one camera."
        )


def choose_camera_frames(frames: Sequence[Any], camera: str) -> tuple[list[Any], str | None]:
    """Pick the frames that one named camera took.

    The name matches a frame's camera when they are the same camera (any
    spelling or listed alias), or when the text is part of the frame's camera
    name, ignoring case, such as ``ASI 533MM``. Frames with no camera recorded
    are not picked, because nothing says they came from this camera.

    Parameters
    ----------
    frames : `Sequence`
        Frame records to choose from.
    camera : `str`
        The camera wanted.

    Returns
    -------
    chosen : `list`
        The frames of that camera, in the order given.
    problem : `str` or `None`
        `None` when the camera was found. Otherwise a sentence saying that the
        name matched no camera, or more than one, and naming the cameras there
        are.
    """
    frames_by_camera = split_frames_by_camera(frames)
    wanted_identity = camera_identity(camera)
    wanted_text = camera.strip().lower()
    matching_names = [
        name
        for name in frames_by_camera
        if camera_identity(name) == wanted_identity or (wanted_text and wanted_text in name.lower())
    ]
    if not matching_names:
        return [], (
            f"No frames from a camera matching {camera!r}. The cameras here are: "
            f"{describe_cameras(frames_by_camera) or 'none recorded'}."
        )
    if len(matching_names) > 1:
        return [], (
            f"{camera!r} matches more than one camera: {', '.join(repr(name) for name in matching_names)}. "
            "Give more of the name."
        )
    return list(frames_by_camera[matching_names[0]]), None
