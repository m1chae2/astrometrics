"""Purpose: Tests for capturing with the main camera.

Description: `capture_frames` turns the filter wheel once, takes the
exposures, and dithers between them with one guide pulse per axis. The
tests replace the device commands with recorders and check: the filter is
set once before the first frame; each frame is exposed; dithers follow
the cadence and pattern, with a pulse length from the plate scale; bad
arguments and a camera that does not start are refused; and the progress
of a running job moves.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import ConfigurationError, HardwareError, InvalidArgumentError
from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.tasks.control_tasks import hardware_operations, imaging_capture


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Replace the device commands and the waits with recorders.

    Returns
    -------
    commands : `list` [`tuple` [`str`, `Any`]]
        Each command and its argument, in order.
    """
    recorded: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        hardware_operations, "set_filter", lambda context, name: recorded.append(("filter", name)) or True
    )
    monkeypatch.setattr(
        hardware_operations,
        "capture_image",
        lambda context, seconds: recorded.append(("expose", seconds)) or True,
    )
    monkeypatch.setattr(
        hardware_operations,
        "pulse",
        lambda context, direction, duration_ms: recorded.append(("pulse", (direction, duration_ms))) or True,
    )
    monkeypatch.setattr(imaging_capture, "time", SimpleNamespace(sleep=lambda seconds: None))
    return recorded


def _context(plate_scale_arcsec_per_px: float | None = 2.0) -> SimpleNamespace:
    """Build the parts of a `ControlContext` the capture reads.

    Parameters
    ----------
    plate_scale_arcsec_per_px : `float`, optional
        The main camera's plate scale, or `None` for no active equipment.

    Returns
    -------
    context : `types.SimpleNamespace`
        The active telescope and camera lookups.
    """
    if plate_scale_arcsec_per_px is None:
        return SimpleNamespace(active_telescope=lambda: None, active_camera=lambda: None)
    return SimpleNamespace(active_telescope=lambda: "telescope", active_camera=lambda: "camera")


@pytest.fixture
def plate_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every equipment pairing report 2 arcseconds per pixel."""

    class _Equipment:
        """Stands in for `EquipmentConfiguration`."""

        def __init__(self, telescope: Any, camera: Any) -> None:
            """Ignore the equipment."""

        def plate_scale_arcsec_per_px(self) -> float:
            """Return a fixed plate scale.

            Returns
            -------
            plate_scale : `float`
                2 arcseconds per pixel.
            """
            return 2.0

    monkeypatch.setattr(
        "wayfindinglib.models.equipment_and_site.equipment.EquipmentConfiguration", _Equipment
    )


def test_the_filter_is_set_once_before_the_frames(commands: list) -> None:
    """One filter change, then one exposure per frame."""
    result = imaging_capture.capture_frames(_context(), 30.0, count=3, filter_name="Ha")

    assert commands == [("filter", "Ha"), ("expose", 30.0), ("expose", 30.0), ("expose", 30.0)]
    assert result.frames_captured == 3
    assert result.filter_name == "Ha"
    assert result.dithers == 0


def test_dithers_follow_the_cadence_and_pattern(commands: list, plate_scale: None) -> None:
    """Every second frame dithers around the start, never after the last."""
    config = DitherConfig(enabled=True, every_n_frames=2, pixels=3.0)
    result = imaging_capture.capture_frames(_context(), 10.0, count=5, dither=config)

    pulses = [argument for command, argument in commands if command == "pulse"]
    expected_ms = 3.0 * 2.0 / GUIDE_RATE_ARCSEC_PER_S * 1000.0
    assert [direction for direction, _ in pulses] == ["north", "west", "south", "east"]
    assert all(duration == pytest.approx(expected_ms) for _, duration in pulses)
    assert result.dithers == 2
    assert result.frames_captured == 5


def test_dither_true_uses_the_default_cadence(commands: list, plate_scale: None) -> None:
    """`dither=True` dithers every 3 frames."""
    result = imaging_capture.capture_frames(_context(), 1.0, count=7, dither=True)
    assert result.dithers == 2


def test_dithering_needs_active_equipment(commands: list) -> None:
    """Without a telescope and camera the pulse length is unknown."""
    with pytest.raises(ConfigurationError):
        imaging_capture.capture_frames(_context(None), 1.0, count=2, dither=True)


@pytest.mark.parametrize(
    "arguments",
    [
        {"exposure_seconds": 0.0},
        {"exposure_seconds": 1.0, "count": 0},
        {"exposure_seconds": 1.0, "delay_seconds": -1},
    ],
)
def test_bad_arguments_are_refused(commands: list, arguments: dict) -> None:
    """A zero exposure, zero count or negative delay is refused at once."""
    with pytest.raises(InvalidArgumentError):
        imaging_capture.capture_frames(_context(), **arguments)
    assert commands == []


def test_a_camera_that_does_not_start_raises(monkeypatch: pytest.MonkeyPatch, commands: list) -> None:
    """A refused exposure stops the run with `HardwareError`."""
    monkeypatch.setattr(hardware_operations, "capture_image", lambda context, seconds: False)
    with pytest.raises(HardwareError, match="frame 1 of 2"):
        imaging_capture.capture_frames(_context(), 1.0, count=2)


def test_a_running_job_sees_each_frame(monkeypatch: pytest.MonkeyPatch, commands: list) -> None:
    """The progress of the job the capture runs in moves once per frame."""
    stages: list[tuple[int, str]] = []
    job = SimpleNamespace(stage=lambda progress, message: stages.append((progress, message)))
    monkeypatch.setattr(imaging_capture, "get_current_job", lambda: job)

    imaging_capture.capture_frames(_context(), 1.0, count=2, filter_name="L")

    assert stages == [(0, "Selecting filter L"), (0, "Capturing frame 1/2"), (50, "Capturing frame 2/2")]
