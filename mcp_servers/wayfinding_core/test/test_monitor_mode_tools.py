"""Purpose: Prove that monitor mode sends no command through the MCP tools.

Description: The user decided on 2026-10-24 (issue #95, settled decision
S10) that, in monitor mode, every motion and capture command stays
refused, while device settings and the app's own INDI connect and
disconnect stay allowed.
The library test (`wayfindinglib/test/test_monitor_mode_sends_no_commands.py`)
proves it through the library facade. This file proves it through the
wayfindinglib-core server's tools, which call the same facade.

The commands come from the server's `tool_manifest.json`: every tool of
class ``actuate`` (or ``safe-stop``), minus the named allowed list below.
A new command is therefore tested the day it joins the manifest. The
registry here holds every tool, before any profile removes the hardware
ones, so the guard itself is tested and not the profile.

This file uses only the libraries' public API (an MCP server may not import
more), so its stand-in drivers are plain mocks set through the public driver
properties of `control`. The lists below repeat those of the library test,
because the MCP servers may not import the library's test helpers.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_servers.common.reflection import register_reflected_tools
from mcp_servers.common.tool_errors import ToolErrorContent
from mcp_servers.common.tool_registry import ToolRegistry
from mcp_servers.wayfinding_core.definition import BRANCH_PREFIXES, MANIFEST_PATH
from wayfindinglib import Wayfinder

pytestmark = pytest.mark.anyio

COMMAND_TOOL_CLASSES = frozenset({"actuate", "safe-stop"})
"""Manifest classes of hardware commands (``safe-stop``: `abort_motion`)."""

ALLOWED_IN_MONITOR_MODE = frozenset({
    "observatory_equipment_connect",
    "observatory_equipment_disconnect",
    "observatory_equipment_set_device_property",
})
"""Commands that stay allowed in monitor mode: the user's decision of
2026-10-24 (issue #95, settled decision S10). Device settings and the app's
own INDI connect and disconnect move nothing and take no picture."""

CALLER_SUPPLIED_STEP_TOOLS = frozenset({
    "execution_execute_meridian_flip",
    "execution_recover_fault",
    "execution_recover_guide_star_loss",
    "observatory_guiding_run_calibration",
    "observatory_guiding_run_backlash_calibration",
    "observatory_safety_execute_safe_state",
})
"""Commands that run steps the caller hands in. A JSON call cannot carry
them and they touch no driver themselves. Reported to the systems engineer
(issue #95, batch 5); the library test keeps the same list."""

JSON_CANNOT_CARRY_THE_ARGUMENT = frozenset({"observatory_guiding_run_loop", "observatory_mount_sync"})
"""Tools whose argument a JSON call cannot carry: a `threading.Event`, or a
sky position (the tool's schema asks for text). They must fail without
sending a command, though not with `permission_denied`."""

QUIET_BUT_NOT_REFUSED_TOOLS = frozenset({"execution_advance_session"})
"""Commands that record instead of refusing; they must still send nothing."""

SAMPLE_ARGUMENTS = {
    "observatory_guiding_expose": {"exposure_seconds": 1.0},
    "observatory_guiding_pulse": {"direction": "north", "duration_ms": 100.0},
    "observatory_guiding_run_exposure_test": {},
    "observatory_guiding_run_loop": {},
    "observatory_imaging_capture_image": {"exposure_seconds": 1.0, "register_job": False},
    "observatory_imaging_focus_move": {"steps": 10},
    "observatory_imaging_set_filter": {"filter_name": "L"},
    "observatory_mount_abort_motion": {},
    "observatory_mount_manual_move": {"direction": "north"},
    "observatory_mount_park": {},
    "observatory_mount_set_slew_rate": {"rate_index": 1},
    "observatory_mount_set_tracking": {"enabled": True},
    "observatory_mount_slew": {"destination": "M 81"},
    "observatory_mount_sync": {"position": "10.0 20.0"},
    "observatory_mount_unpark": {},
    "observatory_safety_close_enclosure": {},
    "observatory_safety_open_enclosure": {},
    "execution_advance_session": {},
}
"""Sample arguments in the form the tool schemas accept. A command with no
entry fails `test_every_refused_command_has_sample_arguments`."""

DRIVER_COMMAND_METHODS = frozenset({
    "slew",
    "sync",
    "park",
    "unpark",
    "set_tracking",
    "abort_motion",
    "move",
    "set_slew_rate",
    "pulse_guide",
    "expose",
    "move_relative",
    "set_position",
    "open",
    "close",
})
"""Driver methods that move something or take a picture."""

DRIVER_ATTRIBUTES = (
    "mount_driver",
    "focuser_driver",
    "filter_wheel_driver",
    "camera_driver",
    "guide_camera_driver",
    "enclosure_driver",
)
"""The `control` driver properties the test replaces."""


def refused_tool_names() -> list[str]:
    """List the commands that monitor mode must stop.

    Returns
    -------
    names : `list` [`str`]
        Every command tool in the manifest minus the allowed and the
        caller-supplied ones.
    """
    tools = json.loads(Path(MANIFEST_PATH).read_text())["tools"]
    return [
        name
        for name, row in tools.items()
        if row["tool_class"] in COMMAND_TOOL_CLASSES
        and name not in ALLOWED_IN_MONITOR_MODE
        and name not in CALLER_SUPPLIED_STEP_TOOLS
    ]


class Recorder:
    """Stand-in drivers that count the commands they receive.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        Its control sub-API gets the stand-ins through the public driver
        setters.
    """

    def __init__(self, wayfinder: Wayfinder) -> None:
        """Install one mock per driver and a mock INDI session."""
        self.drivers = {name: MagicMock() for name in DRIVER_ATTRIBUTES}
        for mock in self.drivers.values():
            mock.connect = AsyncMock(return_value=True)
            mock.disconnect = AsyncMock(return_value=True)
            mock.get_last_image = AsyncMock(return_value=None)
        self.indi_session = MagicMock()
        self.indi_session.set_property.return_value = True
        for name, mock in self.drivers.items():
            setattr(wayfinder.control, name, mock)
        wayfinder.control.driver = self.indi_session

    def commands_sent(self) -> list[str]:
        """List the movement and capture commands any driver received.

        Returns
        -------
        commands : `list` [`str`]
            Entries like ``"mount_driver.slew"``.
        """
        return [
            f"{name}.{call[0].split('.')[0]}"
            for name, mock in self.drivers.items()
            for call in mock.mock_calls
            if call[0].split(".")[0] in DRIVER_COMMAND_METHODS
        ]

    def calls_to(self, method_name: str) -> int:
        """Count the calls to one method over all the drivers.

        Parameters
        ----------
        method_name : `str`
            For example ``"connect"``.

        Returns
        -------
        count : `int`
            The number of calls.
        """
        return sum(1 for mock in self.drivers.values() for call in mock.mock_calls if call[0] == method_name)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> tuple[ToolRegistry, Wayfinder, Recorder]:
    """Build the server's tools over a `Wayfinder` in monitor mode.

    A named target resolves to a fixed sky position, and the guide-camera
    set-up checks of `run_exposure_test` are answered, so a refusal can only
    come from the monitor-mode guard.

    Returns
    -------
    server : `tuple`
        The registry, the library it calls, and the recording drivers.
    """
    from types import SimpleNamespace

    from wayfindinglib import SkyPosition

    wayfinder = Wayfinder()
    recorder = Recorder(wayfinder)
    wayfinder.control.safety.enter_monitoring_mode(evidence_note="test of monitor mode")
    # Module paths are given as text: MCP may import only the public API.
    monkeypatch.setattr(
        "wayfindinglib.tasks.control_tasks.hardware_operations.resolve_destination",
        lambda context, destination: SkyPosition(ra_deg=10.0, dec_deg=20.0),
    )
    monkeypatch.setattr(
        "wayfindinglib.tasks.control_tasks.remote_transfer_tasks.check_remote_connection",
        lambda context: True,
    )
    context = wayfinder.control._context
    monkeypatch.setattr(context, "active_guide_camera", lambda: SimpleNamespace(name="Guider"))
    monkeypatch.setattr(context, "guider_plate_scale_arcsec_per_px", lambda: 1.0)
    registry = ToolRegistry(sandbox_paths=True)
    register_reflected_tools(registry, wayfinder, BRANCH_PREFIXES)
    return registry, wayfinder, recorder


def _text(content: list) -> str:
    """Join the text blocks of a tool reply.

    Parameters
    ----------
    content : `list`
        The reply of `ToolRegistry.execute`.

    Returns
    -------
    text : `str`
        The blocks' text.
    """
    return "\n".join(block.text for block in content)


def test_every_refused_command_has_sample_arguments() -> None:
    """Each command in the manifest has sample arguments."""
    names = refused_tool_names()

    assert names
    assert [name for name in names if name not in SAMPLE_ARGUMENTS] == []


@pytest.mark.parametrize("tool_name", refused_tool_names())
async def test_every_command_tool_is_refused_or_quiet_in_monitor_mode(server: tuple, tool_name: str) -> None:
    """A command tool answers `permission_denied` and sends nothing.

    `advance_session` records instead of refusing. Its queue cannot be
    built from the public API without an active rig, so here it only proves
    that an unknown session sends nothing; the library test runs a real
    queue. A tool whose argument JSON cannot carry fails early. All must
    send nothing.
    """
    registry, _wayfinder, recorder = server
    arguments = dict(SAMPLE_ARGUMENTS[tool_name])
    if tool_name in QUIET_BUT_NOT_REFUSED_TOOLS:
        arguments["session"] = "no-such-session"

    content = await registry.execute(tool_name, arguments)

    if tool_name in QUIET_BUT_NOT_REFUSED_TOOLS:
        assert _text(content).startswith("not_found"), _text(content)
    elif tool_name in JSON_CANNOT_CARRY_THE_ARGUMENT:
        assert isinstance(content, ToolErrorContent)
    else:
        assert isinstance(content, ToolErrorContent), _text(content)
        assert _text(content).startswith("permission_denied"), _text(content)
    assert recorder.commands_sent() == []


async def test_connect_and_device_settings_stay_allowed_in_monitor_mode(server: tuple) -> None:
    """Connect, disconnect and a device setting still work as tools (S10).

    The recording drivers see the allowed calls, so the "no command"
    results above are not a broken test rig.
    """
    registry, _wayfinder, recorder = server

    connect = await registry.execute("observatory_equipment_connect", {})
    setting = await registry.execute(
        "observatory_equipment_set_device_property",
        {
            "device_name": "Telescope Simulator",
            "property_name": "TELESCOPE_TRACK_MODE",
            "value": "On",
            "element": "TRACK_SIDEREAL",
        },
    )
    disconnect = await registry.execute("observatory_equipment_disconnect", {})

    for content in (connect, setting, disconnect):
        assert not isinstance(content, ToolErrorContent), _text(content)
    assert recorder.calls_to("connect") > 0
    assert recorder.calls_to("disconnect") > 0
    assert recorder.indi_session.set_property.call_count == 1
    assert recorder.commands_sent() == []


def test_the_registry_offers_every_command_in_the_manifest(
    server: tuple,
) -> None:
    """Each command tool of the manifest is a real tool of the server."""
    registry = server[0]
    tools = json.loads(Path(MANIFEST_PATH).read_text())["tools"]
    commands = [name for name, row in tools.items() if row["tool_class"] in COMMAND_TOOL_CLASSES]

    assert [name for name in commands if name not in registry.tools] == []
