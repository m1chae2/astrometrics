"""Judges whether a finished stack came out well.

The builder here reads the measurements already made on the stacked image
(rejected, saturated and zero pixel shares, sharpness, spectral registration
concerns) and turns the ones past their limits into plain-language flag
reasons. The limits live in `stack_quality` and `shared/quality`. The result
is a `StackingOutputQuality` (see `models/stacking_quality.py`).
"""

from astrometricslib.models.gate_result import (
    GateResult,
    GateStatus,
    failed_gate,
    passed_gate,
    unchecked_gate,
)
from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics
from astrometricslib.models.stacking_quality import StackingOutputQuality
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
    DEFAULT_FWHM_DEGRADATION_RATIO,
    DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT,
    DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
    DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
)

REJECTED_GATE_NAME = "rejected_pixel_fraction"
SHARPNESS_GATE_NAME = "stack_sharpness"
SPECTRAL_REGISTRATION_GATE_NAME = "spectral_registration"
SATURATION_GATE_NAME = "saturated_pixel_fraction"
ZERO_FRACTION_GATE_NAME = "zero_pixel_fraction"
NEGATIVE_PIXELS_GATE_NAME = "negative_pixels"


def output_quality_gates(
    metrics: StackingPipelineQualityMetrics, quality_processing_applied: bool = True
) -> list[GateResult]:
    """Build one gate for each measurement made on the finished stack.

    A measurement that was not made is ``not_checked`` with the reason, never
    a pass: the ``*_flagged`` fields in `metrics` stay false both when a value
    is within its limit and when it was never measured. The verdict of a
    measured value is the ``*_flagged`` field the measuring code already set,
    so the limit lives in one place.

    Parameters
    ----------
    metrics : `StackingPipelineQualityMetrics`
        The measurements of the finished stack.
    quality_processing_applied : `bool`, optional
        False for a single-frame stack, which has no rejection or
        registration to judge.

    Returns
    -------
    gates : `list` [`GateResult`]
        Six gates, in a fixed order.
    """
    gates: list[GateResult] = []
    rejected_source = "validated: normal sessions stay under 10%"
    if metrics.rejected_pixel_fraction is None:
        reason = (
            "a single-frame stack rejects no pixels"
            if not quality_processing_applied
            else "the stacker did not report how many pixels it rejected"
        )
        gates.append(unchecked_gate(REJECTED_GATE_NAME, reason, rejected_source))
    elif metrics.rejected_fraction_flagged:
        gates.append(
            failed_gate(
                REJECTED_GATE_NAME,
                f"rejected pixel fraction {metrics.rejected_pixel_fraction:.1%} at or above threshold",
                metrics.rejected_pixel_fraction,
                DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
                rejected_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                REJECTED_GATE_NAME,
                metrics.rejected_pixel_fraction,
                DEFAULT_REJECTED_FRACTION_FLAG_THRESHOLD,
                rejected_source,
            )
        )

    sharpness_source = "ratio to the width the input frames predict; true-positive sensitivity unvalidated"
    if metrics.is_spectral:
        gates.append(
            unchecked_gate(
                SHARPNESS_GATE_NAME, "star width does not apply to a spectral stack", sharpness_source
            )
        )
    elif not quality_processing_applied:
        gates.append(
            unchecked_gate(
                SHARPNESS_GATE_NAME,
                "a single-frame stack has no input frames to compare with",
                sharpness_source,
            )
        )
    elif metrics.stacked_fwhm_px is None or metrics.expected_stack_fwhm_px is None:
        gates.append(
            unchecked_gate(
                SHARPNESS_GATE_NAME,
                "the stack's or its input frames' star width could not be measured",
                sharpness_source,
            )
        )
    else:
        ratio = metrics.stacked_fwhm_px / metrics.expected_stack_fwhm_px
        if metrics.fwhm_degraded:
            gates.append(
                failed_gate(
                    SHARPNESS_GATE_NAME,
                    f"stacked FWHM {metrics.stacked_fwhm_px:.2f}px degraded vs the "
                    f"{metrics.expected_stack_fwhm_px:.2f}px its input frames predict",
                    ratio,
                    DEFAULT_FWHM_DEGRADATION_RATIO,
                    sharpness_source,
                )
            )
        else:
            gates.append(
                passed_gate(SHARPNESS_GATE_NAME, ratio, DEFAULT_FWHM_DEGRADATION_RATIO, sharpness_source)
            )

    registration_source = "zero-order star tracking per frame"
    if metrics.spectral_registration_flags:
        # Flags prove the check ran, whatever else the metrics say.
        gates.append(
            failed_gate(
                SPECTRAL_REGISTRATION_GATE_NAME,
                f"{len(metrics.spectral_registration_flags)} frame(s) with spectral registration concerns",
                float(len(metrics.spectral_registration_flags)),
                0.0,
                registration_source,
            )
        )
    elif not metrics.is_spectral:
        gates.append(
            unchecked_gate(SPECTRAL_REGISTRATION_GATE_NAME, "not a spectral stack", registration_source)
        )
    elif not quality_processing_applied or not metrics.spectral_registration_checked:
        gates.append(
            unchecked_gate(
                SPECTRAL_REGISTRATION_GATE_NAME,
                "the registration check did not run: its per-frame lists did not line up",
                registration_source,
            )
        )
    else:
        gates.append(passed_gate(SPECTRAL_REGISTRATION_GATE_NAME, 0.0, 0.0, registration_source))

    saturation_source = "share of pixels at the camera's saturation level"
    if metrics.saturated_pixel_fraction is None:
        gates.append(
            unchecked_gate(
                SATURATION_GATE_NAME, "the stack's saturated pixels could not be measured", saturation_source
            )
        )
    elif metrics.saturation_flagged:
        gates.append(
            failed_gate(
                SATURATION_GATE_NAME,
                f"saturated pixel fraction {metrics.saturated_pixel_fraction:.2%} at or above threshold",
                metrics.saturated_pixel_fraction,
                DEFAULT_SATURATION_FLAG_THRESHOLD,
                saturation_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                SATURATION_GATE_NAME,
                metrics.saturated_pixel_fraction,
                DEFAULT_SATURATION_FLAG_THRESHOLD,
                saturation_source,
            )
        )

    zero_source = "validated on the Nikon D5300 blank stacks; images only"
    if metrics.is_spectral:
        gates.append(
            unchecked_gate(
                ZERO_FRACTION_GATE_NAME,
                "a spectral stack's sky is legitimately at or below zero",
                zero_source,
            )
        )
    elif metrics.zero_pixel_fraction is None:
        gates.append(
            unchecked_gate(
                ZERO_FRACTION_GATE_NAME, "the stack file could not be read to count zero pixels", zero_source
            )
        )
    elif metrics.zero_fraction_flagged:
        gates.append(
            failed_gate(
                ZERO_FRACTION_GATE_NAME,
                f"{metrics.zero_pixel_fraction:.0%} of the stack's pixels are exactly zero: "
                "calibration removed more than the sky (blank stack)",
                metrics.zero_pixel_fraction,
                DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
                zero_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                ZERO_FRACTION_GATE_NAME,
                metrics.zero_pixel_fraction,
                DEFAULT_ZERO_FRACTION_FLAG_THRESHOLD,
                zero_source,
            )
        )

    negative_source = "Siril's own 'many negative pixels' warning"
    if metrics.negative_pixels_flagged:
        gates.append(
            failed_gate(
                NEGATIVE_PIXELS_GATE_NAME,
                f"Siril reported up to {metrics.negative_pixel_max_percent}% negative pixels after "
                "dark subtraction: calibration frames are probably incorrect",
                float(metrics.negative_pixel_max_percent),
                float(DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT),
                negative_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                NEGATIVE_PIXELS_GATE_NAME,
                float(metrics.negative_pixel_max_percent)
                if metrics.negative_pixel_max_percent is not None
                else None,
                float(DEFAULT_NEGATIVE_PIXEL_FLAG_PERCENT),
                negative_source,
                "Siril printed no negative-pixel warning"
                if metrics.negative_pixel_max_percent is None
                else "",
            )
        )
    return gates


def assess_output_quality(
    metrics: StackingPipelineQualityMetrics, quality_processing_applied: bool = True
) -> StackingOutputQuality:
    """Judge the stacked image from the measurements made on it.

    Parameters
    ----------
    metrics : `StackingPipelineQualityMetrics`
        The measurements of the finished stack. Each ``*_flagged`` field
        already holds the result of that measurement's own threshold test.
    quality_processing_applied : `bool`, optional
        False for a single-frame stack.

    Returns
    -------
    quality : `StackingOutputQuality`
        The output judgement, with a sentence for each problem found. The
        sentences are the failed gates' details (see `output_quality_gates`).
    """
    reasons = [
        gate.detail
        for gate in output_quality_gates(metrics, quality_processing_applied)
        if gate.status is GateStatus.FAILED
    ]
    return StackingOutputQuality(
        rejected_pixel_fraction=metrics.rejected_pixel_fraction,
        saturated_pixel_fraction=metrics.saturated_pixel_fraction,
        zero_pixel_fraction=metrics.zero_pixel_fraction,
        negative_pixel_max_percent=metrics.negative_pixel_max_percent,
        stacked_fwhm_px=metrics.stacked_fwhm_px,
        median_input_fwhm_px=metrics.median_input_fwhm_px,
        expected_stack_fwhm_px=metrics.expected_stack_fwhm_px,
        spectral_registration_concern_count=len(metrics.spectral_registration_flags),
        is_flagged=bool(reasons),
        flag_reasons=reasons,
    )
