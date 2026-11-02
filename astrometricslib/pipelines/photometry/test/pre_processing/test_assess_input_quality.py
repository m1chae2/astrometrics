"""Unit tests for `assess_input_quality`.

Covers the derived coverage fraction and the two boolean flags against
the drift limit that the registration_drift gate uses.
"""

import pytest

from astrometricslib.models.quality.photometry_quality import InputQualityAssessment
from astrometricslib.pipelines.photometry.pre_processing.assess_input_quality import (
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


def test_assess_input_quality_zero_frames_available_gives_zero_coverage() -> None:
    """Verify a zero denominator falls back to 0.0 coverage, not a crash."""
    assessment = assess_input_quality(
        frames_measured=0,
        frames_available=0,
        saturated_fraction=0.0,
        max_registration_drift_px=None,
    )

    assert assessment.coverage_fraction == pytest.approx(0.0)


def test_assess_input_quality_stores_drift_without_a_flag() -> None:
    """Verify the drift is stored as given, and stays None when unmeasured."""
    large = assess_input_quality(
        frames_measured=10,
        frames_available=10,
        saturated_fraction=0.0,
        max_registration_drift_px=UNSTABLE_TRACKING_DRIFT_PX + 1.0,
    )
    small = assess_input_quality(
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

    assert large.max_registration_drift_px == pytest.approx(UNSTABLE_TRACKING_DRIFT_PX + 1.0)
    assert small.max_registration_drift_px == pytest.approx(UNSTABLE_TRACKING_DRIFT_PX - 1.0)
    assert unmeasured.max_registration_drift_px is None
    assert "is_low_coverage" not in InputQualityAssessment.model_fields
    assert "is_tracking_unstable" not in InputQualityAssessment.model_fields
