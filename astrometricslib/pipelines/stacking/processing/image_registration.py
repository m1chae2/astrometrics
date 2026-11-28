"""Resample an image onto another grid, wherever a map says to read it.

Two steps move an image onto a new grid: the exposure-group alignment
(`GroupPositionMap.resample`, for the rejection maps) and the color step
(`solution_alignment`, which maps one stack's pixels to another's through two
plate solutions). Both give `resample_at_positions` a function from the new
grid's pixel to the place to read in the old image. The function is cut into
blocks of rows so a 24 megapixel frame needs no gigabytes of sample positions.

Positions are written (row, column) as arrays, the order NumPy uses.
"""

from collections.abc import Callable

import numpy as np
from scipy.ndimage import map_coordinates, spline_filter

__all__ = ["COVERED_PIXEL_THRESHOLD", "resample_at_positions"]

# An output pixel counts as holding real data when, resampling an image of
# ones onto it (linearly), the result is at least this. A pixel that is only
# partly inside the moving image's edge falls below it.
COVERED_PIXEL_THRESHOLD = 0.999

# An image is resampled this many rows at a time, so the sample positions of a
# 24 megapixel frame do not need gigabytes at once.
_WARP_ROWS_PER_BLOCK = 256


def resample_at_positions(
    image: np.ndarray,
    positions: Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]],
    order: int,
    output_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Fill an output grid by reading the moving image where a map points.

    Each output pixel is traced back to a place in the moving image by
    `positions`, and the image is sampled there, a block of rows at a time.
    The group position maps and the color step use it.

    Parameters
    ----------
    image : `numpy.ndarray`
        The moving image, 2-D or colour (channels first).
    positions : `Callable`
        Takes the output pixels' rows and columns (two arrays of one shape)
        and gives the rows and columns of the moving image to read there.
    order : `int`
        The spline order of the resampling: 3 (cubic) keeps stars sharp; use 1
        for a map of counts that must not overshoot.
    output_shape : `tuple` [`int`, `int`]
        The (rows, columns) of the output grid.

    Returns
    -------
    moved : `numpy.ndarray`
        The moved image (`float32`), zero where the map reaches outside the
        moving image.
    covered : `numpy.ndarray`
        A 2-D boolean mask on the output grid, `True` where `moved` holds real
        data from inside the moving image.
    """
    array = np.asarray(image, dtype=np.float32)
    planes = array[np.newaxis] if array.ndim == 2 else array
    input_shape = planes.shape[-2:]
    out_shape = tuple(output_shape)
    filtered = [
        spline_filter(plane.astype(np.float64), order=order, mode="constant") if order > 1 else plane
        for plane in planes
    ]
    moved = np.zeros((len(planes), *out_shape), dtype=np.float32)
    covered = np.zeros(out_shape, dtype=bool)
    margin = 1.0 - COVERED_PIXEL_THRESHOLD
    columns = np.arange(out_shape[1], dtype=np.float64)
    for first in range(0, out_shape[0], _WARP_ROWS_PER_BLOCK):
        rows = np.arange(first, min(first + _WARP_ROWS_PER_BLOCK, out_shape[0]), dtype=np.float64)
        grid_columns, grid_rows = np.meshgrid(columns, rows)
        source_rows, source_columns = positions(grid_rows, grid_columns)
        block = slice(first, first + len(rows))
        for channel, plane in enumerate(filtered):
            moved[channel, block] = map_coordinates(
                plane,
                [source_rows, source_columns],
                order=order,
                mode="constant",
                cval=0.0,
                prefilter=False,
            )
        covered[block] = (
            (source_rows >= margin)
            & (source_rows <= input_shape[0] - 1 - margin)
            & (source_columns >= margin)
            & (source_columns <= input_shape[1] - 1 - margin)
        )
    # Interpolation near the edge reads outside the image as zero; keep zero
    # wherever the output pixel has no real data behind it.
    moved[:, ~covered] = 0.0
    return (moved[0] if array.ndim == 2 else moved), covered
