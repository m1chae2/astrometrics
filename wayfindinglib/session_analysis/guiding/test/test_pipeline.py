"""Purpose: Unit tests for the guiding analysis as a whole.

Description: Verifies that the three stages run in order and hand their
results on, that a night with too little data skips processing but still gets
a summary, that flags follow the warnings, and that the analysis is a pure
function of its request.
"""

from typing import Any

import pytest

from wayfindinglib.models.session.session_quality import RecommendationKind
from wayfindinglib.session_analysis.guiding.pipeline import (
    GuidingAnalysisRequest,
    GuidingSessionPipeline,
    analyze_guiding_session_request,
)
from wayfindinglib.session_analysis.pipeline_base import run_session_pipeline


def _request(envelope: Any, samples: Any, runs: Any, **overrides: Any) -> GuidingAnalysisRequest:
    """Build a request for the night of 2026-09-23.

    Returns
    -------
    request : `GuidingAnalysisRequest`
        The request, with any overrides applied.
    """
    values: dict[str, Any] = {
        "session_id": "2026-09-23",
        "equipment_fingerprint": "fingerprint-a",
        "envelope": envelope,
        "limits_equipment_match": "exact",
        "samples": samples,
        "runs": runs,
    }
    values.update(overrides)
    return GuidingAnalysisRequest(**values)


def test_a_good_night_is_not_flagged(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify a healthy night has no warnings and a guiding verdict."""
    analysis = analyze_guiding_session_request(
        _request(make_envelope(), make_samples(count=1500, sigma=0.8), [make_run(duration=4900.0)])
    )

    assert analysis.flagged is False
    assert analysis.flag_reasons == []
    assert analysis.input_quality.has_enough_samples is True
    assert analysis.performance.rms_per_axis_arcsec == pytest.approx(0.8, rel=0.1)
    assert RecommendationKind.GUIDING_WITHIN_LIMIT in [r.kind for r in analysis.recommendations]


def test_a_bad_night_is_flagged_with_the_reasons(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify warnings set the flag and are listed by kind."""
    run = make_run(duration=4900.0, frames_total=1500, frames_lost=400, ra_rate=446.0)

    analysis = analyze_guiding_session_request(
        _request(make_envelope(), make_samples(count=1500, sigma=2.0, snr=40.0), [run])
    )

    assert analysis.flagged is True
    assert RecommendationKind.RECALIBRATE_GUIDER.value in analysis.flag_reasons
    assert RecommendationKind.CHECK_GUIDE_SIGNAL.value in analysis.flag_reasons


def test_too_little_data_skips_processing_but_still_gives_a_summary(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a night that cannot be analysed says so and measures nothing."""
    analysis = analyze_guiding_session_request(
        _request(make_envelope(), make_samples(count=12), [make_run()])
    )

    assert analysis.input_quality.has_enough_samples is False
    assert analysis.performance.rms_per_axis_arcsec is None
    assert [r.kind for r in analysis.recommendations] == [RecommendationKind.INSUFFICIENT_DATA]
    assert analysis.flagged is False


def test_the_summary_records_the_limits_it_used(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify a result can be traced to the numbers behind it."""
    envelope = make_envelope()

    analysis = analyze_guiding_session_request(_request(envelope, make_samples(), [make_run()]))

    assert analysis.resolved_parameters["guiding_rms_limit"] == pytest.approx(
        envelope.value("guiding_rms_limit")
    )
    assert analysis.resolved_parameters["limits_equipment_match"] == "exact"
    assert analysis.resolved_parameters["blur_tolerance_fraction"] == pytest.approx(0.10)
    assert analysis.equipment_fingerprint == "fingerprint-a"
    assert analysis.pipeline_name == "guiding"


def test_the_analysis_is_a_pure_function_of_its_request(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify the same request always gives the same analysis."""
    envelope = make_envelope()
    samples = make_samples(count=800)
    runs = [make_run(duration=2600.0)]

    first = analyze_guiding_session_request(_request(envelope, samples, runs))
    second = analyze_guiding_session_request(_request(envelope, samples, runs))

    assert first.model_dump(exclude={"created_at"}) == second.model_dump(exclude={"created_at"})


def test_with_no_envelope_the_analysis_still_runs_without_verdicts(make_samples: Any, make_run: Any) -> None:
    """Verify missing equipment gives measurements but no verdicts."""
    analysis = analyze_guiding_session_request(
        _request(None, make_samples(count=800), [make_run(duration=2600.0)], limits_equipment_match="none")
    )

    assert analysis.performance.rms_per_axis_arcsec is not None
    assert analysis.input_quality.has_low_signal is None
    assert analysis.recommendations == []


def test_the_pipeline_follows_the_three_stage_order(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify the stages run in order: pre, processing, post."""
    calls: list[str] = []

    class RecordingPipeline(GuidingSessionPipeline):
        """A pipeline that records the order its stages run in."""

        def process_input(self, request: Any) -> Any:
            """Record, then run pre-processing.

            Returns
            -------
            result : `SessionAnalysisResult`
                The pre-processing result.
            """
            calls.append("pre")
            return super().process_input(request)

        def run(self, request: Any, result: Any) -> Any:
            """Record, then run processing.

            Returns
            -------
            result : `SessionAnalysisResult`
                The processing result.
            """
            calls.append("processing")
            return super().run(request, result)

        def validate_output(self, request: Any, result: Any) -> Any:
            """Record, then run post-processing.

            Returns
            -------
            summary : `GuidingSessionAnalysis`
                The summary.
            """
            calls.append("post")
            return super().validate_output(request, result)

    run_session_pipeline(
        RecordingPipeline(), _request(make_envelope(), make_samples(count=800), [make_run(duration=2600.0)])
    )

    assert calls == ["pre", "processing", "post"]


def test_the_result_dict_uses_camel_case_keys(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify the summary serialises with the wire aliases."""
    request = _request(make_envelope(), make_samples(count=800), [make_run(duration=2600.0)])
    pipeline = GuidingSessionPipeline()
    result = pipeline.process_input(request)
    result = pipeline.run(request, result)
    summary = pipeline.validate_output(request, result)

    data = pipeline.to_result_dict(request, result, summary)

    assert data["sessionId"] == "2026-09-23"
    assert "inputQuality" in data
    assert "rmsPerAxisArcsec" in data["performance"]
