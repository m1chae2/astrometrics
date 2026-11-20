"""Tests for the helpers that tuners share.

The statistics get numbers with a known answer. The colour check gets a small
stand-in library, and the frame header reader is replaced so no file is read.
"""

from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers import tuning_support
from astrometricslib._internal.drivers.tuning_support import (
    camera_is_colour,
    finite_values,
    robust_sigma,
    widest_gap,
)


def test_robust_sigma_ignores_a_stray_value() -> None:
    """A single wild value does not change the spread of the others."""
    clean = [0.0, 1.0, -1.0, 2.0, -2.0]
    with_stray = [*clean, 1000.0]
    assert robust_sigma(with_stray) < 2 * robust_sigma(clean)


def test_robust_sigma_of_identical_values_is_zero() -> None:
    """Numbers that are all alike have no spread."""
    assert robust_sigma([3.0, 3.0, 3.0]) == pytest.approx(0.0)


def test_widest_gap_finds_the_biggest_stretch() -> None:
    """The widest gap lies between the clean group and the broken one."""
    assert widest_gap([0.0, 0.01, 0.02, 0.9, 1.0]) == (0.02, 0.9)


def test_widest_gap_needs_two_different_values() -> None:
    """One value, or copies of one value, have no gap."""
    assert widest_gap([0.5, 0.5]) is None
    assert widest_gap([]) is None


def test_finite_values_drops_missing_and_infinite() -> None:
    """Only real numbers stay."""
    assert finite_values([1.0, None, float("nan"), float("inf"), 2.0]) == [1.0, 2.0]


def _library(*frames: SimpleNamespace) -> SimpleNamespace:
    """Build a stand-in library with one target that holds the given frames.

    Returns
    -------
    library : `SimpleNamespace`
        An object whose ``targets.list()`` gives that one target.
    """
    target = SimpleNamespace(frames=list(frames))
    return SimpleNamespace(targets=SimpleNamespace(list=lambda: [target]))


def _frame(camera: str, path: str, role: str = "LIGHT") -> SimpleNamespace:
    """Build a stand-in frame record.

    Returns
    -------
    frame : `SimpleNamespace`
        A frame with a camera, a path and a role.
    """
    return SimpleNamespace(camera=camera, path=path, role=role)


def test_camera_is_colour_reads_the_first_readable_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first light frame of the camera with a header decides."""
    monkeypatch.setattr(tuning_support, "camera_identity", lambda name, configuration=None: name.lower())
    answers = {"a.fit": None, "b.fit": True}
    monkeypatch.setattr(tuning_support, "frame_uses_color_filter_array", lambda path: answers[path])
    library = _library(_frame("Cam", "a.fit"), _frame("Cam", "b.fit"), _frame("Other", "c.fit"))
    assert camera_is_colour(library, "CAM", None) is True


def test_camera_is_colour_is_unknown_without_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """No frame of the camera means no answer."""
    monkeypatch.setattr(tuning_support, "camera_identity", lambda name, configuration=None: name.lower())
    library = _library(_frame("Other", "c.fit"))
    assert camera_is_colour(library, "Cam", None) is None
