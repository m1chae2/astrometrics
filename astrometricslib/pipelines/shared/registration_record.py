"""Keeps, beside a stack, which frames Siril lined up and how it moved them.

When Siril stacks a target it lines every frame up with one reference
frame. For each frame it saves a transform (a 3 x 3 matrix) that moves the
frame onto the reference. The stacking run holds those transforms, and the
list of frame files they belong to, only while it runs. The astrometry run
needs them later. It solves the finished stack. Once the stack has a plate
solution, the target's sky position gives a pixel on the stack, and the
inverse of each frame's transform gives the pixel on that frame (see
`pipelines/astrometry/post_processing/target_clipping.py`).

The stacking run therefore writes this record next to the stack, with the
stack's other companion files (``<stack name>_registration_frames.json``),
and the astrometry run reads it. Siril's own ``_Registration.seq`` file holds
the same transforms, but it names the frames only by position, so the list of
frame paths in Siril's order has to be kept as well. The record also keeps the
(columns, rows) trimmed from the stack's left and top edges, because the
plate solution of a trimmed stack is on the trimmed grid and the transforms
are on the registration reference frame's grid.

A stack made before this record existed has none. A reader then gets `None`,
and the stack is stacked again to write one.
"""

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "REGISTRATION_RECORD_SUFFIX",
    "RegistrationRecord",
    "build_registration_record",
    "is_already_registered",
    "read_registration_record",
    "registration_record_path",
    "write_registration_record",
]

# What follows a stack's own name (without .fits) in the name of its record.
REGISTRATION_RECORD_SUFFIX = "_registration_frames.json"

# Raise this when the layout of the record changes, so an old reader refuses a
# newer file and a newer reader refuses an older one.
REGISTRATION_RECORD_VERSION = 1

# A transform is a 3 x 3 matrix written as nine numbers.
HOMOGRAPHY_LENGTH = 9


@dataclass(frozen=True)
class RegistrationRecord:
    """Siril's registration of one stack, frame by frame.

    Attributes
    ----------
    light_paths : `tuple` of `str`
        The frame files in Siril's order. A position here names the same frame
        as the same position in `homographies` and `included`.
    homographies : `tuple` of `tuple` of `float`
        For each frame, the nine numbers of its 3 x 3 transform in row order
        (the transform that moves the frame onto the reference). Siril's x
        counts along the columns and its y counts up from the last row of the
        array (y = height - 1 - row).
    included : `tuple` of `bool`
        For each frame, `False` when Siril could not register it and left it
        out of the stack.
    crop_offset : `tuple` of `int`
        The (columns, rows) trimmed from the stack's left and top edges after
        stacking.
    reference_frame : `str` or `None`
        The path of the frame every other frame was lined up with, or `None`
        when the run could not name one with certainty.
    is_spectral : `bool`
        Whether the stack is spectral. A spectral stack is lined up by shift
        only, on the zero-order star, so its transforms are not those of an
        image.
    """

    light_paths: tuple[str, ...]
    homographies: tuple[tuple[float, ...], ...]
    included: tuple[bool, ...]
    crop_offset: tuple[int, int]
    reference_frame: str | None
    is_spectral: bool = False


def _is_identity(homography: Sequence[float]) -> bool:
    """Say whether one frame's transform is the identity (no shift, no turn).

    Parameters
    ----------
    homography : `Sequence` [`float`]
        The nine numbers of the transform.

    Returns
    -------
    is_identity : `bool`
        `True` when the shift and the rotation are exactly zero.
    """
    return not homography[2] and not homography[5] and not (homography[3] - homography[1])


def is_already_registered(record: RegistrationRecord) -> bool:
    """Say whether the frames were registered before Siril read them.

    Frames that were lined up before Siril read them all get the identity
    transform, so the shifts and angles it reports say nothing about the
    pointing. A single frame cannot be told apart from a reference frame, so
    only a sequence of two or more is judged.

    Parameters
    ----------
    record : `RegistrationRecord`
        The stack's registration.

    Returns
    -------
    already_registered : `bool`
        `True` when there are two or more frames and every transform is the
        identity.
    """
    return len(record.homographies) > 1 and all(_is_identity(h) for h in record.homographies)


def registration_record_path(stack_path: str) -> str:
    """Name the record that goes with a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack's path.

    Returns
    -------
    record_path : `str`
        The stack's path without ``.fits``, plus `REGISTRATION_RECORD_SUFFIX`.
    """
    return os.path.splitext(stack_path)[0] + REGISTRATION_RECORD_SUFFIX


def build_registration_record(
    diagnostics: dict[str, Any],
    crop_offset: tuple[int, int],
    reference_frame: str | None,
    *,
    is_spectral: bool = False,
) -> RegistrationRecord | None:
    """Collect the registration facts of a stacking run into a record.

    Parameters
    ----------
    diagnostics : `dict`
        What the stacking run reported. ``symlinked_light_paths`` (the frame
        paths in Siril's order), ``registration_frames`` (one dict per frame,
        each with a ``homography``) and ``registration_included`` (one flag per
        frame) are read.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) trimmed from the stack's left and top edges.
    reference_frame : `str` or `None`
        The registration reference frame, if the run could name one.
    is_spectral : `bool`, optional
        Whether the stack is spectral.

    Returns
    -------
    record : `RegistrationRecord` or `None`
        `None` when the frame list and the registration table are missing or
        differ in length, because then a position cannot name a frame, or when
        a frame's transform is missing or is not nine numbers. A missing list
        of inclusion flags means every frame was included.
    """
    paths = diagnostics.get("symlinked_light_paths") or []
    registration_frames = diagnostics.get("registration_frames") or []
    if not paths or len(paths) != len(registration_frames):
        return None
    if any(len(facts.get("homography") or []) != HOMOGRAPHY_LENGTH for facts in registration_frames):
        return None
    flags = diagnostics.get("registration_included") or []
    included = tuple(bool(flag) for flag in flags) if len(flags) == len(paths) else (True,) * len(paths)
    return RegistrationRecord(
        light_paths=tuple(str(path) for path in paths),
        homographies=tuple(
            tuple(float(value) for value in facts["homography"]) for facts in registration_frames
        ),
        included=included,
        crop_offset=(int(crop_offset[0]), int(crop_offset[1])),
        reference_frame=reference_frame,
        is_spectral=is_spectral,
    )


def write_registration_record(stack_path: str, record: RegistrationRecord) -> None:
    """Write a stack's registration record next to it.

    A failure to write is logged and never raised: the record is a by-product
    of the stack.

    Parameters
    ----------
    stack_path : `str`
        The finished stack.
    record : `RegistrationRecord`
        What to keep.
    """
    document = {
        "version": REGISTRATION_RECORD_VERSION,
        "light_paths": list(record.light_paths),
        "homographies": [list(values) for values in record.homographies],
        "included": list(record.included),
        "crop_offset": list(record.crop_offset),
        "reference_frame": record.reference_frame,
        "is_spectral": record.is_spectral,
    }
    path = registration_record_path(stack_path)
    try:
        with open(path, "w", encoding="utf-8") as record_file:
            json.dump(document, record_file)
    except OSError as error:
        logger.warning("The registration record %s could not be written: %s", path, error)


def read_registration_record(stack_path: str) -> RegistrationRecord | None:
    """Read the registration record that goes with a stack.

    Parameters
    ----------
    stack_path : `str`
        The stack.

    Returns
    -------
    record : `RegistrationRecord` or `None`
        `None` when the stack has no record, or the record is unreadable or
        of another version or its lists differ in length.
    """
    path = registration_record_path(stack_path)
    try:
        with open(path, encoding="utf-8") as record_file:
            document = json.load(record_file)
        if document["version"] != REGISTRATION_RECORD_VERSION:
            return None
        paths = tuple(str(value) for value in document["light_paths"])
        homographies = tuple(tuple(float(number) for number in values) for values in document["homographies"])
        included = tuple(bool(flag) for flag in document["included"])
        crop_offset = (int(document["crop_offset"][0]), int(document["crop_offset"][1]))
        reference = document["reference_frame"]
        is_spectral = bool(document["is_spectral"])
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        logger.debug("No usable registration record at %s: %s", path, error)
        return None
    if not (len(paths) == len(homographies) == len(included)) or any(
        len(h) != HOMOGRAPHY_LENGTH for h in homographies
    ):
        return None
    return RegistrationRecord(
        paths, homographies, included, crop_offset, None if reference is None else str(reference), is_spectral
    )
