"""Purpose: Tests that a stack's counts describe only the groups combined.

Description: A stack of several exposure lengths can leave a whole group out
(it failed in Siril, could not be lined up, or was nonlinear). These tests
check that the frame count, the session breakdown and the sharpness
sample use only the groups that went into the final stack, that a
left-out group reports no stacked frames, and that the
`exposure_groups_combined` gate names what was lost. A stack with no
group information must behave as before.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
    EXPOSURE_GROUPS_COMBINED_GATE_NAME,
    build_group_summary,
    exposure_groups_combined_gate,
)
from astrometricslib.pipelines.stacking.stack_runner import _merge_diagnostics

MEASURE = "astrometricslib.pipelines.astrometry.pre_processing.fwhm.measure_image_fwhm"


def make_frames(prefix: str, count: int) -> list[SimpleNamespace]:
    """Build stand-in frames with a path, a timestamp and a gain setting.

    Returns
    -------
    frames : `list` [`SimpleNamespace`]
        The frames, named ``<prefix><index>``.
    """
    return [
        SimpleNamespace(
            path=f"{prefix}{index}", camera="cam", iso="800", offset="0", timestamp=1710540000.0 + index
        )
        for index in range(count)
    ]


def entry(exposure: float, submitted: int, reason: str | None = None) -> dict:
    """Build a group summary entry.

    Returns
    -------
    entry : `dict`
        The fields of an exposure group summary.
    """
    return build_group_summary(
        exposure, make_frames("x", submitted), {"images_stacked": submitted}, [], left_out_reason=reason
    )


def test_a_left_out_group_reports_no_stacked_frames() -> None:
    """A left-out group holds no frames of the combined stack."""
    assert entry(120.0, 13)["frames_stacked"] == 13
    assert entry(180.0, 65, "was left out: nonlinear")["frames_stacked"] == 0


def test_the_gate_is_not_checked_for_one_group() -> None:
    """One exposure length leaves nothing to combine."""
    gate = exposure_groups_combined_gate([entry(120.0, 13)])
    assert gate.name == EXPOSURE_GROUPS_COMBINED_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED
    assert exposure_groups_combined_gate([]).status is GateStatus.NOT_CHECKED


def test_the_gate_passes_when_every_group_was_combined() -> None:
    """Nothing dropped means a pass with no frames lost."""
    gate = exposure_groups_combined_gate([entry(60.0, 10), entry(180.0, 20)])
    assert gate.name == EXPOSURE_GROUPS_COMBINED_GATE_NAME
    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(0.0)


def test_the_gate_fails_and_names_a_group_that_failed_to_stack() -> None:
    """The M 106 case: 65 of 78 frames lost to a failed group."""
    gate = exposure_groups_combined_gate([entry(120.0, 13), entry(181.846, 65, "could not be stacked")])
    assert gate.name == EXPOSURE_GROUPS_COMBINED_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(65 / 78)
    assert "13 of 78 frames" in gate.detail
    assert "181.846 s (65 frames: could not be stacked)" in gate.detail


def test_the_gate_fails_for_a_group_left_out_as_nonlinear() -> None:
    """A group left out after it was stacked fails the gate too."""
    gate = exposure_groups_combined_gate([entry(60.0, 10, "was left out: nonlinear"), entry(180.0, 60)])
    assert gate.name == EXPOSURE_GROUPS_COMBINED_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert "left out: 60 s (10 frames: was left out: nonlinear)" in gate.detail


def test_the_merged_diagnostics_list_only_the_combined_frames() -> None:
    """The paths of a group that was not given to the merge do not appear."""
    kept = SimpleNamespace(exposure_seconds=120.0, frames=make_frames("kept", 3))
    merged = _merge_diagnostics([(kept, "kept.fits", {"images_stacked": 3})])
    assert merged["combined_frame_paths"] == ["kept0", "kept1", "kept2"]


def test_frames_stacked_and_sessions_count_only_combined_frames() -> None:
    """78 frames submitted but only 13 combined reports 13 stacked."""
    frames = [*make_frames("a", 13), *make_frames("b", 65)]
    diagnostics = {"combined_frame_paths": [frame.path for frame in frames[:13]]}
    summary = stage._base_stack_quality_summary(
        SimpleNamespace(id="M 106"), False, 90, frames, [], diagnostics
    )
    assert summary.stacking_metrics.frames_stacked == 13
    assert sum(session.frames_contributed for session in summary.target_session_breakdown) == 13


def test_frames_stacked_is_unchanged_without_group_information() -> None:
    """A single-group stack still counts all its frames minus corrupt ones."""
    frames = make_frames("a", 20)
    summary = stage._base_stack_quality_summary(
        SimpleNamespace(id="M 31"), False, 20, frames, [], {"corrupt_frames_skipped": ["a0"]}
    )
    assert summary.stacking_metrics.frames_stacked == 19
    assert stage._frames_in_combined_groups(frames, {}) is frames


def test_the_sharpness_sample_uses_only_combined_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Frames of a left-out group are not measured for the expected width."""
    monkeypatch.setattr(stage, "_fwhm_worker_count", lambda sample_size: 1)
    frames = [*make_frames("kept", 30), *make_frames("dropped", 30)]
    combined = stage._frames_in_combined_groups(
        frames, {"combined_frame_paths": [frame.path for frame in frames[:30]]}
    )
    measured: list[str] = []

    def fake_measure(path: str) -> float:
        """Record the path and give a fixed width.

        Returns
        -------
        width : `float`
            2 pixels.
        """
        measured.append(path)
        return 2.0

    summary = SimpleNamespace(
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=60, frames_stacked=30
        )
    )
    with patch(MEASURE, side_effect=fake_measure):
        stage._measure_fwhm_degradation(summary, "stack.fits", combined, stacked_fwhm=2.0)

    assert measured
    assert all(path.startswith("kept") for path in measured)
