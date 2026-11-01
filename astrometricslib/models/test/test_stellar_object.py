"""Tests for StellarObject and VariableCandidate's derived fields.

Purpose: variability_score and VariableCandidate.score both used to be
stored separately from coefficient_of_variation, which is the same
statistic on a different scale -- letting the two drift apart if one
was updated without the other. Both are now computed from
coefficient_of_variation instead of stored, so these tests pin down
that they stay in lockstep by construction.
"""

import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject, VariableCandidate


def test_variability_score_tracks_coefficient_of_variation() -> None:
    """Verify variability_score is always coefficient_of_variation * 100."""
    star = StellarObject(id="TestStar", photometry=PhotometryResult(coefficient_of_variation=0.075))

    assert star.variability_score == pytest.approx(7.5)


def test_variability_score_is_none_before_any_variability_is_measured() -> None:
    """Verify a star with no coefficient_of_variation yet reports None."""
    star = StellarObject(id="TestStar")

    assert star.photometry.coefficient_of_variation is None
    assert star.variability_score is None


def test_variable_candidate_score_is_capped_coefficient_of_variation() -> None:
    """Verify score matches coefficient_of_variation below the 1.0 cap."""
    candidate = VariableCandidate(id="TestStar", meanFlux=100.0, coefficientOfVariation=0.3, ra=10.0, dec=5.0)

    assert candidate.score == pytest.approx(0.3)


def test_variable_candidate_score_caps_at_one() -> None:
    """Verify a very high coefficient_of_variation still caps score at 1."""
    candidate = VariableCandidate(id="TestStar", meanFlux=100.0, coefficientOfVariation=2.5, ra=10.0, dec=5.0)

    assert candidate.score == pytest.approx(1.0)


def test_photometry_quality_models_load_rows_stored_with_the_removed_flags() -> None:
    """Verify rows saved before the yes/no flags were removed still load.

    Older stored records carry `isLowCoverage`, `isTrackingUnstable`,
    `isLowConfidence` and `isTrustworthy`. The models must ignore them and
    keep the numbers.
    """
    from astrometricslib.models.quality.photometry_quality import (
        InputQualityAssessment,
        OutputQualityAssessment,
    )

    input_quality = InputQualityAssessment.model_validate({
        "framesMeasured": 4,
        "framesAvailable": 10,
        "coverageFraction": 0.4,
        "saturatedFraction": 0.0,
        "maxRegistrationDriftPx": 25.0,
        "isLowCoverage": True,
        "isTrackingUnstable": True,
    })
    output_quality = OutputQualityAssessment.model_validate({
        "coefficientOfVariation": 0.1,
        "adaptiveCutoff": 0.05,
        "marginInMadUnits": 0.5,
        "isLowConfidence": True,
        "isTrustworthy": False,
    })

    assert input_quality.coverage_fraction == pytest.approx(0.4)
    assert input_quality.max_registration_drift_px == pytest.approx(25.0)
    assert output_quality.margin_in_mad_units == pytest.approx(0.5)
    assert not hasattr(input_quality, "is_low_coverage")
    assert not hasattr(output_quality, "is_trustworthy")
