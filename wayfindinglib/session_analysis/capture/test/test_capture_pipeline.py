"""Purpose: Unit tests for the capture-analysis pipeline.

Description: Runs whole nights through pre-processing, processing and
post-processing and checks the summary: that a good night is not flagged,
that a night with a real problem is flagged with the limits behind it, that a
night with too little data skips the measurements, and that the result can be
traced to the limits and definitions it used.
"""

from typing import Any

import pytest

from wayfindinglib.session_analysis.capture.pipeline import (
    CaptureAnalysisRequest,
    CaptureSessionPipeline,
    analyze_capture_session_request,
)
from wayfindinglib.session_analysis.capture.test.conftest import SENSOR_PIXELS
from wayfindinglib.session_analysis.pipeline_base import run_session_pipeline


def _request(make_envelope: Any, frames: list, captures: list, **overrides: Any) -> CaptureAnalysisRequest:
    """Build a request for a night of this observatory's equipment.

    Returns
    -------
    request : `CaptureAnalysisRequest`
        The night's data, with an envelope and an Ekos record.
    """
    fields: dict[str, Any] = {
        "session_id": "2026-09-23",
        "equipment_fingerprint": "fingerprint-a",
        "envelope": make_envelope(),
        "limits_equipment_match": "exact",
        "frames": frames,
        "captures": captures,
        "has_ekos_record": True,
        "sensor_pixel_count": SENSOR_PIXELS,
    }
    fields.update(overrides)
    return CaptureAnalysisRequest(**fields)


def test_a_good_night_is_not_flagged(make_frame: Any, make_capture: Any, make_envelope: Any) -> None:
    """Verify a complete, sharp night passes with no warnings."""
    frames = [make_frame(index) for index in range(40)]
    captures = [make_capture(index) for index in range(40)]

    analysis = analyze_capture_session_request(_request(make_envelope, frames, captures))

    assert not analysis.flagged
    assert analysis.flag_reasons == []
    assert analysis.pipeline_name == "capture"
    assert analysis.performance.star_quality.median_star_width_arcsec == pytest.approx(5.5)
    assert [r.kind.value for r in analysis.recommendations] == ["capture_within_limits"]


def test_soft_stars_flag_the_night_with_the_limit_behind_it(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify wide stars raise a warning that cites the earlier-night limit."""
    frames = [make_frame(index, star_width_arcsec=12.0) for index in range(40)]
    captures = [make_capture(index) for index in range(40)]

    analysis = analyze_capture_session_request(_request(make_envelope, frames, captures))

    assert analysis.flagged
    assert analysis.flag_reasons == ["star_width_above_baseline"]
    assert analysis.resolved_parameters["night_star_width_high_limit"] is not None


def test_a_download_gap_and_a_clipped_star_are_both_reported(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify problems from different stages appear together."""
    clipped = 50 / SENSOR_PIXELS
    frames = [
        make_frame(index, is_spectral=True, exposure_seconds=2.0, saturated_pixel_fraction=clipped)
        for index in range(20)
    ]
    captures = [make_capture(index, exposure_seconds=2.0) for index in range(20)]
    captures += [make_capture(30 + index) for index in range(5)]

    analysis = analyze_capture_session_request(_request(make_envelope, frames, captures))

    kinds = {recommendation.kind.value for recommendation in analysis.recommendations}
    assert {"captures_not_in_library", "spectral_star_clipped"} <= kinds
    assert analysis.input_quality.captures_without_frame == 5


def test_a_night_with_too_few_frames_skips_processing(make_frame: Any, make_envelope: Any) -> None:
    """Verify processing is skipped when there is too little data."""
    request = _request(make_envelope, [make_frame(index) for index in range(3)], [])

    result = CaptureSessionPipeline().process_input(request)
    analysis = analyze_capture_session_request(request)

    assert not result.has_work
    assert analysis.performance.clipping == []
    assert [r.kind.value for r in analysis.recommendations] == ["insufficient_data"]


def test_the_summary_records_the_definitions_it_used(
    make_frame: Any, make_capture: Any, make_envelope: Any
) -> None:
    """Verify the science-library definitions are named in the result."""
    frames = [make_frame(index) for index in range(12)]

    analysis = analyze_capture_session_request(_request(make_envelope, frames, []))

    assert analysis.resolved_parameters["saturated_star_minimum_pixels"] == 4
    assert analysis.resolved_parameters["clipped_frame_fraction"] == pytest.approx(0.5)
    assert analysis.resolved_parameters["dark_temperature_tolerance_c"] == pytest.approx(3.0)
    assert analysis.resolved_parameters["minimum_star_measurement_exposure"] is not None


def test_the_pipeline_runs_through_the_shared_runner(make_frame: Any, make_envelope: Any) -> None:
    """Verify the pipeline is an ordinary session-analysis pipeline."""
    request = _request(make_envelope, [make_frame(index) for index in range(12)], [])
    pipeline = CaptureSessionPipeline()

    summary = run_session_pipeline(pipeline, request)
    as_dict = pipeline.to_result_dict(request, pipeline.process_input(request), summary)

    assert as_dict["pipelineName"] == "capture"
    assert as_dict["sessionId"] == "2026-09-23"
    assert "inputQuality" in as_dict
    assert "performance" in as_dict
