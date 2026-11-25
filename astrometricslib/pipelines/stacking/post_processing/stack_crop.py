"""Trims the thinly covered edges off a finished stack.

Frames never line up exactly. Dithering moves each frame a few pixels, and a
meridian flip or a night's re-centering can move it by hundreds. After
registration, every frame covers a shifted rectangle of the sky, so the edges
of a stack can be covered by fewer frames than the middle. An edge pixel then
has fewer samples to average, so it is noisier, and a ragged border appears
where frames drop out. On the 82-frame M 27 stack, which combines three nights,
the first 100 columns and the first 400 rows had 18% to 33% more noise than the
interior. On the 123-frame NGC 7331 stack, 12 frames after a meridian flip
point 710 pixels away, and the right side is covered by only 111 of 123
frames.

The rule is a count of coverage. From each frame's registration transform, the
module counts how many included frames cover each pixel. When the stack was
built from exposure groups, each frame's transform reaches only its own group's
grid, so the count first moves the pixel back by the inverse of the group's
alignment with the reference group (shift, rotation and scale). The rim of a
resampled group, where the spline mixes in the empty border, does not count as
covered. Without this step, a group moved by hundreds of pixels would look as
if it covered the whole stack. It makes two crops
from that count, each the largest box of the original shape inside the region
covered by at least a set fraction of the frames, placed as near the centre of
the original image as the covered area allows (the telescope points the target
at the centre, so this keeps it there). Both keep the proportions of the
original, so a square stack stays square.

* The linear stack is trimmed only where fewer than half the frames cover it
  (`STACK_MINIMUM_COVERAGE_FRACTION`). Registration leaves part-empty borders
  there. Above it the stack keeps all its real data, so photometry,
  astrometry and star-shape measurements can use the stars near the edges.
* The processed picture, the one people look at, is cropped further, to the
  area covered by at least 85% of the frames
  (`PROCESSED_MINIMUM_COVERAGE_FRACTION`). Its box lies inside the stack's.
  The crop is made before GraXpert and Cosmic Clarity run, so their models
  never see the noisy edge. `find_processed_crop_box` finds the box and
  `crop_file` applies it.

Every crop gives its reason: which sides were cut, by how many pixels, how few
frames covered them, and the numbers of the rule. The reason is logged and
returned with the box, so a crop is never silent.

When the registration transforms are not available, the module falls back to
measuring noise. It then trims each side back to where the noise falls to
within a set factor of the interior's, and it does nothing to a stack whose
edges are clean.

It changes only the size of the files. Pixel values and the sky-coordinate
solution are kept, with the solution's reference pixel moved to match.
"""

import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from scipy.ndimage import median_filter

from astrometricslib._internal.drivers.fits_access import collapse_to_2d, read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import MAD_TO_SIGMA

logger = logging.getLogger(__name__)

__all__ = [
    "CROP_HEIGHT_KEYWORD",
    "CROP_WIDTH_KEYWORD",
    "CROP_X0_KEYWORD",
    "CROP_Y0_KEYWORD",
    "CropBox",
    "StackCrops",
    "crop_file",
    "crop_stack_edges",
    "find_coverage_crop_box",
    "find_edge_crop_box",
    "frame_coverage_counts",
    "shift_header_for_crop",
    "turn_box",
]

# The rejection map is written beside the stack under this suffix.
_REJECTION_MAP_SUFFIX = "_RejMap.fits"

# The edge profile is measured in strips of this many pixels. A strip is wide
# enough to hold thousands of sky pixels for a stable noise estimate, and
# narrow enough to place the edge within a few dozen pixels.
STRIP_PIXELS = TunedParameter(
    name="stacking.strip_pixels",
    default=25,
    unit="pixels",
    description="Width of the strips in which the edge profile is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-23",
)

# Strips whose noise exceeds the interior's by more than this factor are
# trimmed. M 27's edges measured 1.18 to 1.33; the edges of the Bubble Nebula,
# M 81 and M 13 stacks measured at most 1.06. 1.15 sits between them. For
# frames of equal weight it corresponds to an edge covered by about three
# quarters of the frames (noise goes as one over the square root of the
# coverage).
MAXIMUM_EDGE_NOISE_RATIO = TunedParameter(
    name="stacking.maximum_edge_noise_ratio",
    default=1.15,
    unit="ratio",
    description="Ratio of strip noise to interior noise above which a strip is trimmed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-23",
)

# The linear stack is trimmed where fewer than this fraction of the included
# frames cover it. Below it the registration leaves part-empty borders, where
# a pixel has only a few samples. Above it the stack keeps all its real data,
# so photometry, astrometry and the star-shape measurements can use stars near
# the edges. Validation STK-23 showed the noise of an edge follows one over
# the square root of its coverage, so an edge kept at 50% is at most 1.41
# times as noisy as the interior, and the picture people look at is cropped
# more tightly (`PROCESSED_MINIMUM_COVERAGE_FRACTION`).
STACK_MINIMUM_COVERAGE_FRACTION = TunedParameter(
    name="stacking.stack_minimum_coverage_fraction",
    default=0.5,
    unit="fraction",
    description="Smallest share of the frames that must cover a pixel for the linear stack to keep it.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-23",
)

# The processed picture is cropped to the area covered by at least this
# fraction of the included frames. Measured on 15 stacks (validation STK-23),
# the mean edge noise was 1.001, 1.014, 1.036 and 1.070 times the interior's
# for coverage of at least 0.99, 0.95 to 0.99, 0.90 to 0.95 and 0.80 to 0.90.
# At 0.85 the edge noise is at most 1/sqrt(0.85) = 1.085 times the interior's,
# and at most 5% of the area is lost on 11 of the 15 stacks. A stricter 0.95
# cut lost 42% of the area on NGC 7331 and M 13 and 33% on NGC 2244.
PROCESSED_MINIMUM_COVERAGE_FRACTION = TunedParameter(
    name="stacking.processed_minimum_coverage_fraction",
    default=0.85,
    unit="fraction",
    description="Smallest share of the frames that must cover a pixel for the processed picture to keep it.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-23",
)

# Coverage is counted on every this-many-th pixel in each direction, which is
# fine enough to place an edge to a few pixels (the edges are straight lines)
# and 16 times cheaper than counting every pixel.
_COVERAGE_GRID_STEP = TunedParameter(
    name="stacking.coverage_grid_step",
    default=4,
    unit="pixels",
    description="Spacing of the pixels on which frame coverage is counted.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-23",
)

# When exposure groups are lined up with each other, a group's stack is
# resampled with a cubic spline, and the spline mixes the empty border into
# every pixel within two pixels of the group's edge. Those pixels are not
# counted as covered by the group's frames. Three pixels is the spline's two
# pixel reach plus one for the spacing of the counted pixels.
_ALIGNED_EDGE_MARGIN_PX = TunedParameter(
    name="stacking.aligned_edge_margin_px",
    default=3,
    unit="pixels",
    description="Width of the resampled border of a lined-up exposure group that does not count as covered.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D84",
)

# The profile along one axis is measured on the middle of the other axis, so a
# corner that is noisy in both directions does not count twice.
_CROSS_AXIS_START = TunedParameter(
    name="stacking.cross_axis_start",
    default=0.2,
    unit="fraction",
    description="Start of the middle part of the cross axis on which an edge profile is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D58",
)
_CROSS_AXIS_END = TunedParameter(
    name="stacking.cross_axis_end",
    default=0.8,
    unit="fraction",
    description="End of the middle part of the cross axis on which an edge profile is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D58",
)

# No side is trimmed by more than this share of its length. A bigger crop
# means something other than edge coverage is wrong with the stack, and the
# stack is left whole for a person to look at.
MAXIMUM_TRIM_FRACTION = TunedParameter(
    name="stacking.maximum_trim_fraction",
    default=0.25,
    unit="fraction",
    description="Largest share of its length by which a side may be trimmed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-23",
)

# A trim this small is not worth making. Dithering of a few pixels leaves
# margins like this, and cutting them would only change the file size.
_MINIMUM_WORTH_TRIMMING_PX = TunedParameter(
    name="stacking.minimum_worth_trimming_px",
    default=3,
    unit="pixels",
    description="Smallest trim worth making.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D58",
)

# Pixels brighter than the sky by more than this many robust standard
# deviations are treated as stars and left out of the noise estimate.
_STAR_SIGMA = TunedParameter(
    name="stacking.star_sigma",
    default=3.0,
    unit="sigma",
    description="Robust sigmas above the sky at which pixels count as stars in the edge noise estimate.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D58",
)


@dataclass(frozen=True)
class CropBox:
    """The part of a stack that is kept.

    Attributes
    ----------
    x0, x1 : `int`
        First column kept and one past the last, in array indices.
    y0, y1 : `int`
        First row kept and one past the last, in array indices (the first
        row of the array is the first row stored in the FITS file).
    reason : `str`
        Why the box is smaller than the image, with the numbers of the rule.
        Empty for a box that keeps the whole image. It is left out of
        comparisons.
    lost_area_fraction : `float`
        The share of the image area the crop removes, 0 to 1. Set by
        `crop_stack_edges`; left out of comparisons.
    """

    x0: int
    x1: int
    y0: int
    y1: int
    reason: str = field(default="", compare=False)
    lost_area_fraction: float = field(default=0.0, compare=False)

    def lost_fraction(self, width: int, height: int) -> float:
        """Give the share of the image area the crop removes.

        Returns
        -------
        fraction : `float`
            0 for no crop, up to 1.
        """
        return 1.0 - ((self.x1 - self.x0) * (self.y1 - self.y0)) / (width * height)


def _strip_noise(strip: np.ndarray, axis: int) -> float:
    """Measure the pixel noise in one strip, as a fraction of its sky level.

    The noise comes from the difference of neighbouring pixels that are not
    stars, so a smooth gradient does not count.

    Parameters
    ----------
    strip : `numpy.ndarray`
        A 2-D piece of the stack.
    axis : `int`
        The axis along which neighbouring pixels are differenced.

    Returns
    -------
    noise : `float`
        The noise divided by the strip's median, or `nan` if the strip has no
        usable sky.
    """
    median = float(np.median(strip))
    sigma = MAD_TO_SIGMA * float(np.median(np.abs(strip - median)))
    if median <= 0 or sigma <= 0:
        return float("nan")
    sky = strip < median + resolve(_STAR_SIGMA).value * sigma
    if axis == 1:
        differences = (strip[:, 1:] - strip[:, :-1])[sky[:, 1:] & sky[:, :-1]]
    else:
        differences = (strip[1:, :] - strip[:-1, :])[sky[1:, :] & sky[:-1, :]]
    if differences.size < 100:
        return float("nan")
    centre = float(np.median(differences))
    return MAD_TO_SIGMA * float(np.median(np.abs(differences - centre))) / np.sqrt(2.0) / median


def _trim_from_ends(ratios: np.ndarray) -> tuple[int, int]:
    """Count the noisy strips at each end of a noise profile.

    Parameters
    ----------
    ratios : `numpy.ndarray`
        The noise of each strip divided by the interior's noise.

    Returns
    -------
    start, end : `tuple` [`int`, `int`]
        How many strips to trim from the start and from the end: the run of
        strips from that end whose ratio exceeds `MAXIMUM_EDGE_NOISE_RATIO`.
    """
    smoothed = median_filter(np.nan_to_num(ratios, nan=1.0), size=3, mode="nearest")
    smoothed[0], smoothed[-1] = (
        ratios[0] if np.isfinite(ratios[0]) else 1.0,
        ratios[-1] if np.isfinite(ratios[-1]) else 1.0,
    )
    noisy = smoothed > resolve(MAXIMUM_EDGE_NOISE_RATIO).value
    start = 0
    while start < len(noisy) and noisy[start]:
        start += 1
    end = 0
    while end < len(noisy) - start and noisy[len(noisy) - 1 - end]:
        end += 1
    return start, end


def find_edge_crop_box(image: np.ndarray) -> CropBox:
    """Find how far to trim each side of a stack to get rid of noisy edges.

    Parameters
    ----------
    image : `numpy.ndarray`
        The stack's pixels. A colour stack is averaged over its channels.

    Returns
    -------
    box : `CropBox`
        The part to keep. The whole image if the edges are clean, if the
        stack has no measurable sky, or if a side would lose more than
        `MAXIMUM_TRIM_FRACTION` of its length.
    """
    strip_pixels = resolve(STRIP_PIXELS).value
    cross_axis_start = resolve(_CROSS_AXIS_START).value
    cross_axis_end = resolve(_CROSS_AXIS_END).value
    maximum_trim_fraction = resolve(MAXIMUM_TRIM_FRACTION).value
    plane = np.asarray(collapse_to_2d(np.asarray(image, dtype=np.float64)))
    height, width = plane.shape
    whole = CropBox(0, width, 0, height)
    rows_a, rows_b = int(cross_axis_start * height), int(cross_axis_end * height)
    cols_a, cols_b = int(cross_axis_start * width), int(cross_axis_end * width)
    strips_x, strips_y = width // strip_pixels, height // strip_pixels
    if strips_x < 8 or strips_y < 8:
        return whole
    columns = np.array([
        _strip_noise(plane[rows_a:rows_b, i * strip_pixels : (i + 1) * strip_pixels], axis=1)
        for i in range(strips_x)
    ])
    rows = np.array([
        _strip_noise(plane[i * strip_pixels : (i + 1) * strip_pixels, cols_a:cols_b], axis=0)
        for i in range(strips_y)
    ])
    if not np.isfinite(columns).any() or not np.isfinite(rows).any():
        return whole
    column_ratios = columns / np.nanmedian(columns[strips_x // 4 : 3 * strips_x // 4])
    row_ratios = rows / np.nanmedian(rows[strips_y // 4 : 3 * strips_y // 4])
    left, right = (n * strip_pixels for n in _trim_from_ends(column_ratios))
    start, end = (n * strip_pixels for n in _trim_from_ends(row_ratios))
    if max(left, right) > maximum_trim_fraction * width or max(start, end) > maximum_trim_fraction * height:
        logger.warning(
            "The stack's edges are noisy over more than %.0f%% of a side; leaving it whole.",
            100 * maximum_trim_fraction,
        )
        return whole
    return _same_shape_box(CropBox(left, width - right, start, height - end), width, height)


def _same_shape_box(clean: CropBox, width: int, height: int) -> CropBox:
    """Fit the largest same-shaped box inside the clean area.

    Parameters
    ----------
    clean : `CropBox`
        The rectangle left after the noisy margins are removed.
    width, height : `int`
        The original image's size in pixels.

    Returns
    -------
    box : `CropBox`
        The largest box with the original width-to-height ratio that fits in
        `clean`, as near the original image's centre as `clean` allows. It is
        `clean` itself when `clean` is the whole image.
    """
    clean_width, clean_height = clean.x1 - clean.x0, clean.y1 - clean.y0
    if clean_width == width and clean_height == height:
        return clean
    ratio = width / height
    if clean_width / clean_height > ratio:
        new_height, new_width = clean_height, round(clean_height * ratio)
    else:
        new_width, new_height = clean_width, round(clean_width / ratio)
    x0 = round(width / 2 - new_width / 2)
    y0 = round(height / 2 - new_height / 2)
    x0 = min(max(x0, clean.x0), clean.x1 - new_width)
    y0 = min(max(y0, clean.y0), clean.y1 - new_height)
    return CropBox(x0, x0 + new_width, y0, y0 + new_height)


def frame_coverage_counts(
    homographies: Sequence[Sequence[float]],
    included: Sequence[bool],
    width: int,
    height: int,
    step: int | None = None,
    alignments: Sequence[Sequence[float] | None] | None = None,
) -> np.ndarray:
    """Count how many included frames cover each pixel of the stack.

    Each frame's transform moves its pixels onto the reference frame's grid,
    which is the stack's grid before any crop. A stack pixel is covered by a
    frame when the inverse transform lands inside that frame.

    When the stack was built from exposure groups, each group was registered
    to its own reference frame, and the group stacks were then lined up with
    each other. A frame's transform only reaches its own group's grid, so the
    stack pixel is first moved back onto that grid by the inverse of the
    group's alignment. A stack pixel that falls outside the group's grid, or
    within `_ALIGNED_EDGE_MARGIN_PX` of its border, is not covered by any frame
    of the group.

    Parameters
    ----------
    homographies : `Sequence` of `Sequence` of `float`
        For each frame, the nine numbers of its 3 x 3 transform in row order.
        Siril's x counts along the columns and its y counts up from the last
        row of the array (y = height - 1 - row).
    included : `Sequence` of `bool`
        For each frame, whether it is in the stack.
    width, height : `int`
        The size in pixels of the frames and of the uncropped stack.
    step : `int`, optional
        Spacing of the counted pixels. Defaults to the tuned grid step.
    alignments : `Sequence` of (`Sequence` of `float` or `None`), optional
        For each frame, how its group's stack was moved onto the reference
        group's grid: the shift along the rows, the shift along the columns,
        the rotation in degrees and the scale, as in `AlignmentResult` (the
        rotation and scale are about the image centre, and the shifts are
        those of the centre). `None` for a frame of the reference group. Left
        out, no frame's group was moved.

    Returns
    -------
    counts : `numpy.ndarray`
        An integer array of shape (ceil(height / step), ceil(width / step)).
        The entry at (i, j) is the count at pixel row i * step, column
        j * step.
    """
    spacing = int(step if step is not None else resolve(_COVERAGE_GRID_STEP).value)
    margin = float(resolve(_ALIGNED_EDGE_MARGIN_PX).value)
    columns = np.arange(0, width, spacing, dtype=np.float64)
    rows = np.arange(0, height, spacing, dtype=np.float64)
    stack_columns, stack_rows = np.meshgrid(columns, rows)
    centre = complex((width - 1) / 2.0, (height - 1) / 2.0)
    counts = np.zeros(stack_columns.shape, dtype=np.int32)
    moves = [None] * len(homographies) if alignments is None else alignments
    for values, is_included, move in zip(homographies, included, moves, strict=True):
        if not is_included:
            continue
        group_columns, group_rows = stack_columns, stack_rows
        inside_group = True
        if move is not None:
            # The alignment sent a group position z (column + 1j * row) to
            # a * (z - c) + c + t, so a stack position z' came from
            # (z' - c - t) / a + c.
            shift_rows, shift_columns, rotation_degrees, scale = move
            turn = scale * np.exp(1j * np.radians(rotation_degrees))
            position = (stack_columns + 1j * stack_rows - centre - complex(shift_columns, shift_rows)) / turn
            group_columns, group_rows = position.real + centre.real, position.imag + centre.imag
            inside_group = (
                (group_columns >= margin)
                & (group_columns <= width - 1 - margin)
                & (group_rows >= margin)
                & (group_rows <= height - 1 - margin)
            )
        siril_x, siril_y = group_columns, height - 1 - group_rows
        inverse = np.linalg.inv(np.asarray(values, dtype=np.float64).reshape(3, 3))
        scale_term = inverse[2, 0] * siril_x + inverse[2, 1] * siril_y + inverse[2, 2]
        frame_x = (inverse[0, 0] * siril_x + inverse[0, 1] * siril_y + inverse[0, 2]) / scale_term
        frame_y = (inverse[1, 0] * siril_x + inverse[1, 1] * siril_y + inverse[1, 2]) / scale_term
        counts += (
            inside_group & (frame_x >= 0) & (frame_x <= width - 1) & (frame_y >= 0) & (frame_y <= height - 1)
        )
    return counts


def _largest_box_in_mask(good: np.ndarray, ratio: float) -> tuple[int, int, int, int] | None:
    """Find the largest box of a given shape inside the good cells.

    Parameters
    ----------
    good : `numpy.ndarray`
        A boolean grid, `True` where a cell may be kept.
    ratio : `float`
        The box's width divided by its height.

    Returns
    -------
    box : `tuple` [`int`, `int`, `int`, `int`] or `None`
        The first column, one past the last, the first row and one past the
        last, in cells, of the largest box that holds only good cells. Among
        the positions of that size, the one nearest the grid's centre. `None`
        if no cell is good.
    """
    rows, columns = good.shape
    bad_total = np.zeros((rows + 1, columns + 1), dtype=np.int64)
    bad_total[1:, 1:] = np.cumsum(np.cumsum(~good, axis=0), axis=1)

    def clean_positions(box_height: int) -> tuple[np.ndarray, int]:
        """Find where a box of one height fits.

        Parameters
        ----------
        box_height : `int`
            The box's height in cells.

        Returns
        -------
        fits, box_width : `tuple` [`numpy.ndarray`, `int`]
            A grid that is `True` where the box fits, and the box's width.
        """
        box_width = max(1, round(box_height * ratio))
        if box_width > columns or box_height > rows:
            return np.zeros((1, 1), dtype=bool), box_width
        bad_inside = (
            bad_total[box_height:, box_width:]
            - bad_total[: rows - box_height + 1, box_width:]
            - bad_total[box_height:, : columns - box_width + 1]
            + bad_total[: rows - box_height + 1, : columns - box_width + 1]
        )
        return bad_inside == 0, box_width

    low, high = 0, rows
    while low < high:
        middle = (low + high + 1) // 2
        if clean_positions(middle)[0].any():
            low = middle
        else:
            high = middle - 1
    if low == 0:
        return None
    fits, box_width = clean_positions(low)
    top_choices, left_choices = np.nonzero(fits)
    distance = (left_choices + box_width / 2 - columns / 2) ** 2 + (top_choices + low / 2 - rows / 2) ** 2
    best = int(np.argmin(distance))
    left, top = int(left_choices[best]), int(top_choices[best])
    return left, left + box_width, top, top + low


def find_coverage_crop_box(
    counts: np.ndarray,
    step: int,
    width: int,
    height: int,
    fraction: float | None = None,
    within: CropBox | None = None,
    *,
    limit_trim: bool = True,
) -> CropBox:
    """Find the part of a stack that enough frames cover.

    Parameters
    ----------
    counts : `numpy.ndarray`
        The frame count at each counted pixel, from `frame_coverage_counts`.
    step : `int`
        The spacing of the counted pixels.
    width, height : `int`
        The uncropped stack's size in pixels.
    fraction : `float`, optional
        The smallest share of the frames that must cover a kept pixel.
        Defaults to `STACK_MINIMUM_COVERAGE_FRACTION`.
    within : `CropBox`, optional
        A box the result must lie inside, in the same pixels as the counts
        (the stack's own crop, for the processed picture's box).
    limit_trim : `bool`, optional
        Whether to leave the image whole when a side would lose more than
        `MAXIMUM_TRIM_FRACTION` of its length. The processed picture's box
        sets this to `False`: its rule is the coverage alone.

    Returns
    -------
    box : `CropBox`
        The largest box of the original shape inside the region covered by at
        least `fraction` of the most-covered pixel's frame count, as near the
        centre as that region allows, with its reason. The whole image, with
        an empty reason, when the whole image qualifies, no pixel does, or
        (with `limit_trim`) a side would lose more than
        `MAXIMUM_TRIM_FRACTION` of its length.
    """
    whole = CropBox(0, width, 0, height)
    total = int(counts.max())
    if total == 0:
        return whole
    fraction = float(resolve(STACK_MINIMUM_COVERAGE_FRACTION).value) if fraction is None else fraction
    required = math.ceil(fraction * total - 1e-9)
    good = counts >= required
    if within is not None:
        cell_columns = np.arange(counts.shape[1]) * step
        cell_rows = np.arange(counts.shape[0]) * step
        inside = ((cell_rows >= within.y0) & (cell_rows < within.y1))[:, None] & (
            (cell_columns >= within.x0) & (cell_columns < within.x1)
        )[None, :]
        good = good & inside
    if good.all():
        return whole
    found = _largest_box_in_mask(good, width / height)
    if found is None:
        return whole
    left, right, top, bottom = found
    clean = CropBox(
        0 if left == 0 else left * step,
        width if right == counts.shape[1] else (right - 1) * step + 1,
        0 if top == 0 else top * step,
        height if bottom == counts.shape[0] else (bottom - 1) * step + 1,
    )
    box = _same_shape_box(clean, width, height)
    maximum_trim_fraction = float(resolve(MAXIMUM_TRIM_FRACTION).value)
    sides = {"left": box.x0, "right": width - box.x1, "top": box.y0, "bottom": height - box.y1}
    if limit_trim and (
        max(sides["left"], sides["right"]) > maximum_trim_fraction * width
        or max(sides["top"], sides["bottom"]) > maximum_trim_fraction * height
    ):
        logger.warning(
            "The stack is covered by fewer than %.0f%% of its frames over more than %.0f%% of a side; "
            "leaving it whole.",
            100 * fraction,
            100 * maximum_trim_fraction,
        )
        return whole
    return CropBox(
        box.x0, box.x1, box.y0, box.y1, _coverage_reason(counts, step, box, sides, required, total, fraction)
    )


def _coverage_reason(
    counts: np.ndarray,
    step: int,
    box: CropBox,
    sides: Mapping[str, int],
    required: int,
    total: int,
    fraction: float,
) -> str:
    """Write why a coverage crop was made.

    Parameters
    ----------
    counts : `numpy.ndarray`
        The frame count at each counted pixel.
    step : `int`
        The spacing of the counted pixels.
    box : `CropBox`
        The part kept.
    sides : `Mapping` [`str`, `int`]
        Pixels cut from the left, right, top and bottom.
    required : `int`
        The fewest frames a kept pixel may have.
    total : `int`
        The largest frame count anywhere, which is the number of included
        frames when some pixel is seen by all.
    fraction : `float`
        The rule's fraction.

    Returns
    -------
    reason : `str`
        A sentence naming each cut side, its width and the lowest frame count
        in the cut strip, then the rule's numbers.
    """
    cut = {"left": (None, box.x0), "right": (box.x1, None), "top": (None, box.y0), "bottom": (box.y1, None)}
    in_rows = slice(-(-box.y0 // step), -(-box.y1 // step))
    in_columns = slice(-(-box.x0 // step), -(-box.x1 // step))
    parts = []
    for name, pixels in sides.items():
        if pixels <= 0:
            continue
        start, stop = cut[name]
        first = None if start is None else -(-start // step)
        last = None if stop is None else -(-stop // step)
        if name in ("left", "right"):
            strip = counts[in_rows, first:last]
        else:
            strip = counts[first:last, in_columns]
        lowest = int(strip.min()) if strip.size else total
        parts.append(f"{name} {pixels} px (covered by as few as {lowest} of {total} frames)")
    return (
        f"Cropped to the area covered by at least {required} of {total} frames "
        f"({100 * fraction:.0f}%), keeping the original shape: " + "; ".join(parts) + "."
    )


def _coverage_from_diagnostics(
    diagnostics: Mapping[str, Any] | None, width: int, height: int
) -> np.ndarray | None:
    """Count frame coverage from a stacking run's registration facts.

    Parameters
    ----------
    diagnostics : `Mapping` or `None`
        What the stacking run reported: ``registration_frames`` (one dict per
        frame with a ``homography``) and ``registration_included``. For a
        stack of exposure groups, ``exposure_group_alignments`` gives each
        group's frame count and how its stack was moved onto the reference
        group's grid.
    width, height : `int`
        The uncropped stack's size.

    Returns
    -------
    counts : `numpy.ndarray` or `None`
        The coverage counts, or `None` when the transforms are missing or not
        nine numbers each, or when the groups' frame counts do not add up to
        the number of frames (so a frame cannot be given its group's move).
    """
    frames = (diagnostics or {}).get("registration_frames") or []
    homographies = [facts.get("homography") or [] for facts in frames]
    if not homographies or any(len(values) != 9 for values in homographies):
        return None
    flags = (diagnostics or {}).get("registration_included") or []
    included = (
        [bool(flag) for flag in flags] if len(flags) == len(homographies) else [True] * len(homographies)
    )
    groups = (diagnostics or {}).get("exposure_group_alignments") or []
    alignments: list[Sequence[float] | None] | None = None
    if any(group.get("alignment") is not None for group in groups):
        alignments = [group.get("alignment") for group in groups for _ in range(int(group["frame_count"]))]
        if len(alignments) != len(homographies):
            logger.warning(
                "The exposure groups' frame counts (%d) do not match the registered frames (%d); "
                "frame coverage is not counted.",
                len(alignments),
                len(homographies),
            )
            return None
    return frame_coverage_counts(homographies, included, width, height, alignments=alignments)


# Header cards that record where a processed picture sits on its stack. The
# stack is the saved file, after any quarter turn.
CROP_X0_KEYWORD = "CROPX0"
CROP_Y0_KEYWORD = "CROPY0"
CROP_WIDTH_KEYWORD = "CROPW"
CROP_HEIGHT_KEYWORD = "CROPH"


@dataclass(frozen=True)
class StackCrops:
    """The two crops made from one stack's coverage.

    Attributes
    ----------
    stack : `CropBox` or `None`
        The part of the registered stack kept, in the uncropped stack's
        pixels. `None` when the stack was left whole.
    processed : `CropBox` or `None`
        The part of the (trimmed) stack the processed picture keeps, in the
        trimmed stack's pixels as registered (before any quarter turn).
        `None` when the picture keeps the whole stack.
    """

    stack: CropBox | None
    processed: CropBox | None


def turn_box(box: CropBox, degrees: int, width: int, height: int) -> CropBox:
    """Move a box to where its pixels are after an image is turned.

    The turn is the one `quarter_turn.turn_image` makes: counterclockwise by
    quarter turns, with the first row at the top.

    Parameters
    ----------
    box : `CropBox`
        A box on the image before the turn.
    degrees : `int`
        0, 90, 180 or 270.
    width, height : `int`
        The image's size before the turn.

    Returns
    -------
    turned : `CropBox`
        The same pixels as a box on the turned image. Its reason and lost
        area are kept.
    """
    x0, x1, y0, y1 = box.x0, box.x1, box.y0, box.y1
    for _ in range((degrees // 90) % 4):
        # One counterclockwise turn sends (row, column) to
        # (width - 1 - column, row).
        x0, x1, y0, y1 = y0, y1, width - x1, width - x0
        width, height = height, width
    return replace(box, x0=x0, x1=x1, y0=y0, y1=y1)


def shift_header_for_crop(header: Any, box: CropBox) -> None:
    """Move a header's reference pixel for a crop and note the crop.

    The distortion terms (SIP) are measured from the reference pixel, so
    moving the reference pixel by the crop's offset keeps the plate solution
    exact: a star is at the same sky position before and after.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header to change in place.
    box : `CropBox`
        The part of the image kept.
    """
    if "CRPIX1" in header:
        header["CRPIX1"] = float(header["CRPIX1"]) - box.x0
    if "CRPIX2" in header:
        header["CRPIX2"] = float(header["CRPIX2"]) - box.y0


def crop_file(path: str, box: CropBox, *, note_crop: bool = False) -> None:
    """Crop one FITS file in place and move its coordinate reference.

    Parameters
    ----------
    path : `str`
        The stack, rejection-map or picture file.
    box : `CropBox`
        The part to keep.
    note_crop : `bool`, optional
        Whether to write the crop's place on the file's grid into the header
        (``CROPX0``, ``CROPY0``, ``CROPW`` and ``CROPH``). If the file already
        has them, the new offset adds to the old, so the cards always say
        where the file sits on the saved stack.
    """
    data = np.asarray(read_data(path))
    header = read_header(path)
    cropped = data[..., box.y0 : box.y1, box.x0 : box.x1]
    shift_header_for_crop(header, box)
    if note_crop:
        header[CROP_X0_KEYWORD] = (
            int(header.get(CROP_X0_KEYWORD, 0)) + box.x0,
            "columns cut from the stack's left",
        )
        header[CROP_Y0_KEYWORD] = (
            int(header.get(CROP_Y0_KEYWORD, 0)) + box.y0,
            "rows cut from the stack's top",
        )
        header[CROP_WIDTH_KEYWORD] = (box.x1 - box.x0, "width kept")
        header[CROP_HEIGHT_KEYWORD] = (box.y1 - box.y0, "height kept")
    temporary = path + ".crop.fits"
    write_image(temporary, np.ascontiguousarray(cropped), header)
    os.replace(temporary, path)


def crop_stack_edges(stack_path: str, diagnostics: Mapping[str, Any] | None = None) -> StackCrops | None:
    """Trim the thinly covered edges off a stack and its rejection map.

    The stack is cut to the area covered by at least
    `STACK_MINIMUM_COVERAGE_FRACTION` of the frames. The processed picture's
    box (`PROCESSED_MINIMUM_COVERAGE_FRACTION`) is found at the same time and
    given back, but nothing is cut for it here: the picture is cropped when it
    is made. A failure is logged and leaves the files as they were.

    Parameters
    ----------
    stack_path : `str`
        Path of the stack's FITS file.
    diagnostics : `Mapping`, optional
        What the stacking run reported about registration. With the frames'
        transforms in it, the crops are by coverage. Without them, the stack
        is trimmed by measured noise and the picture keeps all of it.

    Returns
    -------
    crops : `StackCrops` or `None`
        What was found. `StackCrops.stack` is the part kept (with the reason
        for the crop), or `None` if the stack was not trimmed.
        `StackCrops.processed` is the picture's box on the trimmed stack, or
        `None` if the picture keeps all of it. `None` when neither applies.
    """
    processed: CropBox | None = None
    stack_box: CropBox | None = None
    try:
        data = np.asarray(read_data(stack_path))
        height, width = data.shape[-2:]
        counts = _coverage_from_diagnostics(diagnostics, width, height)
        step = int(resolve(_COVERAGE_GRID_STEP).value)
        minimum_px = resolve(_MINIMUM_WORTH_TRIMMING_PX).value
        if counts is None:
            box = find_edge_crop_box(data)
            box = CropBox(
                box.x0,
                box.x1,
                box.y0,
                box.y1,
                "Cropped because its edges were noisier than the interior."
                if box.lost_fraction(width, height)
                else "",
            )
        else:
            box = find_coverage_crop_box(counts, step, width, height)
            wide = find_coverage_crop_box(
                counts,
                step,
                width,
                height,
                float(resolve(PROCESSED_MINIMUM_COVERAGE_FRACTION).value),
                within=box,
                limit_trim=False,
            )
            relative = CropBox(
                wide.x0 - box.x0, wide.x1 - box.x0, wide.y0 - box.y0, wide.y1 - box.y0, wide.reason
            )
            kept_width, kept_height = box.x1 - box.x0, box.y1 - box.y0
            if (
                max(relative.x0, kept_width - relative.x1, relative.y0, kept_height - relative.y1)
                >= minimum_px
            ):
                processed = replace(
                    relative, lost_area_fraction=relative.lost_fraction(kept_width, kept_height)
                )
        trimmed = (box.x0, width - box.x1, box.y0, height - box.y1)
        if max(trimmed) >= minimum_px:
            stack_box = replace(box, lost_area_fraction=box.lost_fraction(width, height))
            crop_file(stack_path, stack_box)
            stem, _ = os.path.splitext(stack_path)
            rejection_map = stem + _REJECTION_MAP_SUFFIX
            if os.path.isfile(rejection_map):
                crop_file(rejection_map, stack_box)
    except (OSError, ValueError) as error:
        logger.warning("Could not trim the edges of '%s': %s. It is left whole.", stack_path, error)
        return None
    if stack_box is not None:
        logger.info(
            "Trimmed '%s' to columns %d-%d and rows %d-%d (%.1f%% of the area). %s",
            stack_path,
            stack_box.x0,
            stack_box.x1,
            stack_box.y0,
            stack_box.y1,
            100 * stack_box.lost_area_fraction,
            stack_box.reason,
        )
    if processed is not None:
        logger.info("The processed picture of '%s' will keep %s", stack_path, processed.reason)
    return StackCrops(stack_box, processed) if stack_box is not None or processed is not None else None
