"""Purpose: Prove that monitor mode sends no command through the backend's RPC.

Description: The user decided on 2026-10-24 (issue #95, settled decision
S10) that, in monitor mode, every motion and capture command stays
refused, while device settings and the app's own INDI connect and
disconnect stay allowed.
The library test (`wayfindinglib/test/test_monitor_mode_sends_no_commands.py`)
proves it through the library. This file proves it through the backend's
RPC dispatch, the door the desktop app uses.

Monitor mode is switched on through the RPC method
``observatory:enter_monitoring_mode``. The drivers of the shared
container are replaced by recording stand-ins, so a command that got
past the guard would show up as a call.

Two kinds of RPC method send commands:

* methods that call a library command directly. They are found by
  reading the RPC table and the wayfindinglib-core manifest
  (``tool_class`` of ``actuate``), so a new one is covered
  automatically;
* methods that go through a backend service first. They are named in
  `SERVICE_COMMANDS` below.

The allowed methods (``telescope:connect``, ``telescope:set_indi_property``)
are checked to still work.
"""

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from backend.container import container
from backend.routers import rpc_router
from backend.services.rpc_protocol import RPCRequest
from wayfindinglib.test import monitor_mode_support as support

pytestmark = pytest.mark.anyio

SERVICE_COMMANDS: dict[str, dict[str, Any]] = {
    "telescope:slew_coordinates": {"ra": 1.0, "dec": 20.0},
    "telescope:slew_target": {"target_name": "M 81"},
    "guiding:capture_frame": {"exposure": 1.0},
    "imaging:capture": {"target_id": "M 81", "exposure_seconds": 1.0, "count": 1},
}
"""RPC methods whose service calls a library command and reports a refusal
as an error reply. Values are sample parameters."""

BACKGROUND_COMMANDS: dict[str, dict[str, Any]] = {
    "guiding:start": {},
    "telescope:alignment_start": {"target_ra": "12h 00m 00s", "target_dec": "+45d 00m 00s"},
}
"""RPC methods that start a thread. The thread logs a refusal instead of
replying with it, so the test waits for the thread and checks that no
driver got a command."""


def _direct_command_methods() -> dict[str, str]:
    """Find the RPC methods that call a library command directly.

    Returns
    -------
    methods : `dict` [`str`, `str`]
        RPC method name -> the manifest tool it calls, for every method
        in the RPC table that is served by a ``wayfinder.control.<child>``
        method and whose tool is one of the refused commands.
    """
    refused = set(support.refused_tool_names())
    found = {}
    for method, handler in rpc_router._HANDLERS.items():
        if not isinstance(handler, tuple):
            continue
        service_name, method_name = handler
        parts = service_name.split(".")
        if parts[:2] == ["wayfinder", "control"] and len(parts) == 3:
            tool = f"observatory_{parts[2]}_{method_name}"
            if tool in refused:
                found[method] = tool
    return found


@pytest.fixture
def monitor_mode() -> Iterator[support.RecordingDrivers]:
    """Switch the shared container to monitor mode with recording drivers.

    Yields
    ------
    recording : `RecordingDrivers`
        The stand-in drivers. The real drivers and the saved delegation
        policy are put back afterward.
    """
    context = container.wayfinder.control._context
    saved_policy = context.delegation_policy()
    recording = support.RecordingDrivers(context)
    yield recording
    recording.restore()
    context.butler.put(saved_policy, "delegation_policy", {"id": saved_policy.id})


async def _call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call an RPC method the way the HTTP route does and decode the reply.

    Parameters
    ----------
    method : `str`
        The RPC method name.
    params : `dict` [`str`, `Any`]
        The parameters.

    Returns
    -------
    body : `dict` [`str`, `Any`]
        The JSON-RPC reply.
    """
    import json

    response = await rpc_router.handle_rpc(RPCRequest(method=method, params=params, id=1))
    return json.loads(response.body)


async def _enter_monitor_mode() -> None:
    """Switch on monitor mode through the RPC method the app uses."""
    reply = await _call("observatory:enter_monitoring_mode", {})
    assert "error" not in reply


def _wait_for_background_threads() -> None:
    """Wait for the threads the background RPC methods start."""
    for thread in (container.guiding_service._thread, container.alignment_service._alignment_thread):
        if isinstance(thread, threading.Thread):
            thread.join(timeout=10.0)


def test_the_direct_command_methods_are_found() -> None:
    """The scan of the RPC table finds the commands the app sends directly."""
    found = _direct_command_methods()

    assert {"telescope:park", "telescope:unpark", "telescope:abort_motion", "telescope:set_tracking"} <= set(
        found
    )
    assert {"telescope:focus_move", "telescope:set_filter", "telescope:manual_move"} <= set(found)
    assert "telescope:connect" not in found


async def test_every_direct_command_rpc_is_refused_in_monitor_mode(
    monitor_mode: support.RecordingDrivers,
) -> None:
    """Each RPC method that calls a library command is refused."""
    await _enter_monitor_mode()
    for method, tool in _direct_command_methods().items():
        reply = await _call(method, support.sample_arguments(tool))

        assert reply["error"]["data"]["code"] == "permission_denied", method
    assert monitor_mode.commands_sent() == []


async def test_every_service_command_rpc_is_refused_in_monitor_mode(
    monitor_mode: support.RecordingDrivers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RPC methods that go through a backend service are refused too."""
    from sys import modules

    from wayfindinglib import SkyPosition

    monkeypatch.setattr(
        modules["wayfindinglib.tasks.control_tasks.hardware_operations"],
        "resolve_destination",
        lambda context, destination: SkyPosition(ra_deg=10.0, dec_deg=20.0),
    )
    await _enter_monitor_mode()
    for method, params in SERVICE_COMMANDS.items():
        reply = await _call(method, params)

        assert reply["error"]["data"]["code"] == "permission_denied", method
    assert monitor_mode.commands_sent() == []


async def test_background_command_rpcs_send_nothing_in_monitor_mode(
    monitor_mode: support.RecordingDrivers,
) -> None:
    """Methods that start a thread (guiding, centering) send no command."""
    await _enter_monitor_mode()
    for method, params in BACKGROUND_COMMANDS.items():
        await _call(method, params)
        _wait_for_background_threads()

    assert monitor_mode.commands_sent() == []


async def test_connect_and_device_settings_stay_allowed_in_monitor_mode(
    monitor_mode: support.RecordingDrivers,
) -> None:
    """``telescope:connect`` and ``telescope:set_indi_property`` work (S10).

    The connect call reaches the recording drivers, so the "no command"
    results above are not a broken test rig.
    """
    await _enter_monitor_mode()

    connect_reply = await _call("telescope:connect", {})
    setting_reply = await _call(
        "telescope:set_indi_property",
        {
            "device_name": "Telescope Simulator",
            "property_name": "TELESCOPE_TRACK_MODE",
            "value": True,
            "element": "TRACK_SIDEREAL",
        },
    )

    assert "error" not in connect_reply
    assert "error" not in setting_reply
    assert monitor_mode.calls_to("connect") > 0
    assert monitor_mode.indi_session.set_property.call_count == 1
    assert monitor_mode.commands_sent() == []


def test_the_named_allowed_rpc_methods_exist_in_the_rpc_table() -> None:
    """The allowed list names real RPC methods, so a rename cannot hide one."""
    assert support.ALLOWED_RPC_IN_MONITOR_MODE <= set(rpc_router._HANDLERS)
