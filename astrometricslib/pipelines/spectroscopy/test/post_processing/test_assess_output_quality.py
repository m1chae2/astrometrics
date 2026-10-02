"""Purpose: Unit tests for the post-processing classification-trust checks.

Description: Verifies the low-confidence and ambiguous-match flags, and
that `build_spectral_classification_concerns` only raises a concern for
a classified star that is actually shaky, not for one left unclassified
or one that matched cleanly. These tests used to live in
`test_spectral_classifier.py`; they moved here when the trust/concern
functions moved out of `spectral_classifier.py` into
`post_processing/assess_output_quality.py`.
"""

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    build_spectral_classification_concerns,
    is_classification_ambiguous,
    is_classification_low_confidence,
)


def test_is_classification_low_confidence_flags_weak_correlations():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the threshold check separates weak matches from strong ones."""
    assert is_classification_low_confidence(0.33) is True
    assert is_classification_low_confidence(0.93) is False


def test_is_classification_low_confidence_treats_unclassified_as_not_low_confidence():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unclassified star (confidence None) isn't "low confidence".

    It's a separate "nothing to compare" case -- flagging it the same
    way would conflate "no data" with "shaky match".
    """
    assert is_classification_low_confidence(None) is False


def test_is_classification_ambiguous_flags_a_near_tie():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a near-tie between the top two candidates is caught."""
    near_tie = [
        {"spectral_type": "K5V", "probability": 0.51, "correlation": 0.93},
        {"spectral_type": "M0V", "probability": 0.49, "correlation": 0.93},
        {"spectral_type": "M5V", "probability": 0.0, "correlation": 0.63},
    ]
    assert is_classification_ambiguous(near_tie) is True


def test_is_classification_ambiguous_accepts_a_clear_winner():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a clear winner isn't flagged as ambiguous."""
    clear_winner = [
        {"spectral_type": "M5V", "probability": 0.98, "correlation": 0.87},
        {"spectral_type": "M0V", "probability": 0.02, "correlation": 0.71},
    ]
    assert is_classification_ambiguous(clear_winner) is False


def test_is_classification_ambiguous_handles_fewer_than_two_candidates():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a single candidate (or none) can't be "too close to call"."""
    single_candidate = [{"spectral_type": "G0V", "probability": 1.0, "correlation": 0.9}]
    assert is_classification_ambiguous([]) is False
    assert is_classification_ambiguous(single_candidate) is False


def test_build_spectral_classification_concerns_flags_low_confidence_and_ambiguous():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify concerns are built only for classified, shaky stars."""
    unclassified = StellarObject(
        id="Unclassified", spectroscopy=SpectroscopyResult(self_determined_spectral_type="Unknown")
    )
    low_confidence = StellarObject(
        id="LowConfidenceStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="O5V",
            self_determined_spectral_type_confidence=0.33,
            self_determined_spectral_type_candidates=[
                {"spectral_type": "O5V", "probability": 0.65, "correlation": 0.33},
                {"spectral_type": "B0V", "probability": 0.34, "correlation": 0.32},
            ],
        ),
    )
    ambiguous = StellarObject(
        id="AmbiguousStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="K5V",
            self_determined_spectral_type_confidence=0.93,
            self_determined_spectral_type_candidates=[
                {"spectral_type": "K5V", "probability": 0.51, "correlation": 0.93},
                {"spectral_type": "M0V", "probability": 0.49, "correlation": 0.93},
            ],
        ),
    )
    confident = StellarObject(
        id="ConfidentStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="M5V",
            self_determined_spectral_type_confidence=0.87,
            self_determined_spectral_type_candidates=[
                {"spectral_type": "M5V", "probability": 0.98, "correlation": 0.87},
                {"spectral_type": "M0V", "probability": 0.02, "correlation": 0.71},
            ],
        ),
    )

    concerns = build_spectral_classification_concerns([unclassified, low_confidence, ambiguous, confident])

    concerns_by_id = {concern["star_id"]: concern for concern in concerns}
    assert set(concerns_by_id) == {"LowConfidenceStar", "AmbiguousStar"}
    assert concerns_by_id["LowConfidenceStar"]["reason"] == "low_confidence"
    assert concerns_by_id["AmbiguousStar"]["reason"] == "ambiguous"
