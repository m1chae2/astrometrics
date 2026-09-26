"""Line up the stacked image of each exposure group before they are combined.

Siril registers the frames of one group to that group's own reference frame,
so two group stacks of the same field are not lined up with each other. On the
2026 NGC 2244, NGC 2903 and NGC 2403 sessions they were off by 6 to 7 pixels
(2.5, -6.4), (5.1, -6.2) and (0.6, 7.0), and combining them as they were would
smear every star. This module finds the offset of each group stack against a
reference group stack and moves it onto the reference.

Only a shift is measured. Frames in one session share a rotation, and a
shift-only alignment matches what the spectral registration already assumes
(see `run_siril_stack`). A group whose offset cannot be found is reported, so
the caller can leave it out rather than blur the stack with it.

Everything here works on arrays and returns arrays; reading and writing files
is left to the caller.
"""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.ndimage import shift as shift_image
from skimage.registration import phase_cross_correlation

# Structure wider than this many pixels (background gradients, nebulosity) is
# removed before the offset is measured, so stars decide it. Stars in these
# stacks have a FWHM of about 3 px (2.8-3.8 px, see
# `logs/stack_exposure_groups_20260920.json`); 8 px is a little under three
# FWHM, wide enough to leave a star's profile alone.
ALIGNMENT_HIGH_PASS_SIGMA_PIXELS = 8.0

# After high-pass filtering, values are clipped at this many noise sigmas so
# that a few saturated stars do not decide the offset on their own.
ALIGNMENT_CLIP_SIGMAS = 10.0

# The offset is measured to 1/20 of a pixel. Real offsets between group
# stacks are several pixels, and a 0.05 px error blurs a star's profile by
# far less than the seeing does.
ALIGNMENT_UPSAMPLE_FACTOR = 20

# An offset smaller than this is not applied: resampling smooths the image, and
# a shift this small does not change where a star sits.
NEGLIGIBLE_SHIFT_PIXELS = 0.05

# The offset is trusted when, after moving one stack onto the other, the two
# filtered images correlate at least this well. Measured on real pairs of
# stacks of the same field (NGC 2244, NGC 2903, NGC 2403, NGC 1893 and the
# halves of one group) the correlation was 0.55 to 0.96; on two stacks of
# different fields, where no true offset exists, it was 0.03 to 0.09. 0.3 sits
# between the two groups.
MINIMUM_ALIGNMENT_CORRELATION = 0.3

# A pixel counts as covered by the shifted image where a mask of ones, shifted
# the same way, is still this close to one. (Linear interpolation of the mask
# makes the border pixels partial values.)
COVERED_PIXEL_THRESHOLD = 0.999

# For a spectral target, the offset between two group stacks is measured on
# only the central share of each dimension, not the whole frame. A slitless
# spectrum's dispersed trail can run at any angle (see
# `SpectroscopyConfig.dispersion_orientation`/`dispersion_angle_degrees`), so
# its position within the frame cannot be predicted from the pipeline here,
# but the star that casts it is framed near the centre of the field the same
# way any target is. On a real stacked Albireo frame (3008 x 3008 px, see
# `logs/stack_AlbireoPhaseCorrTest4...log`) essentially all of the signal --
# the star's zero order and the brightest part of its trail -- fell within
# the central 50% of the frame; the rest was empty sky, whose noise pattern
# differs between exposure groups and would otherwise be free to dominate the
# correlation. Not yet validated on other spectral sessions.
SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION = 0.5

# Aligning spectral group stacks on the zero-order star instead of on the
# image as a whole. The dispersed trail is a long streak, so shifting an image
# along it barely changes how well two stacks match, and a saturated long
# exposure and a faint short one look too different for the correlation to
# lock on. On the 2026 Vega group stacks the 0.05 s group's true offset from
# the 5 s group was about (+6 rows, +26 columns), where the correlation
# reported (-39, -5), and the 0.1 s group's came out 395 px off; the star's own
# position in each stack agreed to 1 px across the 1 s to 5 s groups. The star
# is found as the brightest spot of the image smoothed by this many pixels
# (seeing spreads the star over a few pixels).
ZERO_ORDER_SMOOTHING_SIGMA_PIXELS = 2.0

# The star is looked for only within this many pixels of the centre of the
# image, because a saturated long-exposure stack has a second spot as bright as
# the star: its dispersed trail, about 350 px below the star. On the Vega 5 s
# stack the brightest smoothed spot in the central half of the frame was the
# trail (0.76 of full scale, against 0.77 for the star), and the trail beat the
# star outright in the 1 s stack. The spectroscopy target is framed near the
# image centre (the same assumption `SPECTRAL_ALIGNMENT_CENTER_CROP_FRACTION`
# makes): on the Vega nights the star sat within 35 px of it. 150 px covers
# four times that and stays well inside the 350 px to the trail. A target
# framed farther from the centre is not found, and the images are correlated
# instead.
ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS = 150

# The star's centre is the brightness-weighted centre of the pixels within this
# many pixels of the brightest spot that are at least
# ZERO_ORDER_CENTROID_FRACTION_OF_PEAK of it. A saturated star is a flat
# plateau, where the brightest single pixel is arbitrary but the centre of the
# plateau is not. The window is wider than a saturated plateau (about 15 px
# across on the Vega 5 s stack, unmeasured elsewhere) and far narrower than the
# 40 px separation below.
ZERO_ORDER_CENTROID_HALF_WINDOW_PIXELS = 25
ZERO_ORDER_CENTROID_FRACTION_OF_PEAK = 0.5

# The star is only believed when nothing else at least this far away is
# brighter than ZERO_ORDER_MAX_RIVAL_FRACTION of it. A stack with two similar
# bright spots (a double star, or the trail as bright as the star) is
# ambiguous, and then the caller falls back to correlating the images.
# Unvalidated choices: on the Vega group stacks the brightest spot away from
# the star was well under half the star's brightness.
ZERO_ORDER_RIVAL_SEPARATION_PIXELS = 40
ZERO_ORDER_MAX_RIVAL_FRACTION = 0.5


@dataclass
class AlignmentResult:
    """How one image lines up with the reference.

    Attributes
    ----------
    shift_rows_pixels : `float`
        How far the image must move along the rows (the y axis) to line up
        with the reference. Positive moves it toward higher rows.
    shift_columns_pixels : `float`
        How far the image must move along the columns (the x axis).
    correlation : `float`
        How well the two filtered images agree once the image is moved. Near
        one is a match; near zero means the offset found is not real.
    is_star_based : `bool`
        `True` when the offset is the difference between the two images'
        zero-order star positions (see `find_zero_order_position`) rather
        than the result of correlating the images. Such an offset is trusted
        without the correlation test, since the images may look very
        different (a saturated long exposure against a faint short one).
    """

    shift_rows_pixels: float
    shift_columns_pixels: float
    correlation: float
    is_star_based: bool = False

    @property
    def trusted(self) -> bool:
        """Whether the correlation is high enough to believe the offset.

        Returns
        -------
        trusted : `bool`
            `True` for a star-based offset, otherwise `True` at or above
            `MINIMUM_ALIGNMENT_CORRELATION`.
        """
        return self.is_star_based or self.correlation >= MINIMUM_ALIGNMENT_CORRELATION


def _to_plane(image: np.ndarray) -> np.ndarray:
    """Reduce a possibly colour image to one plane for measuring.

    Returns
    -------
    plane : `numpy.ndarray`
        The image itself when it is 2-D, otherwise the mean over its first
        (colour) axis, as `float32`.
    """
    array = np.asarray(image, dtype=np.float32)
    return array if array.ndim == 2 else array.mean(axis=0)


def _center_crop(plane: np.ndarray, crop_fraction: float) -> np.ndarray:
    """Take the central share of a 2-D array, along each axis separately.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D array. It does not need to be square.
    crop_fraction : `float`
        The share of each dimension to keep, from 0 (exclusive) to 1. A
        value of 0.5 keeps the central half of the rows and the central half
        of the columns.

    Returns
    -------
    cropped : `numpy.ndarray`
        A view onto the central region of `plane`.
    """
    height, width = plane.shape
    row_margin = round(height * (1.0 - crop_fraction) / 2.0)
    column_margin = round(width * (1.0 - crop_fraction) / 2.0)
    return plane[row_margin : height - row_margin, column_margin : width - column_margin]


def _filtered(plane: np.ndarray) -> np.ndarray:
    """Remove wide structure and tame the brightest stars.

    Returns
    -------
    filtered : `numpy.ndarray`
        The plane minus its Gaussian blur, clipped at
        `ALIGNMENT_CLIP_SIGMAS` robust sigmas.
    """
    filtered = plane - gaussian_filter(plane, ALIGNMENT_HIGH_PASS_SIGMA_PIXELS)
    sigma = 1.4826 * float(np.median(np.abs(filtered - np.median(filtered))))
    if sigma > 0:
        np.clip(filtered, -ALIGNMENT_CLIP_SIGMAS * sigma, ALIGNMENT_CLIP_SIGMAS * sigma, out=filtered)
    return filtered


def find_zero_order_position(plane: np.ndarray) -> tuple[float, float] | None:
    """Find the star that casts a slitless spectrum in a stacked image.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D stacked image (already cropped to where the star can be).

    Returns
    -------
    position : `tuple` [`float`, `float`] or `None`
        The star's (row, column), or `None` when the centre of the image
        has no bright spot, or has a second spot too bright to tell which is
        the star (see `ZERO_ORDER_MAX_RIVAL_FRACTION`).
    """
    smooth = gaussian_filter(
        np.nan_to_num(np.asarray(plane, dtype=np.float32)), ZERO_ORDER_SMOOTHING_SIGMA_PIXELS
    )
    rows, columns = np.ogrid[: smooth.shape[0], : smooth.shape[1]]
    in_search_window = (np.abs(rows - (smooth.shape[0] - 1) / 2.0) <= ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS) & (
        np.abs(columns - (smooth.shape[1] - 1) / 2.0) <= ZERO_ORDER_SEARCH_HALF_WIDTH_PIXELS
    )
    searched = np.where(in_search_window, smooth, 0.0)
    peak_row, peak_column = np.unravel_index(int(np.argmax(searched)), searched.shape)
    peak = float(searched[peak_row, peak_column])
    if peak <= 0:
        return None
    distance_squared = (rows - peak_row) ** 2 + (columns - peak_column) ** 2
    far_away = distance_squared > ZERO_ORDER_RIVAL_SEPARATION_PIXELS**2
    rival = float(searched[far_away & in_search_window].max(initial=0.0))
    if rival > ZERO_ORDER_MAX_RIVAL_FRACTION * peak:
        return None
    half = ZERO_ORDER_CENTROID_HALF_WINDOW_PIXELS
    row_slice = slice(max(peak_row - half, 0), min(peak_row + half + 1, smooth.shape[0]))
    column_slice = slice(max(peak_column - half, 0), min(peak_column + half + 1, smooth.shape[1]))
    window = smooth[row_slice, column_slice]
    weights = np.where(window >= ZERO_ORDER_CENTROID_FRACTION_OF_PEAK * peak, window, 0.0)
    window_rows, window_columns = np.mgrid[row_slice, column_slice]
    total = float(weights.sum())
    return (
        float((weights * window_rows).sum() / total),
        float((weights * window_columns).sum() / total),
    )


def measure_alignment(
    reference: np.ndarray,
    image: np.ndarray,
    crop_fraction: float | None = None,
    prefer_star_position: bool = False,
) -> AlignmentResult:
    """Find the shift that puts `image` onto `reference`.

    Parameters
    ----------
    reference : `numpy.ndarray`
        The stack to line up with, 2-D or colour (channels first).
    image : `numpy.ndarray`
        The stack to move, the same shape as `reference`.
    crop_fraction : `float` or `None`, optional
        When given, only the central share of each dimension (see
        `_center_crop`) is used to measure the offset, though the offset
        found still applies to the whole image. Cropping to the same window
        in both images does not change the offset between them; it only
        keeps frame edges and empty sky from influencing it. `None` measures
        on the whole frame, as before.
    prefer_star_position : `bool`, optional
        When `True`, the offset is first taken from the zero-order star's
        position in each image (see `find_zero_order_position`), which
        works when the two images look very different. If either image has
        no clear star, the images are correlated as usual.

    Returns
    -------
    result : `AlignmentResult`
        The shift and how far it can be trusted.

    Raises
    ------
    ValueError
        If the two images do not have the same shape.
    """
    if np.shape(reference) != np.shape(image):
        raise ValueError("The images to align must have the same shape.")
    reference_plane = _to_plane(reference)
    image_plane = _to_plane(image)
    if crop_fraction is not None:
        reference_plane = _center_crop(reference_plane, crop_fraction)
        image_plane = _center_crop(image_plane, crop_fraction)
    fixed = _filtered(reference_plane)
    moving = _filtered(image_plane)
    star_positions = None
    if prefer_star_position:
        reference_star = find_zero_order_position(reference_plane)
        image_star = find_zero_order_position(image_plane)
        if reference_star is not None and image_star is not None:
            star_positions = (reference_star, image_star)
    if star_positions is not None:
        shift = np.array([
            star_positions[0][0] - star_positions[1][0],
            star_positions[0][1] - star_positions[1][1],
        ])
    else:
        shift, _, _ = phase_cross_correlation(
            fixed, moving, upsample_factor=ALIGNMENT_UPSAMPLE_FACTOR, normalization=None
        )
    is_star_based = star_positions is not None
    moved = shift_image(moving, shift, order=1, mode="constant", cval=0.0)
    covered = (
        shift_image(np.ones_like(moving), shift, order=1, mode="constant", cval=0.0) > COVERED_PIXEL_THRESHOLD
    )
    if not covered.any():
        return AlignmentResult(float(shift[0]), float(shift[1]), 0.0, is_star_based)
    correlation = float(np.corrcoef(fixed[covered], moved[covered])[0, 1])
    if not np.isfinite(correlation):
        correlation = 0.0
    return AlignmentResult(float(shift[0]), float(shift[1]), correlation, is_star_based)


def apply_shift(
    image: np.ndarray, shift_rows_pixels: float, shift_columns_pixels: float, order: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Move an image by a shift, and say which pixels the move filled in.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image, 2-D or colour (channels first).
    shift_rows_pixels : `float`
        The shift along the rows.
    shift_columns_pixels : `float`
        The shift along the columns.
    order : `int`, optional
        The spline order of the resampling: 3 (cubic) for an image, 1 for a
        map of counts that must not overshoot.

    Returns
    -------
    shifted : `numpy.ndarray`
        The moved image (`float32`), zero where the move brought in nothing.
    covered : `numpy.ndarray`
        A 2-D boolean mask, `True` where `shifted` holds real data.
    """
    array = np.asarray(image, dtype=np.float32)
    plane_shape = array.shape[-2:]
    covered = (
        shift_image(
            np.ones(plane_shape, dtype=np.float32),
            (shift_rows_pixels, shift_columns_pixels),
            order=1,
            mode="constant",
            cval=0.0,
        )
        > COVERED_PIXEL_THRESHOLD
    )
    if (
        abs(shift_rows_pixels) < NEGLIGIBLE_SHIFT_PIXELS
        and abs(shift_columns_pixels) < NEGLIGIBLE_SHIFT_PIXELS
    ):
        return array.copy(), np.ones(plane_shape, dtype=bool)
    offsets = (0.0,) * (array.ndim - 2) + (shift_rows_pixels, shift_columns_pixels)
    shifted = shift_image(array, offsets, order=order, mode="constant", cval=0.0).astype(np.float32)
    return shifted, covered


def align_images_to_reference(
    images: list[np.ndarray],
    reference_index: int,
    crop_fraction: float | None = None,
    prefer_star_position: bool = False,
) -> tuple[list[np.ndarray | None], list[np.ndarray | None], list[AlignmentResult | None]]:
    """Line up every image with the reference image.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The group stacks, all the same shape.
    reference_index : `int`
        Which image the others are moved onto. It is returned unchanged.
    crop_fraction : `float` or `None`, optional
        Passed to `measure_alignment` for every pair; see there. The images
        themselves are never cropped, only the region used to measure the
        offset between them.
    prefer_star_position : `bool`, optional
        Passed to `measure_alignment` for every pair; see there.

    Returns
    -------
    aligned : `list` [`numpy.ndarray` or `None`]
        Each image on the reference's pixel grid, or `None` for an image whose
        offset could not be trusted.
    covered : `list` [`numpy.ndarray` or `None`]
        For each image, the mask of pixels that hold real data (all `True` for
        the reference), or `None` where the image was left out.
    results : `list` [`AlignmentResult` or `None`]
        The measured offsets; `None` for the reference itself.
    """
    aligned: list[np.ndarray | None] = []
    covered_masks: list[np.ndarray | None] = []
    results: list[AlignmentResult | None] = []
    for index, image in enumerate(images):
        if index == reference_index:
            aligned.append(np.asarray(image, dtype=np.float32))
            covered_masks.append(np.ones(np.shape(image)[-2:], dtype=bool))
            results.append(None)
            continue
        result = measure_alignment(
            images[reference_index],
            image,
            crop_fraction=crop_fraction,
            prefer_star_position=prefer_star_position,
        )
        results.append(result)
        if not result.trusted:
            aligned.append(None)
            covered_masks.append(None)
            continue
        shifted, covered = apply_shift(image, result.shift_rows_pixels, result.shift_columns_pixels)
        aligned.append(shifted)
        covered_masks.append(covered)
    return aligned, covered_masks, results
