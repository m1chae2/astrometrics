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
  `SERVICE_COMMANDS` below. This includes the two stop methods
  (``guiding:stop``, ``telescope:alignment_stop``): the user decided on
  2026-10-24 (issue #95, decision U4) that monitor mode refuses a stop too.

The allowed methods (``telescope:connect``, ``telescope:set_indi_property``)
are checked to still work.

Every method in the RPC table must also be named in one of the lists below
(read-only, local writes, allowed, or command-tested).
`test_every_rpc_method_is_classified` fails on a method in none of them, so a
new method cannot join the table without someone saying whether it can send
a command to a device.
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
    "guiding:stop": {},
    "telescope:alignment_stop": {},
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


BACKGROUND_COMMANDS_STARTED_BY_QUEUE = frozenset({"sequencer:begin"})
"""RPC methods that run the night plan's queue on a thread. They get their
own test below, because the queue needs a stored session first."""

READ_ONLY_METHODS = frozenset({
    "advisory:result",
    "advisory:target_report",
    "advisory:tonight",
    "analysis:get_results",
    "astronomy:analyze_periodicity",
    "astronomy:count",
    "astronomy:get",
    "astronomy:get_overlay_stars",
    "astronomy:get_status",
    "astronomy:get_stellar_objects",
    "astronomy:list",
    "astronomy:spectral_class_summary",
    "astronomy:stars_by_spectral_class",
    "astronomy:target_data_availability",
    "astronomy:visible",
    "calibration:get_stats",
    "calibration:query",
    "docs:get_topic",
    "docs:list_topics",
    "docs:search_topics",
    "execution:get_session",
    "execution:list_sessions",
    "guiding:status",
    "handoff:get_state",
    "handoff:list_devices",
    "history:live_session_status",
    "history:night_quality",
    "history:nights",
    "history:target_changes",
    "images:get_fits_header",
    "images:get_light_frame_data",
    "images:get_target_frame",
    "images:last",
    "imaging:get_active_jobs",
    "ingestion:list_files",
    "ingestion:scan",
    "ingestion:stats",
    "ingestion:status",
    "mosaic:preview",
    "observatory:get_equipment_configuration",
    "observatory:get_active_telescope",
    "observatory:list_cameras",
    "observatory:safety_status",
    "plan:get",
    "plan:current_session",
    "plan:list_packages",
    "plan:package_for_target",
    "quality:resolve_rows",
    "planetarium:get_catalog_sources",
    "planetarium:get_constellation_lines",
    "planetarium:get_deep_catalog_status",
    "planetarium:get_observer_location",
    "planetarium:get_sources",
    "planetarium:get_targets",
    "planetarium:get_visibility",
    "planetarium:list_catalog_drivers",
    "processing:active_jobs",
    "processing:get_job",
    "processing:job_log_tail",
    "processing:jobs_for_target",
    "processing:list_jobs",
    "processing:status",
    "quality:metrics",
    "quality:query",
    "sequencer:create_plan",
    "sequencer:get_queue",
    "system:cameras",
    "system:completions",
    "system:filters",
    "system:get_config",
    "system:health",
    "system:introspection",
    "system:notifications",
    "system:pulse",
    "target:get",
    "target:get_camera_index",
    "target:get_files",
    "target:get_frame_header",
    "target:get_frames",
    "target:get_frames_grouped",
    "target:get_targets",
    "target:list",
    "telescope:get_cumulative_tracking_data",
    "telescope:get_focuser_position",
    "telescope:get_guiding_spectrum",
    "telescope:get_performance_envelope",
    "telescope:get_pointing_model",
    "telescope:get_session_alignment",
    "telescope:indi_devices",
    "telescope:indi_properties",
    "telescope:is_syncing",
    "telescope:list_alignment_sessions",
    "telescope:status",
    "terminal:completions",
    "terminal:get_recipe",
    "terminal:get_workspace",
    "terminal:list_recipes",
    "terminal:list_scripts",
    "terminal:read_script",
    "ui:editor_get",
})
"""RPC methods that only read or compute. They change nothing and command
no device."""

LOCAL_WRITE_METHODS = frozenset({
    "analysis:analyze_image",
    "analysis:cancel",
    "astronomy:delete",
    "astronomy:save",
    "calibration:measure_sensor_noise",
    "execution:abort_session",
    "execution:reconcile_session",
    "execution:record_divergence",
    "handoff:beam",
    "handoff:send_alert",
    "handoff:share_file",
    "handoff:update_state",
    "images:convert_fits",
    "images:delete",
    "ingestion:reindex",
    "ingestion:start",
    "mosaic:create",
    "observatory:enter_controller_mode",
    "observatory:enter_monitoring_mode",
    "observatory:set_active_camera",
    # Saves the telescope's safety limits to the config file; no device
    # command. The screen greys Apply while Ekos controls, but that is a
    # screen rule (user decision), not a backend one.
    "observatory:set_active_telescope",
    "plan:create",
    "plan:edit",
    "plan:edit_package",
    "plan:refresh_suggestions",
    "plan:create_package",
    "telescope:apply_promotion_decision",
    "plan:tick_checklist_item",
    "processing:cancel",
    "processing:delete_job",
    "processing:measure_odd_even_difference_noise",
    "processing:measure_night_contributions",
    "processing:measure_noise_growth",
    "processing:record_frame_measurements",
    "processing:siril_open",
    "processing:stack",
    "sequencer:add",
    "sequencer:modify",
    "sequencer:remove",
    "sequencer:reorder",
    "system:frontend_log",
    "system:save",
    "system:save_config",
    "target:add_data",
    "target:create",
    "target:delete",
    "target:refresh",
    "target:send_to_phone",
    "target:update",
    "telescope:sync",
    "telescope:sync_logs",
    "terminal:execute",
    "terminal:reset_workspace",
    "terminal:save_script",
    "ui:editor_set",
    "ui:inspect_variable",
    "ui:navigate",
})
"""RPC methods that change the app's own data, files, settings or screens,
or copy files from the telescope computer. None sends a command to a device.
``terminal:execute`` runs a user script, and a script reaches hardware only
through the library calls that the monitor-mode guard already refuses."""


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


def test_every_rpc_method_is_classified() -> None:
    """Each RPC method is read-only, a local write, allowed or tested.

    A method in none of the lists fails here, so nobody can add a method
    that might send a command and leave it out of the monitor-mode proof.
    """
    command_tested = (
        set(_direct_command_methods())
        | set(SERVICE_COMMANDS)
        | set(BACKGROUND_COMMANDS)
        | set(BACKGROUND_COMMANDS_STARTED_BY_QUEUE)
    )
    buckets = {
        "read only": set(READ_ONLY_METHODS),
        "local write": set(LOCAL_WRITE_METHODS),
        "allowed": set(support.ALLOWED_RPC_IN_MONITOR_MODE),
        "command tested": command_tested,
    }
    served = set(rpc_router._HANDLERS)

    classified = set().union(*buckets.values())
    assert sorted(served - classified) == [], "RPC methods in no list: say whether they can send a command"
    assert sorted(classified - served) == [], "lists name RPC methods that no longer exist"
    names = list(buckets)
    for position, first in enumerate(names):
        for second in names[position + 1 :]:
            assert buckets[first] & buckets[second] == set(), f"in both {first} and {second}"


async def test_sequencer_begin_sends_no_command_in_monitor_mode(
    monitor_mode: support.RecordingDrivers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting the queue in monitor mode commands no device."""
    executor = container.target_imaging_executor
    session = support.make_night_session(container.wayfinder)
    support.force_safe_assessment(container.wayfinder, monkeypatch)
    # The sequencer uses tonight's session; point it at the prepared one.
    monkeypatch.setattr(executor, "_existing_session", lambda: session)
    monkeypatch.setattr(executor, "_session", lambda: session)
    await _enter_monitor_mode()

    reply = await _call("sequencer:begin", {})
    if executor._thread is not None:
        executor._thread.join(timeout=10.0)
    executor.pause_imaging()

    assert "error" not in reply
    assert monitor_mode.commands_sent() == []
