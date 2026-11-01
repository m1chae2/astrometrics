"""Unit tests for `assess_output_quality`.

Covers the margin calculation and its sign on each side of the cutoff.
"""

import pytest

from astrometricslib.pipelines.photometry.post_processing.assess_output_quality import (
    assess_output_quality,
)


def test_assess_output_quality_margin_is_signed_mad_distance_from_cutoff() -> None:
    """Verify the margin is (cv - cutoff) / mad_cv, positive above cutoff."""
    assessment = assess_output_quality(coefficient_of_variation=0.10, adaptive_cutoff=0.05, mad_cv=0.01)

    assert assessment.margin_in_mad_units == pytest.approx(5.0)


def test_assess_output_quality_negative_margin_below_cutoff() -> None:
    """Verify a star well below cutoff gets a confidently negative margin."""
    assessment = assess_output_quality(coefficient_of_variation=0.01, adaptive_cutoff=0.05, mad_cv=0.01)

    assert assessment.margin_in_mad_units == pytest.approx(-4.0)


def test_assess_output_quality_guards_against_zero_mad() -> None:
    """Verify a zero mad_cv doesn't raise a division error."""
    assessment = assess_output_quality(coefficient_of_variation=0.10, adaptive_cutoff=0.05, mad_cv=0.0)

    assert assessment.margin_in_mad_units == pytest.approx(0.05 / 1e-4)
