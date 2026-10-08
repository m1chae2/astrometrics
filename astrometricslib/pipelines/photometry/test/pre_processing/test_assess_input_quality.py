"""Unit tests for `assess_input_quality`.

Covers the derived coverage fraction and the two boolean flags against
their thresholds.
"""

import pytest

from astrometricslib.pipelines.photometry.pre_processing.assess_input_quality import (
    LOW_COVERAGE_THRESHOLD,
    UNSTABLE_TRACKING_DRIFT_PX,
    assess_input_quality,
)


def test_assess_input_quality_computes_coverage_fraction() -> None:
    """Verify coverage_fraction is frames_measured / frames_available."""
    assessment = assess_input_quality(
        frames_measured=8,
        frames_available=10,
        saturated_fraction=0.0,
        max_registration_drift_px=1.0,
    )

    assert assessment.coverage_fraction == pytest.approx(0.8)
    assert assessment.is_low_coverage is False


def test_assess_input_quality_zero_frames_available_gives_zero_coverage() -> None:
    """Verify a zero denominator falls back to 0.0 coverage, not a crash."""
    assessment = assess_input_quality(
        frames_measured=0,
        frames_available=0,
        saturated_fraction=0.0,
        max_registration_drift_px=None,
    )

    assert assessment.coverage_fraction == pytest.approx(0.0)
    assert assessment.is_low_coverage is True


def test_assess_input_quality_flags_low_coverage_below_threshold() -> None:
    """Verify coverage above vs. below the threshold flags correctly."""
    just_above = assess_input_quality(
        frames_measured=int(LOW_COVERAGE_THRESHOLD * 100) + 1,
        frames_available=100,
        saturated_fraction=0.0,
        max_registration_drift_px=None,
    )
    just_below = assess_input_quality(
        frames_measured=int(LOW_COVERAGE_THRESHOLD * 100) - 1,
        frames_available=100,
        saturated_fraction=0.0,
        max_registration_drift_px=None,
    )

    assert just_above.is_low_coverage is False
    assert just_below.is_low_coverage is True


def test_assess_input_quality_flags_unstable_tracking_above_drift_threshold() -> None:
    """Verify drift above/below threshold, and missing drift, flags right."""
    unstable = assess_input_quality(
        frames_measured=10,
        frames_available=10,
        saturated_fraction=0.0,
        max_registration_drift_px=UNSTABLE_TRACKING_DRIFT_PX + 1.0,
    )
    stable = assess_input_quality(
        frames_measured=10,
        frames_available=10,
        saturated_fraction=0.0,
        max_registration_drift_px=UNSTABLE_TRACKING_DRIFT_PX - 1.0,
    )
    unmeasured = assess_input_quality(
        frames_measured=10,
        frames_available=10,
        saturated_fraction=0.0,
        max_registration_drift_px=None,
    )

    assert unstable.is_tracking_unstable is True
    assert stable.is_tracking_unstable is False
    assert unmeasured.is_tracking_unstable is False
    assert unmeasured.max_registration_drift_px is None
