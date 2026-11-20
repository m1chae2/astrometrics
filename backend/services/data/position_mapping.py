"""Follow one pixel of an image to the same spot on another image.

The Star profile panel pins a measured star. When the viewer shows another
image, the panel asks where that star is on the new one. This module finds
the answer in three ways, in order:

1. Sky position. Both images have a plate solution (``PLTSOLVD`` true or the
   SIP terms of a solved image). The pixel goes to the sky and back to a pixel.
2. Stack alignment. A stack has a record of how Siril moved each frame onto
   the stack's reference grid (``<stack name>_registration_frames.json``).
   That gives frame to stack, stack to frame and frame to frame, and it can be
   chained with the sky position when the two images belong to different
   stacks.
3. The same pixel, as a guess, when nothing else applies.

All positions are 1-based FITS pixels (the center of the first pixel is 1, 1).
Siril counts pixels from 0, and its y axis runs up from the last row of the
array (``y = height - 1 - row``), as `astrometricslib` uses it (checked on the
M 57 night of 2026-10-03). The transform saved for a frame moves the frame onto
the reference, the stack was then trimmed ``crop_offset`` columns and rows from
its left and top edges, so a stack pixel is the reference pixel minus the
trim.
"""

import json
import logging
import math
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.drivers import is_path_inside

logger = logging.getLogger(__name__)

__all__ = [
    "REGISTRATION_RECORD_SUFFIX",
    "CannotMapError",
    "Mapped",
    "PositionMapper",
    "frame_to_stack",
    "stack_to_frame",
]

# What follows a stack's name (without .fits) in the name of its record.
# (The suffix of a stack's registration record.)
REGISTRATION_RECORD_SUFFIX = "_registration_frames.json"

# The record layout this reader understands.
REGISTRATION_RECORD_VERSION = 1

# A transform is a 3 x 3 matrix written as nine numbers.
HOMOGRAPHY_LENGTH = 9

# The picture the app shows for a stack is saved next to it with this ending.
PROCESSED_PICTURE_ENDING = "_processed"


class CannotMapError(Exception):
    """A position cannot be followed; the message says why."""


@dataclass(frozen=True)
class Mapped:
    """A position on the new image and how it was found.

    Attributes
    ----------
    x, y : `float`
        The position, 1-based.
    method : `str`
        "sky", "stack alignment", "stack alignment + sky" or "same pixel".
    note : `str`
        A sentence about the result.
    """

    x: float
    y: float
    method: str
    note: str


@dataclass(frozen=True)
class _Record:
    """One stack's registration record, as read from its file."""

    stack_path: str
    light_paths: tuple[str, ...]
    homographies: tuple[tuple[float, ...], ...]
    included: tuple[bool, ...]
    crop_offset: tuple[int, int]
    is_spectral: bool


def _stack_path_of_record(record_path: str) -> str:
    """Name the stack file a registration record belongs to.

    Parameters
    ----------
    record_path : `str`
        The record's path.

    Returns
    -------
    stack_path : `str`
        The path without the record suffix, plus ``.fits``.
    """
    return record_path[: -len(REGISTRATION_RECORD_SUFFIX)] + ".fits"


def _read_record(record_path: str) -> _Record | None:
    """Read one registration record file.

    Parameters
    ----------
    record_path : `str`
        The record's path.

    Returns
    -------
    record : `_Record` or `None`
        `None` when the file is missing, unreadable, of another version or its
        lists differ in length.
    """
    try:
        with open(record_path, encoding="utf-8") as record_file:
            document = json.load(record_file)
        if document["version"] != REGISTRATION_RECORD_VERSION:
            return None
        paths = tuple(str(value) for value in document["light_paths"])
        homographies = tuple(tuple(float(number) for number in values) for values in document["homographies"])
        included = tuple(bool(flag) for flag in document["included"])
        crop = (int(document["crop_offset"][0]), int(document["crop_offset"][1]))
        is_spectral = bool(document.get("is_spectral", False))
    except OSError, ValueError, KeyError, IndexError, TypeError:
        return None
    if not (len(paths) == len(homographies) == len(included)) or any(
        len(h) != HOMOGRAPHY_LENGTH for h in homographies
    ):
        return None
    return _Record(_stack_path_of_record(record_path), paths, homographies, included, crop, is_spectral)


def _record_path_for_stack(stack_path: str) -> list[str]:
    """List the places a stack's record may be.

    The record is beside the stack, or beside the stack that a ``_processed``
    picture belongs to.

    Parameters
    ----------
    stack_path : `str`
        The stack, or its ``_processed`` picture.

    Returns
    -------
    record_paths : `list` [`str`]
        Candidate record paths, most direct first.
    """
    base = os.path.splitext(stack_path)[0]
    candidates = [base + REGISTRATION_RECORD_SUFFIX]
    if base.endswith(PROCESSED_PICTURE_ENDING):
        candidates.append(base[: -len(PROCESSED_PICTURE_ENDING)] + REGISTRATION_RECORD_SUFFIX)
    return candidates


def _position_of(record: _Record, frame_path: str) -> int | None:
    """Find a frame's place in a record.

    Parameters
    ----------
    record : `_Record`
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
    for index, path in enumerate(record.light_paths):
        if os.path.realpath(path) == real:
            return index
    name = os.path.basename(frame_path)
    matches = [index for index, path in enumerate(record.light_paths) if os.path.basename(path) == name]
    return matches[0] if len(matches) == 1 else None


def _matrix(record: _Record, position: int) -> np.ndarray:
    """Return one frame's 3 x 3 transform, or refuse when it cannot be used.

    Parameters
    ----------
    record : `_Record`
        The stack's record.
    position : `int`
        The frame's index.

    Returns
    -------
    matrix : `numpy.ndarray`
        The transform that moves the frame onto the reference grid.

    Raises
    ------
    CannotMapError
        If the stack is spectral, or Siril left the frame out of the stack.
    """
    if record.is_spectral:
        raise CannotMapError(
            "A spectral stack is lined up by shift on the zero-order star, so its frames cannot be followed."
        )
    if not record.included[position]:
        raise CannotMapError("Siril could not register that frame, so it is not in the stack.")
    return np.asarray(record.homographies[position], dtype=float).reshape(3, 3)


def frame_to_stack(
    record: _Record, position: int, x: float, y: float, frame_height: int
) -> tuple[float, float]:
    """Move a pixel of a frame onto its stack.

    Parameters
    ----------
    record : `_Record`
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
    CannotMapError
        If the frame cannot be followed.
    """
    matrix = _matrix(record, position)
    moved = matrix @ np.array([x - 1.0, frame_height - 1 - (y - 1.0), 1.0])
    if math.isclose(moved[2], 0.0, abs_tol=1e-12):
        raise CannotMapError("The frame's transform cannot place that pixel.")
    reference_column = moved[0] / moved[2]
    reference_row = frame_height - 1 - moved[1] / moved[2]
    return (
        float(reference_column - record.crop_offset[0]) + 1.0,
        float(reference_row - record.crop_offset[1]) + 1.0,
    )


def stack_to_frame(
    record: _Record, position: int, x: float, y: float, frame_height: int
) -> tuple[float, float]:
    """Move a pixel of a stack back onto one of its frames.

    Parameters
    ----------
    record : `_Record`
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
    CannotMapError
        If the frame cannot be followed.
    """
    matrix = _matrix(record, position)
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as error:
        raise CannotMapError("The frame's transform cannot be turned around.") from error
    reference_column = (x - 1.0) + record.crop_offset[0]
    reference_row = (y - 1.0) + record.crop_offset[1]
    moved = inverse @ np.array([reference_column, frame_height - 1 - reference_row, 1.0])
    if math.isclose(moved[2], 0.0, abs_tol=1e-12):
        raise CannotMapError("The frame's transform cannot place that pixel.")
    return float(moved[0] / moved[2]) + 1.0, float(frame_height - 1 - moved[1] / moved[2]) + 1.0


def _header_of(path: str) -> fits.Header:
    """Read the first header of a FITS file.

    The file is not mapped into memory.

    Parameters
    ----------
    path : `str`
        The file.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A copy of the header.
    """
    with fits.open(path, memmap=False) as hdul:
        return hdul[0].header.copy()


def _picture_offset(path: str) -> tuple[int, int]:
    """Say where a cropped processed picture starts on its stack.

    The picture is cropped to the well-covered area, and its header holds the
    columns and rows cut from the stack's left and top (``CROPX0`` and
    ``CROPY0``). A stack, or a picture with no crop, has none.

    Parameters
    ----------
    path : `str`
        The image.

    Returns
    -------
    offset : `tuple` [`int`, `int`]
        Columns and rows to subtract from a stack pixel to get the picture
        pixel; (0, 0) for an image with no crop.
    """
    header = _header_of(path)
    return int(header.get("CROPX0", 0)), int(header.get("CROPY0", 0))


def _is_plate_solved(header: fits.Header) -> bool:
    """Say whether a header holds a real plate solution.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header.

    Returns
    -------
    solved : `bool`
        `True` when ``PLTSOLVD`` is true or the header has SIP terms, and the
        sky axes are there. Pointing
        keywords from the capture program alone do not count.
    """
    flag = header.get("PLTSOLVD", False)
    flagged = flag is True or str(flag).strip().upper() in {"T", "TRUE", "1"}
    has_sip = "A_ORDER" in header
    has_axes = all(key in header for key in ("CTYPE1", "CRVAL1", "CRPIX1"))
    return (flagged or has_sip) and has_axes


class PositionMapper:
    """Maps a pixel of one image to another image.

    Both images must be inside the folders the app may read.

    Parameters
    ----------
    allowed_roots : `Callable` [[], `Iterable` [`str`]]
        Gives the real paths of the folders images may be read from.
    record_roots : `Callable` [[], `Iterable` [`str`]]
        Gives the folders searched for stack registration records.
    """

    def __init__(
        self, allowed_roots: Callable[[], Iterable[str]], record_roots: Callable[[], Iterable[str]]
    ) -> None:
        self._allowed_roots = allowed_roots
        self._record_roots = record_roots
        # Records by path, with the file's time stamp, so a record is read
        # again only when it changes.
        self._cache: dict[str, tuple[float, _Record | None]] = {}

    def _inside(self, path: str) -> bool:
        """Say whether a real path is inside an allowed folder.

        Returns
        -------
        inside : `bool`
            `True` when it is.
        """
        return any(is_path_inside(root, path) for root in self._allowed_roots())

    def _record_at(self, record_path: str) -> _Record | None:
        """Read a record through the cache.

        Returns
        -------
        record : `_Record` or `None`
            The record, or `None` when there is no usable one.
        """
        try:
            stamp = os.path.getmtime(record_path)
        except OSError:
            return None
        cached = self._cache.get(record_path)
        if cached is None or cached[0] != stamp:
            cached = (stamp, _read_record(record_path))
            self._cache[record_path] = cached
        return cached[1]

    def _records_holding(self, frame_path: str) -> list[_Record]:
        """Find the records of the stacks that a frame is in.

        Parameters
        ----------
        frame_path : `str`
            The frame.

        Returns
        -------
        records : `list` [`_Record`]
            Records whose stack file exists and that list the frame.
        """
        found: list[_Record] = []
        for root in self._record_roots():
            if not os.path.isdir(root):
                continue
            for record_path in Path(root).rglob("*" + REGISTRATION_RECORD_SUFFIX):
                if "_previous" in record_path.parts:
                    continue
                record = self._record_at(str(record_path))
                if (
                    record
                    and os.path.exists(record.stack_path)
                    and _position_of(record, frame_path) is not None
                ):
                    found.append(record)
        return found

    def _record_of_stack(self, stack_path: str) -> _Record | None:
        """Read the record of a stack file, or of its processed picture.

        Returns
        -------
        record : `_Record` or `None`
            The record, or `None` when the stack has none.
        """
        for candidate in _record_path_for_stack(stack_path):
            record = self._record_at(candidate)
            if record is not None:
                return record
        return None

    def _frame_height(self, frame_path: str) -> int:
        """Read a frame's number of rows from its header.

        Returns
        -------
        height : `int`
            The number of rows (1 when the header does not say).
        """
        return int(_header_of(frame_path).get("NAXIS2", 0)) or 1

    def _sky(self, from_path: str, to_path: str, x: float, y: float) -> tuple[float, float] | None:
        """Follow a pixel through the sky between two plate-solved images.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            The pixel on the second image, or `None` when either image has no
            plate solution.
        """
        from_header, to_header = _header_of(from_path), _header_of(to_path)
        if not (_is_plate_solved(from_header) and _is_plate_solved(to_header)):
            return None
        try:
            world = WCS(from_header, naxis=2).all_pix2world([[x, y]], 1)
            pixel = WCS(to_header, naxis=2).all_world2pix(world, 1)
        except ValueError, RuntimeError:
            return None
        new_x, new_y = float(pixel[0][0]), float(pixel[0][1])
        return (new_x, new_y) if math.isfinite(new_x) and math.isfinite(new_y) else None

    def _onto_a_solved_stack(self, path: str, x: float, y: float) -> tuple[str, float, float] | None:
        """Bring a pixel onto a plate-solved file.

        That is the file itself, or the stack of a frame.

        Returns
        -------
        landing : `tuple` or `None`
            The solved file and the pixel on it, or `None` when none is found.
        """
        if _is_plate_solved(_header_of(path)):
            return path, x, y
        height = self._frame_height(path)
        for record in self._records_holding(path):
            position = _position_of(record, path)
            if position is None or not _is_plate_solved(_header_of(record.stack_path)):
                continue
            try:
                stack_x, stack_y = frame_to_stack(record, position, x, y, height)
            except CannotMapError:
                continue
            return record.stack_path, stack_x, stack_y
        return None

    def _alignment(self, from_path: str, to_path: str, x: float, y: float) -> tuple[float, float] | None:
        """Follow a pixel through one stack's registration.

        This works when a record links the two images.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            The pixel on the second image, or `None` when no record links them.
        """
        to_record = self._record_of_stack(to_path)
        if to_record is not None and (position := _position_of(to_record, from_path)) is not None:
            stack_x, stack_y = frame_to_stack(to_record, position, x, y, self._frame_height(from_path))
            offset_x, offset_y = _picture_offset(to_path)
            return stack_x - offset_x, stack_y - offset_y
        from_record = self._record_of_stack(from_path)
        if from_record is not None and (position := _position_of(from_record, to_path)) is not None:
            offset_x, offset_y = _picture_offset(from_path)
            return stack_to_frame(
                from_record, position, x + offset_x, y + offset_y, self._frame_height(to_path)
            )
        # Two frames of one stack.
        for record in self._records_holding(from_path):
            from_position = _position_of(record, from_path)
            to_position = _position_of(record, to_path)
            if from_position is not None and to_position is not None:
                stack_x, stack_y = frame_to_stack(record, from_position, x, y, self._frame_height(from_path))
                return stack_to_frame(record, to_position, stack_x, stack_y, self._frame_height(to_path))
        return None

    def map_position(self, from_path: str, x: float, y: float, to_path: str) -> Mapped:
        """Find where a pixel of one image is on another image.

        Parameters
        ----------
        from_path, to_path : `str`
            The two FITS images, as real paths inside the allowed folders.
        x, y : `float`
            The pixel on the first image, 1-based.

        Returns
        -------
        mapped : `Mapped`
            The pixel on the second image and how it was found.

        Raises
        ------
        CannotMapError
            If a file is outside the allowed folders or cannot be read, or the
            pixel falls off the second image.
        """
        for path in (from_path, to_path):
            if not self._inside(path):
                raise CannotMapError("The image must be inside the library folders.")
        try:
            return self._map(from_path, x, y, to_path)
        except OSError as error:
            raise CannotMapError(f"An image could not be read: {error}") from error

    def _map(self, from_path: str, x: float, y: float, to_path: str) -> Mapped:
        """Run the three ways in order (see the module text).

        Returns
        -------
        mapped : `Mapped`
            The position and how it was found.
        """
        if os.path.realpath(from_path) == os.path.realpath(to_path):
            return Mapped(x, y, "same pixel", "It is the same image.")
        sky = self._sky(from_path, to_path, x, y)
        if sky is not None:
            return Mapped(sky[0], sky[1], "sky", "Followed by the plate solution of both images.")
        aligned = self._alignment(from_path, to_path, x, y)
        if aligned is not None:
            return Mapped(
                aligned[0], aligned[1], "stack alignment", "Followed by the stack's registration record."
            )
        # Different stacks: bring each image onto a plate-solved stack, then go
        # through the sky.
        start = self._onto_a_solved_stack(from_path, x, y)
        target = self._onto_a_solved_stack(to_path, 1.0, 1.0)
        if start is not None and target is not None and (start[0] != from_path or target[0] != to_path):
            through_sky = self._sky(start[0], target[0], start[1], start[2])
            if through_sky is not None:
                landed = self._back_from_stack(target[0], to_path, through_sky)
                if landed is not None:
                    return Mapped(
                        landed[0],
                        landed[1],
                        "stack alignment + sky",
                        "Followed through the stack's registration and the sky position of the stacks.",
                    )
        return Mapped(
            x, y, "same pixel", "No plate solution or registration links these images; this is a guess."
        )

    def _back_from_stack(
        self, stack_path: str, to_path: str, position: tuple[float, float]
    ) -> tuple[float, float] | None:
        """Bring a pixel of a solved stack onto the image reached through it.

        Returns
        -------
        position : `tuple` [`float`, `float`] or `None`
            The pixel on `to_path` (the stack itself, or one of its frames).
        """
        if os.path.realpath(stack_path) == os.path.realpath(to_path):
            return position
        record = self._record_of_stack(stack_path)
        frame_position = _position_of(record, to_path) if record is not None else None
        if record is None or frame_position is None:
            return None
        try:
            return stack_to_frame(
                record, frame_position, position[0], position[1], self._frame_height(to_path)
            )
        except CannotMapError:
            return None
