"""Purpose: Unit tests for sky-position pre-processing, advice and pipeline.

Description: Checks that coverage and gaps are counted by nights, that advice
follows the evidence (a poor region, a suggested minimum altitude only above
the configured one, gaps, and the statement that nothing was poor), and that a
whole analysis runs from a request to a summary.
"""

from typing import Any

import pytest

from wayfindinglib.models.session.session_quality import RecommendationKind, RecommendationSeverity
from wayfindinglib.session_analysis.sky.pipeline import SkyAnalysisRequest, analyze_sky_request
from wayfindinglib.session_analysis.sky.pre_processing.assess_sky_input_quality import (
    MINIMUM_NIGHTS_PER_BIN,
    assess_sky_input_quality,
)


def _request(samples: list, **overrides: Any) -> SkyAnalysisRequest:
    """Build a request for this observatory's telescope.

    Returns
    -------
    request : `SkyAnalysisRequest`
        A request covering every altitude, with the standard tolerance.
    """
    fields: dict[str, Any] = {
        "session_id": "2026-01-01..2026-01-08",
        "equipment_fingerprint": "fingerprint-a",
        "envelope": None,
        "samples": samples,
        "minimum_altitude_degrees": 0.0,
        "maximum_altitude_degrees": 90.0,
        "blur_tolerance_fraction": 0.10,
    }
    fields.update(overrides)
    return SkyAnalysisRequest(**fields)


def test_coverage_counts_nights_and_samples_per_part(make_nights: Any) -> None:
    """Verify each part of the sky reports the nights that reached it."""
    samples = make_nights(nights=6, bands={35.0: 1.0, 65.0: 1.0})

    quality = assess_sky_input_quality(samples, 2, 1, 0.0, 90.0)
    by_part = {(bin_.dimension, bin_.label): bin_ for bin_ in quality.coverage}

    assert quality.nights == 6
    assert quality.samples_without_position == 2
    assert quality.guiding_nights_excluded == 1
    assert by_part["altitude", "30-45 deg"].nights == 6
    assert by_part["altitude", "30-45 deg"].samples == 60
    assert by_part["altitude", "45-60 deg"].nights == 0
    assert quality.altitude_range_degrees == [35.0, 65.0]
    assert quality.has_enough_data


def test_parts_reached_on_too_few_nights_are_gaps(make_nights: Any) -> None:
    """Verify an empty or barely visited part is listed, not assumed fine."""
    quality = assess_sky_input_quality(make_nights(nights=6), 0, 0, 30.0, 90.0)

    assert "altitude 45-60 deg" in quality.gaps
    assert "pier_side East" in quality.gaps
    assert "altitude 30-45 deg" not in quality.gaps
    assert "altitude 0-15 deg" not in [bin_.label for bin_ in quality.coverage]
    assert quality.minimum_nights_per_bin == MINIMUM_NIGHTS_PER_BIN


def test_star_roundness_does_not_count_a_frame_twice(make_night: Any) -> None:
    """Verify coverage counts frames once, not once per metric."""
    samples = make_night("2026-01-01") + make_night("2026-01-01", metric="star_roundness", base=0.9)

    quality = assess_sky_input_quality(samples, 0, 0, 0.0, 90.0)
    low = next(b for b in quality.coverage if (b.dimension, b.label) == ("altitude", "30-45 deg"))

    assert low.samples == 10


def test_too_few_nights_is_only_a_statement_of_the_shortfall(make_nights: Any) -> None:
    """Verify a handful of nights gives no comparison."""
    analysis = analyze_sky_request(_request(make_nights(nights=3)))

    assert [r.kind for r in analysis.recommendations] == [RecommendationKind.INSUFFICIENT_DATA]
    assert analysis.performance.metrics == []


def test_a_planted_low_altitude_effect_gives_advice_and_a_minimum_altitude(make_nights: Any) -> None:
    """Verify the poor band and the altitude suggestion both appear."""
    samples = make_nights(bands={35.0: 1.3, 65.0: 1.0})

    analysis = analyze_sky_request(_request(samples))
    kinds = [r.kind for r in analysis.recommendations]

    assert RecommendationKind.SKY_REGION_POOR in kinds
    assert RecommendationKind.MINIMUM_ALTITUDE_SUGGESTED in kinds
    poor = next(r for r in analysis.recommendations if r.kind == RecommendationKind.SKY_REGION_POOR)
    assert poor.severity == RecommendationSeverity.ADVICE
    assert poor.confidence == "medium"
    assert "poor hour" in poor.message
    assert poor.compared_to["blur_tolerance_fraction"] == pytest.approx(0.10)


def test_no_minimum_altitude_is_suggested_below_the_configured_one(make_nights: Any) -> None:
    """Verify a telescope limited to 50 degrees is not told to use 45."""
    samples = make_nights(bands={35.0: 1.3, 65.0: 1.0})

    analysis = analyze_sky_request(_request(samples, minimum_altitude_degrees=50.0))

    assert RecommendationKind.MINIMUM_ALTITUDE_SUGGESTED not in [r.kind for r in analysis.recommendations]


def test_nothing_poor_says_so(make_nights: Any) -> None:
    """Verify uniform nights give the within-limits statement and the gaps."""
    analysis = analyze_sky_request(_request(make_nights(bands={35.0: 1.0, 65.0: 1.0})))
    kinds = {r.kind for r in analysis.recommendations}

    assert RecommendationKind.SKY_REGION_POOR not in kinds
    assert RecommendationKind.SKY_COVERAGE_GAP in kinds
    assert not analysis.flagged
    assert analysis.pipeline_name == "sky"
    assert analysis.resolved_parameters["minimum_nights_per_bin"] == MINIMUM_NIGHTS_PER_BIN


def test_a_dimension_nobody_compared_along_is_explained(make_nights: Any) -> None:
    """Verify pier side, seen from one side only, says how to compare."""
    analysis = analyze_sky_request(_request(make_nights(bands={35.0: 1.0, 65.0: 1.0})))

    messages = [r.message for r in analysis.recommendations if r.kind == RecommendationKind.SKY_COVERAGE_GAP]

    assert any("pier side" in message and "both sides of the meridian" in message for message in messages)
