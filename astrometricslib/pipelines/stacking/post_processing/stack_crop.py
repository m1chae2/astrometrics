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
module counts how many included frames cover each pixel. It removes every
region covered by fewer than a set fraction of the frames. The kept area is
the largest box of the original shape inside what is left, placed as near the
centre of the original image as the covered area allows (the telescope points
the target at the centre, so this keeps it there). The stack keeps the
proportions of the original, so a square stack stays square.

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
    "CropBox",
    "crop_stack_edges",
    "find_coverage_crop_box",
    "find_edge_crop_box",
    "frame_coverage_counts",
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

# Regions covered by fewer than this fraction of the included frames are cut.
# Noise goes as one over the square root of the number of frames averaged, so
# an edge covered by 95% of the frames is at most 1/sqrt(0.95) = 1.026 times
# as noisy as the interior. Measured coverage: NGC 7331 has an edge covered by
# 111 of 123 frames (0.90) after a meridian flip, which this cuts, and M 31
# has shifts of at most 25 pixels with a thin edge covered by 114 of 121
# frames (0.94), which this cuts at a cost of about 25 pixels. The earlier
# noise rule (1.15, about three quarters of the frames) kept both.
MINIMUM_COVERAGE_FRACTION = TunedParameter(
    name="stacking.minimum_coverage_fraction",
    default=0.95,
    unit="fraction",
    description="Smallest share of the included frames that must cover a pixel for it to be kept.",
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
) -> np.ndarray:
    """Count how many included frames cover each pixel of the stack.

    Each frame's transform moves its pixels onto the reference frame's grid,
    which is the stack's grid before any crop. A stack pixel is covered by a
    frame when the inverse transform lands inside that frame.

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

    Returns
    -------
    counts : `numpy.ndarray`
        An integer array of shape (ceil(height / step), ceil(width / step)).
        The entry at (i, j) is the count at pixel row i * step, column
        j * step.
    """
    spacing = int(step if step is not None else resolve(_COVERAGE_GRID_STEP).value)
    columns = np.arange(0, width, spacing, dtype=np.float64)
    rows = np.arange(0, height, spacing, dtype=np.float64)
    siril_x, siril_y = np.meshgrid(columns, height - 1 - rows)
    counts = np.zeros(siril_x.shape, dtype=np.int32)
    for values, is_included in zip(homographies, included, strict=True):
        if not is_included:
            continue
        inverse = np.linalg.inv(np.asarray(values, dtype=np.float64).reshape(3, 3))
        scale = inverse[2, 0] * siril_x + inverse[2, 1] * siril_y + inverse[2, 2]
        frame_x = (inverse[0, 0] * siril_x + inverse[0, 1] * siril_y + inverse[0, 2]) / scale
        frame_y = (inverse[1, 0] * siril_x + inverse[1, 1] * siril_y + inverse[1, 2]) / scale
        counts += (frame_x >= 0) & (frame_x <= width - 1) & (frame_y >= 0) & (frame_y <= height - 1)
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


def find_coverage_crop_box(counts: np.ndarray, step: int, width: int, height: int) -> CropBox:
    """Find the part of a stack that nearly all frames cover.

    Parameters
    ----------
    counts : `numpy.ndarray`
        The frame count at each counted pixel, from `frame_coverage_counts`.
    step : `int`
        The spacing of the counted pixels.
    width, height : `int`
        The uncropped stack's size in pixels.

    Returns
    -------
    box : `CropBox`
        The largest box of the original shape inside the region covered by at
        least `MINIMUM_COVERAGE_FRACTION` of the most-covered pixel's frame
        count, as near the centre as that region allows, with its reason. The
        whole image, with an empty reason, when the whole image qualifies, no
        pixel does, or a side would lose more than `MAXIMUM_TRIM_FRACTION` of
        its length.
    """
    whole = CropBox(0, width, 0, height)
    total = int(counts.max())
    if total == 0:
        return whole
    fraction = float(resolve(MINIMUM_COVERAGE_FRACTION).value)
    required = math.ceil(fraction * total - 1e-9)
    good = counts >= required
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
    if max(sides["left"], sides["right"]) > maximum_trim_fraction * width or (
        max(sides["top"], sides["bottom"]) > maximum_trim_fraction * height
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
        frame with a ``homography``) and ``registration_included``.
    width, height : `int`
        The uncropped stack's size.

    Returns
    -------
    counts : `numpy.ndarray` or `None`
        The coverage counts, or `None` when the transforms are missing or not
        nine numbers each.
    """
    frames = (diagnostics or {}).get("registration_frames") or []
    homographies = [facts.get("homography") or [] for facts in frames]
    if not homographies or any(len(values) != 9 for values in homographies):
        return None
    flags = (diagnostics or {}).get("registration_included") or []
    included = (
        [bool(flag) for flag in flags] if len(flags) == len(homographies) else [True] * len(homographies)
    )
    return frame_coverage_counts(homographies, included, width, height)


def _crop_file(path: str, box: CropBox) -> None:
    """Crop one FITS file in place and move its coordinate reference.

    Parameters
    ----------
    path : `str`
        The stack or rejection-map file.
    box : `CropBox`
        The part to keep.
    """
    data = np.asarray(read_data(path))
    header = read_header(path)
    cropped = data[..., box.y0 : box.y1, box.x0 : box.x1]
    if "CRPIX1" in header:
        header["CRPIX1"] = float(header["CRPIX1"]) - box.x0
    if "CRPIX2" in header:
        header["CRPIX2"] = float(header["CRPIX2"]) - box.y0
    temporary = path + ".crop.fits"
    write_image(temporary, np.ascontiguousarray(cropped), header)
    os.replace(temporary, path)


def crop_stack_edges(stack_path: str, diagnostics: Mapping[str, Any] | None = None) -> CropBox | None:
    """Trim the thinly covered edges off a stack and its rejection map.

    A failure is logged and leaves the files as they were.

    Parameters
    ----------
    stack_path : `str`
        Path of the stack's FITS file.
    diagnostics : `Mapping`, optional
        What the stacking run reported about registration. With the frames'
        transforms in it, the crop is by coverage. Without them, it is by
        measured noise.

    Returns
    -------
    box : `CropBox` or `None`
        The part kept, with the reason for the crop, or `None` if nothing was
        trimmed.
    """
    try:
        data = np.asarray(read_data(stack_path))
        height, width = data.shape[-2:]
        counts = _coverage_from_diagnostics(diagnostics, width, height)
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
            box = find_coverage_crop_box(counts, int(resolve(_COVERAGE_GRID_STEP).value), width, height)
        trimmed = (box.x0, width - box.x1, box.y0, height - box.y1)
        if max(trimmed) < resolve(_MINIMUM_WORTH_TRIMMING_PX).value:
            return None
        box = replace(box, lost_area_fraction=box.lost_fraction(width, height))
        _crop_file(stack_path, box)
        stem, _ = os.path.splitext(stack_path)
        rejection_map = stem + _REJECTION_MAP_SUFFIX
        if os.path.isfile(rejection_map):
            _crop_file(rejection_map, box)
    except (OSError, ValueError) as error:
        logger.warning("Could not trim the edges of '%s': %s. It is left whole.", stack_path, error)
        return None
    logger.info(
        "Trimmed '%s' to columns %d-%d and rows %d-%d (%.1f%% of the area). %s",
        stack_path,
        box.x0,
        box.x1,
        box.y0,
        box.y1,
        100 * box.lost_fraction(width, height),
        box.reason,
    )
    return box
