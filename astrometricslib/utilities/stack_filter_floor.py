"""Keeping a sharpness filter from throwing away too many images.

Siril can drop the blurriest frames before stacking, keeping only a
requested percentage of the sharpest ones. That percentage is fine for
a big batch of images, but on a small batch it can leave too few
frames to make a good stack. This file loosens the requested
percentage, step by step, until enough frames would survive.
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
