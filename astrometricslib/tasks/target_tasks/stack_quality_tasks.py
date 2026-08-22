"""Purpose: Post-stack validation decision logic.

Description: Pure decision logic for the minimum-surviving-frames floor
and post-stack FWHM/rejection-fraction flagging. Standard and spectral
stacks get different quality-check shapes (FWHM-based vs
zero-order-tracking-based) rather than one shared schema, per the finding
from spectral_registration_quality_analysis.py that a whole-field FWHM
check doesn't characterize what matters for a spectral trace.

The schema half of the former targetlib/stack_quality.py
(StackQualitySummary, StackingPipelineQualityMetrics) moved to
models/quality_summary.py instead.
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
    """Resolve a percentage-based frame filter against a survivor floor.

    Returns (effective_filter_wfwhm, was_loosened). filter_wfwhm=X% keeps
    round(num_lights * X / 100) frames deterministically -- Siril doesn't need
    to run for this to be computed, since a percentage filter always keeps that
    fraction of the input regardless of which specific frames pass. If the
    requested percentage would drop the survivor count below the floor, loosens
    one step at a time through FILTER_WFWHM_LOOSENING_LADDER (matching
    rejection_threshold_analysis.py's tested grid) until at or above the floor,
    falling back to unfiltered if even that isn't enough. was_loosened is True
    whenever the effective setting differs from what was requested, so callers
    can flag it.

    Parameters
    ----------
    num_lights : `int`
        Number of light frames the filter is being applied to.
    requested_filter_wfwhm : `str` or `None`
        Requested filter_wfwhm setting, e.g. "80%", or `None` for unfiltered.
    minimum_surviving_frames : `int`, optional
        Minimum number of surviving frames the effective setting must keep.
        Defaults to `DEFAULT_MINIMUM_SURVIVING_FRAMES`.

    Returns
    -------
    result : `tuple`
        The `(effective_filter_wfwhm, was_loosened)` pair: the filter setting
        to actually use, and whether it differs from what was requested.
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
    """Check whether the stacked FWHM is worse than expected from the inputs.

    A stacked FWHM meaningfully worse than the inputs' own median FWHM suggests
    a registration problem (misalignment smears the PSF), not just ordinary
    seeing.

    Returns
    -------
    is_degraded : `bool`
        `True` if `stacked_fwhm` exceeds `median_input_fwhm` scaled by
        `degradation_ratio`; `False` if `median_input_fwhm` is
        non-positive.
    """
    if median_input_fwhm <= 0:
        return False
    return stacked_fwhm > median_input_fwhm * degradation_ratio


def is_rejected_fraction_significant(
    rejected_fraction: float,
    flag_threshold: float = DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
) -> bool:
    """Check whether the rejected-pixel fraction is high enough to flag.

    Returns
    -------
    is_significant : `bool`
        `True` if `rejected_fraction` is at or above `flag_threshold`.
    """
    return rejected_fraction >= flag_threshold
