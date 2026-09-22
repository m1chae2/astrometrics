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
    """

    shift_rows_pixels: float
    shift_columns_pixels: float
    correlation: float

    @property
    def trusted(self) -> bool:
        """Whether the correlation is high enough to believe the offset.

        Returns
        -------
        trusted : `bool`
            `True` at or above `MINIMUM_ALIGNMENT_CORRELATION`.
        """
        return self.correlation >= MINIMUM_ALIGNMENT_CORRELATION


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


def measure_alignment(reference: np.ndarray, image: np.ndarray) -> AlignmentResult:
    """Find the shift that puts `image` onto `reference`.

    Parameters
    ----------
    reference : `numpy.ndarray`
        The stack to line up with, 2-D or colour (channels first).
    image : `numpy.ndarray`
        The stack to move, the same shape as `reference`.

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
    fixed = _filtered(_to_plane(reference))
    moving = _filtered(_to_plane(image))
    shift, _, _ = phase_cross_correlation(
        fixed, moving, upsample_factor=ALIGNMENT_UPSAMPLE_FACTOR, normalization=None
    )
    moved = shift_image(moving, shift, order=1, mode="constant", cval=0.0)
    covered = (
        shift_image(np.ones_like(moving), shift, order=1, mode="constant", cval=0.0) > COVERED_PIXEL_THRESHOLD
    )
    if not covered.any():
        return AlignmentResult(float(shift[0]), float(shift[1]), 0.0)
    correlation = float(np.corrcoef(fixed[covered], moved[covered])[0, 1])
    if not np.isfinite(correlation):
        correlation = 0.0
    return AlignmentResult(float(shift[0]), float(shift[1]), correlation)


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
    images: list[np.ndarray], reference_index: int
) -> tuple[list[np.ndarray | None], list[np.ndarray | None], list[AlignmentResult | None]]:
    """Line up every image with the reference image.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The group stacks, all the same shape.
    reference_index : `int`
        Which image the others are moved onto. It is returned unchanged.

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
        result = measure_alignment(images[reference_index], image)
        results.append(result)
        if not result.trusted:
            aligned.append(None)
            covered_masks.append(None)
            continue
        shifted, covered = apply_shift(image, result.shift_rows_pixels, result.shift_columns_pixels)
        aligned.append(shifted)
        covered_masks.append(covered)
    return aligned, covered_masks, results
