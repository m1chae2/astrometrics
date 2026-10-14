"""Judges how good the raw per-frame measurements behind a light curve were.

This only looks at signals from measurement and normalization
themselves -- coverage, saturation, and registration drift -- so it can
run right after normalization settles a star's final light curve, and
never needs to change when the variability-flagging logic does.
"""

from astrometricslib.models.photometry_quality import InputQualityAssessment

# A star missing from half or more of a session's frames has a light
# curve that is mostly gaps, not signal. 0.5 matches the least-strict
# fallback step `_select_reference_ensemble` already relaxes coverage to
# (0.8 -> 0.5 -> 0.25 -> 0.0) before giving up on stricter requirements --
# below even that most-relaxed accepted bar, coverage itself is the
# dominant quality problem.
LOW_COVERAGE_THRESHOLD = 0.5

# `locate_star_centroid` only searches a +/-40px box around a star's
# expected position (its `search_half_width` default). Once frame-to-frame
# drift approaches half that radius, a star with even a little of its own
# positional jitter risks landing outside the search box and silently
# returning no centroid. 20px catches instability well before that
# failure mode.
UNSTABLE_TRACKING_DRIFT_PX = 20.0


def assess_input_quality(
    *,
    frames_measured: int,
    frames_available: int,
    saturated_fraction: float,
    max_registration_drift_px: float | None,
) -> InputQualityAssessment:
    """Build the input-quality assessment for one star's light curve.

    Every parameter here is already computed elsewhere (during
    measurement or normalization) -- this just gathers them into one
    structured object instead of leaving them as separate values a
    caller has to know to look for.

    Returns
    -------
    assessment : `InputQualityAssessment`
        The structured data-quality record; see that class for what
        each field means.
    """
    coverage_fraction = frames_measured / frames_available if frames_available else 0.0
    is_tracking_unstable = (
        max_registration_drift_px is not None and max_registration_drift_px > UNSTABLE_TRACKING_DRIFT_PX
    )
    return InputQualityAssessment(
        frames_measured=frames_measured,
        frames_available=frames_available,
        coverage_fraction=coverage_fraction,
        saturated_fraction=saturated_fraction,
        max_registration_drift_px=max_registration_drift_px,
        is_low_coverage=coverage_fraction < LOW_COVERAGE_THRESHOLD,
        is_tracking_unstable=is_tracking_unstable,
    )
