"""Purpose: The ``frames=`` argument: which frames a method works on.

Description: A public method that works on some of a target's frames takes
them as one argument, ``frames=``. A caller passes what
`targets.frames.query` gave back (a `Page` of frame records), a list of frame
records, or a list of file paths. `FrameChoice` names those three forms.

`frames_from_choice` turns any of them into a list of frame records. It
refuses a page that was cut short (one with a ``next_offset``), because such
a page does not hold every frame the query matched, and a run on it would
quietly leave frames out. Paths are matched against the target's own frames.

`check_frame_list` is the one frame-list check: the list is not empty, and
its frames come from one camera, one optic, and one kind (imaging or
spectral). A stack and its prediction run it on an exact frame list.
`check_frames` does both steps; it is public as
`astrometricslib.arguments.check_frames`, so an adapter can refuse a frame
choice before it starts a job.

Every refusal is an `InvalidArgumentError` or a `NotFoundError`.
"""

import os
from collections.abc import Sequence
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.camera_selection import ensure_single_camera
from astrometricslib.pipelines.shared.optic_selection import ensure_single_optic
from astrometricslib.utilities.frame_identity import frame_is_spectral

__all__ = ["FrameChoice", "check_frame_list", "check_frames", "frames_from_choice"]


FrameChoice = Page[FrameRecord] | Sequence[FrameRecord] | Sequence[str | os.PathLike[str]]
"""The frames a method works on: the `Page` that `targets.frames.query`
returns, a list of frame records, or a list of file paths."""


def frames_from_choice(frames: FrameChoice | Any, target: Target | None = None) -> list[FrameRecord]:
    """Turn a ``frames=`` argument into a list of frame records.

    Parameters
    ----------
    frames : `FrameChoice`
        A `Page` of frame records, a list of frame records, or a list of
        file paths.
    target : `Target`, optional
        The target the frames belong to. Needed when ``frames`` holds paths:
        each path is matched to one of the target's frames.

    Returns
    -------
    records : `list` [`FrameRecord`]
        The frames, in the order given.

    Raises
    ------
    InvalidArgumentError
        If ``frames`` is a page that was cut short, a single string, a list
        that mixes records and paths, or a list of something else, or it
        holds paths and no ``target`` is given.
    NotFoundError
        If a path is not one of the target's frames.
    """
    if isinstance(frames, Page):
        if frames.next_offset is not None:
            raise InvalidArgumentError(
                f"frames is a page cut short: it holds {len(frames)} of {frames.total} matching frames. "
                "Query again without a limit, or pass the frames you want as a list.",
                details={"items": len(frames), "total": frames.total, "next_offset": frames.next_offset},
            )
        return list(frames.items)
    if isinstance(frames, str | bytes | os.PathLike) or not isinstance(frames, Sequence):
        raise InvalidArgumentError(
            "frames must be the page targets.frames.query returns, a list of frame records, "
            "or a list of paths."
        )
    items = list(frames)
    if all(isinstance(item, FrameRecord) for item in items):
        return items
    if not all(isinstance(item, str | os.PathLike) for item in items):
        raise InvalidArgumentError("frames must hold only frame records or only file paths, not a mix.")
    if target is None:
        raise InvalidArgumentError("frames given as paths need the target they belong to.")
    by_path = {os.path.normpath(frame.path): frame for frame in target.frames}
    records: list[FrameRecord] = []
    missing: list[str] = []
    for item in items:
        path = os.path.normpath(os.fspath(item))
        if path in by_path:
            records.append(by_path[path])
        else:
            missing.append(os.fspath(item))
    if missing:
        raise NotFoundError(
            f"{len(missing)} of the paths in frames are not frames of {target.id!r}.",
            details={"target": target.id, "missing": missing[:20]},
        )
    return records


def check_frame_list(frames: Sequence[FrameRecord], *, target_id: str | None = None) -> None:
    """Refuse an exact frame list that cannot be worked on as one set.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames.
    target_id : `str`, optional
        The target, for the messages. Given, a mix of cameras or optics is
        an `InvalidArgumentError` that names the target; left out, it is a
        `ConflictError` (see `ensure_single_camera`).

    Raises
    ------
    InvalidArgumentError
        If the list is empty, its frames come from more than one camera or
        optic, or it mixes imaging and spectral frames. The message gives
        the counts.
    """
    if not frames:
        raise InvalidArgumentError("frames is empty: there is nothing to work on.")
    ensure_single_camera(frames, target_id=target_id)
    ensure_single_optic(frames, target_id=target_id)
    spectral_count = sum(1 for frame in frames if frame_is_spectral(frame))
    if 0 < spectral_count < len(frames):
        # The request itself is wrong (the caller can fix it by picking one
        # kind), so this is a refusal of the argument, not a conflict with
        # the library's state.
        raise InvalidArgumentError(
            f"These frames mix imaging and spectral frames ({len(frames) - spectral_count} imaging, "
            f"{spectral_count} spectral). Select only one kind and stack again.",
            details={
                "imaging_frames": len(frames) - spectral_count,
                "spectral_frames": spectral_count,
            },
        )


def check_frames(frames: FrameChoice, target: Target | None = None) -> list[FrameRecord]:
    """Check a ``frames=`` argument as a pipeline run would, without running.

    The check reads no frame file, so it is quick.

    Parameters
    ----------
    frames : `FrameChoice`
        A `Page` of frame records, a list of frame records, or a list of
        file paths.
    target : `Target`, optional
        The target the frames belong to. Needed when ``frames`` holds paths.

    Returns
    -------
    records : `list` [`FrameRecord`]
        The frames, in the order given.

    Notes
    -----
    Raises `InvalidArgumentError` if ``frames`` is a page cut short, empty,
    or of the wrong form, or its frames come from more than one camera or
    optic, or mix imaging and spectral frames; the message names each group
    and its count. Raises `NotFoundError` if a path is not one of the
    target's frames.
    """
    records = frames_from_choice(frames, target)
    check_frame_list(records, target_id=target.id if target is not None else None)
    return records
