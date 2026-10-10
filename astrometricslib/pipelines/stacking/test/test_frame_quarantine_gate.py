"""Tests that the quarantine step's gate record tells its outcomes apart.

The gate must go red when bad frames are left in the stack, must stay green
only when a session was really judged, and must say "not checked" when
nothing could be judged. A session too small to judge must never read as
a clean pass.
"""

from pathlib import Path
from typing import Any

from astrometricslib.models.gate_result import GateStatus
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    GATE_NAME,
    MINIMUM_FRAMES_TO_JUDGE,
    find_frames_to_quarantine,
    quarantine_gate,
)
from astrometricslib.pipelines.stacking.test.test_frame_quarantine import make_frame, make_measurement


def _batch(tmp_path: Path, count: int, prefix: str = "f", exposure: str = "120.0") -> list[Any]:
    """Build one session of stand-in frames.

    Returns
    -------
    frames : `list`
        ``count`` frames of one camera, filter and exposure, a minute apart.
    """
    return [
        make_frame(tmp_path / f"{prefix}{index}.fits", float(index), exposure=exposure)
        for index in range(count)
    ]


def test_gate_fails_when_too_many_bad_frames_are_left_in_the_stack(tmp_path: Path) -> None:
    """Five bad frames of 12 exceed the cap, so the gate is red."""
    frames = _batch(tmp_path, 12)
    measurements = {
        frame.path: make_measurement(frame.path, roundness=0.4 if index < 5 else 0.97)
        for index, frame in enumerate(frames)
    }

    gate = quarantine_gate(find_frames_to_quarantine(frames, measure=lambda path: dict(measurements[path])))

    assert gate.name == GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert "still in the stack" in gate.detail


def test_gate_passes_when_a_session_was_judged_and_is_clean(tmp_path: Path) -> None:
    """A clean session of enough frames is a real pass."""
    frames = _batch(tmp_path, 12)

    gate = quarantine_gate(find_frames_to_quarantine(frames, measure=lambda path: make_measurement(path)))

    assert gate.status is GateStatus.PASSED
    assert gate.limit_source is not None


def test_gate_is_not_checked_when_every_session_is_too_small(tmp_path: Path) -> None:
    """Too few frames to judge is "not checked", never a pass."""
    frames = _batch(tmp_path, MINIMUM_FRAMES_TO_JUDGE - 1)

    gate = quarantine_gate(find_frames_to_quarantine(frames, measure=lambda path: make_measurement(path)))

    assert gate.status is GateStatus.NOT_CHECKED
    assert gate.status is not GateStatus.PASSED


def test_gate_is_not_checked_when_unreadable_frames_leave_too_few(tmp_path: Path) -> None:
    """A session left too small by read errors is not checked.

    This used to look like a clean pass, because judging a short list moves
    nothing.
    """
    frames = _batch(tmp_path, MINIMUM_FRAMES_TO_JUDGE + 1)

    def measure(path: str) -> dict[str, Any]:
        if int(Path(path).stem.removeprefix("f")) >= 2:
            raise OSError("truncated file")
        return make_measurement(path)

    gate = quarantine_gate(find_frames_to_quarantine(frames, measure=measure))

    assert gate.status is GateStatus.NOT_CHECKED


def test_gate_names_the_small_sessions_it_could_not_check(tmp_path: Path) -> None:
    """A pass says how many sessions were too small, so nothing is hidden."""
    small = _batch(tmp_path, 3, prefix="s", exposure="30.0")
    big = _batch(tmp_path, 12, prefix="b")

    gate = quarantine_gate(
        find_frames_to_quarantine([*small, *big], measure=lambda path: make_measurement(path))
    )

    assert gate.status is GateStatus.PASSED
    assert "1 session(s) too small to check" in gate.detail


def test_gate_is_not_checked_with_no_frames() -> None:
    """No frames at all is "not checked": nothing was judged."""
    gate = quarantine_gate(find_frames_to_quarantine([], measure=lambda path: make_measurement(path)))

    assert gate.status is GateStatus.NOT_CHECKED
