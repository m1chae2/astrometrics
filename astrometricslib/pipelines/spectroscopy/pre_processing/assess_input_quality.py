"""Judges how good the raw data behind a spectrum was.

This only looks at signals that come from extraction itself -- the
instrument's resolution, saturation, coverage, and signal-to-noise -- so
it can run before anything is found in the spectrum, and never needs to
change when the classifier or feature tests do.

The same numbers also form quality checkpoint 1 (the calibrated spectrum),
built by `input_quality_checkpoint` in the common `StageQualityCheckpoint`
shape. The limit for signal-to-noise is
`MINIMUM_SPECTRUM_SIGNAL_TO_NOISE`, defined once in
`processing.spectrum_signal`.
"""

from astrometricslib.models.spectroscopy_quality import (
    InputQualityAssessment,
    StageQualityCheckpoint,
    metric,
)
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
)


def assess_input_quality(
    *,
    resolution_element_angstrom: float,
    is_resolution_measured: bool,
    zero_order_saturated_pixel_fraction: float | None,
    valid_fraction: float | None,
    signal_to_noise: float | None,
) -> InputQualityAssessment:
    """Build the input-quality assessment for one extracted spectrum.

    Every parameter here is already computed elsewhere (during extraction,
    or as one of `analyze_spectrum`'s own results) -- this just gathers
    them into one structured object instead of leaving them as separate
    fields a caller has to know to look for.

    Returns
    -------
    assessment : `InputQualityAssessment`
        The structured data-quality record; see that class for what each
        field means.
    """
    return InputQualityAssessment(
        resolution_element_angstrom=resolution_element_angstrom,
        is_resolution_measured=is_resolution_measured,
        zero_order_saturated_pixel_fraction=zero_order_saturated_pixel_fraction,
        valid_fraction=valid_fraction,
        signal_to_noise=signal_to_noise,
    )


def input_quality_checkpoint(assessment: InputQualityAssessment) -> StageQualityCheckpoint:
    """Build quality checkpoint 1 from an input-quality assessment.

    Parameters
    ----------
    assessment : `InputQualityAssessment`
        The result of `assess_input_quality`.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The ``pre_processing`` checkpoint. It carries the assessment's five
        numbers. Resolution has no limit: a coarse resolution limits what the
        later stages can tell apart but is not a failure by itself.
    """
    saturation = metric(
        "zero_order_saturated_fraction",
        assessment.zero_order_saturated_pixel_fraction,
        "fraction",
        limit=DEFAULT_SATURATION_FLAG_THRESHOLD,
        higher_is_better=False,
        note="limit is the run's zero-order saturation flag level; a guess, not validated",
        limit_is_a_pass=False,
    )
    signal_to_noise = metric(
        "signal_to_noise",
        assessment.signal_to_noise,
        "per resolution element",
        limit=MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
        note="below the limit the spectrum is not classified",
    )
    metrics = [
        metric(
            "resolution_element",
            assessment.resolution_element_angstrom,
            "angstrom",
            note="how much the instrument blurred this spectrum",
        ),
        metric(
            "resolution_measured",
            1.0 if assessment.is_resolution_measured else 0.0,
            "flag",
            note="1 when measured from the spectrum's own trail width, 0 when a fixed fallback was used",
        ),
        saturation,
        metric("valid_fraction", assessment.valid_fraction, "fraction"),
        signal_to_noise,
    ]
    flags = []
    if not assessment.is_resolution_measured:
        flags.append("resolution_assumed")
    if signal_to_noise.passed is False:
        flags.append("low_signal_to_noise")
    if saturation.passed is False:
        flags.append("zero_order_saturated")
    return StageQualityCheckpoint(stage="pre_processing", metrics=metrics, flags=flags)
