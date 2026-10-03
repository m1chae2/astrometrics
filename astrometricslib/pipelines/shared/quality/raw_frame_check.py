"""Purpose: Quick quality check of raw light frames, before any ingestion.

Description: During an observing session the raw frames sit on the
telescope computer or in a staging folder, not yet in the library. This
module measures each frame's stars (how many, how wide, how round, how
long the longest streak is), the sky level, the saturated pixels, and how
far the star field moved since the previous frame. It then flags the
frames that stand out from the rest of the batch: too few stars, trailed,
soft, or shifted a long way.

Every limit compares a frame with the median of its own batch, so it adapts
to the telescope, the camera and the night. The numeric limits below record
how they were chosen and which data they were checked against.
"""

import glob
import logging
import os
import statistics
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy import ndimage

from astrometricslib.drivers.fits_access import read_data

logger = logging.getLogger(__name__)

DETECTION_SIGMA = 40.0
"""A bright region must stand this many noise units above the background.

The noise is the spread of the smoothed, background-subtracted frame. At
this level a clean 9-megapixel frame from the 2026-10-02 session held 3,200
to 3,550 bright regions, none longer than 44 pixels, and a frame trailed by
a two-minute drift held 783, one of them 396 pixels long. Lower levels (8
noise units) let the halos of bright stars merge into regions hundreds of
pixels long and flagged clean frames as trailed.
"""

BACKGROUND_BLOCK_PIXELS = 32
"""Side of the square blocks whose medians estimate the sky background.

A block holds a thousand pixels, so a star covering a tenth of it does not
move the median. Block medians took 0.3 s per frame against 4.9 s for a
sliding median filter plus resampling, with the same flags on the
2026-10-02 frames.
"""

NOISE_SAMPLE_STEP = 3
"""Every this-many pixels is used to estimate the noise, for speed."""

MAXIMUM_STARS_MEASURED = 150
"""Brightest stars used for width, roundness and frame-to-frame matching."""

CENTROID_HALF_BOX_PIXELS = 7
"""Half-width of the box used to measure one star's centre and size."""

SATURATION_ADU = 65000
"""Pixels at or above this value count as saturated (16-bit frames)."""

LOW_STAR_FRACTION = 0.5
"""A frame with fewer stars than this fraction of the batch median is flagged.

On 2026-10-02 a typical frame held 3,200 to 3,550 bright regions. The frame
exposed during a two-minute drift held 783, under a quarter of that.
"""

TRAIL_MULTIPLE = 6.0
"""A longest streak above this multiple of the batch median is a trail."""

TRAIL_FLOOR_PIXELS = 60
"""The shortest streak that can count as a trail, whatever the batch median.

Clean frames of that session had a longest region of 29 to 44 pixels (the
largest bright stars). The trailed frame had one of 396.
"""

SOFT_FWHM_MULTIPLE = 1.25
"""A frame whose star width exceeds this multiple of the median is soft.

Frames with a 10% wider median star width on 2026-10-02 looked acceptable.
The clearly soft frame (frame 092, 5.4 pixels against 3.75) was 1.43 times
the median.
"""

ROUND_STAR_RATIO = 0.88
"""A frame whose median star roundness falls below this is elongated.

Roundness is the narrow width divided by the wide width, 1.0 for a circle.
Clean frames measured 0.93 to 0.99. A frame guided badly through a dither
measured 0.86.
"""

SHIFT_FRACTION_OF_WIDTH = 0.015
"""A move larger than this share of the frame width is flagged as a jump.

A deliberate dither of a few guide pixels moves the field 5 to 8 pixels
(0.2% of the 3008-pixel width). The field jump of 2026-10-02 moved it 375
pixels (12%).
"""

MINIMUM_MATCH_VOTES = 5
"""Matching star pairs needed before a frame-to-frame shift is trusted."""

_SHIFT_BIN_PIXELS = 4
"""Bin size of the offset histogram used to match star patterns."""


def _block_background(image: np.ndarray) -> np.ndarray:
    """Estimate the sky background as a map of block medians.

    Parameters
    ----------
    image : `numpy.ndarray`
        The 2-D pixel array.

    Returns
    -------
    background : `numpy.ndarray`
        An array the shape of `image`, constant inside each block. Edge
        pixels beyond the last whole block take the nearest block's value.
    """
    block = BACKGROUND_BLOCK_PIXELS
    rows, columns = image.shape[0] // block, image.shape[1] // block
    blocks = image[: rows * block, : columns * block].reshape(rows, block, columns, block)
    medians = np.median(blocks.swapaxes(1, 2).reshape(rows, columns, -1), axis=2)
    expanded = np.repeat(np.repeat(medians, block, axis=0), block, axis=1)
    pad_rows, pad_columns = image.shape[0] - expanded.shape[0], image.shape[1] - expanded.shape[1]
    return np.pad(expanded, ((0, pad_rows), (0, pad_columns)), mode="edge")


def _detect_stars(image: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Find the bright stars in one image.

    Parameters
    ----------
    image : `numpy.ndarray`
        The 2-D pixel array.

    Returns
    -------
    stars : `numpy.ndarray`
        Rows of ``[y, x, sigma_x, sigma_y]`` for the brightest stars with a
        measurable shape, centroids in pixels.
    blob_count : `int`
        Number of connected bright regions found (a count of stars that
        collapses when stars trail).
    longest_blob_pixels : `int`
        The longest extent of any bright region.
    """
    image = image.astype(np.float32, copy=False)
    background = _block_background(image)
    residual = image - background
    smooth = ndimage.gaussian_filter(residual, 1.5)
    sample = smooth[::NOISE_SAMPLE_STEP, ::NOISE_SAMPLE_STEP]
    noise = 1.4826 * np.median(np.abs(sample - np.median(sample)))

    labels, blob_count = ndimage.label(smooth > DETECTION_SIGMA * noise)
    extents = [
        max(box[0].stop - box[0].start, box[1].stop - box[1].start) for box in ndimage.find_objects(labels)
    ]
    longest_blob = int(max(extents)) if extents else 0

    is_peak = (smooth == ndimage.maximum_filter(smooth, 15)) & (smooth > DETECTION_SIGMA * noise)
    ys, xs = np.nonzero(is_peak)
    margin = CENTROID_HALF_BOX_PIXELS + 1
    inside = (xs > margin) & (xs < image.shape[1] - margin) & (ys > margin) & (ys < image.shape[0] - margin)
    ys, xs = ys[inside], xs[inside]
    brightest = np.argsort(smooth[ys, xs])[::-1][:MAXIMUM_STARS_MEASURED]

    half = CENTROID_HALF_BOX_PIXELS
    yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
    rows = []
    for y, x in zip(ys[brightest], xs[brightest], strict=True):
        patch = residual[y - half : y + half + 1, x - half : x + half + 1].clip(0)
        total = patch.sum()
        if total <= 0:
            continue
        centre_y, centre_x = (patch * yy).sum() / total, (patch * xx).sum() / total
        sigma_y = np.sqrt((patch * (yy - centre_y) ** 2).sum() / total)
        sigma_x = np.sqrt((patch * (xx - centre_x) ** 2).sum() / total)
        rows.append([y + centre_y, x + centre_x, sigma_x, sigma_y])
    return np.array(rows).reshape(-1, 4), int(blob_count), longest_blob


def _match_shift(previous_stars: np.ndarray, stars: np.ndarray) -> tuple[tuple[float, float] | None, int]:
    """Measure how far the star field moved between two frames.

    Every star in one frame is paired with every star in the other, and
    the offset most pairs agree on is taken as the move. A fixed sensor
    pattern would fool a correlation of the images; matching stars does
    not.

    Parameters
    ----------
    previous_stars, stars : `numpy.ndarray`
        Star rows from `_detect_stars` for the earlier and the later frame.

    Returns
    -------
    shift : `tuple` [`float`, `float`] or `None`
        The ``(dy, dx)`` move in pixels, or `None` if too few pairs agree.
    votes : `int`
        How many pairs agreed.
    """
    if len(previous_stars) < MINIMUM_MATCH_VOTES or len(stars) < MINIMUM_MATCH_VOTES:
        return None, 0
    offsets = (stars[None, :, :2] - previous_stars[:, None, :2]).reshape(-1, 2)
    extent = int(np.abs(offsets).max()) + _SHIFT_BIN_PIXELS
    bins = np.arange(-extent, extent + 1, _SHIFT_BIN_PIXELS)
    histogram, y_edges, x_edges = np.histogram2d(offsets[:, 0], offsets[:, 1], bins=[bins, bins])
    peak_y, peak_x = np.unravel_index(histogram.argmax(), histogram.shape)
    centre_y = y_edges[peak_y] + _SHIFT_BIN_PIXELS / 2
    centre_x = x_edges[peak_x] + _SHIFT_BIN_PIXELS / 2
    agree = (np.abs(offsets[:, 0] - centre_y) < 6) & (np.abs(offsets[:, 1] - centre_x) < 6)
    votes = int(agree.sum())
    if votes < MINIMUM_MATCH_VOTES:
        return None, votes
    return (float(offsets[agree, 0].mean()), float(offsets[agree, 1].mean())), votes


def measure_raw_frame(path: str) -> dict[str, Any]:
    """Measure one raw frame's stars, sky and saturation.

    Parameters
    ----------
    path : `str`
        Path to a FITS light frame.

    Returns
    -------
    measurement : `dict`
        ``star_count`` (connected bright regions), ``fwhm_px`` and
        ``roundness`` (medians over the brightest stars, `None` if none
        could be measured), ``longest_trail_px``, ``sky_median_adu``,
        ``saturated_pixels``, ``width_px``, and the private ``_stars`` array
        used for matching.
    """
    image = np.asarray(read_data(path), dtype=float)
    stars, blob_count, longest_blob = _detect_stars(image)
    fwhm = roundness = None
    if len(stars):
        fwhm = float(2.355 * np.median(np.sqrt(stars[:, 2] * stars[:, 3])))
        narrow = np.minimum(stars[:, 2], stars[:, 3])
        wide = np.maximum(stars[:, 2], stars[:, 3])
        roundness = float(np.median(narrow / np.maximum(wide, 1e-6)))
    return {
        "path": path,
        "star_count": blob_count,
        "fwhm_px": fwhm,
        "roundness": roundness,
        "longest_trail_px": longest_blob,
        "sky_median_adu": float(np.median(image)),
        "saturated_pixels": int((image >= SATURATION_ADU).sum()),
        "width_px": int(image.shape[1]),
        "_stars": stars,
    }


def flag_frames(measurements: Sequence[dict[str, Any]]) -> None:
    """Add a ``flags`` list to each measurement, judged against the batch.

    Parameters
    ----------
    measurements : `Sequence` [`dict`]
        Output of `measure_raw_frame`, in time order, each already holding
        ``shift_from_previous_px`` (or `None` for the first frame).
    """
    star_median = statistics.median(m["star_count"] for m in measurements)
    trail_median = statistics.median(m["longest_trail_px"] for m in measurements)
    widths = [m["fwhm_px"] for m in measurements if m["fwhm_px"] is not None]
    fwhm_median = statistics.median(widths) if widths else None

    for index, measurement in enumerate(measurements):
        flags: list[str] = []
        if index > 0 and measurement.get("shift_from_previous_px") is None:
            flags.append(
                "Could not match its stars to the previous frame: "
                "the field moved far or the stars are trailed."
            )
        if len(measurements) > 1 and measurement["star_count"] < LOW_STAR_FRACTION * star_median:
            flags.append(
                f"Only {measurement['star_count']} stars against a typical {star_median:.0f}: "
                "trailed or clouded."
            )
        trail_limit = max(TRAIL_FLOOR_PIXELS, TRAIL_MULTIPLE * trail_median)
        if len(measurements) > 1 and measurement["longest_trail_px"] > trail_limit:
            flags.append(f"A star streak {measurement['longest_trail_px']} px long: trailed.")
        if (
            len(measurements) > 1
            and fwhm_median is not None
            and measurement["fwhm_px"] is not None
            and measurement["fwhm_px"] > SOFT_FWHM_MULTIPLE * fwhm_median
        ):
            flags.append(
                f"Star width {measurement['fwhm_px']:.1f} px against a typical {fwhm_median:.1f}: soft."
            )
        if measurement["roundness"] is not None and measurement["roundness"] < ROUND_STAR_RATIO:
            flags.append(f"Stars are elongated (roundness {measurement['roundness']:.2f}).")
        shift = measurement.get("shift_from_previous_px")
        if (
            shift is not None
            and max(abs(shift[0]), abs(shift[1])) > SHIFT_FRACTION_OF_WIDTH * measurement["width_px"]
        ):
            flags.append(f"The field moved {shift[0]:+.0f}, {shift[1]:+.0f} px since the previous frame.")
        measurement["flags"] = flags


def check_raw_frames(
    paths: Sequence[str] | None = None, folder: str | None = None, last_count: int | None = None
) -> dict[str, Any]:
    """Check raw light frames and flag the ones that stand out.

    Parameters
    ----------
    paths : `Sequence` [`str`], optional
        Frames to check, in time order. Used instead of `folder` if given.
    folder : `str`, optional
        A folder of ``*.fits`` frames, checked in file-name order.
    last_count : `int`, optional
        Check only the newest this many frames of `folder`.

    Returns
    -------
    report : `dict`
        ``frames`` (one dict per frame: the measurements,
        ``shift_from_previous_px``, ``shift_matched_stars`` and ``flags``)
        and ``batch`` (frame count, median star count, median star width,
        flagged count).

    Raises
    ------
    ValueError
        If neither `paths` nor `folder` is given.
    """
    if paths is None:
        if folder is None:
            raise ValueError("Give either paths or a folder of FITS frames.")
        paths = sorted(glob.glob(os.path.join(folder, "*.fits")))
        if last_count:
            paths = paths[-last_count:]
    if not paths:
        return {"frames": [], "batch": {"frame_count": 0}}

    measurements = []
    previous_stars: np.ndarray | None = None
    for path in paths:
        measurement = measure_raw_frame(path)
        stars = measurement["_stars"]
        shift, votes = _match_shift(previous_stars, stars) if previous_stars is not None else (None, 0)
        measurement["shift_from_previous_px"] = list(shift) if shift is not None else None
        measurement["shift_matched_stars"] = votes
        previous_stars = stars
        measurements.append(measurement)

    flag_frames(measurements)
    for measurement in measurements:
        measurement.pop("_stars")
        measurement.pop("width_px")
    widths = [m["fwhm_px"] for m in measurements if m["fwhm_px"] is not None]
    return {
        "frames": measurements,
        "batch": {
            "frame_count": len(measurements),
            "median_star_count": statistics.median(m["star_count"] for m in measurements),
            "median_fwhm_px": statistics.median(widths) if widths else None,
            "flagged_count": sum(1 for m in measurements if m["flags"]),
        },
    }
