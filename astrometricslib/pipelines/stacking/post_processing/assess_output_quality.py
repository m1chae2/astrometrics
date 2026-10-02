"""Judges whether a finished stack came out well.

The builder here reads the measurements already made on the stacked image
(rejected, saturated and zero pixel shares, sharpness, spectral registration
concerns) and turns the ones past their limits into plain-language flag
reasons. The limits live in `stack_quality` and `shared/quality`. The result
is a `StackingOutputQuality` (see `models/stacking_quality.py`).
"""

from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics
from astrometricslib.models.stacking_quality import StackingOutputQuality


def assess_output_quality(metrics: StackingPipelineQualityMetrics) -> StackingOutputQuality:
    """Judge the stacked image from the measurements made on it.

    Parameters
    ----------
    metrics : `StackingPipelineQualityMetrics`
        The measurements of the finished stack. Each ``*_flagged`` field
        already holds the result of that measurement's own threshold test.

    Returns
    -------
    quality : `StackingOutputQuality`
        The output judgement, with a sentence for each problem found.
    """
    reasons = []
    if metrics.rejected_fraction_flagged:
        reasons.append(f"rejected pixel fraction {metrics.rejected_pixel_fraction:.1%} at or above threshold")
    if metrics.fwhm_degraded:
        reasons.append(
            f"stacked FWHM {metrics.stacked_fwhm_px:.2f}px degraded vs median input "
            f"{metrics.median_input_fwhm_px:.2f}px"
        )
    if metrics.spectral_registration_flags:
        reasons.append(
            f"{len(metrics.spectral_registration_flags)} frame(s) with spectral registration concerns"
        )
    if metrics.saturation_flagged:
        reasons.append(
            f"saturated pixel fraction {metrics.saturated_pixel_fraction:.2%} at or above threshold"
        )
    if metrics.zero_fraction_flagged:
        reasons.append(
            f"{metrics.zero_pixel_fraction:.0%} of the stack's pixels are exactly zero: "
            "calibration removed more than the sky (blank stack)"
        )
    if metrics.negative_pixels_flagged:
        reasons.append(
            f"Siril reported up to {metrics.negative_pixel_max_percent}% negative pixels after "
            "dark subtraction: calibration frames are probably incorrect"
        )
    return StackingOutputQuality(
        rejected_pixel_fraction=metrics.rejected_pixel_fraction,
        saturated_pixel_fraction=metrics.saturated_pixel_fraction,
        zero_pixel_fraction=metrics.zero_pixel_fraction,
        negative_pixel_max_percent=metrics.negative_pixel_max_percent,
        stacked_fwhm_px=metrics.stacked_fwhm_px,
        median_input_fwhm_px=metrics.median_input_fwhm_px,
        spectral_registration_concern_count=len(metrics.spectral_registration_flags),
        is_flagged=bool(reasons),
        flag_reasons=reasons,
    )
