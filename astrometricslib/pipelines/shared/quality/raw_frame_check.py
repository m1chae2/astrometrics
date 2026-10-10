"""Purpose: Quick quality check of raw light frames, before any ingestion.

Description: During an observing session the raw frames sit on the
telescope computer or in a staging folder, not yet in the library. This
module measures each frame's stars (how many, how wide, how round, how
long the longest streak is), the sky level, the saturated pixels, any long
straight trail from a satellite or aircraft, and how far the star field
moved since the previous frame. It then flags the frames that stand out
from the rest of the batch: too few or too many stars, trailed, soft,
crossed by a straight trail, or shifted a long way.

Most limits compare a frame with the median of its own batch, so they adapt
to the telescope, the camera and the night. The numeric limits below record
how they were chosen and which data they were checked against. Two limits
are not batch-relative. The saturation level comes from the camera's
profile (a 14-bit camera clips near 16383 ADU, far below a 16-bit camera's
65535 ADU), and the result records where it came from. The straight-trail
detector looks at one frame alone, because a faint trail can lift a frame's
star count without being long enough to trip the streak limit.
"""

import glob
import logging
import os
import statistics
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy import ndimage

from astrometricslib.drivers.fits_access import open_primary_hdu
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.shared.quality.spectral_frame_check import resolve_saturation_threshold

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

LOW_STAR_FRACTION = 0.5
"""A frame with fewer stars than this fraction of the batch median is flagged.

On 2026-10-02 a typical frame held 3,200 to 3,550 bright regions. The frame
exposed during a two-minute drift held 783, under a quarter of that.
"""

HIGH_STAR_MULTIPLE = 1.4
"""A frame with more stars than this multiple of the batch median is flagged.

This is a designed value, not one fitted to data. It mirrors the low-count
rule: the low rule flags a count under 0.5 times the median (a drop of a
half), and this one flags a count over 1.4 times the median, a rise large
enough that ordinary scatter between frames cannot reach it (the four clean
M 13 lights held 602 to 635 regions, within 4% of the median). A frame that
crosses it usually has extra bright regions that are not stars, such as a
satellite or aircraft trail, a light leak or a burst of hot pixels. On the
M 13 lights the one frame with a faint trail held 934 regions against a
median of 624 (1.50 times), and no other frame came near the limit.
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

TRAIL_MIN_DIAGONAL_FRACTION = 0.25
"""A straight trail must be at least this fraction of the frame diagonal.

Configurable per call (``trail_min_diagonal_fraction``). A quarter of the
diagonal is 1,063 pixels on a 3008 x 3008 frame. Satellite and aircraft
trails usually cross most of a frame. The longest straight chains that stars
and noise line up into on the clean M 13 lights ran 20 to 228 pixels (376
on the spectroscopy frame, where the spectrum itself is a straight streak).
The trailed light gave 3,220 pixels.
"""

TRAIL_BLOCK_PIXELS = 4
"""Side of the square blocks averaged before looking for straight trails.

A trail about 4 pixels wide is about one block wide after averaging, so its
signal-to-noise ratio rises by about the block side (4 times) while the frame
shrinks 16-fold, which also makes the line search fast.
"""

TRAIL_THRESHOLD_SIGMA = 3.0
"""A block counts as bright when it is this many block-noise units high.

The block noise is the robust spread of the block averages. On the M 13
light with the faint trail the trail blocks stood well above this level
(the trail had a peak of about 40 noise units after the smoothing used for
star detection, and block averaging raises its signal-to-noise further),
while pure noise passes it in 0.13% of blocks.
"""

TRAIL_STAR_SIGMA = 60.0
"""Blocks near smoothed values above this many noise units are masked as stars.

The cut-off is above the faint trail on purpose. On the M 13 light with the
trail, the 353 detection regions on the trail had a median peak of 42 noise
units and a 90th percentile of 50. Half of all other regions peaked above
about 95. A lower cut-off (for example the 8 noise units that first came to
mind) masks the trail itself and hides it from the line search.
"""

TRAIL_STAR_MARGIN_BLOCKS = 2
"""Blocks added around each masked star, to cover its halo (8 pixels)."""

TRAIL_ANGLE_STEP_DEGREES = 0.5
"""Spacing of the line angles tried by the line search."""

TRAIL_STRIP_HALF_WIDTH_BLOCKS = 1.5
"""A bright block lies on a candidate line when it is this close to it."""

TRAIL_MAXIMUM_GAP_BLOCKS = 4
"""Longest stretch of a trail that may be missing (16 pixels).

Gaps appear where the trail dips below the threshold or passes behind a
masked star. A longer gap ends the trail.
"""

TRAIL_MINIMUM_HIT_FRACTION = 0.5
"""At least this share of the blocks along a trail must be bright.

The rest may lie under masked stars. This stops a line from being claimed
through a dense star field that happens to be masked end to end.
"""

TRAIL_CANDIDATE_LINES = 5
"""How many of the strongest lines in the line search are checked in full."""

TRAIL_MAXIMUM_BRIGHT_FRACTION = 0.2
"""If more of the blocks than this are bright, the line search is skipped.

A frame that bright (a bad flat field, thick cloud or a light leak) has no
trail to find, and its many bright blocks would only slow the search.
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


def _bright_block_map(
    residual: np.ndarray, smooth: np.ndarray, noise: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """Average the frame in blocks and split the bright blocks from the stars.

    Parameters
    ----------
    residual : `numpy.ndarray`
        The frame with its sky background subtracted.
    smooth : `numpy.ndarray`
        The same, smoothed (the image the star detection works on).
    noise : `float`
        The noise of `smooth`, in ADU.

    Returns
    -------
    bright : `numpy.ndarray`
        Boolean block map, `True` where the block average is above the
        trail threshold and the block is not inside a star.
    star : `numpy.ndarray`
        Boolean block map of the masked stars and their halos.

    Both maps are `None` together when the frame is too empty or too bright
    for a line search to mean anything.
    """
    block = TRAIL_BLOCK_PIXELS
    rows, columns = residual.shape[0] // block, residual.shape[1] // block
    if rows < 8 or columns < 8:
        return None
    crop = (slice(0, rows * block), slice(0, columns * block))
    average = residual[crop].reshape(rows, block, columns, block).mean(axis=(1, 3))
    peak = smooth[crop].reshape(rows, block, columns, block).max(axis=(1, 3))
    block_noise = 1.4826 * np.median(np.abs(average - np.median(average)))
    if not block_noise > 0:
        return None
    margin = 2 * TRAIL_STAR_MARGIN_BLOCKS + 1
    star = ndimage.binary_dilation(peak > TRAIL_STAR_SIGMA * noise, structure=np.ones((margin, margin)))
    bright = (average > TRAIL_THRESHOLD_SIGMA * block_noise) & ~star
    if bright.mean() > TRAIL_MAXIMUM_BRIGHT_FRACTION or bright.sum() < 2:
        return None
    return bright, star


def _strongest_lines(xs: np.ndarray, ys: np.ndarray, diagonal: int) -> list[tuple[float, float]]:
    """Find the lines that pass through the most of the given points.

    This is a Hough transform. Every point votes for each line through it.
    A line is described by its angle ``theta`` and its distance ``rho`` from
    the origin, with ``rho = x cos(theta) + y sin(theta)``. Points on one
    straight line all vote for the same ``(theta, rho)`` cell.

    Parameters
    ----------
    xs, ys : `numpy.ndarray`
        Block coordinates of the bright blocks.
    diagonal : `int`
        Length of the block map's diagonal, rounded up. It sets the range
        of ``rho``.

    Returns
    -------
    lines : `list` [`tuple` [`float`, `float`]]
        ``(theta, rho)`` of up to `TRAIL_CANDIDATE_LINES` strongest cells,
        strongest first, with ``theta`` in radians.
    """
    angles = np.deg2rad(np.arange(0.0, 180.0, TRAIL_ANGLE_STEP_DEGREES))
    bins = 2 * diagonal + 1
    votes = np.zeros((len(angles), bins), dtype=np.int32)
    for row, angle in enumerate(angles):
        rho = np.rint(xs * np.cos(angle) + ys * np.sin(angle)).astype(np.int64) + diagonal
        votes[row] = np.bincount(rho, minlength=bins)
    # A trail a couple of blocks wide spreads over neighbouring distances,
    # so each cell also counts the cells either side of it.
    votes = votes + np.roll(votes, 1, axis=1) + np.roll(votes, -1, axis=1)
    lines = []
    suppress = round(2.0 / TRAIL_ANGLE_STEP_DEGREES)
    for _ in range(TRAIL_CANDIDATE_LINES):
        row, column = np.unravel_index(votes.argmax(), votes.shape)
        if votes[row, column] <= 0:
            break
        lines.append((float(angles[row]), float(column - diagonal)))
        votes[max(row - suppress, 0) : row + suppress + 1, max(column - 3, 0) : column + 4] = 0
    return lines


def _trail_along_line(
    theta: float, rho: float, bright_xy: np.ndarray, star_xy: np.ndarray
) -> tuple[float, float] | None:
    """Measure the straight trail that follows one candidate line.

    The line is first refined by fitting the bright blocks close to it.
    Then the blocks along the refined line are walked in one-block steps.
    A step counts as covered when it holds a bright block or a masked star.
    The trail is the longest run of covered steps with no gap longer than
    `TRAIL_MAXIMUM_GAP_BLOCKS`, from its first to its last bright block.

    Parameters
    ----------
    theta, rho : `float`
        The candidate line from `_strongest_lines` (radians and blocks).
    bright_xy, star_xy : `numpy.ndarray`
        ``(n, 2)`` arrays of ``(x, y)`` block coordinates of the bright
        blocks and of the masked-star blocks.

    Returns
    -------
    trail : `tuple` [`float`, `float`] or `None`
        ``(length, angle)``: the trail length in blocks and the line
        direction in degrees from the +x axis towards +y (down the array),
        in [0, 180). `None` if the line is not a trail.
    """
    normal = np.array([np.cos(theta), np.sin(theta)])
    near = bright_xy[np.abs(bright_xy @ normal - rho) <= 2 * TRAIL_STRIP_HALF_WIDTH_BLOCKS]
    if len(near) < 10:
        return None
    centre = near.mean(axis=0)
    direction = np.linalg.svd(near - centre, full_matrices=False)[2][0]
    normal = np.array([-direction[1], direction[0]])

    def positions(points: np.ndarray) -> np.ndarray:
        """Find where along the line the points inside its strip lie.

        Parameters
        ----------
        points : `numpy.ndarray`
            ``(n, 2)`` array of ``(x, y)`` block coordinates.

        Returns
        -------
        along : `numpy.ndarray`
            Distance along the line, in blocks, of each point within
            `TRAIL_STRIP_HALF_WIDTH_BLOCKS` of the line.
        """
        offsets = (points - centre) @ normal
        inside = np.abs(offsets) <= TRAIL_STRIP_HALF_WIDTH_BLOCKS
        return (points[inside] - centre) @ direction

    bright_t, star_t = positions(bright_xy), positions(star_xy)
    if len(bright_t) < 10:
        return None
    start = int(np.floor(min(bright_t.min(), star_t.min() if len(star_t) else np.inf)))
    stop = int(np.ceil(max(bright_t.max(), star_t.max() if len(star_t) else -np.inf)))
    size = stop - start + 1
    hit = np.zeros(size, dtype=bool)
    hit[np.floor(bright_t).astype(int) - start] = True
    covered = hit.copy()
    if len(star_t):
        covered[np.floor(star_t).astype(int) - start] = True

    steps = np.flatnonzero(covered)
    breaks = np.flatnonzero(np.diff(steps) > TRAIL_MAXIMUM_GAP_BLOCKS + 1) + 1
    best_length = 0
    for chain in np.split(steps, breaks):
        hits = chain[hit[chain]]
        if len(hits) == 0:
            continue
        length = int(hits[-1] - hits[0]) + 1
        if len(hits) >= TRAIL_MINIMUM_HIT_FRACTION * length and length > best_length:
            best_length = length
    if best_length == 0:
        return None
    angle = float(np.degrees(np.arctan2(direction[1], direction[0])) % 180.0)
    return float(best_length), angle


def _detect_straight_trail(
    residual: np.ndarray, smooth: np.ndarray, noise: float, minimum_fraction: float
) -> tuple[int, float] | None:
    """Look for a long, faint, straight trail such as a satellite or aircraft.

    The frame is averaged in 4 x 4 blocks, which brings a faint trail up
    out of the noise. Blocks above a low threshold are kept, except those
    inside stars. Straight lines through the kept blocks are found with a
    Hough transform (see `_strongest_lines`), and the strongest ones are
    followed along their length by `_trail_along_line`. The line search is
    plain NumPy. It is used instead of scikit-image's line finder because
    scikit-image is not a declared dependency of this package, and because
    this version returns one complete trail instead of several overlapping
    pieces.

    Parameters
    ----------
    residual : `numpy.ndarray`
        The frame with its sky background subtracted.
    smooth : `numpy.ndarray`
        The same, smoothed (the image the star detection works on).
    noise : `float`
        The noise of `smooth`, in ADU.
    minimum_fraction : `float`
        The shortest trail that counts, as a fraction of the frame diagonal.

    Returns
    -------
    trail : `tuple` [`int`, `float`] or `None`
        ``(length_px, angle_degrees)`` of the longest trail, or `None` if
        there is none. The angle runs from the +x axis (along the rows)
        towards +y (down the array), in [0, 180).
    """
    maps = _bright_block_map(residual, smooth, noise)
    if maps is None:
        return None
    bright, star = maps
    ys, xs = np.nonzero(bright)
    diagonal = int(np.ceil(np.hypot(*bright.shape)))
    bright_xy = np.column_stack([xs, ys]).astype(float)
    star_y, star_x = np.nonzero(star)
    star_xy = np.column_stack([star_x, star_y]).astype(float)

    best: tuple[float, float] | None = None
    for theta, rho in _strongest_lines(xs, ys, diagonal):
        trail = _trail_along_line(theta, rho, bright_xy, star_xy)
        if trail is not None and (best is None or trail[0] > best[0]):
            best = trail
    if best is None:
        return None
    length_px = best[0] * TRAIL_BLOCK_PIXELS
    if length_px < minimum_fraction * np.hypot(*residual.shape):
        return None
    return round(length_px), best[1]


def _detect_stars(
    image: np.ndarray, trail_min_diagonal_fraction: float = TRAIL_MIN_DIAGONAL_FRACTION
) -> tuple[np.ndarray, int, int, tuple[int, float] | None]:
    """Find the bright stars, and any long straight trail, in one image.

    Parameters
    ----------
    image : `numpy.ndarray`
        The 2-D pixel array.
    trail_min_diagonal_fraction : `float`, optional
        The shortest straight trail that counts, as a fraction of the frame
        diagonal. See `TRAIL_MIN_DIAGONAL_FRACTION`.

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
    straight_trail : `tuple` [`int`, `float`] or `None`
        ``(length_px, angle_degrees)`` of the longest faint straight trail
        (see `_detect_straight_trail`), or `None` if there is none.
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
    trail = _detect_straight_trail(residual, smooth, noise, trail_min_diagonal_fraction)
    return np.array(rows).reshape(-1, 4), int(blob_count), longest_blob, trail


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


def measure_raw_frame(
    path: str, trail_min_diagonal_fraction: float = TRAIL_MIN_DIAGONAL_FRACTION
) -> dict[str, Any]:
    """Measure one raw frame's stars, sky, saturation and straight trails.

    Parameters
    ----------
    path : `str`
        Path to a FITS light frame.
    trail_min_diagonal_fraction : `float`, optional
        The shortest straight trail that counts, as a fraction of the frame
        diagonal (default `TRAIL_MIN_DIAGONAL_FRACTION`).

    Returns
    -------
    measurement : `dict`
        ``star_count`` (connected bright regions), ``fwhm_px`` and
        ``roundness`` (medians over the brightest stars, `None` if none
        could be measured), ``longest_trail_px`` (the longest bright
        region), ``line_trail_px`` and ``line_trail_angle_deg`` (the faint
        straight trail, 0 and `None` if there is none), ``sky_median_adu``,
        ``saturated_pixels``, ``saturation_threshold_adu`` and
        ``saturation_threshold_source`` (the level used and where it came
        from), ``camera`` (the header's ``INSTRUME`` text, or `None`),
        ``width_px``, and the private ``_stars`` array used for matching.
    """
    with open_primary_hdu(path) as hdu:
        image = np.asarray(hdu.data, dtype=float)
        camera = hdu.header.get("INSTRUME")
    camera = str(camera) if camera else None
    stars, blob_count, longest_blob, straight_trail = _detect_stars(image, trail_min_diagonal_fraction)
    threshold_adu, threshold_source = resolve_saturation_threshold(camera)
    fwhm = roundness = None
    if len(stars):
        fwhm = float(2.355 * np.median(np.sqrt(stars[:, 2] * stars[:, 3])))
        narrow = np.minimum(stars[:, 2], stars[:, 3])
        wide = np.maximum(stars[:, 2], stars[:, 3])
        roundness = float(np.median(narrow / np.maximum(wide, 1e-6)))
    return {
        "path": path,
        "camera": camera,
        "star_count": blob_count,
        "fwhm_px": fwhm,
        "roundness": roundness,
        "longest_trail_px": longest_blob,
        "line_trail_px": straight_trail[0] if straight_trail else 0,
        "line_trail_angle_deg": straight_trail[1] if straight_trail else None,
        "sky_median_adu": float(np.median(image)),
        "saturated_pixels": int((image >= threshold_adu).sum()),
        "saturation_threshold_adu": float(threshold_adu),
        "saturation_threshold_source": threshold_source,
        "width_px": int(image.shape[1]),
        "_stars": stars,
    }


def flag_frames(measurements: Sequence[dict[str, Any]]) -> None:
    """Add a ``flags`` list to each measurement, judged against the batch.

    A frame is flagged when it shows any of these: a star count under
    `LOW_STAR_FRACTION` or over `HIGH_STAR_MULTIPLE` times the batch median,
    a faint straight trail, a bright streak much longer than the batch's,
    stars much wider or less round than the batch's, a lost match to the
    previous frame, or a large move since it.

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
        if len(measurements) > 1 and measurement["star_count"] > HIGH_STAR_MULTIPLE * star_median:
            flags.append(
                f"{measurement['star_count']} stars against a typical {star_median:.0f}: "
                "more bright regions than stars alone explain (a trail, light leak or hot pixels)."
            )
        if measurement.get("line_trail_px"):
            flags.append(
                f"A faint straight trail {measurement['line_trail_px']} px long at "
                f"{measurement['line_trail_angle_deg']:.1f} degrees: a satellite or aircraft."
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
    paths: Sequence[str] | None = None,
    folder: str | None = None,
    last_count: int | None = None,
    trail_min_diagonal_fraction: float = TRAIL_MIN_DIAGONAL_FRACTION,
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
    trail_min_diagonal_fraction : `float`, optional
        The shortest faint straight trail that counts, as a fraction of the
        frame diagonal (default `TRAIL_MIN_DIAGONAL_FRACTION`).

    Returns
    -------
    report : `dict`
        ``frames`` (one dict per frame: the measurements,
        ``shift_from_previous_px``, ``shift_matched_stars`` and ``flags``)
        and ``batch`` (frame count, median star count, median star width,
        flagged count).

    Raises
    ------
    InvalidArgumentError
        If neither `paths` nor `folder` is given.
    """
    if paths is None:
        if folder is None:
            raise InvalidArgumentError("Give either paths or a folder of FITS frames.")
        paths = sorted(glob.glob(os.path.join(folder, "*.fits")))
        if last_count:
            paths = paths[-last_count:]
    if not paths:
        return {"frames": [], "batch": {"frame_count": 0}}

    measurements = []
    previous_stars: np.ndarray | None = None
    for path in paths:
        measurement = measure_raw_frame(path, trail_min_diagonal_fraction)
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
