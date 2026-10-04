"""Trims the noisy edges off a finished stack.

Frames never line up exactly. Dithering moves each frame a few pixels, and a
night's re-centering can move it by hundreds. After registration, every frame
covers a shifted rectangle of the sky, so the edges of a stack can be covered
by fewer frames than the middle. An edge pixel then has fewer samples to
average, so it is noisier, and a ragged border appears where frames drop out.
On the 82-frame M 27 stack, which combines three nights, the first 100 columns
and the first 400 rows had 18% to 33% more noise than the interior.

Whether an edge is noisy depends on which frames reach it, how long they were
exposed and how sharp they were. A count of overlapping frames does not capture
that. On the Bubble Nebula, M 81 and M 13 stacks, the frame shifts would have
cut 14% to 36% of the area, yet the edges of those stacks were as clean as
their middles. So this module measures the noise instead. It trims each side
back to where the noise falls to within a set factor of the interior's, and it
does nothing to a stack whose edges are clean.

The trimmed stack keeps the proportions of the original, so a square stack
stays square. After the noisy margins are found, the largest box of the
original shape that fits inside what is left is cut out, placed as near the
centre of the original image as the clean area allows (the telescope points
the target at the centre, so this keeps it there).

It changes only the size of the files. Pixel values and the sky-coordinate
solution are kept, with the solution's reference pixel moved to match.
"""

import logging
import os
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from astrometricslib.drivers.fits_access import collapse_to_2d, read_data, read_header, write_image

logger = logging.getLogger(__name__)

__all__ = ["CropBox", "crop_stack_edges", "find_edge_crop_box"]

# The rejection map is written beside the stack under this suffix.
_REJECTION_MAP_SUFFIX = "_RejMap.fits"

# The edge profile is measured in strips of this many pixels. A strip is wide
# enough to hold thousands of sky pixels for a stable noise estimate, and
# narrow enough to place the edge within a few dozen pixels.
STRIP_PIXELS = 25

# Strips whose noise exceeds the interior's by more than this factor are
# trimmed. M 27's edges measured 1.18 to 1.33; the edges of the Bubble Nebula,
# M 81 and M 13 stacks measured at most 1.06. 1.15 sits between them. For
# frames of equal weight it corresponds to an edge covered by about three
# quarters of the frames (noise goes as one over the square root of the
# coverage).
MAXIMUM_EDGE_NOISE_RATIO = 1.15

# The profile along one axis is measured on the middle of the other axis, so a
# corner that is noisy in both directions does not count twice.
_CROSS_AXIS_START = 0.2
_CROSS_AXIS_END = 0.8

# No side is trimmed by more than this share of its length. A bigger crop
# means something other than edge coverage is wrong with the stack, and the
# stack is left whole for a person to look at.
MAXIMUM_TRIM_FRACTION = 0.25

# A trim this small is not worth making. Dithering of a few pixels leaves
# margins like this, and cutting them would only change the file size.
_MINIMUM_WORTH_TRIMMING_PX = 3

# The robust standard deviation (from the median absolute deviation) is 1.4826
# times the median absolute deviation for Gaussian noise.
_MAD_TO_SIGMA = 1.4826

# Pixels brighter than the sky by more than this many robust standard
# deviations are treated as stars and left out of the noise estimate.
_STAR_SIGMA = 3.0


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
    """

    x0: int
    x1: int
    y0: int
    y1: int

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
    sigma = _MAD_TO_SIGMA * float(np.median(np.abs(strip - median)))
    if median <= 0 or sigma <= 0:
        return float("nan")
    sky = strip < median + _STAR_SIGMA * sigma
    if axis == 1:
        differences = (strip[:, 1:] - strip[:, :-1])[sky[:, 1:] & sky[:, :-1]]
    else:
        differences = (strip[1:, :] - strip[:-1, :])[sky[1:, :] & sky[:-1, :]]
    if differences.size < 100:
        return float("nan")
    centre = float(np.median(differences))
    return _MAD_TO_SIGMA * float(np.median(np.abs(differences - centre))) / np.sqrt(2.0) / median


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
    noisy = smoothed > MAXIMUM_EDGE_NOISE_RATIO
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
    plane = np.asarray(collapse_to_2d(np.asarray(image, dtype=np.float64)))
    height, width = plane.shape
    whole = CropBox(0, width, 0, height)
    rows_a, rows_b = int(_CROSS_AXIS_START * height), int(_CROSS_AXIS_END * height)
    cols_a, cols_b = int(_CROSS_AXIS_START * width), int(_CROSS_AXIS_END * width)
    strips_x, strips_y = width // STRIP_PIXELS, height // STRIP_PIXELS
    if strips_x < 8 or strips_y < 8:
        return whole
    columns = np.array([
        _strip_noise(plane[rows_a:rows_b, i * STRIP_PIXELS : (i + 1) * STRIP_PIXELS], axis=1)
        for i in range(strips_x)
    ])
    rows = np.array([
        _strip_noise(plane[i * STRIP_PIXELS : (i + 1) * STRIP_PIXELS, cols_a:cols_b], axis=0)
        for i in range(strips_y)
    ])
    if not np.isfinite(columns).any() or not np.isfinite(rows).any():
        return whole
    column_ratios = columns / np.nanmedian(columns[strips_x // 4 : 3 * strips_x // 4])
    row_ratios = rows / np.nanmedian(rows[strips_y // 4 : 3 * strips_y // 4])
    left, right = (n * STRIP_PIXELS for n in _trim_from_ends(column_ratios))
    start, end = (n * STRIP_PIXELS for n in _trim_from_ends(row_ratios))
    if max(left, right) > MAXIMUM_TRIM_FRACTION * width or max(start, end) > MAXIMUM_TRIM_FRACTION * height:
        logger.warning(
            "The stack's edges are noisy over more than %.0f%% of a side; leaving it whole.",
            100 * MAXIMUM_TRIM_FRACTION,
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


def crop_stack_edges(stack_path: str) -> CropBox | None:
    """Trim the noisy edges off a stack and its rejection map.

    A failure is logged and leaves the files as they were.

    Parameters
    ----------
    stack_path : `str`
        Path of the stack's FITS file.

    Returns
    -------
    box : `CropBox` or `None`
        The part kept, or `None` if nothing was trimmed.
    """
    try:
        data = np.asarray(read_data(stack_path))
        height, width = data.shape[-2:]
        box = find_edge_crop_box(data)
        trimmed = (box.x0, width - box.x1, box.y0, height - box.y1)
        if max(trimmed) < _MINIMUM_WORTH_TRIMMING_PX:
            return None
        _crop_file(stack_path, box)
        stem, _ = os.path.splitext(stack_path)
        rejection_map = stem + _REJECTION_MAP_SUFFIX
        if os.path.isfile(rejection_map):
            _crop_file(rejection_map, box)
    except (OSError, ValueError) as error:
        logger.warning("Could not trim the edges of '%s': %s. It is left whole.", stack_path, error)
        return None
    logger.info(
        "Trimmed '%s' to columns %d-%d and rows %d-%d (%.1f%% of the area) because its edges were noisy.",
        stack_path,
        box.x0,
        box.x1,
        box.y0,
        box.y1,
        100 * box.lost_fraction(width, height),
    )
    return box
