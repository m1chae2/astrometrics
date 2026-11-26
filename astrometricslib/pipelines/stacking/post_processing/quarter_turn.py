"""Turns a finished stack by a quarter turn so that north is near the top.

Siril lines every frame up with one reference frame, so the finished stack
has the sky orientation of that frame. When the reference frame was taken
after a meridian flip, the stack is upside down: north is at the bottom of
the picture. A person looking at the stack, or comparing it with a chart,
expects north near the top.

The stack is turned by a multiple of 90 degrees, never by another angle. A
quarter turn only moves each pixel to a new place. No pixel value is mixed
with its neighbors, so no resampling blur or ringing can enter the image, and
the turn can be undone exactly. The best of the four turns (0, 90, 180 and 270
degrees) is the one that leaves the top of the picture pointing closest to
north. Turning by 180 degrees is the usual case, and a turn of 90 or 270
degrees is rare (a camera mounted at a right angle).

The turn needs the stack's plate solution, which says where north is. It moves
the stack's sky map with the pixels: the reference pixel, the matrix and the
distortion terms. The distortion terms (SIP) are turned exactly, because a
quarter turn only swaps and negates the coordinates they use. Nothing is
fitted again.

A turn is done once. The stack's header gets the keyword `QUARTER_TURN_KEYWORD`
with the degrees turned (0 when no turn was needed), and a stack that has the
keyword is left alone.

The measurements of the stacking step (noise, saturation, trim, registration)
all work on the stack as Siril registered it. `read_stack_plane` therefore
gives them the stack with the turn taken back, and
`RegistrationRecord.quarter_turn_degrees` says how far the saved file was
turned, so a pixel of the saved file can be moved back with
`to_registration_grid`.
"""

import logging
import math
import os
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, Sip

from astrometricslib._internal.drivers.fits_access import (
    FITS_READ_ERRORS,
    read_data,
    read_header,
    update_first_header,
    write_image,
)
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.shared.session_identification import (
    header_is_plate_solved,
    put_wcs_in_header,
)
from astrometricslib.utilities.stack_preview_path import processed_fits_path_for

logger = logging.getLogger(__name__)

__all__ = [
    "QUARTER_TURN_KEYWORD",
    "QuarterTurnResult",
    "apply_quarter_turn",
    "choose_quarter_turn",
    "read_quarter_turn",
    "registration_grid_header",
    "registration_grid_wcs",
    "to_registration_grid",
    "turn_image",
    "turn_wcs",
    "undo_quarter_turn",
    "up_angle_degrees",
]

# The header card that holds the degrees a stack was turned.
QUARTER_TURN_KEYWORD = "QTURN"

# The rejection map is written beside the stack under this suffix.
_REJECTION_MAP_SUFFIX = "_RejMap.fits"

# The four turns, in the order that decides a tie.
_TURNS_DEGREES = (0, 90, 180, 270)


@dataclass(frozen=True)
class QuarterTurnResult:
    """What the quarter-turn step did to a stack.

    Attributes
    ----------
    degrees : `int`
        The turn applied: 0, 90, 180 or 270 (counterclockwise on the screen
        with the first row at the top). 0 means the stack was left as it was.
    up_angle_before : `float`
        Where the top of the picture pointed before, in degrees from north
        toward east (0 up to 360).
    up_angle_after : `float`
        The same angle after the turn.
    reason : `str`
        A plain sentence that says what was done and why.
    """

    degrees: int
    up_angle_before: float
    up_angle_after: float
    reason: str


def read_quarter_turn(header: fits.Header) -> int | None:
    """Read how far a stack was turned from its header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The first header of the stack.

    Returns
    -------
    degrees : `int` or `None`
        0, 90, 180 or 270, or `None` when the step has not run on this stack.
    """
    value = header.get(QUARTER_TURN_KEYWORD)
    if value is None:
        return None
    try:
        return int(value) % 360
    except TypeError, ValueError:
        return None


def turn_image(data: np.ndarray, degrees: int) -> np.ndarray:
    """Turn the last two axes of an image counterclockwise by quarter turns.

    Parameters
    ----------
    data : `numpy.ndarray`
        An image of rows by columns, or channels by rows by columns.
    degrees : `int`
        0, 90, 180 or 270.

    Returns
    -------
    turned : `numpy.ndarray`
        The image turned, as a new contiguous array. No value changes.
    """
    return np.ascontiguousarray(np.rot90(data, k=(degrees // 90) % 4, axes=(-2, -1)))


def undo_quarter_turn(data: np.ndarray, degrees: int) -> np.ndarray:
    """Take back a turn made by `turn_image`.

    Parameters
    ----------
    data : `numpy.ndarray`
        A turned image.
    degrees : `int`
        The turn that was applied.

    Returns
    -------
    original : `numpy.ndarray`
        The image as it was before the turn.
    """
    return turn_image(data, -degrees % 360)


def to_registration_grid(
    row: float, column: float, degrees: int, turned_height: int, turned_width: int
) -> tuple[float, float]:
    """Move a pixel of a turned stack back to the grid before the turn.

    Parameters
    ----------
    row, column : `float`
        The pixel on the saved (turned) stack, counted from 0.
    degrees : `int`
        The turn that was applied to the saved stack.
    turned_height, turned_width : `int`
        The size of the saved (turned) stack.

    Returns
    -------
    row, column : `float`
        The same pixel on the stack as Siril registered it.
    """
    for _ in range((-degrees % 360) // 90):
        # One counterclockwise turn sends (row, column) to
        # (width - 1 - column, row).
        row, column, turned_height, turned_width = (
            turned_width - 1 - column,
            row,
            turned_width,
            turned_height,
        )
    return row, column


def _turn_sip_once(sip: Sip | None, new_crpix: np.ndarray) -> Sip | None:
    """Turn a distortion polynomial by one counterclockwise quarter turn.

    A quarter turn sends the offsets from the reference pixel (u, v) to
    (v, -u). The first-axis polynomial then becomes the second-axis one with
    its arguments exchanged, and the other way round, with a sign change, so
    the result is exact.

    Parameters
    ----------
    sip : `astropy.wcs.Sip` or `None`
        The polynomial, or `None`.
    new_crpix : `numpy.ndarray`
        The reference pixel after the turn, counted from 1.

    Returns
    -------
    turned : `astropy.wcs.Sip` or `None`
        The turned polynomial.
    """
    if sip is None:
        return None

    def square(array: np.ndarray | None, size: int) -> np.ndarray | None:
        """Pad a coefficient table with zeros to size by size.

        Parameters
        ----------
        array : `numpy.ndarray` or `None`
            The table.
        size : `int`
            The side length wanted.

        Returns
        -------
        padded : `numpy.ndarray` or `None`
            The padded table, or `None` when there was none.
        """
        if array is None:
            return None
        padded = np.zeros((size, size))
        padded[: array.shape[0], : array.shape[1]] = array
        return padded

    def turned_pair(first: np.ndarray | None, second: np.ndarray | None) -> tuple:
        """Give the turned pair of tables.

        Parameters
        ----------
        first, second : `numpy.ndarray` or `None`
            The tables of the first and second axis.

        Returns
        -------
        pair : `tuple`
            The new first-axis and second-axis tables.
        """
        if first is None or second is None:
            return None, None
        signs = np.array([(-1.0) ** power for power in range(first.shape[0])])[:, None]
        return (second * signs).T, -(first * signs).T

    size = max(
        table.shape[0]
        for table in (sip.a, sip.b, sip.ap, sip.bp)
        if table is not None and getattr(table, "size", 0)
    )
    a_table, b_table = turned_pair(square(sip.a, size), square(sip.b, size))
    ap_table, bp_table = turned_pair(square(sip.ap, size), square(sip.bp, size))
    return Sip(a_table, b_table, ap_table, bp_table, new_crpix)


def _turn_wcs_once(wcs: WCS, width: int) -> WCS:
    """Turn a sky map by one counterclockwise quarter turn of its image.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The sky map of the image before the turn.
    width : `int`
        The width of the image before the turn, in pixels.

    Returns
    -------
    turned : `astropy.wcs.WCS`
        The sky map of the image after the turn.
    """
    turned = wcs.deepcopy()
    # The pixel (x, y), counted from 0, goes to (y, width - 1 - x).
    old_x, old_y = wcs.wcs.crpix[0] - 1.0, wcs.wcs.crpix[1] - 1.0
    turned.wcs.crpix = [old_y + 1.0, width - 1.0 - old_x + 1.0]
    rotation_transposed = np.array([[0.0, -1.0], [1.0, 0.0]])
    if wcs.wcs.has_cd():
        turned.wcs.cd = np.asarray(wcs.wcs.cd) @ rotation_transposed
    else:
        turned.wcs.pc = np.asarray(wcs.wcs.get_pc()) @ rotation_transposed
    turned.sip = _turn_sip_once(wcs.sip, np.array(turned.wcs.crpix))
    return turned


def turn_wcs(wcs: WCS, degrees: int, width: int, height: int) -> WCS:
    """Turn a sky map with its image by a multiple of 90 degrees.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The celestial sky map of the image before the turn.
    degrees : `int`
        0, 90, 180 or 270, counterclockwise on the screen with the first row
        at the top (the direction of `numpy.rot90`).
    width, height : `int`
        The size of the image before the turn.

    Returns
    -------
    turned : `astropy.wcs.WCS`
        The sky map of the turned image. A pixel of the turned image has the
        sky position that the pixel it came from had.
    """
    turned = wcs
    for _ in range((degrees // 90) % 4):
        turned = _turn_wcs_once(turned, width)
        width, height = height, width
    return turned


def registration_grid_wcs(wcs: WCS, degrees: int, turned_width: int, turned_height: int) -> WCS:
    """Give the sky map of a turned stack as it was before the turn.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The celestial sky map of the saved (turned) stack.
    degrees : `int`
        The turn that was applied to the saved stack.
    turned_width, turned_height : `int`
        The size of the saved (turned) stack.

    Returns
    -------
    original : `astropy.wcs.WCS`
        The sky map on the stack as Siril registered it.
    """
    return turn_wcs(wcs, -degrees % 360, turned_width, turned_height)


def registration_grid_header(header: fits.Header) -> fits.Header:
    """Give a stack's header with its sky map moved back before the turn.

    A header of a stack that was not turned, or that has no sky map, is given
    back as it is.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header of the saved stack.

    Returns
    -------
    registered : `astropy.io.fits.Header`
        A copy whose sky map matches `read_stack_plane`'s image.
    """
    degrees = read_quarter_turn(header) or 0
    if not degrees:
        return header
    wcs = celestial_wcs(header)
    if wcs is None:
        return header
    try:
        original = registration_grid_wcs(wcs, degrees, int(header["NAXIS1"]), int(header["NAXIS2"]))
    except ValueError, KeyError:
        return header
    registered = header.copy()
    put_wcs_in_header(registered, original, mark_plate_solved=header_is_plate_solved(header))
    return registered


def up_angle_degrees(wcs: WCS, *, top_down: bool) -> float:
    """Say where the top of the picture points on the sky.

    This is the viewer's rule (`measurePlateGeometry` in
    ``ui/common/fitsViewer/plateGeometry.ts``): the first row is at the top
    for a file stored ``ROWORDER = TOP-DOWN`` and at the bottom otherwise.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The celestial sky map.
    top_down : `bool`
        Whether the file's first row is drawn at the top.

    Returns
    -------
    angle : `float`
        Degrees from north toward east, 0 up to 360. 0 means north is at the
        top.
    """
    matrix = np.asarray(wcs.pixel_scale_matrix, dtype=float)
    row_step = -1.0 if top_down else 1.0
    up_east = matrix[0, 1] * row_step
    up_north = matrix[1, 1] * row_step
    return math.degrees(math.atan2(up_east, up_north)) % 360.0


def _distance_from_north(angle: float) -> float:
    """Give how far an angle is from 0, in degrees, between 0 and 180.

    Parameters
    ----------
    angle : `float`
        An angle in degrees.

    Returns
    -------
    distance : `float`
        The distance from 0 going the short way round.
    """
    wrapped = angle % 360.0
    return min(wrapped, 360.0 - wrapped)


def choose_quarter_turn(wcs: WCS, width: int, height: int, *, top_down: bool) -> int:
    """Pick the quarter turn that puts north closest to the top.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The celestial sky map of the stack.
    width, height : `int`
        The size of the stack.
    top_down : `bool`
        Whether the file's first row is drawn at the top.

    Returns
    -------
    degrees : `int`
        0, 90, 180 or 270. A tie goes to the smaller turn.
    """
    best = 0
    best_distance = math.inf
    for degrees in _TURNS_DEGREES:
        angle = up_angle_degrees(turn_wcs(wcs, degrees, width, height), top_down=top_down)
        distance = _distance_from_north(angle)
        if distance < best_distance - 1e-9:
            best, best_distance = degrees, distance
    return best


def _turn_one_file(path: str, degrees: int, wcs: WCS | None, *, keep_solved_mark: bool) -> None:
    """Turn the pixels and the sky map of one FITS file in place.

    The file is written beside the original and then moved over it, so a
    failure leaves the original.

    Parameters
    ----------
    path : `str`
        The file.
    degrees : `int`
        The turn to apply.
    wcs : `astropy.wcs.WCS` or `None`
        The turned sky map to write, or `None` to leave the sky keywords out.
    keep_solved_mark : `bool`
        Whether to write the plate-solved card with the sky map.
    """
    data = np.asarray(read_data(path))
    header = read_header(path)
    if wcs is not None:
        put_wcs_in_header(header, wcs, mark_plate_solved=keep_solved_mark)
    header[QUARTER_TURN_KEYWORD] = (degrees, "degrees the image was turned for north-up")
    temporary = path + ".turn.fits"
    write_image(temporary, turn_image(data, degrees), header)
    os.replace(temporary, path)


def _same_size(first: fits.Header, second: fits.Header) -> bool:
    """Say whether two headers describe images of the same size.

    Parameters
    ----------
    first, second : `astropy.io.fits.Header`
        The headers.

    Returns
    -------
    same : `bool`
        `True` when the width and the height match.
    """
    return (first.get("NAXIS1"), first.get("NAXIS2")) == (second.get("NAXIS1"), second.get("NAXIS2"))


def _turn_companion(path: str, stack_header: fits.Header, degrees: int, stack_wcs: WCS) -> None:
    """Turn a file that shares the stack's pixel grid.

    A file of another size is left alone, with a warning. The sky map is
    turned along with the pixels when the file had one.

    Parameters
    ----------
    path : `str`
        The companion file.
    stack_header : `astropy.io.fits.Header`
        The stack's header before the turn.
    degrees : `int`
        The turn to apply.
    stack_wcs : `astropy.wcs.WCS`
        The stack's celestial sky map before the turn.
    """
    try:
        header = read_header(path)
        if read_quarter_turn(header) is not None:
            return
        if not _same_size(header, stack_header):
            logger.warning("%s is not the size of its stack, so it was not turned.", path)
            return
        has_map = header_is_plate_solved(header)
        wcs = (
            turn_wcs(stack_wcs, degrees, stack_header["NAXIS1"], stack_header["NAXIS2"]) if has_map else None
        )
        _turn_one_file(path, degrees, wcs, keep_solved_mark=True)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Could not turn %s: %s.", path, error)


def apply_quarter_turn(stack_path: str) -> QuarterTurnResult | None:
    """Turn a stack and its companions so north is near the top.

    The stack needs a plate solution. It is left alone when it has none, when
    the step has already run on it (`QUARTER_TURN_KEYWORD` is in its header),
    or when it cannot be read. When no turn is needed, the header only gets
    the keyword with 0 degrees, so the step is not repeated.

    Parameters
    ----------
    stack_path : `str`
        The stack's FITS file.

    Returns
    -------
    result : `QuarterTurnResult` or `None`
        What was done, or `None` when the stack was left alone. A result with
        0 degrees means the stack was checked and needs no turn.
    """
    try:
        header = read_header(stack_path)
        if read_quarter_turn(header) is not None or not header_is_plate_solved(header):
            return None
        data = read_data(stack_path)
        if data is None:
            return None
        height, width = np.asarray(data).shape[-2:]
        wcs = celestial_wcs(header)
        if wcs is None:
            return None
        top_down = str(header.get("ROWORDER", "")).upper() == "TOP-DOWN"
        before = up_angle_degrees(wcs, top_down=top_down)
        degrees = choose_quarter_turn(wcs, width, height, top_down=top_down)
        if degrees == 0:
            update_first_header(stack_path, lambda card_header: _set_turn_card(card_header, 0))
            return QuarterTurnResult(
                0, before, before, "North is already near the top, so the stack was not turned."
            )
        turned_wcs = turn_wcs(wcs, degrees, width, height)
        _turn_one_file(stack_path, degrees, turned_wcs, keep_solved_mark=True)
        stem, _ = os.path.splitext(stack_path)
        for companion in (stem + _REJECTION_MAP_SUFFIX, processed_fits_path_for(stack_path)):
            if os.path.isfile(companion):
                _turn_companion(companion, header, degrees, wcs)
    except (*FITS_READ_ERRORS, ValueError, KeyError) as error:
        logger.warning("Could not turn the stack %s so that north is near the top: %s.", stack_path, error)
        return None
    after = up_angle_degrees(turned_wcs, top_down=top_down)
    reason = (
        f"Turned {degrees} degrees so north is near the top (the top pointed {before:.0f} degrees from "
        f"north before, {after:.0f} degrees after)."
    )
    logger.info("%s %s", os.path.basename(stack_path), reason)
    return QuarterTurnResult(degrees, before, after, reason)


def _set_turn_card(header: fits.Header, degrees: int) -> bool:
    """Write the turn card into a header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header to change.
    degrees : `int`
        The turn to record.

    Returns
    -------
    changed : `bool`
        Always `True`.
    """
    header[QUARTER_TURN_KEYWORD] = (degrees, "degrees the image was turned for north-up")
    return True
