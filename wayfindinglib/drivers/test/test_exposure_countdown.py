"""Purpose: Unit tests for the camera exposure countdown.

Description: The countdown in `IndiInterface._handle_exposure_update` runs on
a clock. The tests use a fake clock and no hardware. They replay the
readings that made the old direction-guessing code jump, and they check
back-to-back frames, the Idle and Downloading states, the FITS EXPTIME
override and whole-second formatting.
"""

import re
import threading

import pytest

from wayfindinglib.drivers.indi_interface import IndiInterface


class _Clock:
    """A fake monotonic clock that tests advance by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the current fake time.

        Returns
        -------
        now : `float`
            Fake time in seconds.
        """
        return self.now


def _make() -> tuple[IndiInterface, _Clock]:
    """Build an IndiInterface with only the exposure fields and a fake clock.

    Returns
    -------
    interface : `IndiInterface`
        Interface ready for `_handle_exposure_update`.
    clock : `_Clock`
        The fake clock the interface uses.
    """
    interface = IndiInterface.__new__(IndiInterface)
    interface.status = {"CAMERA_STATUS": "Idle"}
    interface._exposure_in_progress = False
    interface._active_exposure_duration = None
    interface._exposure_start_time = None
    interface._last_exposure_val = None
    interface._exposure_lock = threading.Lock()
    clock = _Clock()
    interface._monotonic_clock = clock
    return interface, clock


def _seconds(interface: IndiInterface) -> int:
    """Return the whole seconds shown in an "Exposing (Ns)" status.

    Returns
    -------
    seconds : `int`
        The number in the status text.
    """
    match = re.fullmatch(r"Exposing \((\d+)s\)", interface.status["CAMERA_STATUS"])
    assert match, interface.status["CAMERA_STATUS"]
    return int(match.group(1))


def test_stale_reading_does_not_make_countdown_jump() -> None:
    """A stale 59 between 57 and 55 never makes the countdown jump."""
    interface, clock = _make()
    interface._handle_exposure_update(60.0, True)
    shown = []
    for value in (57.0, 59.0, 55.0, 54.0, 52.0):
        clock.now += 1.0
        interface._handle_exposure_update(value, True)
        shown.append(_seconds(interface))
    assert shown == sorted(shown, reverse=True)
    assert shown[-1] >= 50


def test_back_to_back_frames_start_a_new_countdown() -> None:
    """A jump from 0 to 60 while busy starts a new frame, not a Downloading."""
    interface, clock = _make()
    interface._handle_exposure_update(3.0, True, fits_exptime=60.0)
    for value in (2.0, 1.0):
        clock.now += 1.0
        interface._handle_exposure_update(value, True, fits_exptime=60.0)
    clock.now += 1.0
    interface._handle_exposure_update(0.0, True, fits_exptime=60.0)
    assert interface.status["CAMERA_STATUS"] == "Downloading"
    clock.now += 2.0
    interface._handle_exposure_update(60.0, True, fits_exptime=60.0)
    assert interface.status["CAMERA_STATUS"] == "Exposing (60s)"
    clock.now += 1.0
    interface._handle_exposure_update(59.0, True, fits_exptime=60.0)
    assert interface.status["CAMERA_STATUS"] == "Exposing (59s)"


def test_busy_to_not_busy_is_idle_and_next_busy_starts_new_frame() -> None:
    """Not busy gives Idle; the next busy reading starts a fresh countdown."""
    interface, clock = _make()
    interface._handle_exposure_update(10.0, True)
    interface._handle_exposure_update(0.0, False)
    assert interface.status["CAMERA_STATUS"] == "Idle"
    clock.now += 30.0
    interface._handle_exposure_update(20.0, True)
    assert interface.status["CAMERA_STATUS"] == "Exposing (20s)"


def test_fits_exptime_overrides_first_value() -> None:
    """The header EXPTIME sets the frame length, not the first value seen."""
    interface, clock = _make()
    interface._handle_exposure_update(45.0, True, fits_exptime=60.0)
    assert interface._active_exposure_duration == pytest.approx(60.0)
    clock.now += 10.0
    # The reported 35 is below the clock's 50, so the clock moves to it.
    interface._handle_exposure_update(35.0, True, fits_exptime=60.0)
    assert interface.status["CAMERA_STATUS"] == "Exposing (35s)"


def test_clock_runs_between_readings() -> None:
    """With no new reading the clock still counts down."""
    interface, clock = _make()
    interface._handle_exposure_update(30.0, True)
    clock.now += 4.0
    interface._handle_exposure_update(26.0, True)
    clock.now += 4.0
    interface._handle_exposure_update(26.0, True)  # stale repeat
    assert _seconds(interface) == 22


def test_remaining_time_is_rounded_up_to_whole_seconds() -> None:
    """0.3 s left shows as 1 s, never 0 s while exposing."""
    interface, clock = _make()
    interface._handle_exposure_update(10.0, True)
    clock.now += 9.7
    interface._handle_exposure_update(9.9, True)
    assert interface.status["CAMERA_STATUS"] == "Exposing (1s)"
