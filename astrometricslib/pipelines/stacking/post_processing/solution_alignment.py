"""Puts a picture of one stack onto the pixel grid of another stack.

The color picture of a monochrome stack (see `color_from_other_stack`) needs
the color stack's pixels to lie on the monochrome stack's pixels. The two
stacks come from different cameras or optics, so they differ in pixel size,
turn, framing and lens distortion. This module moves one onto the other with
plate solutions (WCS, the map from a pixel to a sky position):

1. *Solve both stacks again with Siril.* The plate solutions stored in the
   stack headers are not used: on the M 27 pair from the Nikon D5300 at 300 mm
   and the ZWO ASI533MM they disagreed by 6 pixels. Siril matches the stars of
   each stack to the Gaia catalog and fits the lens distortion (SIP terms), so
   each stack is placed on the sky on its own, and star-finder differences
   between a wide and a narrow star cancel (the shared Siril driver does the
   solve, see `solve_stack`).
2. *Find where each picture lies in its stack.* The finished picture of a
   stack is a crop of the stack (see `stack_crop`), so the solution of the
   stack serves the picture once its position inside the stack is known
   (`placement_in_stack`).
3. *Resample.* Every pixel of the monochrome grid is turned into a sky
   position with the monochrome solution, and that position into a pixel of
   the color stack with the color solution. The color stack is read there
   (`SkyAlignment.resample`). The monochrome picture is never resampled or
   moved, so its stars keep their width.

There is no other way to align the two: when either stack cannot be solved,
the color picture is not made, and the caller reports the reason.

The result says how well the two grids can agree: the combined fit residual of
the two solutions, in monochrome pixels, and the share of the monochrome grid
that the color stack covers.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.ndimage import map_coordinates

from astrometricslib._internal.drivers.fits_access import read_header
from astrometricslib._internal.drivers.interfaces.plate_solve_driver import read_fit_statistics
from astrometricslib._internal.drivers.siril.astrometry.plate_solve import solve_image
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.stacking.processing.image_registration import resample_at_positions

logger = logging.getLogger(__name__)

__all__ = [
    "GridPlacement",
    "SkyAlignment",
    "SolvedStack",
    "combined_residual_pixels",
    "placement_in_stack",
    "solve_stack",
]

# The spacing, in pixels, of the grid on which the sky map between the two
# grids is worked out; the map between the nodes is a straight-line blend.
# The distortion of a lens changes over hundreds of pixels, so over 16 pixels
# the map is straight to far less than a thousandth of a pixel, and the
# expensive inverse of the SIP terms is worked out at one point in 256.
_MAP_STEP_PIXELS = 16

# How far, in pixels, the offset of a picture inside its stack may be from a
# whole number. The picture is cut from the stack by whole pixels, and its
# header is the stack's header with the reference pixel moved, so the two
# solutions differ by a whole offset to rounding of the header numbers.
_OFFSET_TOLERANCE_PIXELS = 0.05

# The inverse of the SIP terms is found by iteration. It stops when a step
# moves the position by less than this many pixels.
_INVERSE_TOLERANCE_PIXELS = 1.0e-4
_INVERSE_MAXIMUM_ITERATIONS = 50

# A position that cannot be traced into the color stack is set here, far
# outside it, so it reads as not covered.
_OUTSIDE_PIXELS = -1.0e6


@dataclass(frozen=True)
class SolvedStack:
    """A stack that Siril has plate-solved.

    Attributes
    ----------
    wcs : `astropy.wcs.WCS`
        The stack's new sky map, with the SIP distortion terms, for the
        stack's own pixels.
    matched_star_count : `int` or `None`
        How many stars Siril matched to the catalog.
    fit_residual_rms_arcsec : `float` or `None`
        How far a matched star sits from its catalog position, root mean
        square, in arcseconds.
    pixel_scale_arcsec : `float`
        The stack's mean pixel scale, in arcseconds per pixel.
    """

    wcs: WCS
    matched_star_count: int | None
    fit_residual_rms_arcsec: float | None
    pixel_scale_arcsec: float

    @property
    def fit_residual_pixels(self) -> float | None:
        """The fit residual in the stack's own pixels, or `None` if unknown."""
        if self.fit_residual_rms_arcsec is None:
            return None
        return self.fit_residual_rms_arcsec / self.pixel_scale_arcsec

    def record(self) -> dict[str, float | int | None]:
        """Give the solve as plain numbers for a JSON record.

        Returns
        -------
        record : `dict`
            The matched star count, the residual and the pixel scale.
        """
        return {
            "matched_stars": self.matched_star_count,
            "fit_residual_arcsec": None
            if self.fit_residual_rms_arcsec is None
            else round(self.fit_residual_rms_arcsec, 3),
            "pixel_scale_arcsec": round(self.pixel_scale_arcsec, 4),
        }


@dataclass(frozen=True)
class GridPlacement:
    """A grid of pixels and where it lies in a solved stack.

    Attributes
    ----------
    wcs : `astropy.wcs.WCS`
        The sky map of the solved stack.
    row_offset, column_offset : `int`
        The stack row and column of this grid's first pixel. Zero for the
        stack itself, more for a picture cut from it.
    """

    wcs: WCS
    row_offset: int = 0
    column_offset: int = 0


def solve_stack(stack_path: str, siril_executable: str) -> tuple[SolvedStack | None, str]:
    """Plate-solve a stack with Siril, ignoring the solution it already has.

    The stack's own stored solution only gives the start position. Siril
    solves the stack again (the Siril driver's `solve_image`, in a short
    Siril process of its own) and the new solution is the one used.

    Parameters
    ----------
    stack_path : `str`
        The stack's FITS file. It is only read.
    siril_executable : `str`
        The command that starts Siril.

    Returns
    -------
    solved : `SolvedStack` or `None`
        The new solution, or `None` when Siril could not solve the stack.
    reason : `str`
        Why not, or an empty string.
    """
    try:
        header = read_header(stack_path, first_hdu_only=True)
    except (OSError, ValueError) as error:
        return None, f"the stack {stack_path} could not be read ({error})"
    hints: dict[str, float] = {}
    stored = celestial_wcs(header)
    columns, rows = header.get("NAXIS1"), header.get("NAXIS2")
    if stored is not None and isinstance(columns, int) and isinstance(rows, int):
        center_ra, center_dec = stored.all_pix2world([(columns - 1) / 2.0], [(rows - 1) / 2.0], 0)
        hints = {"center_ra": float(center_ra[0]), "center_dec": float(center_dec[0])}
    solved_header = solve_image(stack_path, siril_executable, hints)
    if solved_header is None:
        return None, f"Siril could not solve the stack {stack_path}"
    wcs = celestial_wcs(solved_header)
    if wcs is None:
        return None, f"Siril's solution of the stack {stack_path} has no sky map"
    residual, star_count = read_fit_statistics(solved_header)
    scale = float(np.mean(proj_plane_pixel_scales(wcs))) * 3600.0
    return SolvedStack(wcs, star_count, residual, scale), ""


def placement_in_stack(
    stack_header: fits.Header, picture_header: fits.Header, picture_shape: tuple[int, int]
) -> tuple[int, int] | None:
    """Find where a picture, cut from a stack, lies in that stack.

    The picture's own stored plate solution and the stack's stored plate
    solution describe the same sky, so the sky position of the picture's
    first pixel, read back through the stack's stored solution, is the
    picture's place in the stack. Both of its far corners are tested, so a
    picture that was also scaled is refused.

    Parameters
    ----------
    stack_header : `astropy.io.fits.Header`
        The stack's header, with its stored plate solution.
    picture_header : `astropy.io.fits.Header`
        The picture's header, with its stored plate solution.
    picture_shape : `tuple` [`int`, `int`]
        The picture's rows and columns.

    Returns
    -------
    offset : `tuple` [`int`, `int`] or `None`
        The stack row and column of the picture's first pixel, or `None` when
        the picture is not a whole-pixel cut of the stack.
    """
    stack_wcs, picture_wcs = celestial_wcs(stack_header), celestial_wcs(picture_header)
    if stack_wcs is None or picture_wcs is None:
        return None
    rows, columns = picture_shape
    corners_x = np.array([0.0, columns - 1.0])
    corners_y = np.array([0.0, rows - 1.0])
    x_in_stack, y_in_stack = stack_wcs.all_world2pix(*picture_wcs.all_pix2world(corners_x, corners_y, 0), 0)
    offsets_x, offsets_y = x_in_stack - corners_x, y_in_stack - corners_y
    if not (np.all(np.isfinite(offsets_x)) and np.all(np.isfinite(offsets_y))):
        return None
    column_offset, row_offset = float(np.round(offsets_x[0])), float(np.round(offsets_y[0]))
    off_by = max(
        float(np.max(np.abs(offsets_x - column_offset))), float(np.max(np.abs(offsets_y - row_offset)))
    )
    if off_by > _OFFSET_TOLERANCE_PIXELS:
        return None
    return int(row_offset), int(column_offset)


class SkyAlignment:
    """The move from a reference grid to a moving grid, through the sky.

    Parameters
    ----------
    reference : `GridPlacement`
        The grid that stays where it is, with its solved stack's sky map.
    moving : `GridPlacement`
        The grid that is read onto the reference grid.
    reference_shape : `tuple` [`int`, `int`]
        The reference grid's rows and columns.
    """

    def __init__(
        self, reference: GridPlacement, moving: GridPlacement, reference_shape: tuple[int, int]
    ) -> None:
        """Work out where each node of a coarse grid of the reference lands."""
        self.reference_shape = reference_shape
        rows, columns = reference_shape
        self._row_nodes = np.linspace(0.0, rows - 1.0, max(2, math.ceil((rows - 1) / _MAP_STEP_PIXELS) + 1))
        self._column_nodes = np.linspace(
            0.0, columns - 1.0, max(2, math.ceil((columns - 1) / _MAP_STEP_PIXELS) + 1)
        )
        node_columns, node_rows = np.meshgrid(self._column_nodes, self._row_nodes)
        right_ascension, declination = reference.wcs.all_pix2world(
            node_columns + reference.column_offset, node_rows + reference.row_offset, 0
        )
        moving_columns, moving_rows = moving.wcs.all_world2pix(
            right_ascension,
            declination,
            0,
            tolerance=_INVERSE_TOLERANCE_PIXELS,
            maxiter=_INVERSE_MAXIMUM_ITERATIONS,
            quiet=True,
        )
        # A node the inverse could not place is sent far outside the grid.
        moving_columns = np.where(
            np.isfinite(moving_columns), moving_columns - moving.column_offset, _OUTSIDE_PIXELS
        )
        moving_rows = np.where(np.isfinite(moving_rows), moving_rows - moving.row_offset, _OUTSIDE_PIXELS)
        self._node_rows = moving_rows
        self._node_columns = moving_columns

    def positions(self, rows: np.ndarray, columns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Say where in the moving grid each reference pixel reads.

        Parameters
        ----------
        rows, columns : `numpy.ndarray`
            Reference pixel rows and columns, two arrays of one shape.

        Returns
        -------
        source_rows, source_columns : `numpy.ndarray`
            The moving grid's rows and columns to read at those pixels.
        """
        row_index = np.interp(rows, self._row_nodes, np.arange(len(self._row_nodes)))
        column_index = np.interp(columns, self._column_nodes, np.arange(len(self._column_nodes)))
        source_rows = map_coordinates(self._node_rows, [row_index, column_index], order=1, mode="nearest")
        source_columns = map_coordinates(
            self._node_columns, [row_index, column_index], order=1, mode="nearest"
        )
        return source_rows, source_columns

    def resample(self, image: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
        """Read an image of the moving grid onto the reference grid.

        Parameters
        ----------
        image : `numpy.ndarray`
            The moving grid's image, 2-D or color (channels first).
        order : `int`
            The spline order: 3 (cubic) keeps stars sharp; 1 for a map of
            counts that must not overshoot.

        Returns
        -------
        moved : `numpy.ndarray`
            The image on the reference grid, zero where it has no data.
        covered : `numpy.ndarray`
            A 2-D mask, `True` where the moved image holds real data.
        """
        return resample_at_positions(image, self.positions, order, self.reference_shape)


def combined_residual_pixels(reference: SolvedStack, moving: SolvedStack) -> float | None:
    """Say how well two solutions can agree, in the reference's pixels.

    The two fit residuals are independent, so they add as the root of the sum
    of squares.

    Parameters
    ----------
    reference, moving : `SolvedStack`
        The two solved stacks.

    Returns
    -------
    residual_pixels : `float` or `None`
        The combined residual, or `None` when Siril gave no residual for
        either solution.
    """
    if reference.fit_residual_rms_arcsec is None or moving.fit_residual_rms_arcsec is None:
        return None
    return (
        math.hypot(reference.fit_residual_rms_arcsec, moving.fit_residual_rms_arcsec)
        / reference.pixel_scale_arcsec
    )
