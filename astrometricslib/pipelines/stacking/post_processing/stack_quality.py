"""Rules for deciding if a stacked image is good enough.

This file contains the logic for checking the quality of the final image
after the stacking process is finished. It checks things like whether the
final image is blurrier than the original single frames, and whether too
many pixels had to be thrown out.
"""

# From the original plan text. The ratio is the stack's FWHM divided by the
# FWHM its input frames predict (the RMS of a sample of their FWHMs, see
# `expected_stack_fwhm`), both measured with `measure_image_fwhm`. Measured on
# six real ASI533MM Pro stacks on 2026-10-03: Bubble Nebula 1.10, M 27 1.16,
# M 81 1.06, M 13 1.01, M 101 0.85, NGC 2244 0.77. None reached 1.2, so the
# flag does not fire on a normal stack; M 27 mixes three nights and two camera
# offsets and sits closest. Against the median of the inputs the same stacks
# ranged up to 1.34 (M 81), which is why the RMS is used. True-positive
# sensitivity -- whether 1.2x catches a *genuine* registration failure --
# remains unvalidated: no real failed-registration session exists in the
# library to check against.
DEFAULT_FWHM_DEGRADATION_RATIO = 1.2

# Validated against three real sessions (see stack_quality_validation_plan
# memory / logs/rejection_threshold_analysis_*.json): rejected_fraction never
# exceeded ~10% even on NGC 2403's cloud-affected 70f session at the loosest
# tested sigma. 15% has real headroom above normal sessions, but is also known
# to be a weaker signal than FWHM -- NGC 2403's problem was invisible in
# rejected_fraction and only showed up in FWHM.
DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD = 0.15


def expected_stack_fwhm(input_fwhms: list[float]) -> float | None:
    """Predict the star width of a stack from the widths of its input frames.

    A stack's star is the average of the stars in its frames. For round
    Gaussian stars of widths w1, w2, ... that average has a variance equal to
    the mean of the individual variances, so its FWHM is the root mean
    square (RMS) of the individual FWHMs. A set of frames from nights with
    different seeing therefore stacks wider than its median frame, and a
    check against the median would call that normal blur a failure.

    Parameters
    ----------
    input_fwhms : `list` [`float`]
        The FWHM of a sample of the stack's input frames, in pixels.

    Returns
    -------
    expected : `float` or `None`
        The RMS of the widths, or `None` for an empty list.
    """
    if not input_fwhms:
        return None
    return float((sum(width * width for width in input_fwhms) / len(input_fwhms)) ** 0.5)


def is_stacked_fwhm_degraded(
    stacked_fwhm: float,
    expected_fwhm: float,
    degradation_ratio: float = DEFAULT_FWHM_DEGRADATION_RATIO,
) -> bool:
    """Check if the final stacked image is blurrier than it should be.

    Normally, combining images makes the stars about as wide as the frames
    they came from (see `expected_stack_fwhm`). If the final image is
    significantly wider than that, it usually means the software failed to
    align the images correctly before adding them together.

    Parameters
    ----------
    stacked_fwhm : `float`
        The star width of the finished stack, in pixels.
    expected_fwhm : `float`
        The star width the stack should have, in pixels (see
        `expected_stack_fwhm`).
    degradation_ratio : `float`, optional
        How much wider than expected counts as degraded.

    Returns
    -------
    is_degraded : `bool`
        True if the final image is much blurrier than expected.
    """
    if expected_fwhm <= 0:
        return False
    return stacked_fwhm > expected_fwhm * degradation_ratio


def is_rejected_fraction_significant(
    rejected_fraction: float,
    flag_threshold: float = DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
) -> bool:
    """Check if too many pixels were thrown out during stacking.

    Returns
    -------
    is_significant : `bool`
        True if the percentage of rejected pixels is above the warning limit.
    """
    return rejected_fraction >= flag_threshold


# A stack whose pixels are mostly exactly zero has had more subtracted than the
# sky it held: on the Nikon D5300, 15 of the 59 library stacks were 98% to
# 100% zero (the dark master already holds the bias and Siril subtracted the
# bias again, measured 2026-09-21), while no other imaging stack had more than
# 39% zero pixels (Vega L, a bright star on a dark sky; the rest had 6% or
# fewer). 50% falls between the two groups. Spectral stacks have their own,
# much lower limit (below): the 93% once quoted here for Vega's spectral stack
# was a symptom (raw frames clipped at zero), not a normal sky.
DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD = 0.5

# The same test for spectral stacks. The stored zero pixel share of every
# spectral stack in the library under stacks/ (17 stacks, read 2026-10-12):
#   0% (13 stacks): Alcor, Navi, M 57, M 27, Albireo, Vega, Mirach, Mars,
#       Mirfak, Alhena, Aldebaran, Schedar, M 13
#   0.28%: Deneb;  0.4%: Arcturus (its raw frames are clipped, see below)
#   32%: Sirius  (the matched dark removed the sky; a known bad stack)
#   35%: Alnath  (every exposure group clipped at zero in the camera)
# Clean stacks stay at or under 0.4%; the two known-bad stacks start at 32%.
# 5% sits in that gap, more than ten times above the cleanest group's worst
# and six times below the lowest bad one. Vega and Albireo read 0% although
# their short raw frames are clipped, because the long groups fill in their
# faint pixels, so this limit does NOT detect clipped raw frames: the
# `raw_frame_clipping` gate does. Validated only on the ASI533MM Pro and the
# Nikon D5300 stacks of this library.
SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD = 0.05

# Siril prints "many negative pixels (N%)" after subtracting the dark. In the
# blank D5300 runs N was 99 and 100; the ASI533 runs never printed it. 50% is
# a stack that is mostly over-subtracted.
DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT = 50


def zero_fraction_threshold(is_spectral: bool) -> float:
    """Choose the zero pixel limit that applies to a stack.

    Parameters
    ----------
    is_spectral : `bool`
        Whether the stack is spectral.

    Returns
    -------
    threshold : `float`
        `SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD` for a spectral stack,
        `DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD` for an image.
    """
    return SPECTRAL_ZERO_FRACTION_FLAG_THRESHOLD if is_spectral else DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD


def is_zero_fraction_significant(
    zero_fraction: float,
    flag_threshold: float = DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
) -> bool:
    """Check if a stacked image is mostly zeros.

    Returns
    -------
    is_significant : `bool`
        True if the share of exactly-zero pixels is at or above the limit.
    """
    return zero_fraction >= flag_threshold


def is_negative_pixel_percent_significant(
    negative_percent: float,
    flag_threshold: float = DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT,
) -> bool:
    """Check if Siril reported most of a frame as negative after calibration.

    Returns
    -------
    is_significant : `bool`
        True if the reported percentage is at or above the limit.
    """
    return negative_percent >= flag_threshold
