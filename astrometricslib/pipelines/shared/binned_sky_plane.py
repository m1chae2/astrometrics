"""Block medians of an image and a plane fitted to them with clipping.

Two stacking steps read a smooth sky surface from an image: the choice
between a fitted plane and GraXpert in the preview (`sky_plane`, which also
measures GraXpert's model in `background_gradient`), and the matching of one
exposure group's sky to another's (`group_sky_matching`). Both work the same
way, so the work lives here once.

1. The image is cut into a grid of blocks, and each block is replaced by the
   median of its pixels (`block_medians`). A median ignores the few pixels of
   each star, and a block's median is far quieter than one pixel. A block
   with too few trusted pixels is left out.
2. A plane (a flat, tilted sheet) is fitted to the block medians by least
   squares (`least_squares_fit`), and the blocks far from it are dropped,
   over and over until the same blocks stay (`fit_clipped_plane`). The
   spread that says how far is "far" comes from the blocks themselves.

The clipping side is a choice. ``"both"`` drops blocks far above or far
below the plane, and measures the spread as the scaled median absolute
deviation (`robust_sigma`). Use it for a difference of two images, where a
star or a nebula in either image can push a block either way. ``"above"``
drops only blocks far above the plane, and measures the spread from the
blocks below the middle only. Use it for one image's sky, where only a
bright object lifts a block, so it cannot be allowed to widen the spread.

This module measures a sky surface, not a sky level. The library's level and
noise of the empty sky come from `image_scaling.measure_sky_background`.
"""

from typing import Literal

import numpy as np

from astrometricslib.utilities.robust_statistics import MAD_TO_SIGMA, robust_sigma

__all__ = [
    "ClipSide",
    "block_medians",
    "even_block_edges",
    "fit_clipped_plane",
    "least_squares_fit",
    "whole_block_edges",
]

ClipSide = Literal["both", "above"]


def even_block_edges(length: int, block_count: int) -> np.ndarray:
    """Spread a fixed number of block edges evenly over one image axis.

    No pixel is left out when the length is not a multiple of the count, so
    the blocks differ in size by at most one pixel.

    Parameters
    ----------
    length : `int`
        The image's size along the axis, in pixels.
    block_count : `int`
        How many blocks the axis is cut into.

    Returns
    -------
    edges : `numpy.ndarray`
        ``block_count + 1`` integer edges, from 0 to ``length``.
    """
    return np.linspace(0, length, block_count + 1).astype(int)


def whole_block_edges(length: int, block_pixels: int) -> np.ndarray:
    """Cut one image axis into blocks of a fixed size.

    The pixels past the last whole block are left out.

    Parameters
    ----------
    length : `int`
        The image's size along the axis, in pixels.
    block_pixels : `int`
        The side of a block, in pixels.

    Returns
    -------
    edges : `numpy.ndarray`
        The integer edges, from 0 to the end of the last whole block. Only
        ``[0]`` when not even one block fits.
    """
    return np.arange(0, (length // block_pixels) * block_pixels + 1, block_pixels)


def block_medians(
    image: np.ndarray,
    row_edges: np.ndarray,
    column_edges: np.ndarray,
    valid: np.ndarray | None = None,
    minimum_valid_fraction: float = 0.0,
) -> np.ndarray:
    """Replace each block of an image by the median of its trusted pixels.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D image, as floating-point numbers.
    row_edges, column_edges : `numpy.ndarray`
        The block edges along the rows and the columns (see
        `even_block_edges` and `whole_block_edges`).
    valid : `numpy.ndarray`, optional
        A 2-D boolean mask of the image's shape, `True` where a pixel can be
        trusted. By default every pixel can. A pixel that is not finite is
        never used.
    minimum_valid_fraction : `float`, optional
        The share of a block's pixels that must be trusted (in ``valid``) for
        the block to be measured. By default one finite pixel is enough.

    Returns
    -------
    medians : `numpy.ndarray`
        A map with one value per block, of shape ``(len(row_edges) - 1,
        len(column_edges) - 1)``. A block that is not measured holds NaN.
    """
    medians = np.full((len(row_edges) - 1, len(column_edges) - 1), np.nan)
    for row in range(medians.shape[0]):
        rows = slice(row_edges[row], row_edges[row + 1])
        for column in range(medians.shape[1]):
            columns = slice(column_edges[column], column_edges[column + 1])
            cell = image[rows, columns]
            trusted = np.ones(cell.shape, dtype=bool) if valid is None else valid[rows, columns]
            if cell.size == 0 or trusted.mean() < minimum_valid_fraction:
                continue
            usable = cell[trusted & np.isfinite(cell)]
            if usable.size:
                medians[row, column] = float(np.median(usable))
    return medians


def least_squares_fit(columns: list[np.ndarray], values: np.ndarray) -> tuple[np.ndarray, float]:
    """Fit a sum of terms to some values by least squares.

    Parameters
    ----------
    columns : `list` [`numpy.ndarray`]
        The terms, each evaluated at every fitted point.
    values : `numpy.ndarray`
        The values at the same points.

    Returns
    -------
    coefficients : `numpy.ndarray`
        One number for each term.
    rms : `float`
        The root-mean-square of what the fit leaves behind, in the units of
        the values.
    """
    design = np.stack(columns, axis=1)
    coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
    residual = values - design @ coefficients
    return coefficients, float(np.sqrt(np.mean(residual**2)))


def fit_clipped_plane(
    first: np.ndarray,
    second: np.ndarray,
    values: np.ndarray,
    *,
    side: ClipSide,
    clip_sigmas: float,
    rounds: int,
    minimum_kept: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit a plane to points and drop the points far from it, over and over.

    Each round measures the points' distance from the plane (the residual),
    takes the median residual of the kept points as the middle, drops the
    points too far from the middle, and fits again. It stops when the kept
    points stop changing, after ``rounds`` rounds, or when a round would
    keep fewer than ``minimum_kept`` points (the last set is then kept).

    Parameters
    ----------
    first, second : `numpy.ndarray`
        The two coordinates of each point, 1-D.
    values : `numpy.ndarray`
        The value at each point, 1-D.
    side : ``"both"`` or ``"above"``
        ``"both"`` drops points more than ``clip_sigmas`` spreads from the
        middle on either side; the spread is the robust spread of the kept
        residuals (`robust_sigma`). ``"above"`` drops only points that many
        spreads above the middle; the spread is `MAD_TO_SIGMA` times the
        median distance of the kept points below the middle, so the points a
        bright object lifts cannot widen it.
    clip_sigmas : `float`
        How many spreads from the middle a point may lie.
    rounds : `int`
        The most clipping rounds.
    minimum_kept : `float`
        The fewest points a round may keep.

    Returns
    -------
    plane : `numpy.ndarray`
        The three numbers ``(level, first_slope, second_slope)`` of the plane
        ``level + first_slope * first + second_slope * second``, fitted to
        the kept points.
    kept : `numpy.ndarray`
        A boolean mask, `True` for each point the final fit used.
    """
    ones = np.ones(len(values))
    kept = np.ones(len(values), dtype=bool)
    plane, _rms = least_squares_fit([ones[kept], first[kept], second[kept]], values[kept])
    for _ in range(rounds):
        residual = values - (plane[0] + plane[1] * first + plane[2] * second)
        middle = float(np.median(residual[kept]))
        if side == "above":
            below = middle - residual[kept & (residual < middle)]
            if below.size == 0:
                break
            spread = MAD_TO_SIGMA * float(np.median(below))
            new_kept = residual < middle + clip_sigmas * spread
        else:
            spread = robust_sigma(residual[kept])
            if not spread > 0:
                break
            new_kept = np.abs(residual - middle) <= clip_sigmas * spread
        if new_kept.sum() < minimum_kept or np.array_equal(new_kept, kept):
            break
        kept = new_kept
        plane, _rms = least_squares_fit([ones[kept], first[kept], second[kept]], values[kept])
    return plane, kept
