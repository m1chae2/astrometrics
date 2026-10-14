"""Purpose: Unit tests for guiding post-processing.

Description: Verifies that each recommendation appears when, and only when,
its evidence calls for it, that it names the evidence and limit behind it,
and that confidence is lowered when the evidence is weaker.
"""

from typing import Any

import pytest

from wayfindinglib.models.session.session_quality import (
    GuidingInputQuality,
    GuidingPerformance,
    RecommendationKind,
    RecommendationSeverity,
)
from wayfindinglib.session_analysis.guiding.post_processing.recommend_for_guiding import recommend_for_guiding


def _quality(**overrides: Any) -> GuidingInputQuality:
    """Build a good night's input quality, with overrides.

    Returns
    -------
    input_quality : `GuidingInputQuality`
        A night with enough samples and no problems.
    """
    values: dict[str, Any] = {
        "has_enough_samples": True,
        "samples_analyzed": 400,
        "limits_equipment_match": "exact",
        "has_high_loss": False,
        "has_frequent_excursions": False,
        "has_low_signal": False,
    }
    values.update(overrides)
    return GuidingInputQuality(**values)


def _kinds(recommendations: Any) -> list[RecommendationKind]:
    """List the kinds of a set of recommendations.

    Returns
    -------
    kinds : `list` [`RecommendationKind`]
        The kinds, in order.
    """
    return [recommendation.kind for recommendation in recommendations]


def test_a_night_with_too_little_data_gets_only_that_statement(make_envelope: Any) -> None:
    """Verify nothing else is said about a night that cannot be judged."""
    quality = _quality(has_enough_samples=False, samples_analyzed=37, calibration_problems=["x"])

    recommendations = recommend_for_guiding(quality, GuidingPerformance(), make_envelope())

    assert _kinds(recommendations) == [RecommendationKind.INSUFFICIENT_DATA]
    assert recommendations[0].evidence["samples"] == pytest.approx(37.0)


def test_good_guiding_is_reported_as_within_the_limit(make_envelope: Any) -> None:
    """Verify a good night gets an information-level statement."""
    envelope = make_envelope()
    performance = GuidingPerformance(rms_per_axis_arcsec=0.8, expected_star_widening_fraction=0.03)

    recommendations = recommend_for_guiding(_quality(), performance, envelope)

    assert _kinds(recommendations) == [RecommendationKind.GUIDING_WITHIN_LIMIT]
    assert recommendations[0].severity == RecommendationSeverity.INFO
    assert recommendations[0].confidence == "high"
    assert recommendations[0].compared_to["guiding_rms_limit"] == pytest.approx(
        envelope.value("guiding_rms_limit")
    )


def test_guiding_above_the_limit_gets_advice_and_names_the_widening(make_envelope: Any) -> None:
    """Verify an error over the limit is advice that quotes the widening."""
    performance = GuidingPerformance(rms_per_axis_arcsec=2.5, expected_star_widening_fraction=0.42)

    recommendations = recommend_for_guiding(_quality(), performance, make_envelope())

    (recommendation,) = recommendations
    assert recommendation.kind == RecommendationKind.GUIDING_ABOVE_LIMIT
    assert recommendation.severity == RecommendationSeverity.ADVICE
    assert "42%" in recommendation.message


def test_an_impossible_calibration_is_a_warning(make_envelope: Any) -> None:
    """Verify a bad calibration asks for recalibration and names the speed."""
    quality = _quality(calibration_problems=["guide_log.txt#0: the RA guide speed is 446.0 arcsec/s"])

    recommendations = recommend_for_guiding(quality, GuidingPerformance(), make_envelope())

    assert recommendations[0].kind == RecommendationKind.RECALIBRATE_GUIDER
    assert recommendations[0].severity == RecommendationSeverity.WARNING
    assert "446.0" in recommendations[0].message


def test_a_weak_lossy_night_is_a_warning_that_lists_what_failed(make_envelope: Any) -> None:
    """Verify each failed check is named, and the causes are hedged."""
    quality = _quality(
        has_low_signal=True,
        median_snr=45.0,
        snr_limit=26.0,
        has_high_loss=True,
        lost_fraction=0.09,
        lost_fraction_limit=0.002,
    )

    recommendations = recommend_for_guiding(quality, GuidingPerformance(), make_envelope())

    (recommendation,) = recommendations
    assert recommendation.kind == RecommendationKind.CHECK_GUIDE_SIGNAL
    assert recommendation.severity == RecommendationSeverity.WARNING
    assert "SNR 45" in recommendation.message
    assert "9.0%" in recommendation.message
    assert "cannot say why" in recommendation.message


def test_a_guide_optics_mismatch_is_a_warning(make_envelope: Any) -> None:
    """Verify disagreement with the configuration asks for it to be updated."""
    quality = _quality(guide_scale_matches_configuration=False)

    recommendations = recommend_for_guiding(
        quality,
        GuidingPerformance(),
        make_envelope(),
        logged_guide_scale=6.39,
        configured_guide_scale=1.91,
    )

    assert recommendations[0].kind == RecommendationKind.UPDATE_GUIDE_OPTICS_CONFIGURATION
    assert recommendations[0].evidence["logged_guide_scale_arcsec_per_px"] == pytest.approx(6.39)
    assert recommendations[0].compared_to["configured_guide_scale_arcsec_per_px"] == pytest.approx(1.91)


def test_error_numbers_from_a_poor_measurement_get_low_confidence(make_envelope: Any) -> None:
    """Verify guiding error is not trusted when the guide signal was bad."""
    quality = _quality(has_low_signal=True, median_snr=40.0, snr_limit=26.0)
    performance = GuidingPerformance(rms_per_axis_arcsec=2.5)

    recommendations = recommend_for_guiding(quality, performance, make_envelope())

    guiding = next(r for r in recommendations if r.kind == RecommendationKind.GUIDING_ABOVE_LIMIT)
    assert guiding.confidence == "low"


def test_limits_from_partly_matching_equipment_get_medium_confidence(make_envelope: Any) -> None:
    """Verify a guide-optics-only match is never high confidence."""
    quality = _quality(limits_equipment_match="guide_optics_only")

    recommendations = recommend_for_guiding(
        quality, GuidingPerformance(rms_per_axis_arcsec=0.8), make_envelope(), []
    )

    assert recommendations[0].confidence == "medium"


def test_no_judgement_of_guiding_error_when_no_limits_apply(make_envelope: Any) -> None:
    """Verify equipment that does not match gets no verdict on the error."""
    quality = _quality(limits_equipment_match="none")

    recommendations = recommend_for_guiding(
        quality, GuidingPerformance(rms_per_axis_arcsec=0.8), make_envelope(), []
    )

    assert recommendations == []


def test_no_polar_alignment_claim_is_ever_made(make_envelope: Any) -> None:
    """Verify no recommendation names polar alignment.

    The declination corrections were tested against the pattern a polar
    misalignment must produce and did not fit, so the analysis does not
    estimate or advise on polar alignment.
    """
    performance = GuidingPerformance(rms_per_axis_arcsec=2.5, net_dec_correction_arcsec_per_minute=8.0)

    recommendations = recommend_for_guiding(_quality(), performance, make_envelope())

    assert not any("polar" in r.kind.value or "polar" in r.message.lower() for r in recommendations)


def test_recommendations_are_ordered_most_serious_first(make_envelope: Any) -> None:
    """Verify warnings come before advice, and advice before information."""
    quality = _quality(calibration_problems=["x"], has_low_signal=True, median_snr=30.0, snr_limit=26.0)
    performance = GuidingPerformance(rms_per_axis_arcsec=2.5)

    recommendations = recommend_for_guiding(quality, performance, make_envelope())

    severities = [recommendation.severity for recommendation in recommendations]
    assert severities == sorted(severities, key=lambda s: {"warning": 0, "advice": 1, "info": 2}[s.value])
    assert severities[0] == RecommendationSeverity.WARNING


def test_a_dim_star_says_how_much_fainter_and_whether_a_shorter_exposure_explains_it() -> None:
    """Verify the warning gives the ratio and rules out the exposure."""
    quality = _quality(
        has_dim_star=True,
        median_star_mass=4000.0,
        star_mass_limit=50000.0,
        typical_star_mass=200000.0,
        cadence_seconds=3.4,
        typical_cadence_seconds=3.2,
    )

    recommendations = recommend_for_guiding(quality, GuidingPerformance(), None)
    (warning,) = [r for r in recommendations if r.kind == RecommendationKind.CHECK_GUIDE_SIGNAL]

    assert "50 times fainter" in warning.message
    assert "explains at most a factor of 1.0" in warning.message
    assert warning.severity == RecommendationSeverity.WARNING
    assert warning.evidence["median_star_mass"] == pytest.approx(4000.0)


def test_a_shorter_guide_cycle_is_credited_with_its_share_of_the_drop() -> None:
    """Verify a cycle cut from 3.2 s to 1.6 s explains a factor of 2."""
    quality = _quality(
        has_dim_star=True,
        median_star_mass=50000.0,
        star_mass_limit=60000.0,
        typical_star_mass=200000.0,
        cadence_seconds=1.6,
        typical_cadence_seconds=3.2,
    )

    (warning,) = [
        r
        for r in recommend_for_guiding(quality, GuidingPerformance(), None)
        if r.kind == RecommendationKind.CHECK_GUIDE_SIGNAL
    ]

    assert "explains at most a factor of 2.0" in warning.message
