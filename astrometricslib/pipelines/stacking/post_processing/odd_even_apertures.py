"""Records where the odd–even difference's values sit, and its line pattern.

The odd–even difference splits a stack's frames into two halves, A and B, and
stacks each. At each empty aperture two numbers are then available:

- the sum value, taken on A + B (the two stacks added, not halved). It holds
  the random noise of both halves and whatever both halves share, including
  the fixed-pattern noise.
- the difference value, taken on A - B. What the halves share cancels, so it
  holds the random noise only.

With no fixed-pattern noise the two have the same spread. Where the sum
scatters more than the difference, the excess is fixed pattern, and its
position on the image says what kind it is: a trend with distance from the
center (a flat-field or vignetting residual), a plane (a sky gradient), a rise
near the target (faint real light), or bands along rows or columns. This module
only measures and stores the data for that test; the shape tests themselves
belong to wayfindinglib.

Two things are made here:

1. The per-aperture record (`build_aperture_records`,
   `save_odd_even_aperture_record`, `load_odd_even_aperture_record`): for each
   usable aperture its position on the stack, its distance from the image
   center, its distance from the target (when known), and its sum and
   difference values. It is a small JSON file beside the library stack
   (`odd_even_apertures_path`), like the stack's per-aperture noise sums. A
   table of about 100 rows does not fit a quality row, which holds one number,
   so the quality row holds the count and the file's path.
2. The line pattern (`measure_line_pattern`): the scatter of the row averages
   and of the column averages of the empty sky, in the difference image and in
   the sum image. Banding adds the same offset along a whole row or column; it
   cancels with the fixed pattern in the difference only if it is the same in
   both halves, so the sum keeps it and a larger scatter in the sum than in
   the difference points to banding.

The estimator and the numbers are declared in `pipelines/stacking/metrics.py`.
"""

import json
import logging
import math
import os
import warnings
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    FAINT_SOURCE_CLEARANCE_PX,
    faint_source_distance,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_SIGMA_ERROR_FACTOR,
    MAD_TO_SIGMA,
)

logger = logging.getLogger(__name__)

__all__ = [
    "APERTURE_RECORD_SUFFIX",
    "ApertureRecord",
    "LinePattern",
    "LinePatternOutcome",
    "build_aperture_records",
    "load_odd_even_aperture_record",
    "measure_line_pattern",
    "odd_even_apertures_path",
    "save_odd_even_aperture_record",
]

# The record sits beside the library stack: the stack's name without its
# extension, then this ending (the same pattern as the stack's noise sums).
APERTURE_RECORD_SUFFIX = "_odd_even_apertures.json"

# The version of the record's layout. Raise it when a field changes.
APERTURE_RECORD_VERSION = 1

# A row or column gives a mean only when at least this many of its pixels are
# usable empty sky. The mean of n independent pixels has a noise of
# sigma / sqrt(n): 10% of the pixel noise for 100. Rows crossing a bright
# nebula or stars are mostly masked and are dropped. Design estimate; no data
# set chose it.
MINIMUM_PIXELS_PER_LINE = 100

# The running mean that is taken off the line means before their scatter is
# measured spans this many lines. A gradient or a vignetting trend changes
# little over 31 lines (about 0.5% of a 6000-line image), and a window centered
# on a line follows a straight trend exactly, so the trend is removed while a
# band a few lines wide is kept. (A running median is wrong here: on a steady
# ramp it returns the line's own value and removes the line noise too.) The
# lines nearer than half a window to the image edge are not used, because their
# window is not centered. Design estimate; no data set chose it.
RUNNING_MEAN_LINES = 31

# At least this many lines must give a mean for a scatter to be reported:
# 20 lines give a 26% error on the spread (1.1664 / sqrt(20)), as the
# empty-aperture noise accepts at its minimum.
MINIMUM_LINES = 20


@dataclass(frozen=True)
class ApertureRecord:
    """One empty aperture's place and its two values from the odd–even halves.

    Parameters
    ----------
    index : `int`
        The aperture's position in the target's stored set.
    x : `float`
        The column on the stack, counted from 0.
    y : `float`
        The row on the stack, counted from 0.
    distance_from_center_px : `float`
        The distance from the center of the image.
    distance_from_target_px : `float` or `None`
        The distance from the target's position, or `None` when the target's
        position on the stack is not known.
    sum_value : `float`
        The sky-subtracted aperture sum on A + B, in the stack's units.
    difference_value : `float`
        The sky-subtracted aperture sum on A - B, in the stack's units.
    """

    index: int
    x: float
    y: float
    distance_from_center_px: float
    distance_from_target_px: float | None
    sum_value: float
    difference_value: float


def odd_even_apertures_path(stack_path: str) -> str:
    """Name the file that keeps a stack's per-aperture odd–even record.

    Parameters
    ----------
    stack_path : `str`
        The library stack.

    Returns
    -------
    path : `str`
        The stack's path without its extension, then `APERTURE_RECORD_SUFFIX`.
    """
    return os.path.splitext(stack_path)[0] + APERTURE_RECORD_SUFFIX


def build_aperture_records(
    pixel_positions: tuple[tuple[float, float], ...],
    sum_values: tuple[float, ...],
    difference_values: tuple[float, ...],
    image_shape: tuple[int, int],
    target_pixel: tuple[float, float] | None,
) -> list[ApertureRecord]:
    """Join each aperture's position with its sum and difference values.

    Parameters
    ----------
    pixel_positions : `tuple`
        The (column, row) of every stored aperture, in the stored order.
    sum_values, difference_values : `tuple` [`float`, ...]
        The sky-subtracted sums on A + B and on A - B, in the same order. NaN
        marks an aperture that was not usable.
    image_shape : `tuple` [`int`, `int`]
        The stack's (rows, columns), for the image center.
    target_pixel : `tuple` [`float`, `float`] or `None`
        The target's (column, row) on the stack, if known.

    Returns
    -------
    records : `list` [`ApertureRecord`]
        One record for each aperture usable in both images, in the stored
        order.
    """
    center_column = (image_shape[1] - 1) / 2.0
    center_row = (image_shape[0] - 1) / 2.0
    records = []
    for index, ((column, row), sum_value, difference_value) in enumerate(
        zip(pixel_positions, sum_values, difference_values, strict=False)
    ):
        if not all(math.isfinite(value) for value in (column, row, sum_value, difference_value)):
            continue
        from_target = (
            None
            if target_pixel is None
            else float(math.hypot(column - target_pixel[0], row - target_pixel[1]))
        )
        records.append(
            ApertureRecord(
                index=index,
                x=float(column),
                y=float(row),
                distance_from_center_px=float(math.hypot(column - center_column, row - center_row)),
                distance_from_target_px=from_target,
                sum_value=float(sum_value),
                difference_value=float(difference_value),
            )
        )
    return records


def save_odd_even_aperture_record(
    stack_path: str, records: list[ApertureRecord], aperture_identity: str, note: str
) -> str | None:
    """Write the per-aperture record beside the library stack.

    A failure to write is logged and ignored: the record is a by-product of the
    odd–even difference, and the difference's rows must not be lost with it.

    Parameters
    ----------
    stack_path : `str`
        The library stack.
    records : `list` [`ApertureRecord`]
        The apertures usable in both images.
    aperture_identity : `str`
        The identity of the aperture set (`ApertureSet.identity`), so a reader
        can tell whether the record belongs to the stack's current set.
    note : `str`
        What the values are, kept in the file for a reader without the code.

    Returns
    -------
    path : `str` or `None`
        The file written, or `None` when it could not be written.
    """
    path = odd_even_apertures_path(stack_path)
    data = {
        "version": APERTURE_RECORD_VERSION,
        "stack": os.path.basename(stack_path),
        "aperture_identity": aperture_identity,
        "note": note,
        "apertures": [
            {
                "index": record.index,
                "x": record.x,
                "y": record.y,
                "distance_from_center_px": record.distance_from_center_px,
                "distance_from_target_px": record.distance_from_target_px,
                "sum": record.sum_value,
                "difference": record.difference_value,
            }
            for record in records
        ],
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError as error:
        logger.warning("The odd–even aperture record of %s could not be saved: %s", stack_path, error)
        return None
    return path


def load_odd_even_aperture_record(path: str) -> dict | None:
    """Read a per-aperture record written by `save_odd_even_aperture_record`.

    Parameters
    ----------
    path : `str`
        The record file (the path stored in the quality row's reason, or
        `odd_even_apertures_path` of the stack).

    Returns
    -------
    record : `dict` or `None`
        The file's content: ``version``, ``stack``, ``aperture_identity``,
        ``note`` and ``apertures`` (a list of dicts with ``index``, ``x``,
        ``y``, ``distance_from_center_px``, ``distance_from_target_px``,
        ``sum`` and ``difference``). `None` when the file is missing, cannot
        be read or is of another version.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError, ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != APERTURE_RECORD_VERSION:
        return None
    return data


@dataclass(frozen=True)
class LinePattern:
    """The scatter of the row and column averages of the two images.

    Parameters
    ----------
    difference_row, difference_column : `float`
        The scatter of the row (column) averages of the empty sky in A - B, in
        the stack's units.
    sum_row, sum_column : `float`
        The same in A + B.
    row_count, column_count : `int`
        How many rows (columns) gave a mean.
    """

    difference_row: float
    difference_column: float
    sum_row: float
    sum_column: float
    row_count: int
    column_count: int

    def error(self, value: float, count: int) -> float:
        """Give the 1-sigma error of one scatter.

        Parameters
        ----------
        value : `float`
            The scatter.
        count : `int`
            The number of lines behind it.

        Returns
        -------
        error : `float`
            1.1664 x value / sqrt(count).
        """
        return MAD_SIGMA_ERROR_FACTOR * value / math.sqrt(count)


@dataclass(frozen=True)
class LinePatternOutcome:
    """The line pattern, or the reason there is none.

    Parameters
    ----------
    result : `LinePattern` or `None`
        The numbers, or `None` when too few lines gave a mean.
    reason : `str`
        Why there is no result. Empty when there is one.
    """

    result: LinePattern | None
    reason: str = ""


def _line_scatter(image: np.ndarray, usable: np.ndarray, axis: int) -> tuple[float, int] | None:
    """Give the scatter of the line averages of the usable pixels of an image.

    Parameters
    ----------
    image : `numpy.ndarray`
        The 2-D image.
    usable : `numpy.ndarray`
        `True` where a pixel may be averaged.
    axis : `int`
        1 averages along each row (one mean per row), 0 along each column.

    Returns
    -------
    scatter : `tuple` [`float`, `int`] or `None`
        The scatter of the means after a running mean of
        `RUNNING_MEAN_LINES` lines is taken off, and the number of lines
        that gave a mean; `None` when fewer than `MINIMUM_LINES` did.
    """
    counts = usable.sum(axis=axis)
    totals = np.where(usable, image, 0.0).sum(axis=axis, dtype=np.float64)
    means = np.full(counts.shape, np.nan)
    enough = counts >= MINIMUM_PIXELS_PER_LINE
    means[enough] = totals[enough] / counts[enough]
    if int(np.count_nonzero(enough)) < MINIMUM_LINES:
        return None
    half = RUNNING_MEAN_LINES // 2
    if means.size <= RUNNING_MEAN_LINES:
        return None
    windows = sliding_window_view(means, RUNNING_MEAN_LINES)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a window of masked lines has no mean
        local = np.nanmean(windows, axis=1)
    residual = means[half : means.size - half] - local
    residual = residual[np.isfinite(residual)]
    if residual.size < MINIMUM_LINES:
        return None
    scatter = MAD_TO_SIGMA * float(np.median(np.abs(residual - np.median(residual))))
    return scatter, int(residual.size)


def measure_line_pattern(odd_plane: np.ndarray, even_plane: np.ndarray) -> LinePatternOutcome:
    """Measure the row and column average scatter of the difference and sum.

    The empty sky is every pixel that holds data in both half stacks and lies
    at least `FAINT_SOURCE_CLEARANCE_PX` from any pixel of the faint-source
    mask (which flags stars too). The mask is made on the sum image, which has
    the depth of the whole stack. This is one more pass of the mask over the
    stack.

    Parameters
    ----------
    odd_plane, even_plane : `numpy.ndarray`
        The two half stacks as 2-D images on one pixel grid.

    Returns
    -------
    outcome : `LinePatternOutcome`
        The four scatters, or the reason there are none.
    """
    if odd_plane.shape != even_plane.shape:
        return LinePatternOutcome(None, "the two half stacks are on different pixel grids")
    holds_data = np.isfinite(odd_plane) & np.isfinite(even_plane) & (odd_plane != 0) & (even_plane != 0)
    total = odd_plane + even_plane
    total[~holds_data] = 0.0
    usable = holds_data & (faint_source_distance(total) > FAINT_SOURCE_CLEARANCE_PX)
    difference = odd_plane - even_plane
    difference[~holds_data] = 0.0
    found: dict[str, tuple[float, int]] = {}
    for name, image, axis in (
        ("difference_row", difference, 1),
        ("difference_column", difference, 0),
        ("sum_row", total, 1),
        ("sum_column", total, 0),
    ):
        scatter = _line_scatter(image, usable, axis)
        if scatter is None:
            return LinePatternOutcome(
                None,
                f"fewer than {MINIMUM_LINES} image lines hold {MINIMUM_PIXELS_PER_LINE} pixels of empty sky",
            )
        found[name] = scatter
    return LinePatternOutcome(
        LinePattern(
            difference_row=found["difference_row"][0],
            difference_column=found["difference_column"][0],
            sum_row=found["sum_row"][0],
            sum_column=found["sum_column"][0],
            row_count=found["difference_row"][1],
            column_count=found["difference_column"][1],
        )
    )
