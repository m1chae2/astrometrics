"""Judges how good the raw data behind a spectrum was.

This only looks at signals that come from extraction itself -- the
instrument's resolution, saturation, coverage, and signal-to-noise -- so
it can run before anything is found in the spectrum, and never needs to
change when the classifier or feature tests do.

The same numbers also form quality checkpoint 1 (the calibrated spectrum),
built by `input_quality_checkpoint` in the common `StageQualityCheckpoint`
shape. The limit for signal-to-noise is
`MINIMUM_SPECTRUM_SIGNAL_TO_NOISE`, defined once in
`processing.spectrum_signal`. The checkpoint also carries the wavelength
zero-point metrics from `wavelength_zero_point` when a measurement is given.

When the extractor computed a variance for every sample (see
`intensity_variance`), the checkpoint also carries three numbers built from
it: the median signal-to-noise per resolution element, the share of samples
below a signal-to-noise of 5, and the ratio of the variance-based
signal-to-noise to the post-hoc estimate above. None of the three has a
limit. No measurement exists that says what value is too low, and the limit
of the post-hoc estimate was set on that estimator's own scale, so it does
not carry over.
"""

from astrometricslib.models.spectroscopy_quality import (
    InputQualityAssessment,
    StageQualityCheckpoint,
    StageQualityMetric,
    metric,
)
from astrometricslib.models.wavelength_scale import WavelengthZeroPointRecord
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance import (
    LOW_SAMPLE_SIGNAL_TO_NOISE,
    SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM,
    SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM,
    PixelNoiseModel,
    SpectrumNoiseSummary,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.wavelength_zero_point import zero_point_metrics
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


def noise_metrics(
    summary: SpectrumNoiseSummary, noise_model: PixelNoiseModel | None
) -> list[StageQualityMetric]:
    """Build the metrics that come from the per-sample variance.

    Parameters
    ----------
    summary : `SpectrumNoiseSummary`
        The signal-to-noise numbers of one spectrum.
    noise_model : `PixelNoiseModel` or `None`
        The camera noise the variance used. Its assumptions go in the notes.

    Returns
    -------
    metrics : `list` [`StageQualityMetric`]
        ``median_snr_per_resolution_element``, ``fraction_samples_snr_below_5``
        and ``snr_estimate_ratio``, all without a limit.
    """
    assumed = []
    if noise_model is not None and noise_model.gain_is_assumed:
        assumed.append("gain assumed to be 1 electron per ADU")
    if noise_model is not None and noise_model.read_noise_is_assumed:
        assumed.append("read noise assumed to be zero")
    if noise_model is not None and noise_model.frames_are_assumed:
        assumed.append("one frame assumed in the stack")
    assumption_note = f" Assumptions: {'; '.join(assumed)}." if assumed else ""
    low_edge = SIGNAL_TO_NOISE_MINIMUM_WAVELENGTH_ANGSTROM
    high_edge = SIGNAL_TO_NOISE_MAXIMUM_WAVELENGTH_ANGSTROM
    span = f"{low_edge:.0f}-{high_edge:.0f} A"
    return [
        metric(
            "median_snr_per_resolution_element",
            summary.median_snr_per_resolution_element,
            "per resolution element",
            note=(
                f"median over {span} of the brightness summed over one resolution element divided by the "
                "error of that sum, from the per-sample variance; reported only, no measured limit."
                + assumption_note
            ),
        ),
        metric(
            "fraction_samples_snr_below_5",
            summary.fraction_samples_snr_below_5,
            "fraction",
            note=(
                f"share of samples in {span} whose brightness is below {LOW_SAMPLE_SIGNAL_TO_NOISE:g} times "
                "their own error; 5 is a convention, not a measured limit."
            ),
        ),
        metric(
            "snr_estimate_ratio",
            summary.snr_estimate_ratio,
            "ratio",
            note=(
                "variance-based signal-to-noise divided by the post-hoc estimate (signal_to_noise). The two "
                "estimate the same quantity, so a ratio far from 1 means one of them is wrong (a wrong gain "
                "or read noise, or scatter in the spectrum that the variance does not model); reported only."
            ),
        ),
    ]


def input_quality_checkpoint(
    assessment: InputQualityAssessment,
    zero_point: WavelengthZeroPointRecord | None = None,
    *,
    noise_summary: SpectrumNoiseSummary | None = None,
    noise_model: PixelNoiseModel | None = None,
) -> StageQualityCheckpoint:
    """Build quality checkpoint 1 from an input-quality assessment.

    Parameters
    ----------
    assessment : `InputQualityAssessment`
        The result of `assess_input_quality`.
    zero_point : `WavelengthZeroPointRecord`, optional
        The spectrum's wavelength zero-point measurement. When given, its
        four metrics and flags are added (see `zero_point_metrics`).
    noise_summary : `SpectrumNoiseSummary`, optional
        The signal-to-noise numbers built from the per-sample variance. When
        given, the checkpoint also carries them (see `noise_metrics`).
    noise_model : `PixelNoiseModel`, optional
        The camera noise behind `noise_summary`. A gain or read noise that
        was assumed adds the ``gain_assumed`` or ``read_noise_assumed`` flag.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The ``pre_processing`` checkpoint. It carries the assessment's five
        numbers. Resolution has no limit: a coarse resolution limits what the
        later stages can tell apart but is not a failure by itself. With a
        zero-point measurement it also carries the four
        ``wavelength_zero_point_*`` metrics.
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
    if noise_summary is not None:
        metrics.extend(noise_metrics(noise_summary, noise_model))
    flags = []
    if not assessment.is_resolution_measured:
        flags.append("resolution_assumed")
    if signal_to_noise.passed is False:
        flags.append("low_signal_to_noise")
    if saturation.passed is False:
        flags.append("zero_order_saturated")
    if zero_point is not None:
        zero_point_entries, zero_point_flags = zero_point_metrics(zero_point)
        metrics.extend(zero_point_entries)
        flags.extend(zero_point_flags)
    if noise_model is not None and noise_model.gain_is_assumed:
        flags.append("gain_assumed")
    if noise_model is not None and noise_model.read_noise_is_assumed:
        flags.append("read_noise_assumed")
    return StageQualityCheckpoint(stage="pre_processing", metrics=metrics, flags=flags)
