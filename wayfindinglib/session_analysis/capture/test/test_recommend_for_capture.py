"""Purpose: Unit tests for capture post-processing.

Description: Verifies that each recommendation appears for the evidence it
rests on and not otherwise: saturation advice for spectroscopy only, star
shape judged against earlier nights only, and every recommendation carrying
its evidence, limit and confidence.
"""

import pytest

from wayfindinglib.models.session.capture_quality import (
    CaptureEfficiency,
    CaptureInputQuality,
    CapturePerformance,
    ExposureClipping,
    StarQuality,
)
from wayfindinglib.models.session.session_quality import RecommendationKind, RecommendationSeverity
from wayfindinglib.session_analysis.capture.post_processing.recommend_for_capture import recommend_for_capture


def _input(**overrides: object) -> CaptureInputQuality:
    """Build an input-quality result for a complete, ordinary night.

    Returns
    -------
    input_quality : `CaptureInputQuality`
        Twelve frames, all measured, every exposure in the library.
    """
    fields: dict[str, object] = {
        "light_frames": 12,
        "imaging_frames": 12,
        "has_ekos_record": True,
        "has_enough_frames": True,
        "captures_without_frame": 0,
        "limits_equipment_match": "exact",
    }
    fields.update(overrides)
    return CaptureInputQuality(**fields)


def _group(
    target: str = "Vega",
    exposure: float = 1.0,
    clipped: bool = True,
    frames: int = 30,
    spectral: bool = True,
    basis: str = "science_stack",
    recommended: float | None = None,
) -> ExposureClipping:
    """Build one clipping result.

    Returns
    -------
    group : `ExposureClipping`
        A target's frames at one exposure length.
    """
    return ExposureClipping(
        target_id=target,
        is_spectral=spectral,
        exposure_seconds=exposure,
        frames=frames,
        clipped_frames=frames if clipped else 0,
        clipped_fraction=1.0 if clipped else 0.0,
        is_clipped=clipped,
        basis=basis,
        science_recommended_exposure_seconds=recommended,
    )


def _performance(clipping: list[ExposureClipping] | None = None, **star: object) -> CapturePerformance:
    """Build a processing result.

    Returns
    -------
    performance : `CapturePerformance`
        The given clipping and star quality, and some efficiency.
    """
    return CapturePerformance(
        clipping=clipping or [],
        star_quality=StarQuality(**star),
        efficiency=CaptureEfficiency(light_exposure_seconds=100.0, span_seconds=200.0, duty_cycle=0.5),
    )


def _kinds(recommendations: list) -> list[RecommendationKind]:
    """List the kinds of a set of recommendations.

    Returns
    -------
    kinds : `list` [`RecommendationKind`]
        The kinds, in order.
    """
    return [recommendation.kind for recommendation in recommendations]


def test_too_few_frames_says_so_and_stops() -> None:
    """Verify a night with too little data gets one information note."""
    quality = _input(light_frames=3, has_enough_frames=False, captures_without_frame=50)

    recommendations = recommend_for_capture(quality, _performance())

    assert _kinds(recommendations) == [RecommendationKind.INSUFFICIENT_DATA]
    assert recommendations[0].severity == RecommendationSeverity.INFO


def test_an_ordinary_night_is_within_limits() -> None:
    """Verify a night with no findings says it was within limits."""
    recommendations = recommend_for_capture(_input(), _performance(median_star_width_arcsec=None))

    assert _kinds(recommendations) == [RecommendationKind.CAPTURE_WITHIN_LIMITS]


def test_exposures_missing_from_the_library_are_advised() -> None:
    """Verify a download gap is reported with its counts."""
    recommendations = recommend_for_capture(
        _input(captures_without_frame=7, ekos_light_captures=19), _performance()
    )

    (recommendation,) = recommendations
    assert recommendation.kind == RecommendationKind.CAPTURES_NOT_IN_LIBRARY
    assert recommendation.evidence["captures_without_frame"] == pytest.approx(7.0)
    assert recommendation.confidence == "high"


def test_unclassified_captures_are_mentioned_but_not_counted_as_missing() -> None:
    """Verify exposures with no logged path do not raise the alarm alone."""
    recommendations = recommend_for_capture(
        _input(captures_without_frame=0, captures_without_frame_of_unknown_kind=40), _performance()
    )

    assert _kinds(recommendations) == [RecommendationKind.CAPTURE_WITHIN_LIMITS]


def test_missing_measurements_are_named() -> None:
    """Verify the advice names each measurement and its count."""
    recommendations = recommend_for_capture(
        _input(has_missing_measurements=True, frames_missing_measurements={"pixel_scale_arcsec": 3}),
        _performance(),
    )

    (recommendation,) = recommendations
    assert recommendation.kind == RecommendationKind.FRAMES_MISSING_MEASUREMENTS
    assert "pixel_scale_arcsec (3 frames)" in recommendation.message


def test_frequent_aborts_are_advised_with_the_limit() -> None:
    """Verify the cancelled share and its limit are in the advice."""
    recommendations = recommend_for_capture(
        _input(
            has_frequent_aborts=True,
            aborted_captures=9,
            abort_fraction=0.3,
            abort_fraction_limit=0.05,
        ),
        _performance(),
    )

    (recommendation,) = recommendations
    assert recommendation.kind == RecommendationKind.FREQUENT_ABORTED_CAPTURES
    assert recommendation.compared_to["abort_fraction_limit"] == pytest.approx(0.05)


def test_temperature_spread_is_information_only() -> None:
    """Verify temperatures beyond the dark tolerance never raise a warning."""
    recommendations = recommend_for_capture(
        _input(
            frames_outside_dark_tolerance=4,
            sensor_temperature_spread_c=9.5,
            dark_temperature_tolerance_c=3.0,
        ),
        _performance(),
    )

    (recommendation,) = recommendations
    assert recommendation.kind == RecommendationKind.SENSOR_TEMPERATURE_DRIFT
    assert recommendation.severity == RecommendationSeverity.INFO


def test_a_spectral_star_clipped_at_every_length_asks_for_a_shorter_exposure() -> None:
    """Verify the advice is to go below the shortest length tried."""
    performance = _performance([_group(exposure=2.0), _group(exposure=1.0)])

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert recommendation.kind == RecommendationKind.SPECTRAL_STAR_CLIPPED
    assert "every exposure length" in recommendation.message
    assert "1 s" in recommendation.message
    assert recommendation.confidence == "high"


def test_a_spectral_star_clipped_at_some_lengths_names_the_longest_clear_one() -> None:
    """Verify mixed results name the longest exposure that stayed clear."""
    performance = _performance([
        _group(exposure=0.1, clipped=False, frames=10),
        _group(exposure=0.5, clipped=False, frames=10),
        _group(exposure=2.0, clipped=True, frames=30),
    ])

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert "0.5 s" in recommendation.message
    assert recommendation.evidence["frames_at_clipped_lengths"] == pytest.approx(30.0)
    assert recommendation.evidence["frames_total"] == pytest.approx(50.0)


def test_the_science_librarys_recommended_exposure_is_quoted() -> None:
    """Verify the stacking analysis's own exposure estimate is passed on."""
    performance = _performance([_group(exposure=2.0, recommended=0.55)])

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert "0.55 s" in recommendation.message
    assert recommendation.compared_to["science_recommended_exposure_seconds"] == pytest.approx(0.55)


def test_a_verdict_worked_out_from_frame_counts_is_only_medium_confidence() -> None:
    """Verify the fallback verdict is weaker than the science library's."""
    performance = _performance([_group(basis="frame_pixel_count")])

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert recommendation.confidence == "medium"


def test_imaging_frames_are_never_advised_against_clipping() -> None:
    """Verify bright stars clipping in an imaging field is not a finding."""
    performance = _performance([_group(spectral=False, exposure=300.0)])

    assert _kinds(recommend_for_capture(_input(), performance)) == [RecommendationKind.CAPTURE_WITHIN_LIMITS]


def test_a_spectral_target_that_never_clipped_gets_no_advice() -> None:
    """Verify an unclipped target is left alone."""
    performance = _performance([_group(clipped=False)])

    assert _kinds(recommend_for_capture(_input(), performance)) == [RecommendationKind.CAPTURE_WITHIN_LIMITS]


def test_each_clipped_target_gets_its_own_recommendation() -> None:
    """Verify two targets give two recommendations, in name order."""
    performance = _performance([_group(target="Vega"), _group(target="Albireo")])

    recommendations = recommend_for_capture(_input(), performance)

    assert [r.evidence["target"] for r in recommendations] == ["Albireo", "Vega"]


def test_wide_stars_are_a_warning_against_earlier_nights() -> None:
    """Verify a width above the earlier-night limit is a warning."""
    performance = _performance(
        frames=40, median_star_width_arcsec=9.0, star_width_limit_arcsec=7.0, roundness_limit=0.7
    )

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert recommendation.kind == RecommendationKind.STAR_WIDTH_ABOVE_BASELINE
    assert recommendation.severity == RecommendationSeverity.WARNING
    assert recommendation.compared_to["night_star_width_high_limit"] == pytest.approx(7.0)


def test_elongated_stars_are_a_warning_against_earlier_nights() -> None:
    """Verify a roundness below the earlier-night limit is a warning."""
    performance = _performance(
        frames=40,
        median_star_width_arcsec=5.0,
        star_width_limit_arcsec=7.0,
        median_roundness=0.6,
        roundness_limit=0.75,
    )

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert recommendation.kind == RecommendationKind.STARS_ELONGATED
    assert recommendation.severity == RecommendationSeverity.WARNING


def test_stars_within_the_limits_raise_nothing() -> None:
    """Verify an ordinary star width and roundness is not a finding."""
    performance = _performance(
        frames=40,
        median_star_width_arcsec=5.0,
        star_width_limit_arcsec=7.0,
        median_roundness=0.86,
        roundness_limit=0.75,
    )

    assert _kinds(recommend_for_capture(_input(), performance)) == [RecommendationKind.CAPTURE_WITHIN_LIMITS]


def test_star_width_without_a_baseline_is_stated_not_judged() -> None:
    """Verify too few earlier nights means neither passed nor failed."""
    performance = _performance(frames=40, median_star_width_arcsec=9.0)

    (recommendation,) = recommend_for_capture(_input(), performance)

    assert recommendation.kind == RecommendationKind.INSUFFICIENT_DATA
    assert recommendation.severity == RecommendationSeverity.INFO


def test_recommendations_are_ordered_most_serious_first() -> None:
    """Verify warnings come before advice, advice before information."""
    performance = _performance(
        [_group()],
        frames=40,
        median_star_width_arcsec=9.0,
        star_width_limit_arcsec=7.0,
    )
    quality = _input(
        frames_outside_dark_tolerance=2,
        sensor_temperature_spread_c=8.0,
        dark_temperature_tolerance_c=3.0,
    )

    severities = [r.severity for r in recommend_for_capture(quality, performance)]

    assert severities == [
        RecommendationSeverity.WARNING,
        RecommendationSeverity.ADVICE,
        RecommendationSeverity.INFO,
    ]


def test_every_recommendation_carries_evidence_and_a_confidence() -> None:
    """Verify no recommendation is a bare assertion."""
    performance = _performance(
        [_group()], frames=40, median_star_width_arcsec=9.0, star_width_limit_arcsec=7.0
    )
    quality = _input(captures_without_frame=3, ekos_light_captures=15)

    for recommendation in recommend_for_capture(quality, performance):
        assert recommendation.evidence
        assert recommendation.confidence in {"high", "medium", "low"}
