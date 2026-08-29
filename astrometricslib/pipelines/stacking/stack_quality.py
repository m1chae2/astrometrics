"""Rules for deciding if a stacked image is good enough.

This file contains the logic for checking the quality of the final image
after the stacking process is finished. It checks things like whether the
final image is blurrier than the original single frames, and whether too
many pixels had to be thrown out.
"""

# From the original plan text. Confirmed to not false-positive across every
# real successful stack tested so far (M 81/M 13/NGC 2403 sessions, and the
# N=5-20 small-session sweep above) -- stacking consistently sharpens relative
# to the inputs (registration + averaging reduces jitter), so fwhm_degraded
# never trips under normal conditions. True-positive sensitivity -- whether
# 1.2x correctly catches a *genuine* registration failure -- remains
# unvalidated: no real failed-registration session exists in the library to
# check against, and constructing a convincing synthetic one (vs. just
# injecting blur, which degrades input FWHM itself rather than simulating a
# registration failure specifically) wasn't attempted.
DEFAULT_FWHM_DEGRADATION_RATIO = 1.2

# Validated against three real sessions (see stack_quality_validation_plan
# memory / logs/rejection_threshold_analysis_*.json): rejected_fraction never
# exceeded ~10% even on NGC 2403's cloud-affected 70f session at the loosest
# tested sigma. 15% has real headroom above normal sessions, but is also known
# to be a weaker signal than FWHM -- NGC 2403's problem was invisible in
# rejected_fraction and only showed up in FWHM.
DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD = 0.15


def is_stacked_fwhm_degraded(
    stacked_fwhm: float,
    median_input_fwhm: float,
    degradation_ratio: float = DEFAULT_FWHM_DEGRADATION_RATIO,
) -> bool:
    """Check if the final stacked image is blurrier than it should be.

    Normally, combining images makes them look sharper. If the final image
    is significantly blurrier than the average original image, it usually
    means the software failed to align the images correctly before adding
    them together.

    Returns
    -------
    is_degraded : `bool`
        True if the final image is much blurrier than the originals.
    """
    if median_input_fwhm <= 0:
        return False
    return stacked_fwhm > median_input_fwhm * degradation_ratio


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
