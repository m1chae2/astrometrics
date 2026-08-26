"""Tests for guiding, input-condition, and rejection-effectiveness analysis.

Validated against synthetic series with known answers: a detector that
cannot recover a period it was handed cannot be trusted on real frames.
"""

import math
import random

import pytest

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.tasks.target_tasks.tracking_analysis_tasks import (
    _dominant_period_seconds,
    analyze_guiding,
    analyze_input_conditions,
    detect_meridian_flips,
    evaluate_rejection_effectiveness,
    fwhm_in_arcsec,
)

SAMPLE_TIMES = [index * 30.0 for index in range(80)]


def _target_with_shifts(times, shifts_x, shifts_y=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build a Target whose frames carry the given registration shifts.

    Returns
    -------
    target : `Target`
        Target with one frame per sample.
    """
    shifts_y = shifts_y if shifts_y is not None else [0.0] * len(times)
    frames = []
    for index, (time, dx, dy) in enumerate(zip(times, shifts_x, shifts_y, strict=True)):
        frame = FrameRecord(
            path=f"/frames/frame_{index:04d}.fits",
            role="LIGHT",
            camera="ZWO ASI 533MM Pro",
            exposure="30.0",
            timestamp=time,
        )
        # Assigned rather than passed to the constructor: these fields
        # carry camelCase aliases, so the snake_case names do not
        # populate them at construction time.
        frame.registration_dx_px = dx
        frame.registration_dy_px = dy
        frames.append(frame)
    return Target(id="TrackingTestTarget", frames=frames)


def test_recovers_a_known_periodic_error_period():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An 8-minute sinusoid must be reported as an ~8-minute period."""
    values = [3.0 * math.sin(2 * math.pi * t / 480.0) for t in SAMPLE_TIMES]

    period, power = _dominant_period_seconds(SAMPLE_TIMES, values)

    assert period == pytest.approx(480.0, abs=20.0)
    assert power > 0.5


def test_periodic_error_survives_superimposed_drift():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Real mounts drift while showing periodic error; both must resolve.

    Regression test for a detrending bug that anchored the slope at the
    first sample while also subtracting the mean, leaving a constant
    offset that pushed this exact case from 0.63 to 0.20 -- under the
    reporting threshold, so a true detection was silently dropped.
    """
    values = [3.0 * math.sin(2 * math.pi * t / 480.0) + 0.02 * t for t in SAMPLE_TIMES]

    period, power = _dominant_period_seconds(SAMPLE_TIMES, values)

    assert period == pytest.approx(480.0, abs=20.0)
    assert power > 0.5


def test_pure_drift_is_not_reported_as_periodic_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A straight ramp has no period and must not claim one."""
    values = [0.01 * t for t in SAMPLE_TIMES]

    _period, power = _dominant_period_seconds(SAMPLE_TIMES, values)

    assert power < 0.25


def test_noise_is_not_reported_as_periodic_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Random jitter must not be mistaken for a mount period."""
    random.seed(7)
    values = [random.gauss(0, 1) for _ in SAMPLE_TIMES]

    _period, power = _dominant_period_seconds(SAMPLE_TIMES, values)

    assert power < 0.25


def test_guiding_analysis_reports_drift_rate():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A steady ramp is reported as a drift rate per hour."""
    shifts = [0.01 * t for t in SAMPLE_TIMES]  # 36 px/hour

    analysis = analyze_guiding(_target_with_shifts(SAMPLE_TIMES, shifts))

    assert analysis["drift_rate_x_px_per_hour"] == pytest.approx(36.0, abs=1.0)
    assert any("drift" in finding.lower() for finding in analysis["findings"])


def test_guiding_analysis_flags_all_zero_shifts_as_unusable():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """All-zero shifts mean shifts were never captured, not good tracking."""
    analysis = analyze_guiding(_target_with_shifts(SAMPLE_TIMES, [0.0] * len(SAMPLE_TIMES)))

    assert any("zero shift" in finding for finding in analysis["findings"])
    assert analysis["periodic_error_period_seconds"] is None


def test_guiding_analysis_detects_a_single_excursion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """One large jump against a quiet series is called out."""
    shifts = [0.0] * len(SAMPLE_TIMES)
    shifts[40] = 60.0

    analysis = analyze_guiding(_target_with_shifts(SAMPLE_TIMES, shifts))

    assert analysis["max_excursion_px"] == pytest.approx(60.0)


def test_guiding_analysis_needs_enough_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Two frames cannot describe tracking."""
    analysis = analyze_guiding(_target_with_shifts([0.0, 30.0], [0.0, 1.0]))

    assert analysis["usable_frames"] == 2
    assert any("Not enough frames" in finding for finding in analysis["findings"])


def test_input_conditions_flag_trailed_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Frames with elongated stars are counted and reported."""
    target = _target_with_shifts(SAMPLE_TIMES[:4], [0.0] * 4)
    for frame, roundness in zip(target.frames, [0.95, 0.42, 0.51, 0.93], strict=True):
        frame.registration_roundness = roundness

    analysis = analyze_input_conditions(target)

    assert analysis["trailed_frame_count"] == 2
    assert any("elongated" in finding for finding in analysis["findings"])


def test_input_conditions_report_stable_night():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Consistent frames produce no findings beyond the all-clear."""
    target = _target_with_shifts(SAMPLE_TIMES[:4], [0.0] * 4)
    for frame in target.frames:
        frame.registration_roundness = 0.93
        frame.registration_fwhm_x_px = 3.1
        frame.background_level = 1000.0

    analysis = analyze_input_conditions(target)

    assert analysis["trailed_frame_count"] == 0
    assert analysis["findings"] == ["Seeing, star shape, and sky background are all consistent."]


def test_rejection_effectiveness_reports_no_rejections():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A target with nothing rejected says so rather than comparing."""
    target = _target_with_shifts(SAMPLE_TIMES[:3], [0.0] * 3)

    analysis = evaluate_rejection_effectiveness(target)

    assert analysis["rejected_count"] == 0
    assert analysis["comparable"] is False


def test_rejection_effectiveness_needs_measured_input():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Unmeasured frames cannot be compared, and the report says why."""
    from astrometricslib.models.quality_summary import (
        ExcludedFrame,
        StackingPipelineQualityMetrics,
        StackQualitySummary,
    )

    target = _target_with_shifts(SAMPLE_TIMES[:3], [0.0] * 3)
    target.stack_quality_summary = StackQualitySummary(
        target_id=target.id,
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False,
            frames_submitted=3,
            frames_stacked=2,
            excluded_frames=[ExcludedFrame(path=target.frames[0].path, reason="outlier")],
        ),
    )

    analysis = evaluate_rejection_effectiveness(target)

    assert analysis["rejected_count"] == 1
    assert analysis["comparable"] is False
    assert any("has not been measured" in finding for finding in analysis["findings"])


def _frame_with(**attributes):  # ruff: ignore[missing-return-type-private-function, missing-type-kwargs]
    """Build a light frame and set the given attributes on it.

    Returns
    -------
    frame : `FrameRecord`
        The configured frame record.
    """
    frame = FrameRecord(path="/frames/f.fits", role="LIGHT", camera="c", exposure="30.0")
    for name, value in attributes.items():
        setattr(frame, name, value)
    return frame


def test_fwhm_converts_to_arcsec():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Pixels become arcseconds using the frame's own pixel scale."""
    frame = _frame_with(registration_fwhm_x_px=3.2, pixel_scale_arcsec=1.915)

    assert fwhm_in_arcsec(frame) == pytest.approx(6.13, abs=0.01)


def test_fwhm_in_arcsec_needs_a_pixel_scale():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Without a scale the pixel count cannot be converted, so None."""
    assert fwhm_in_arcsec(_frame_with(registration_fwhm_x_px=3.2)) is None


def test_meridian_flip_is_detected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A pier-side change mid-session is located by frame index."""
    target = _target_with_shifts([0.0, 30.0, 60.0, 90.0], [0.0] * 4)
    for frame, side in zip(target.frames, ["EAST", "EAST", "WEST", "WEST"], strict=True):
        frame.pier_side = side

    assert detect_meridian_flips(target.frames) == [2]


def test_meridian_flip_shift_is_not_called_a_bump():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The field jump across a flip is expected, not a hardware fault."""
    shifts = [0.0] * len(SAMPLE_TIMES)
    for index in range(40, len(shifts)):
        shifts[index] = 500.0  # the flip displaces the field from here on
    target = _target_with_shifts(SAMPLE_TIMES, shifts)
    for index, frame in enumerate(target.frames):
        frame.pier_side = "EAST" if index < 40 else "WEST"

    analysis = analyze_guiding(target)

    assert analysis["meridian_flips"] == 1
    assert not any("bump" in finding for finding in analysis["findings"])


def test_a_single_session_period_is_hedged_not_asserted():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """One session cannot confirm mechanical periodic error on its own.

    0.25 let periods from 1 to 14 minutes through with no clustering
    across this catalog's sessions -- a real worm period is a mechanical
    constant, so that scatter meant the periodogram was fitting whatever
    a short session happened to contain. The finding text must not claim
    more than one session supports.
    """
    times = [index * 30.0 for index in range(80)]
    values = [3.0 * math.sin(2 * math.pi * t / 480.0) for t in times]
    target = _target_with_shifts(times, values)

    analysis = analyze_guiding(target)

    periodic_findings = [f for f in analysis["findings"] if "Periodic drift" in f]
    assert periodic_findings
    assert "single session cannot confirm" in periodic_findings[0]
    assert "consistent with mount periodic error" not in periodic_findings[0]


def test_a_weak_period_no_longer_clears_the_reporting_bar():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A detection that explained 25-49% of the variance is now dropped.

    That band is exactly what let unrelated periods through on real
    data: high-power detections varied from 1.3 to 5.0 minutes with no
    agreement either, so a weak detection is even less trustworthy.
    """
    times = [index * 30.0 for index in range(80)]
    random.seed(11)
    values = [1.0 * math.sin(2 * math.pi * t / 480.0) + random.gauss(0, 1.4) for t in times]
    _period, power = _dominant_period_seconds(times, values)

    assert power < 0.5
