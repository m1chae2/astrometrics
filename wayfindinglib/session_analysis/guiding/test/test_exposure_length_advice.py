"""Purpose: Unit tests for the exposure-length recommendation.

Description: Verifies that advice appears only when the longest exposure in use
rarely had clean guiding, names the main cause and the length that would have
worked, and has lower confidence when the guide signal was poor.
"""

from typing import Any

from wayfindinglib.models.session.session_quality import (
    ExposureFeasibility,
    GuidingInputQuality,
    GuidingPerformance,
    RecommendationKind,
)
from wayfindinglib.session_analysis.guiding.post_processing.recommend_for_guiding import recommend_for_guiding


def _advice(
    feasibility: list[ExposureFeasibility], reliable: float | None, make_envelope: Any, **quality: Any
) -> Any:
    """Run post-processing and pick out the exposure-length recommendation.

    Returns
    -------
    recommendation : `Recommendation` or `None`
        The exposure-length advice, if any was given.
    """
    input_quality = GuidingInputQuality(has_enough_samples=True, limits_equipment_match="exact", **quality)
    performance = GuidingPerformance(
        exposure_feasibility=feasibility, longest_reliable_exposure_seconds=reliable
    )
    found = recommend_for_guiding(input_quality, performance, make_envelope())
    return next((r for r in found if r.kind == RecommendationKind.EXPOSURE_LENGTH_LIMITED), None)


def _result(length: float, clean: float, lost: float = 0.0, jump: float = 0.0, wobble: float = 0.0) -> Any:
    """Build one exposure-length result.

    Returns
    -------
    result : `ExposureFeasibility`
        A length with 100 windows.
    """
    return ExposureFeasibility(
        exposure_seconds=length,
        windows=100,
        clean_fraction=clean,
        lost_fraction=lost,
        jump_fraction=jump,
        wobble_fraction=wobble,
    )


def test_advice_names_the_main_cause_and_the_length_that_would_work(make_envelope: Any) -> None:
    """Verify the message gives the cause, its share, and a workable length."""
    feasibility = [_result(60, 0.8), _result(300, 0.3, lost=0.6, jump=0.1)]

    recommendation = _advice(feasibility, 60.0, make_envelope)

    assert "30%" in recommendation.message
    assert "300 s" in recommendation.message
    assert "the guide star was lost" in recommendation.message
    assert "60 s" in recommendation.message
    assert recommendation.confidence == "medium"


def test_advice_says_when_no_length_would_work(make_envelope: Any) -> None:
    """Verify the message admits when every length in use fails."""
    recommendation = _advice([_result(300, 0.0, jump=1.0)], None, make_envelope)

    assert "No exposure length in use" in recommendation.message
    assert "the guider jumped" in recommendation.message


def test_no_advice_when_the_longest_length_mostly_works(make_envelope: Any) -> None:
    """Verify a length that works for most exposures gets no advice."""
    assert _advice([_result(300, 0.8, lost=0.1)], 300.0, make_envelope) is None


def test_no_advice_without_a_measurement(make_envelope: Any) -> None:
    """Verify an unknown exposure view gives no advice."""
    assert _advice([], None, make_envelope) is None


def test_poor_guide_signal_lowers_the_confidence(make_envelope: Any) -> None:
    """Verify the numbers of a bad measurement are not trusted fully."""
    recommendation = _advice(
        [_result(300, 0.0, jump=1.0)],
        None,
        make_envelope,
        has_low_signal=True,
        median_snr=30.0,
        snr_limit=100.0,
    )

    assert recommendation.confidence == "low"
