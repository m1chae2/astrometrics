"""Keeps a stack to the frames of one optic.

A camera used through two optics makes frames at two image scales. This
library has a Nikon D5300 behind both a 300 mm lens and a 405 mm telescope,
and those scales differ by 1.35 times. A stack that mixes them has no single
pixel scale, cannot be plate solved well, and blurs the stars of one optic
with the shape of the other. The single-camera check does not catch this,
because the camera is the same. The M 27 stack of 2026-11-15 mixed 60 frames
at 300 mm with 21 at 405 mm this way.

This module does two things, as `camera_selection` does for cameras:

- `choose_optic_frames` picks one optic's frames from a list, when a person
  names the focal length they want.
- `ensure_single_optic` refuses to go on if the frames still come from more
  than one optic.

An optic is known by its focal length, rounded to whole millimeters, the same
rounding `frame_configuration_key` uses. Frames with no focal length recorded
are not counted, because nothing says they differ from the others.
"""

from collections.abc import Sequence
from typing import Any

from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError

__all__ = ["choose_optic_frames", "ensure_single_optic", "split_frames_by_optic"]


def _focal_length_key(frame: Any) -> int | None:
    """Give a frame's focal length in whole millimeters.

    Parameters
    ----------
    frame : `Any`
        A frame record with a ``focal_length_mm``.

    Returns
    -------
    focal_length_mm : `int` or `None`
        The rounded focal length, or `None` when none is recorded.
    """
    focal_length = getattr(frame, "focal_length_mm", None)
    if not focal_length or focal_length <= 0:
        return None
    return round(float(focal_length))


def split_frames_by_optic(frames: Sequence[Any]) -> dict[int, list[Any]]:
    """Group frames by the focal length they were taken at.

    Frames with no focal length recorded are left out.

    Parameters
    ----------
    frames : `Sequence`
        Frame records, each with a ``focal_length_mm``.

    Returns
    -------
    frames_by_optic : `dict` [`int`, `list`]
        The focal length in whole millimeters mapped to its frames, in the
        order given.
    """
    groups: dict[int, list[Any]] = {}
    for frame in frames:
        focal_length = _focal_length_key(frame)
        if focal_length is not None:
            groups.setdefault(focal_length, []).append(frame)
    return groups


def describe_optics(frames_by_optic: dict[int, list[Any]]) -> str:
    """Write the optics and their frame counts as one line.

    Parameters
    ----------
    frames_by_optic : `dict` [`int`, `list`]
        What `split_frames_by_optic` returns.

    Returns
    -------
    description : `str`
        Such as ``405 mm (21 frames), 300 mm (60 frames)``.
    """
    return ", ".join(
        f"{focal_length} mm ({len(group)} frames)" for focal_length, group in frames_by_optic.items()
    )


def ensure_single_optic(frames: Sequence[Any], *, target_id: str | None = None) -> None:
    """Refuse to go on if the frames come from more than one optic.

    It is used next to `ensure_single_camera`, by the same callers and in the
    same way. Internal steps give no target name, so a mixed set is a
    `ConflictError`. The public ``stack`` call gives the target's name, and its
    caller chose the frames, so a mixed set is an `InvalidArgumentError` that
    tells the caller how to choose one optic.

    Parameters
    ----------
    frames : `Sequence`
        The frames about to be stacked.
    target_id : `str`, optional
        The target the frames belong to. Given by the public ``stack`` call;
        it selects the `InvalidArgumentError` and names the target in the
        message.

    Raises
    ------
    ConflictError
        If the frames come from more than one optic and no ``target_id`` is
        given. The message names each focal length and its frame count.
    InvalidArgumentError
        If the frames come from more than one optic and ``target_id`` is
        given. The message names each focal length and its frame count.
    """
    frames_by_optic = split_frames_by_optic(frames)
    if len(frames_by_optic) <= 1:
        return
    if target_id is None:
        raise ConflictError(
            "These frames were taken through more than one optic, and frames at different focal lengths "
            f"cannot be stacked together: {describe_optics(frames_by_optic)}. "
            "Choose the frames of one optic."
        )
    raise InvalidArgumentError(
        f"The selected frames of {target_id!r} were taken through more than one optic: "
        f"{describe_optics(frames_by_optic)}. Frames at different focal lengths are never stacked together. "
        "Pass focal_length_mm to choose one.",
        details={
            "target": target_id,
            "focal_lengths_mm": {focal_length: len(group) for focal_length, group in frames_by_optic.items()},
        },
    )


def choose_optic_frames(frames: Sequence[Any], focal_length_mm: float) -> tuple[list[Any], str | None]:
    """Pick the frames taken at one focal length.

    The focal length is rounded to whole millimeters before it is compared.
    Frames with no focal length recorded are not picked, because nothing says
    they came through this optic.

    Parameters
    ----------
    frames : `Sequence`
        Frame records to choose from.
    focal_length_mm : `float`
        The focal length wanted, in millimeters.

    Returns
    -------
    chosen : `list`
        The frames at that focal length, in the order given.
    problem : `str` or `None`
        `None` when frames were found. Otherwise a sentence naming the focal
        lengths there are.
    """
    frames_by_optic = split_frames_by_optic(frames)
    wanted = round(float(focal_length_mm))
    if wanted not in frames_by_optic:
        return [], (
            f"No frames at a focal length of {wanted} mm. The focal lengths here are: "
            f"{describe_optics(frames_by_optic) or 'none recorded'}."
        )
    return list(frames_by_optic[wanted]), None
