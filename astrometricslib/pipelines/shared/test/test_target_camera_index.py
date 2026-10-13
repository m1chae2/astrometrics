"""Tests for the per-target camera index used by the target list filters."""

from types import SimpleNamespace

import pytest

from astrometricslib.pipelines.shared.target_camera_index import (
    build_target_camera_index,
)

NIKON_HEADER_NAME = "Nikon DSLR DSC D5300"


def _identity(name: str) -> str:
    """Treat the Nikon's header and config spellings as one camera.

    Returns
    -------
    identity : `str`
        The same text for every spelling of one camera.
    """
    lowered = name.lower().replace(" ", "")
    return "nikond5300" if "d5300" in lowered else lowered


def _frame(camera: str, timestamp: float | None, role: str = "LIGHT") -> SimpleNamespace:
    """Build a minimal frame record.

    Returns
    -------
    frame : `SimpleNamespace`
        A frame with ``camera``, ``timestamp`` and ``role``.
    """
    return SimpleNamespace(camera=camera, timestamp=timestamp, role=role)


def test_header_spelling_matches_configured_name() -> None:
    """A header-spelled Nikon frame counts for the configured name."""
    frames = [_frame(NIKON_HEADER_NAME, 10.0), _frame(NIKON_HEADER_NAME, 30.0)]
    target = SimpleNamespace(id="M 31", frames=frames)
    index = build_target_camera_index([target], ["Nikon D5300", "ZWO"], _identity)
    entry = index["targets"]["M 31"]
    assert entry["cameras"]["Nikon D5300"]["frameCount"] == 2
    assert entry["cameras"]["Nikon D5300"]["lastFrameTime"] == pytest.approx(30.0)
    assert entry["lastFrameTime"] == pytest.approx(30.0)
    assert index["cameras"] == [
        {"name": "Nikon D5300", "targetCount": 1},
        {"name": "ZWO", "targetCount": 0},
    ]


def test_calibration_frames_and_unknown_cameras_are_ignored() -> None:
    """Only light frames count; an unlisted camera adds a time only."""
    frames = [
        _frame("ZWO", 5.0, role="FLAT"),
        _frame("Other", 8.0),
        _frame("Unknown", None),
    ]
    target = SimpleNamespace(id="Vega", frames=frames)
    index = build_target_camera_index([target], ["ZWO"], _identity)
    entry = index["targets"]["Vega"]
    assert entry["cameras"] == {}
    assert entry["lastFrameTime"] == pytest.approx(8.0)


def test_target_without_frames_has_no_time() -> None:
    """A target with no frames has no time, so it sorts last."""
    target = SimpleNamespace(id="Moon", frames=[])
    index = build_target_camera_index([target], ["ZWO"], _identity)
    assert index["targets"]["Moon"] == {"lastFrameTime": None, "cameras": {}}
