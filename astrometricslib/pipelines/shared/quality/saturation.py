"""Tools to check if a photo is too bright (overexposed).

This counts how many pixels are completely maxed out (pure white). The
photo isn't thrown away just because a few stars are too bright, but
a warning flag is left so later steps know those specific stars can't
be measured accurately.
"""

import numpy as np

# A stack has no fixed saturation level: Siril rescales it, so a saturated
# star can sit anywhere below 1.0 and an unsaturated stack also tops out at
# 1.0. So the level is found from the stack's own pixels.
#
# In the Vega session's group
# stacks the saturated pixels sit at a ceiling that differs from group to
# group (about 1.0 for the 0.5-2 s groups but 0.81-0.82 for the 3 s and 5 s
# groups, because Siril's normalization rescales each stack), and the 5 s
# group's ceiling lies below 0.95, so its saturated trail was used as if it
# were a measurement. That made the combined spectrum too red (measured
# 2026-09-21, see logs/stack_exposure_groups_20260920.json). So each group's
# ceiling is measured from its own pixels: the median of its brightest
# SATURATION_CEILING_SAMPLE_PIXELS pixels. Saturated pixels pile up there, so
# it is a ceiling only if at least SATURATION_PLATEAU_MINIMUM_PIXELS pixels lie
# within SATURATION_PLATEAU_TOLERANCE of it. On Vega the saturated groups had
# 571-828 such pixels and 101-576 for the 0.5-2 s groups; a real, unsaturated
# peak has only a handful. The counts 25, 50 and 3% were chosen from those
# numbers and have not been tested on other stars. Pixels above
# SATURATION_MASK_FRACTION_OF_CEILING times the ceiling are masked, because the
# plateau is ragged (0.79-0.85 in the 3 s group) and the last few percent
# below it are already clipped by some frames in the average.
SATURATION_CEILING_SAMPLE_PIXELS = 25
SATURATION_PLATEAU_MINIMUM_PIXELS = 50
SATURATION_PLATEAU_TOLERANCE = 0.03
SATURATION_MASK_FRACTION_OF_CEILING = 0.9

# The pile-up test alone is fooled by a stack with no bright star: the
# brightest pixels of plain sky noise also bunch together (the top 25 of a
# 10,000-pixel noise field sit within 3% of each other). A real saturation
# ceiling stands far above the sky, so it must be at least this many times the
# stack's median. The M 13 stacks have ceilings of 1.0 against medians of
# 0.004-0.005 (a ratio near 200) and the Vega group ceilings of 0.8 lie far
# above their sky. The 10 was chosen well below those and well above noise
# (a ratio near 1.5); it has not been tested on other stars.
SATURATION_CEILING_MINIMUM_OVER_MEDIAN = 10.0


def compute_saturated_pixel_fraction(data: np.ndarray, saturation_threshold: float) -> float:
    """Calculate what percentage of the image is completely blown out.

    Parameters
    ----------
    data : `numpy.ndarray`
        The actual pixels of the image.
    saturation_threshold : `float`
        The brightness level that counts as 'maxed out'. This changes
        depending on which camera took the picture.

    Returns
    -------
    saturated_fraction : `float`
        The percentage of pixels that are too bright (from 0.0 to 1.0).
    """
    if data.size == 0:
        return 0.0
    return float(np.count_nonzero(data >= saturation_threshold) / data.size)


def is_saturation_significant(saturated_fraction: float, flag_threshold: float = 0.001) -> bool:
    """Decide if there are enough overexposed pixels to warn the user about it.

    Parameters
    ----------
    saturated_fraction : `float`
        The percentage of maxed-out pixels found.
    flag_threshold : `float`, optional
        How much overexposure is 'too much'. Default is 0.1%. (Note: this
        is just a guess right now and might need to be adjusted later).

    Returns
    -------
    is_significant : `bool`
        True if the image is too bright, False if it's fine.
    """
    return saturated_fraction >= flag_threshold


# The largest value a pixel of a Siril-written stack can hold. A 16-bit raw
# frame counted in ADU always has some pixel above this, so an image whose
# brightest pixel is at or below it is a normalised stack, not a raw frame.
NORMALISED_STACK_MAXIMUM = 1.0


def is_normalised_stack_scale(data: np.ndarray) -> bool:
    """Tell a Siril-normalised stack (values 0 to 1) from a raw frame in ADU.

    Parameters
    ----------
    data : `numpy.ndarray`
        The pixels of the image.

    Returns
    -------
    is_normalised : `bool`
        True if the brightest finite pixel is at or below 1.0.
    """
    finite = data[np.isfinite(data)]
    return finite.size > 0 and float(finite.max()) <= NORMALISED_STACK_MAXIMUM


def find_saturation_plateau_ceiling(image: np.ndarray) -> float | None:
    """Find the value at which a stack's saturated pixels pile up, if they do.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stacked image.

    Returns
    -------
    ceiling : `float` or `None`
        The median of the brightest SATURATION_CEILING_SAMPLE_PIXELS pixels
        when at least SATURATION_PLATEAU_MINIMUM_PIXELS pixels lie within
        SATURATION_PLATEAU_TOLERANCE of it and it is at least
        SATURATION_CEILING_MINIMUM_OVER_MEDIAN times the stack's median;
        `None` when there is no such pile (the stack is not saturated).
    """
    flat = np.asarray(image, dtype=np.float64).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size < SATURATION_PLATEAU_MINIMUM_PIXELS:
        return None
    brightest = np.partition(flat, -SATURATION_CEILING_SAMPLE_PIXELS)[-SATURATION_CEILING_SAMPLE_PIXELS:]
    ceiling = float(np.median(brightest))
    if ceiling <= 0 or ceiling < SATURATION_CEILING_MINIMUM_OVER_MEDIAN * float(np.median(flat)):
        return None
    pixels_near_ceiling = int(np.count_nonzero(flat >= ceiling * (1.0 - SATURATION_PLATEAU_TOLERANCE)))
    if pixels_near_ceiling < SATURATION_PLATEAU_MINIMUM_PIXELS:
        return None
    return ceiling


def compute_stack_saturated_pixel_fraction(data: np.ndarray, region: np.ndarray | None = None) -> float:
    """Calculate the fraction of a stack's pixels that are saturated.

    The ceiling is found from the whole stack, then pixels at or above
    SATURATION_MASK_FRACTION_OF_CEILING times it are counted, the same
    level the exposure-group stacking masks.

    Parameters
    ----------
    data : `numpy.ndarray`
        The whole stacked image, used to find the ceiling.
    region : `numpy.ndarray`, optional
        A cut-out of `data` to count in. Defaults to all of `data`.

    Returns
    -------
    saturated_fraction : `float`
        The fraction of pixels (0.0 to 1.0) at the saturation plateau, or
        0.0 if the stack has no plateau.
    """
    ceiling = find_saturation_plateau_ceiling(data)
    if ceiling is None:
        return 0.0
    counted = data if region is None else region
    return compute_saturated_pixel_fraction(
        np.asarray(counted, dtype=float), ceiling * SATURATION_MASK_FRACTION_OF_CEILING
    )
