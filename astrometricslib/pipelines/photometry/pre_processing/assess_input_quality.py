"""Measures how good the raw per-frame measurements behind a light curve were.

This only looks at signals from measurement and normalization
themselves -- coverage, saturation, and registration drift -- so it can
run right after normalization settles a star's final light curve, and
never needs to change when the variability-flagging logic does.
"""

from astrometricslib.models.quality.photometry_quality import InputQualityAssessment
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import CENTROID_SEARCH_HALF_WIDTH_PX

# `locate_star_centroid` only searches a box around a star's expected
# position (`CENTROID_SEARCH_HALF_WIDTH_PX`, its `search_half_width`
# default). Once frame-to-frame drift approaches half that radius, a star with
# even a little of its own positional jitter risks landing outside the search
# box and silently returning no centroid. The limit is therefore half the
# search half-width (20 px at the default 40 px), which catches instability
# well before that failure mode.
UNSTABLE_TRACKING_DRIFT_PX = TunedParameter(
    name="photometry.unstable_tracking_drift_px",
    default=0.5 * CENTROID_SEARCH_HALF_WIDTH_PX,
    unit="px",
    description="Frame-to-frame shift above which tracking counts as unstable.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="PHO-D16",
)


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
    return InputQualityAssessment(
        frames_measured=frames_measured,
        frames_available=frames_available,
        coverage_fraction=coverage_fraction,
        saturated_fraction=saturated_fraction,
        max_registration_drift_px=max_registration_drift_px,
    )
