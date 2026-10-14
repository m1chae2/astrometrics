"""Purpose: Unit tests for the stacking output-quality judgement.

Description: The builder copies the measurements made on a finished stack
and words each failed threshold as a flag reason. A stack with nothing
flagged has no reasons.
"""

import pytest

from astrometricslib.models.quality_summary import ExcludedFrame, StackingPipelineQualityMetrics
from astrometricslib.pipelines.stacking.post_processing.assess_output_quality import assess_output_quality


def make_metrics() -> StackingPipelineQualityMetrics:
    """Build metrics with nothing measured.

    Returns
    -------
    metrics : `StackingPipelineQualityMetrics`
        Default metrics for a ten-frame image stack.
    """
    return StackingPipelineQualityMetrics(is_spectral=False, frames_submitted=10, frames_stacked=10)


def test_an_unflagged_stack_has_no_reasons() -> None:
    """Nothing flagged gives an unflagged judgement."""
    quality = assess_output_quality(make_metrics())
    assert not quality.is_flagged
    assert quality.flag_reasons == []


def test_each_failed_threshold_gives_one_reason() -> None:
    """Rejected, zero and negative-pixel flags are each worded."""
    metrics = make_metrics()
    metrics.rejected_pixel_fraction = 0.3
    metrics.rejected_fraction_flagged = True
    metrics.zero_pixel_fraction = 0.9
    metrics.zero_fraction_flagged = True
    metrics.negative_pixel_max_percent = 40
    metrics.negative_pixels_flagged = True
    quality = assess_output_quality(metrics)
    assert quality.is_flagged
    assert len(quality.flag_reasons) == 3
    assert quality.rejected_pixel_fraction == pytest.approx(0.3)
    assert any("exactly zero" in reason for reason in quality.flag_reasons)


def test_spectral_registration_concerns_are_counted() -> None:
    """The count of questioned frames is copied and worded."""
    metrics = make_metrics()
    metrics.spectral_registration_flags = [ExcludedFrame(path="a.fits", reason="moved")]
    quality = assess_output_quality(metrics)
    assert quality.spectral_registration_concern_count == 1
    assert quality.flag_reasons == ["1 frame(s) with spectral registration concerns"]
