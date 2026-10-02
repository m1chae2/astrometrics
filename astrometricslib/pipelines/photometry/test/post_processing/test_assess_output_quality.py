"""Unit tests for `assess_output_quality`.

Covers the margin calculation and the low-confidence flag around the
boundary.
"""

import pytest

from astrometricslib.pipelines.photometry.post_processing.assess_output_quality import (
    LOW_CONFIDENCE_MARGIN,
    assess_output_quality,
)


def test_assess_output_quality_margin_is_signed_mad_distance_from_cutoff():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the margin is (cv - cutoff) / mad_cv, positive above cutoff."""
    assessment = assess_output_quality(coefficient_of_variation=0.10, adaptive_cutoff=0.05, mad_cv=0.01)

    assert assessment.margin_in_mad_units == pytest.approx(5.0)
    assert assessment.is_low_confidence is False
    assert assessment.is_trustworthy is True


def test_assess_output_quality_negative_margin_below_cutoff():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a star well below cutoff gets a confidently negative margin."""
    assessment = assess_output_quality(coefficient_of_variation=0.01, adaptive_cutoff=0.05, mad_cv=0.01)

    assert assessment.margin_in_mad_units == pytest.approx(-4.0)
    assert assessment.is_low_confidence is False
    assert assessment.is_trustworthy is True


def test_assess_output_quality_flags_low_confidence_near_the_cutoff():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a margin inside the threshold is low confidence, outside not."""
    mad_cv = 0.01
    cutoff = 0.05
    just_inside = assess_output_quality(
        coefficient_of_variation=cutoff + (LOW_CONFIDENCE_MARGIN - 0.1) * mad_cv,
        adaptive_cutoff=cutoff,
        mad_cv=mad_cv,
    )
    just_outside = assess_output_quality(
        coefficient_of_variation=cutoff + (LOW_CONFIDENCE_MARGIN + 0.1) * mad_cv,
        adaptive_cutoff=cutoff,
        mad_cv=mad_cv,
    )

    assert just_inside.is_low_confidence is True
    assert just_inside.is_trustworthy is False
    assert just_outside.is_low_confidence is False
    assert just_outside.is_trustworthy is True


def test_assess_output_quality_guards_against_zero_mad():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a zero mad_cv doesn't raise a division error."""
    assessment = assess_output_quality(coefficient_of_variation=0.10, adaptive_cutoff=0.05, mad_cv=0.0)

    assert assessment.margin_in_mad_units == pytest.approx(0.05 / 1e-4)
