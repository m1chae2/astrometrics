"""Tests for persisting tracking/input-condition analysis as a quality summary.

`analyze_guiding` and `analyze_input_conditions` are read-only analyses
that existed with no caller outside their own tests; this wires their
results into the same queryable, persisted form every other pipeline's
findings already take.
"""

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking.tracking_analysis import build_tracking_quality_summary


def _frame(index, dx=0.0, dy=0.0, roundness=0.9, fwhm=4.0, background=800.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Build one frame with registration and input-quality data.

    Returns
    -------
    frame : `FrameRecord`
        A light frame carrying registration and background/FWHM facts.
    """
    frame = FrameRecord(
        path=f"/frames/frame_{index:04d}.fits",
        role="LIGHT",
        camera="ZWO ASI 533MM Pro",
        exposure="30.0",
        timestamp=index * 30.0,
    )
    frame.registration_dx_px = dx
    frame.registration_dy_px = dy
    frame.registration_roundness = roundness
    frame.registration_fwhm_x_px = fwhm
    frame.background_level = background
    return frame


def test_a_target_with_no_registration_data_gets_no_summary():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """An unstacked target has nothing this pipeline can analyze."""
    target = Target(id="NoDataTarget", frames=[])

    assert build_tracking_quality_summary(target) is None


def test_a_healthy_target_produces_an_unflagged_summary():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A quiet rig must not be reported as having a finding."""
    frames = [_frame(i, dx=i * 0.001, dy=i * 0.001) for i in range(20)]
    target = Target(id="HealthyTarget", frames=frames)

    summary = build_tracking_quality_summary(target)

    assert summary is not None
    assert summary.flagged is False
    assert summary.tracking_metrics.trailed_frame_count == 0


def test_trailed_frames_are_counted_and_flagged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Elongated stars must reach the persisted record and set flagged."""
    frames = [_frame(i, dx=i * 0.001, roundness=0.9) for i in range(15)]
    frames += [_frame(20 + i, dx=(20 + i) * 0.001, roundness=0.3) for i in range(5)]
    target = Target(id="TrailedTarget", frames=frames)

    summary = build_tracking_quality_summary(target)

    assert summary.tracking_metrics.trailed_frame_count == 5
    assert summary.flagged is True
    assert any("elongated" in reason for reason in summary.flag_reasons)


def test_a_confirmed_polar_misalignment_flags_the_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Sustained one-directional drift must reach flag_reasons."""
    frames = [_frame(i, dx=i * 0.02) for i in range(40)]  # ~2.4 px/hr but sustained
    target = Target(id="DriftingTarget", frames=frames)

    summary = build_tracking_quality_summary(target)

    assert summary.tracking_metrics.drift_rate_x_px_per_hour is not None
