"""Tests that the set-aside step's gate record tells its outcomes apart.

The gate must go red when more than a quarter of a session is set
aside, must stay green only when a session was really judged, and must
say "not checked" when nothing could be judged. A session too small to
judge must never read as a clean pass.
"""

from pathlib import Path
from typing import Any

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
    GATE_NAME,
    MINIMUM_FRAMES_TO_JUDGE,
    find_frames_to_set_aside,
    set_aside_gate,
)
from astrometricslib.pipelines.stacking.test.test_frame_set_aside import make_frame, make_measurement


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


def test_gate_fails_and_says_how_many_when_a_quarter_of_a_session_is_set_aside(tmp_path: Path) -> None:
    """Five bad frames of 12 are all moved; the red gate gives the counts."""
    frames = _batch(tmp_path, 12)
    measurements = {
        frame.path: make_measurement(frame.path, elongation=2.5 if index < 5 else 1.05)
        for index, frame in enumerate(frames)
    }

    gate = set_aside_gate(find_frames_to_set_aside(frames, measure=lambda path: dict(measurements[path])))

    assert gate.name == GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert gate.detail == "5 of 12 frames set aside: 5 trailed"


def test_gate_passes_when_a_session_was_judged_and_is_clean(tmp_path: Path) -> None:
    """A clean session of enough frames is a real pass."""
    frames = _batch(tmp_path, 12)

    gate = set_aside_gate(find_frames_to_set_aside(frames, measure=lambda path: make_measurement(path)))

    assert gate.status is GateStatus.PASSED
    assert gate.limit_source is not None


def test_gate_is_not_checked_when_every_session_is_too_small(tmp_path: Path) -> None:
    """Too few frames to judge is "not checked", never a pass."""
    frames = _batch(tmp_path, MINIMUM_FRAMES_TO_JUDGE.default - 1)

    gate = set_aside_gate(find_frames_to_set_aside(frames, measure=lambda path: make_measurement(path)))

    assert gate.status is GateStatus.NOT_CHECKED
    assert gate.status is not GateStatus.PASSED


def test_gate_is_not_checked_when_unreadable_frames_leave_too_few(tmp_path: Path) -> None:
    """A session left too small by read errors is not checked.

    This used to look like a clean pass, because judging a short list moves
    nothing.
    """
    frames = _batch(tmp_path, MINIMUM_FRAMES_TO_JUDGE.default + 1)

    def measure(path: str) -> dict[str, Any]:
        if int(Path(path).stem.removeprefix("f")) >= 2:
            raise OSError("truncated file")
        return make_measurement(path)

    gate = set_aside_gate(find_frames_to_set_aside(frames, measure=measure))

    assert gate.status is GateStatus.NOT_CHECKED


def test_gate_names_the_small_sessions_it_could_not_check(tmp_path: Path) -> None:
    """A pass says how many sessions were too small, so nothing is hidden."""
    small = _batch(tmp_path, 3, prefix="s", exposure="30.0")
    big = _batch(tmp_path, 12, prefix="b")

    gate = set_aside_gate(
        find_frames_to_set_aside([*small, *big], measure=lambda path: make_measurement(path))
    )

    assert gate.status is GateStatus.PASSED
    assert "1 session(s) too small to check" in gate.detail


def test_gate_is_not_checked_with_no_frames() -> None:
    """No frames at all is "not checked": nothing was judged."""
    gate = set_aside_gate(find_frames_to_set_aside([], measure=lambda path: make_measurement(path)))

    assert gate.status is GateStatus.NOT_CHECKED
