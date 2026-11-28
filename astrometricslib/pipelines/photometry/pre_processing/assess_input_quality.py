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

# A frame whose middle sits this far from the reference frame's has moved a
# long way for a mount that is tracking: the field drifted out of place or the
# mount slipped. The plate solutions place every star wherever the field went,
# so the measuring does not need the limit, but a drift this large is still a
# mount problem the user should see. The value is the limit the earlier
# frame-to-frame tracking used, half of its 40 px search box; the register
# entry PHO-D16 holds it as a design estimate.
UNSTABLE_TRACKING_DRIFT_PX = TunedParameter(
    name="photometry.unstable_tracking_drift_px",
    default=20.0,
    unit="px",
    description="Shift of the middle of a frame from the reference frame above which tracking is unstable.",
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
