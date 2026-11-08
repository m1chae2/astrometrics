"""Purpose: Tests for capturing with the main camera.

Description: `capture_frames` turns the filter wheel once, takes the
exposures, and dithers between them with one guide pulse per axis. The
tests replace the device commands with recorders and check: the filter is
set once before the first frame; each frame is exposed; dithers follow
the cadence and pattern, with a pulse length from the plate scale; bad
arguments and a camera that does not start are refused; and the progress
of a running job moves. A stop request ends the run cleanly: between
frames, or during the wait for an exposure (which also aborts it), and
the frames already taken are kept.
"""

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import ConfigurationError, HardwareError, InvalidArgumentError
from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.models.planning.observation_package import DitherConfig
from wayfindinglib.tasks.control_tasks import hardware_operations, imaging_capture
from wayfindinglib.tasks.control_tasks.capture_runs import CaptureRuns


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
    monkeypatch.setattr(
        hardware_operations, "abort_exposure", lambda context: recorded.append(("abort", None)) or True
    )
    monkeypatch.setattr(imaging_capture, "_wait_or_stop", lambda stop_flag, seconds: stop_flag.is_set())
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
        return SimpleNamespace(
            active_telescope=lambda: None, active_camera=lambda: None, capture_runs=CaptureRuns()
        )
    return SimpleNamespace(
        active_telescope=lambda: "telescope", active_camera=lambda: "camera", capture_runs=CaptureRuns()
    )


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


def test_a_stop_during_an_exposure_aborts_it_and_keeps_the_frames_taken(
    monkeypatch: pytest.MonkeyPatch, commands: list
) -> None:
    """Stopping frame 2 keeps frame 1 and aborts the exposure."""
    context = _context()
    exposures = []

    def expose(_context: Any, seconds: float) -> bool:
        """Record the exposure and request the stop during the second one.

        Returns
        -------
        started : `bool`
            Always `True`.
        """
        exposures.append(seconds)
        commands.append(("expose", seconds))
        if len(exposures) == 2:
            context.capture_runs.request_stop()
        return True

    monkeypatch.setattr(hardware_operations, "capture_image", expose)

    result = imaging_capture.capture_frames(context, 60.0, count=10)

    assert result.stopped is True
    assert result.frames_captured == 1
    assert commands == [("expose", 60.0), ("expose", 60.0), ("abort", None)]
    assert not context.capture_runs.is_running()


def test_a_stop_between_frames_takes_no_further_exposure(
    monkeypatch: pytest.MonkeyPatch, commands: list
) -> None:
    """A stop after a frame ends the run with no abort or new exposure."""
    context = _context()

    def wait_then_stop(stop_flag: threading.Event, seconds: float) -> bool:
        """Finish the exposure normally, then let the stop arrive.

        Returns
        -------
        stopped : `bool`
            Always `False`: this wait itself was not cut short.
        """
        context.capture_runs.request_stop()
        return False

    monkeypatch.setattr(imaging_capture, "_wait_or_stop", wait_then_stop)

    result = imaging_capture.capture_frames(context, 60.0, count=10)

    assert result.stopped is True
    assert result.frames_captured == 1
    assert commands == [("expose", 60.0)]


def test_a_stopped_run_says_so_in_its_job(monkeypatch: pytest.MonkeyPatch, commands: list) -> None:
    """The job log gets a "stopped after N frames" line."""
    context = _context()
    messages: list[str] = []
    job = SimpleNamespace(stage=lambda progress, message: None, info=messages.append)
    monkeypatch.setattr(imaging_capture, "get_current_job", lambda: job)
    monkeypatch.setattr(
        hardware_operations,
        "capture_image",
        lambda _context, seconds: context.capture_runs.request_stop() or True,
    )

    result = imaging_capture.capture_frames(context, 60.0, count=3)

    assert result.frames_captured == 0
    assert messages == ["Capture stopped after 0 frames"]


def test_a_real_stop_ends_a_long_wait_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop from another thread does not wait out a 60 s exposure."""
    context = _context()
    aborted: list[bool] = []
    monkeypatch.setattr(hardware_operations, "capture_image", lambda context, seconds: True)
    monkeypatch.setattr(hardware_operations, "abort_exposure", lambda context: aborted.append(True) or True)
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(imaging_capture.capture_frames(context, 60.0, 5)))

    thread.start()
    deadline = time.monotonic() + 5.0
    while not context.capture_runs.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    context.capture_runs.request_stop()
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert time.monotonic() - started < 5.0
    assert results[0].stopped is True
    assert results[0].frames_captured == 0
    assert aborted == [True]
