"""Rules for deciding if a stacked image is good enough.

This file contains the logic for checking the quality of the final image
after the stacking process is finished. It checks things like whether the
final image is blurrier than the original single frames, and whether too
many pixels had to be thrown out.
"""

# "80%" then "90%" then unfiltered: the two percentiles
# rejection_threshold_analysis.py swept, in order from tightest to loosest.
FILTER_WFWHM_LOOSENING_LADDER: list[str | None] = ["80%", "90%", None]

# Validated with a real small-session sweep (M 13 L-filter, N=5/6/8/10/15/20
# frames of the same session, adaptive Chauvenet sigma, no filter_wfwhm):
# stacked FWHM degrades smoothly from 3.72px (N=20) to 4.07px (N=5, ~9% worse)
# and rejected_fraction from 4.8% to 8.2% -- a gradual reduction in
# noise-averaging benefit as N shrinks, not a cliff or quality collapse at N=5.
# That doesn't prove 5 is the *exact* right number (no failure mode was found
# at 5 to calibrate against, since quality degrades continuously rather than
# breaking at some threshold), but it does confirm 5 isn't producing garbage --
# see logs/ for the raw per-N sweep this was checked against.
DEFAULT_MINIMUM_SURVIVING_FRAMES = 5

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


def resolve_filter_wfwhm_with_floor(
    num_lights: int,
    requested_filter_wfwhm: str | None,
    minimum_surviving_frames: int = DEFAULT_MINIMUM_SURVIVING_FRAMES,
) -> tuple[str | None, bool]:
    """Make sure we don't throw away too many images when filtering.

    If we tell the software to only keep the top 80% sharpest images, but
    we only have 4 images total, keeping 80% might leave us with too few
    images to make a good stack. This function checks if our filter rule
    will leave us with at least a minimum number of frames. If not, it
    loosens the rule (e.g., from 80% to 90%, or turns it off completely)
    until we have enough frames.

    Parameters
    ----------
    num_lights : `int`
        The total number of images we're starting with.
    requested_filter_wfwhm : `str` or `None`
        The rule we want to use (like "80%").
    minimum_surviving_frames : `int`, optional
        The absolute minimum number of images we need to keep.

    Returns
    -------
    result : `tuple`
        A pair containing the rule we should actually use, and a True/False
        flag saying whether we had to loosen the original rule.
    """
    if requested_filter_wfwhm is None:
        return None, False

    if requested_filter_wfwhm in FILTER_WFWHM_LOOSENING_LADDER:
        candidates = FILTER_WFWHM_LOOSENING_LADDER[
            FILTER_WFWHM_LOOSENING_LADDER.index(requested_filter_wfwhm) :
        ]
    else:
        candidates = [requested_filter_wfwhm, None]

    for candidate in candidates:
        if candidate is None:
            return None, True
        percentage = float(candidate.rstrip("%"))
        expected_surviving = round(num_lights * percentage / 100.0)
        if expected_surviving >= minimum_surviving_frames:
            return candidate, candidate != requested_filter_wfwhm

    return None, True


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
