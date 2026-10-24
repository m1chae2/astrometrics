"""Purpose: Prove that monitor mode sends no motion or capture command.

Description: In monitor mode every capability is `DELEGATED`: the app
watches and computes but commands nothing (plan decision 20). The user
decided on 2026-10-24 (issue #95, settled decision S10) that:

* every motion and capture command stays refused (the 15 guarded
  operations of `hardware_operations`, including `abort_motion`);
* device settings (`set_device_property`) and the app's own INDI connect
  and disconnect stay allowed.

These tests go through the library's public facade, `Wayfinder`. The
drivers are replaced by recording stand-ins, so a command that got past
the guard would show up as a call. The list of commands comes from the
wayfindinglib-core manifest (see `monitor_mode_support`), so a new
command is covered the day it is added. The same guarantee is proved
through the backend (`backend/tests/test_monitor_mode_rpc.py`) and the
MCP tools (`mcp_servers/wayfinding_core/test/test_monitor_mode_tools.py`).
"""

import threading

import pytest

from astrometricslib import AppConfiguration, PermissionDeniedError
from wayfindinglib import Wayfinder
from wayfindinglib.test import monitor_mode_support as support


@pytest.fixture
def wayfinder() -> Wayfinder:
    """Build a `Wayfinder` in monitor mode, with recording drivers.

    Returns
    -------
    wayfinder : `Wayfinder`
        Its control context has recording drivers in `wayfinder.recording`.
    """
    wayfinder = Wayfinder(AppConfiguration())
    wayfinder.recording = support.RecordingDrivers(wayfinder.control._context)
    wayfinder.control.safety.enter_monitoring_mode(evidence_note="test of monitor mode")
    return wayfinder


def test_the_manifest_lists_commands_and_each_refused_one_has_sample_arguments() -> None:
    """Every refused command has sample arguments, so none goes untested."""
    names = support.refused_tool_names()
    assert names, "The manifest lists no command tools."
    missing = [
        name
        for name in names
        if name not in ("observatory_guiding_run_loop", "observatory_guiding_stop_loop")
        and name not in support.SAMPLE_ARGUMENTS
    ]
    assert missing == [], f"Add sample arguments to monitor_mode_support.SAMPLE_ARGUMENTS for: {missing}"


def test_the_allowed_list_names_only_real_commands() -> None:
    """The allowed and caller-supplied lists cannot hide a renamed tool."""
    commands = set(support.command_tool_names())
    assert support.ALLOWED_IN_MONITOR_MODE <= commands
    assert support.CALLER_SUPPLIED_STEP_TOOLS <= commands
    assert support.QUIET_BUT_NOT_REFUSED_TOOLS <= commands


@pytest.mark.parametrize("tool_name", support.refused_tool_names())
def test_every_command_in_the_manifest_is_refused_or_quiet_in_monitor_mode(
    wayfinder: Wayfinder, tool_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command is refused with `PermissionDeniedError` and sends nothing.

    The one command that records instead of refusing (`advance_session`)
    must still send nothing.
    """
    wayfinder.recording.make_guide_exposure_test_reachable(monkeypatch)
    method = support.resolve_tool(wayfinder, tool_name)
    arguments = support.sample_arguments(tool_name)
    if tool_name in support.QUIET_BUT_NOT_REFUSED_TOOLS:
        _run_quiet_command(wayfinder, method, arguments, monkeypatch)
    else:
        with pytest.raises(PermissionDeniedError):
            method(**arguments)
    assert wayfinder.recording.commands_sent() == []


def _run_quiet_command(
    wayfinder: Wayfinder, method: object, arguments: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run `advance_session` on a one-entry night plan in monitor mode.

    The safety verdict is forced to safe, so the cycle reaches the slew
    and the capture.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        Supplies the storage and the control sub-API.
    method : `Callable`
        The bound `advance_session`.
    arguments : `dict`
        Sample arguments; ``session`` is replaced by the stored session.
    monkeypatch : `pytest.MonkeyPatch`
        Undoes the forced verdict.
    """
    session = support.make_night_session(wayfinder)
    support.force_safe_assessment(wayfinder, monkeypatch)
    result = method(**{**arguments, "session": session})
    assert result.queue[0].frames_captured == 0
    assert "Capture stopped" in result.queue[0].status_detail


def test_allowed_device_settings_and_connect_still_work(wayfinder: Wayfinder) -> None:
    """Connect, disconnect and a device setting are not refused (S10).

    The recording drivers prove the allowed commands really reach the
    driver, so the "no command" results above are not a broken test rig.
    """
    equipment = wayfinder.control.equipment

    assert equipment.connect() is True
    assert equipment.disconnect() is True
    assert wayfinder.recording.calls_to("connect") > 0
    assert wayfinder.recording.calls_to("disconnect") > 0

    assert equipment.set_device_property(
        "Telescope Simulator", "TELESCOPE_TRACK_MODE", True, "TRACK_SIDEREAL"
    )
    assert wayfinder.recording.indi_session.set_property.call_count == 1
    assert wayfinder.recording.commands_sent() == []


def test_the_same_command_is_sent_once_the_capability_is_authoritative(wayfinder: Wayfinder) -> None:
    """The rig sees a command once it is allowed.

    Promoting `MOUNT_CONTROL` lets `park` through to the mount driver.
    """
    from wayfindinglib.models.policy.delegation import DelegationState, ObservatoryCapability

    wayfinder.control.safety.apply_promotion_decision(
        ObservatoryCapability.MOUNT_CONTROL, DelegationState.AUTHORITATIVE
    )

    wayfinder.control.mount.park()

    assert wayfinder.recording.commands_sent() == ["mount.park"]


def test_caller_supplied_steps_list_matches_the_manifest() -> None:
    """The commands that run caller-supplied steps are real commands.

    These run callables the caller supplies and never touch a driver, so
    the delegation guard cannot apply to them. They are reported to the
    systems engineer (issue #95, batch 5).
    """
    assert support.CALLER_SUPPLIED_STEP_TOOLS <= set(support.command_tool_names())


def test_the_two_stops_are_refused_in_monitor_mode_and_change_nothing(wayfinder: Wayfinder) -> None:
    """Decision U4: stopping a guide loop or a centering run is refused too.

    The guide loop's stop event stays unset and no driver command is sent,
    whether or not a centering run is going.
    """
    stop = threading.Event()
    with pytest.raises(PermissionDeniedError):
        wayfinder.control.guiding.stop_loop(stop)
    assert not stop.is_set()

    for run_is_active in (True, False):
        with pytest.raises(PermissionDeniedError):
            wayfinder.control.mount.cancel_centering(run_is_active)
    assert not wayfinder.control._context.motion_stop.is_set()
    assert wayfinder.recording.commands_sent() == []
