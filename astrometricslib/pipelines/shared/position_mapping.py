"""Follow one pixel of an image to the same spot on another image.

The viewer's Star profile panel pins a measured star. When the viewer shows
another image, the panel asks where that star is on the new one. This module
finds the answer in three ways, in order:

1. Sky position. Both images have a plate solution (``PLTSOLVD`` true or the
   SIP terms of a solved image, with both sky axes; see
   `header_is_plate_solved`).
   The pixel goes to the sky and back to a pixel.
2. Stack alignment. A stack has a record of how Siril moved each frame onto
   the stack's reference grid (`registration_record`). That gives frame to
   stack, stack to frame and frame to frame, and it can be chained with the
   sky position when the two images belong to different stacks.
3. The same pixel, as a guess, when nothing else applies.

All positions are 1-based FITS pixels (the center of the first pixel is 1, 1).
Siril counts pixels from 0, and its y axis runs up from the last row of the
array (``y = height - 1 - row``), as `astrometricslib` uses it (checked on the
M 57 night of 2026-10-03). The transform saved for a frame moves the frame onto
the reference, the stack was then trimmed ``crop_offset`` columns and rows from
its left and top edges, so a stack pixel is the reference pixel minus the
trim. A processed picture cropped from its stack says in ``CROPX0`` and
``CROPY0`` how many columns and rows were cut from the stack's left and top.

`ProcessingError` (from `astrometricslib.exceptions`) marks a pixel that
cannot be followed, such as a frame Siril left out of its stack. `map_position`
turns it into a ``"none"`` answer with the reason, so a caller never has to
catch it.
"""

import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib._internal.drivers.fits_access import header_is_plate_solved, read_header
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.quality.position_mapping import PositionMapping
from astrometricslib.pipelines.shared.registration_record import (
    REGISTRATION_RECORD_SUFFIX,
    RegistrationRecord,
    read_registration_record,
)
from astrometricslib.utilities.stack_preview_path import PROCESSED_FITS_SUFFIX

__all__ = ["RecordCache", "StackRecord", "frame_to_stack", "map_position", "stack_to_frame"]


@dataclass(frozen=True)
class StackRecord:
    """One stack's registration record and the stack file it belongs to.

    Attributes
    ----------
    stack_path : `str`
        The stack file.
    record : `RegistrationRecord`
        Siril's registration of the stack.
    """

    stack_path: str
    record: RegistrationRecord


class RecordCache:
    """Keeps the registration records read, until a record file changes."""

    def __init__(self) -> None:
        # Records by stack path, with the record file's time stamp.
        self._records: dict[str, tuple[float, StackRecord | None]] = {}

    def record_of(self, stack_path: str) -> StackRecord | None:
        """Read the record that goes with a stack, through the cache.

        Parameters
        ----------
        stack_path : `str`
            The stack.

        Returns
        -------
        record : `StackRecord` or `None`
            The record, or `None` when the stack has no usable one.
        """
        record_path = os.path.splitext(stack_path)[0] + REGISTRATION_RECORD_SUFFIX
        try:
            stamp = os.path.getmtime(record_path)
        except OSError:
            return None
        cached = self._records.get(stack_path)
        if cached is None or cached[0] != stamp:
            record = read_registration_record(stack_path)
            cached = (stamp, None if record is None else StackRecord(stack_path, record))
            self._records[stack_path] = cached
        return cached[1]


def _position_of(stack: StackRecord, frame_path: str) -> int | None:
    """Find a frame's place in a stack's record.

    Parameters
    ----------
    stack : `StackRecord`
        The stack's record.
    frame_path : `str`
        The frame file. Paths are compared as real paths first, then by file
        name when the name is unique.

    Returns
    -------
    position : `int` or `None`
        The index in the record, or `None` when the frame is not in it.
    """
    real = os.path.realpath(frame_path)
    paths = stack.record.light_paths
    for index, path in enumerate(paths):
        if os.path.realpath(path) == real:
            return index
    name = os.path.basename(frame_path)
    matches = [index for index, path in enumerate(paths) if os.path.basename(path) == name]
    return matches[0] if len(matches) == 1 else None


def _matrix(stack: StackRecord, position: int) -> np.ndarray:
    """Give one frame's 3 x 3 transform, or refuse when it cannot be used.

    Parameters
    ----------
    stack : `StackRecord`
        The stack's record.
    position : `int`
        The frame's index.

    Returns
    -------
    matrix : `numpy.ndarray`
        The transform that moves the frame onto the reference grid.

    Raises
    ------
    ProcessingError
        If the stack is spectral, or Siril left the frame out of the stack.
    """
    if stack.record.is_spectral:
        raise ProcessingError(
            "A spectral stack is lined up by shift on the zero-order star, so its frames cannot be followed."
        )
    if not stack.record.included[position]:
        raise ProcessingError("Siril could not register that frame, so it is not in the stack.")
    return np.asarray(stack.record.homographies[position], dtype=float).reshape(3, 3)


def frame_to_stack(
    stack: StackRecord, position: int, x: float, y: float, frame_height: int
) -> tuple[float, float]:
    """Move a pixel of a frame onto its stack.

    Parameters
    ----------
    stack : `StackRecord`
        The stack's record.
    position : `int`
        The frame's index in the record.
    x, y : `float`
        The pixel on the frame, 1-based.
    frame_height : `int`
        The frame's number of rows (Siril's y is ``height - 1 - row``).

    Returns
    -------
    x, y : `float`
        The pixel on the stack, 1-based.

    Raises
    ------
    ProcessingError
        If the frame cannot be followed.
    """
    matrix = _matrix(stack, position)
    moved = matrix @ np.array([x - 1.0, frame_height - 1 - (y - 1.0), 1.0])
    if math.isclose(moved[2], 0.0, abs_tol=1e-12):
        raise ProcessingError("The frame's transform cannot place that pixel.")
    reference_column = moved[0] / moved[2]
    reference_row = frame_height - 1 - moved[1] / moved[2]
    crop = stack.record.crop_offset
    return float(reference_column - crop[0]) + 1.0, float(reference_row - crop[1]) + 1.0


def stack_to_frame(
    stack: StackRecord, position: int, x: float, y: float, frame_height: int
) -> tuple[float, float]:
    """Move a pixel of a stack back onto one of its frames.

    Parameters
    ----------
    stack : `StackRecord`
        The stack's record.
    position : `int`
        The frame's index in the record.
    x, y : `float`
        The pixel on the stack, 1-based.
    frame_height : `int`
        The frame's number of rows.

    Returns
    -------
    x, y : `float`
        The pixel on the frame, 1-based.

    Raises
    ------
    ProcessingError
        If the frame cannot be followed.
    """
    matrix = _matrix(stack, position)
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as error:
        raise ProcessingError("The frame's transform cannot be turned around.") from error
    crop = stack.record.crop_offset
    reference_column = (x - 1.0) + crop[0]
    reference_row = (y - 1.0) + crop[1]
    moved = inverse @ np.array([reference_column, frame_height - 1 - reference_row, 1.0])
    if math.isclose(moved[2], 0.0, abs_tol=1e-12):
        raise ProcessingError("The frame's transform cannot place that pixel.")
    return float(moved[0] / moved[2]) + 1.0, float(frame_height - 1 - moved[1] / moved[2]) + 1.0


def _header_of(path: str) -> fits.Header:
    """Read the first header of a FITS file, without mapping it into memory.

    Parameters
    ----------
    path : `str`
        The file.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A copy of the header.
    """
    return read_header(path, first_hdu_only=True)


def _picture_offset(path: str) -> tuple[int, int]:
    """Say where a cropped processed picture starts on its stack.

    Parameters
    ----------
    path : `str`
        The image.

    Returns
    -------
    offset : `tuple` [`int`, `int`]
        Columns and rows to subtract from a stack pixel to get the picture
        pixel (``CROPX0`` and ``CROPY0``); (0, 0) for an image with no crop.
    """
    header = _header_of(path)
    return int(header.get("CROPX0", 0)), int(header.get("CROPY0", 0))


def _frame_height(path: str) -> int:
    """Read an image's number of rows from its header.

    Returns
    -------
    height : `int`
        The number of rows (1 when the header does not say).
    """
    return int(_header_of(path).get("NAXIS2", 0)) or 1


class _Mapper:
    """Follows pixels between images, searching some folders for stack records.

    Parameters
    ----------
    record_roots : `Iterable` [`str`]
        The folders searched for stack registration records.
    cache : `RecordCache`
        The records read so far.
    """

    def __init__(self, record_roots: Iterable[str], cache: RecordCache) -> None:
        self._record_roots = list(record_roots)
        self._cache = cache

    def records_holding(self, frame_path: str) -> list[StackRecord]:
        """Find the records of the stacks that a frame is in.

        Returns
        -------
        records : `list` [`StackRecord`]
            Records whose stack file exists and that list the frame. Stacks
            kept in a ``_previous`` folder are left out.
        """
        found: list[StackRecord] = []
        for root in self._record_roots:
            if not os.path.isdir(root):
                continue
            for record_path in Path(root).rglob("*" + REGISTRATION_RECORD_SUFFIX):
                if "_previous" in record_path.parts:
                    continue
                stack_path = str(record_path)[: -len(REGISTRATION_RECORD_SUFFIX)] + ".fits"
                stack = self._cache.record_of(stack_path)
                if stack and os.path.exists(stack_path) and _position_of(stack, frame_path) is not None:
                    found.append(stack)
        return found

    def record_of_stack(self, stack_path: str) -> StackRecord | None:
        """Read the record of a stack, or of the stack of a processed picture.

        Returns
        -------
        record : `StackRecord` or `None`
            The record, or `None` when the stack has none.
        """
        base, extension = os.path.splitext(stack_path)
        candidates = [stack_path]
        if base.endswith(PROCESSED_FITS_SUFFIX):
            candidates.append(base[: -len(PROCESSED_FITS_SUFFIX)] + extension)
        for candidate in candidates:
            record = self._cache.record_of(candidate)
            if record is not None:
                return record
        return None

    @staticmethod
    def sky(from_path: str, to_path: str, x: float, y: float) -> tuple[float, float] | None:
        """Follow a pixel through the sky between two plate-solved images.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            The pixel on the second image, or `None` when either image has no
            plate solution.
        """
        from_header, to_header = _header_of(from_path), _header_of(to_path)
        if not (header_is_plate_solved(from_header) and header_is_plate_solved(to_header)):
            return None
        try:
            world = WCS(from_header, naxis=2).all_pix2world([[x, y]], 1)
            pixel = WCS(to_header, naxis=2).all_world2pix(world, 1)
        except ValueError, RuntimeError:
            return None
        new_x, new_y = float(pixel[0][0]), float(pixel[0][1])
        return (new_x, new_y) if math.isfinite(new_x) and math.isfinite(new_y) else None

    def onto_a_solved_stack(self, path: str, x: float, y: float) -> tuple[str, float, float] | None:
        """Bring a pixel onto a plate-solved file: itself, or a frame's stack.

        Returns
        -------
        landing : `tuple` or `None`
            The solved file and the pixel on it, or `None` when none is found.
        """
        if header_is_plate_solved(_header_of(path)):
            return path, x, y
        height = _frame_height(path)
        for stack in self.records_holding(path):
            position = _position_of(stack, path)
            if position is None or not header_is_plate_solved(_header_of(stack.stack_path)):
                continue
            try:
                stack_x, stack_y = frame_to_stack(stack, position, x, y, height)
            except ProcessingError:
                continue
            return stack.stack_path, stack_x, stack_y
        return None

    def alignment(self, from_path: str, to_path: str, x: float, y: float) -> tuple[float, float] | None:
        """Follow a pixel through the stack record that links two images.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            The pixel on the second image, or `None` when no record links them.
        """
        to_record = self.record_of_stack(to_path)
        if to_record is not None and (position := _position_of(to_record, from_path)) is not None:
            stack_x, stack_y = frame_to_stack(to_record, position, x, y, _frame_height(from_path))
            offset_x, offset_y = _picture_offset(to_path)
            return stack_x - offset_x, stack_y - offset_y
        from_record = self.record_of_stack(from_path)
        if from_record is not None and (position := _position_of(from_record, to_path)) is not None:
            offset_x, offset_y = _picture_offset(from_path)
            return stack_to_frame(from_record, position, x + offset_x, y + offset_y, _frame_height(to_path))
        # Two frames of one stack.
        for stack in self.records_holding(from_path):
            from_position = _position_of(stack, from_path)
            to_position = _position_of(stack, to_path)
            if from_position is not None and to_position is not None:
                stack_x, stack_y = frame_to_stack(stack, from_position, x, y, _frame_height(from_path))
                return stack_to_frame(stack, to_position, stack_x, stack_y, _frame_height(to_path))
        return None

    def back_from_stack(
        self, stack_path: str, to_path: str, position: tuple[float, float]
    ) -> tuple[float, float] | None:
        """Bring a pixel of a solved stack onto the image reached through it.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            The pixel on ``to_path`` (the stack itself, or one of its frames).
        """
        if os.path.realpath(stack_path) == os.path.realpath(to_path):
            return position
        stack = self.record_of_stack(stack_path)
        frame_position = _position_of(stack, to_path) if stack is not None else None
        if stack is None or frame_position is None:
            return None
        try:
            return stack_to_frame(stack, frame_position, position[0], position[1], _frame_height(to_path))
        except ProcessingError:
            return None

    def map(self, from_path: str, x: float, y: float, to_path: str) -> PositionMapping:
        """Run the three ways in order (see the module text).

        Returns
        -------
        mapping : `PositionMapping`
            The position and how it was found.

        Notes
        -----
        A stack record that names the frame but cannot follow it raises
        `ProcessingError` from `frame_to_stack` or `stack_to_frame`.
        """
        if os.path.realpath(from_path) == os.path.realpath(to_path):
            return PositionMapping(x=x, y=y, method="same pixel", note="It is the same image.")
        sky = self.sky(from_path, to_path, x, y)
        if sky is not None:
            return PositionMapping(
                x=sky[0], y=sky[1], method="sky", note="Followed by the plate solution of both images."
            )
        aligned = self.alignment(from_path, to_path, x, y)
        if aligned is not None:
            return PositionMapping(
                x=aligned[0],
                y=aligned[1],
                method="stack alignment",
                note="Followed by the stack's registration record.",
            )
        # Different stacks: bring each image onto a plate-solved stack, then
        # go through the sky.
        start = self.onto_a_solved_stack(from_path, x, y)
        target = self.onto_a_solved_stack(to_path, 1.0, 1.0)
        if start is not None and target is not None and (start[0] != from_path or target[0] != to_path):
            through_sky = self.sky(start[0], target[0], start[1], start[2])
            if through_sky is not None:
                landed = self.back_from_stack(target[0], to_path, through_sky)
                if landed is not None:
                    return PositionMapping(
                        x=landed[0],
                        y=landed[1],
                        method="stack alignment + sky",
                        note="Followed through the stack's registration and the sky position of the stacks.",
                    )
        return PositionMapping(
            x=x,
            y=y,
            method="same pixel",
            note="No plate solution or registration links these images; this is a guess.",
        )


def map_position(
    from_path: str,
    x: float,
    y: float,
    to_path: str,
    record_roots: Iterable[str],
    cache: RecordCache | None = None,
) -> PositionMapping:
    """Find where a pixel of one image is on another image.

    Parameters
    ----------
    from_path, to_path : `str`
        The two FITS images.
    x, y : `float`
        The pixel on the first image, 1-based.
    record_roots : `Iterable` [`str`]
        The folders searched for stack registration records.
    cache : `RecordCache`, optional
        Records read by earlier calls. A new, empty one when left out.

    Returns
    -------
    mapping : `PositionMapping`
        The pixel on the second image, 1-based, and how it was found. When
        the pixel cannot be followed (a frame Siril left out of its stack, a
        spectral stack, a file that cannot be read), ``x`` and ``y`` are
        `None`, ``method`` is ``"none"`` and ``note`` says why.
    """
    mapper = _Mapper(record_roots, cache if cache is not None else RecordCache())
    try:
        return mapper.map(from_path, float(x), float(y), to_path)
    except ProcessingError as error:
        return PositionMapping(x=None, y=None, method="none", note=str(error))
    except OSError as error:
        return PositionMapping(x=None, y=None, method="none", note=f"An image could not be read: {error}")
