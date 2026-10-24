"""Purpose: Shared helpers for the "monitor mode sends no commands" tests.

Description: Monitor mode means every capability is `DELEGATED`: the app
watches and computes but commands nothing. Three test files prove this
through three doors: the library (`wayfindinglib/test/`), the backend's
RPC dispatch (`backend/tests/`) and the MCP tools of the wayfindinglib-core
server (`mcp_servers/wayfinding_core/test/`). They share this module so
they all work from the same list of commands.

The list of commands is not typed in by hand. It is read from the
wayfindinglib-core server's `tool_manifest.json`: every tool whose
`tool_class` is ``actuate`` (or ``safe-stop``, which also commands the
hardware) is a command. A new command therefore joins the test the
moment it joins the manifest. A command with no sample arguments below
makes the test fail, so nobody can add one and forget to test it.

The commands that stay allowed in monitor mode are a named constant,
`ALLOWED_IN_MONITOR_MODE`, with the user's decision cited.

This module only reads the manifest file from disk. It imports nothing
from `mcp_servers`.
"""

import json
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

from wayfindinglib.drivers.interfaces.camera_driver import CameraDriver
from wayfindinglib.drivers.interfaces.enclosure_driver import EnclosureDriver
from wayfindinglib.drivers.interfaces.filter_wheel_driver import FilterWheelDriver
from wayfindinglib.drivers.interfaces.focuser_driver import FocuserDriver
from wayfindinglib.drivers.interfaces.guiding_driver import GuidingDriver
from wayfindinglib.drivers.interfaces.mount_driver import MountDriver, MountStatus
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.policy.safety import SafetyAssessment, SafetyVerdict
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
)

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "mcp_servers" / "wayfinding_core" / "tool_manifest.json"
"""The wayfindinglib-core server's reviewed list of tools."""

COMMAND_TOOL_CLASSES = frozenset({"actuate", "safe-stop"})
"""Manifest classes whose tools command hardware. ``safe-stop`` is
included because `abort_motion` is a motion command the user wants
refused in monitor mode (decision 2026-10-24)."""

ALLOWED_IN_MONITOR_MODE = frozenset({
    "observatory_equipment_connect",
    "observatory_equipment_disconnect",
    "observatory_equipment_set_device_property",
})
"""Commands that stay allowed in monitor mode.

The user decided this on 2026-10-24 (issue #95, settled decision S10):
every motion and capture command stays refused while delegated, but
device settings (`set_device_property`, the backend's
``telescope:set_indi_property``) and the app's own INDI connect and
disconnect stay allowed, because they move nothing and take no picture.
"""

ALLOWED_RPC_IN_MONITOR_MODE = frozenset({"telescope:connect", "telescope:set_indi_property"})
"""The backend RPC methods that correspond to `ALLOWED_IN_MONITOR_MODE`."""

CALLER_SUPPLIED_STEP_TOOLS = frozenset({
    "execution_execute_meridian_flip",
    "execution_recover_fault",
    "execution_recover_guide_star_loss",
    "observatory_guiding_run_calibration",
    "observatory_guiding_run_backlash_calibration",
    "observatory_safety_execute_safe_state",
})
"""Commands whose hardware steps are handed in by the caller.

These methods run the callables (``steps``, ``reacquire_guide_star`` and
so on) they are given. They never touch a driver themselves, and a
JSON tool call cannot carry a callable. The library does not check the
delegation policy for them. They are reported to the systems engineer
(issue #95, batch 5) and left out of the guarantee on purpose. The test
`test_caller_supplied_steps_list_matches_the_manifest` keeps this list
honest.
"""

QUIET_BUT_NOT_REFUSED_TOOLS = frozenset({"execution_advance_session"})
"""Commands that do not raise in monitor mode but still send nothing.

`advance_session` records what it would have done and issues nothing for
a `DELEGATED` capability (`session_runner`), so it returns normally. The
test checks that no driver command is sent.
"""

COMMAND_DRIVER_METHODS = frozenset({
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
    "set_switch_state",
    "set_variable_value",
})
"""Driver methods that move something or take a picture. Reads such as
`get_status` and `get_position` are not on the list."""

SAMPLE_ARGUMENTS: dict[str, dict[str, Any]] = {
    "observatory_guiding_expose": {"exposure_seconds": 1.0},
    "observatory_guiding_pulse": {"direction": "north", "duration_ms": 100.0},
    "observatory_guiding_run_exposure_test": {},
    "observatory_imaging_capture_image": {"exposure_seconds": 1.0, "register_job": False},
    "observatory_imaging_focus_move": {"steps": 10},
    "observatory_imaging_set_filter": {"filter_name": "L"},
    "observatory_mount_abort_motion": {},
    "observatory_mount_manual_move": {"direction": "north"},
    "observatory_mount_park": {},
    "observatory_mount_set_slew_rate": {"rate_index": 1},
    "observatory_mount_set_tracking": {"enabled": True},
    "observatory_mount_slew": {"destination": {"ra_deg": 10.0, "dec_deg": 20.0}},
    "observatory_mount_sync": {"position": {"ra_deg": 10.0, "dec_deg": 20.0}},
    "observatory_mount_unpark": {},
    "observatory_safety_close_enclosure": {},
    "observatory_safety_open_enclosure": {},
    "execution_advance_session": {"session": "monitor-mode-night"},
}
"""Sample arguments for each command a test calls.

`observatory_guiding_run_loop` is added by `sample_arguments`, because
it needs a fresh `threading.Event`."""

CHILD_PREFIXES = ("mount", "imaging", "guiding", "remote", "history", "safety", "equipment")
"""The children of `Wayfinder.control` that hold operations."""


def command_tool_names() -> list[str]:
    """List every command tool in the manifest, in the manifest's order.

    Returns
    -------
    names : `list` [`str`]
        Names of the tools whose class is in `COMMAND_TOOL_CLASSES`.
    """
    tools = json.loads(MANIFEST_PATH.read_text())["tools"]
    return [name for name, row in tools.items() if row["tool_class"] in COMMAND_TOOL_CLASSES]


def refused_tool_names() -> list[str]:
    """List the commands that monitor mode must stop.

    Returns
    -------
    names : `list` [`str`]
        Every command tool, minus `ALLOWED_IN_MONITOR_MODE` and minus
        `CALLER_SUPPLIED_STEP_TOOLS`.
    """
    return [
        name
        for name in command_tool_names()
        if name not in ALLOWED_IN_MONITOR_MODE and name not in CALLER_SUPPLIED_STEP_TOOLS
    ]


def sample_arguments(tool_name: str) -> dict[str, Any]:
    """Give sample arguments for one command tool.

    Parameters
    ----------
    tool_name : `str`
        A name from `refused_tool_names`. One with no entry in
        `SAMPLE_ARGUMENTS` raises `KeyError`: add its arguments there.

    Returns
    -------
    arguments : `dict` [`str`, `Any`]
        Arguments that pass the tool's own checks, so a refusal can only
        come from monitor mode.
    """
    if tool_name == "observatory_guiding_run_loop":
        return {"stop": threading.Event()}
    return dict(SAMPLE_ARGUMENTS[tool_name])


def resolve_tool(wayfinder: Any, tool_name: str) -> Any:
    """Find the library method that a manifest tool name stands for.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The library entry point.
    tool_name : `str`
        For example ``observatory_mount_slew``.

    Returns
    -------
    method : `Callable`
        The bound method, such as ``wayfinder.control.mount.slew``.

    Raises
    ------
    KeyError
        If the name matches no child of `control` and is not an execution tool.
    """
    if tool_name.startswith("execution_"):
        return getattr(wayfinder.execution, tool_name.removeprefix("execution_"))
    for child in CHILD_PREFIXES:
        prefix = f"observatory_{child}_"
        if tool_name.startswith(prefix):
            return getattr(getattr(wayfinder.control, child), tool_name.removeprefix(prefix))
    raise KeyError(tool_name)


NIGHT_SESSION_ID = "monitor-mode-night"
"""The id of the session `make_night_session` stores."""


def make_night_session(wayfinder: Any) -> ObservationSession:
    """Store a running one-entry night plan and return it.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        Its storage keeps the session, so `advance_session` can find it by id.

    Returns
    -------
    session : `ObservationSession`
        A running session whose one pending entry asks for one 10 s light
        frame, so a cycle reaches the slew and the capture.
    """
    now = datetime.now(UTC)
    entry = QueuedObservationPackage(
        id="entry-1",
        observation_package_id="package-1",
        target_id="M 81",
        start_time_mode=StartTimeMode.SOONEST,
        computed_start_time=now,
        status=QueueEntryStatus.PENDING,
        exposure_requests=[ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=10.0, count=1)],
    )
    session = ObservationSession(
        id=NIGHT_SESSION_ID,
        night_date=date(2026, 8, 5),
        status=SessionStatus.RUNNING,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id="camera-1",
        queue=[entry],
    )
    wayfinder.execution._butler.put(session, "observation_session", {"session_id": session.id})
    return session


def force_safe_assessment(wayfinder: Any, monkeypatch: Any) -> None:
    """Make the safety verdict "safe" for the next queue cycle.

    A real safety assessment reads weather sensors and would hold the
    queue before it reached any command. Forcing "safe" lets the cycle
    reach the slew and the capture, which is the path under test.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        Its `control.safety.status` is wrapped.
    monkeypatch : `pytest.MonkeyPatch`
        Undoes the change after the test.
    """
    real_status = wayfinder.control.safety.status

    def status(include: list[str] | None = None, capability: Any = None) -> Any:
        """Read the real status, with its assessment replaced by "safe".

        Returns
        -------
        result : `SafetyStatus`
            The status.
        """
        result = real_status(include=include, capability=capability)
        if include and "assessment" in include:
            result.assessment = SafetyAssessment(verdict=SafetyVerdict.SAFE)
        return result

    monkeypatch.setattr(wayfinder.control.safety, "status", status)


class RecordingDrivers:
    """Stand-in drivers that count every command they are sent.

    Build one with the control context, call `install`, and read
    `commands_sent` afterward. Each driver is an auto-specified mock of
    its abstract class, so a call to a method that does not exist fails.

    Parameters
    ----------
    context : `ControlContext`
        The context whose drivers are replaced.
    """

    REPLACED_ATTRIBUTES = (
        "_mount_driver",
        "_focuser_driver",
        "_filter_wheel_driver",
        "_camera_driver",
        "_guide_camera_driver",
        "_enclosure_driver",
        "_guiding_driver",
        "_driver",
        "_indi_diagnostics",
    )
    """The context attributes the stand-ins replace, put back by `restore`."""

    def __init__(self, context: Any) -> None:
        """Create the mocks and install them on `context`."""
        self.context = context
        self.drivers: dict[str, Any] = {
            "mount": create_autospec(MountDriver, instance=True),
            "focuser": create_autospec(FocuserDriver, instance=True),
            "filter_wheel": create_autospec(FilterWheelDriver, instance=True),
            "camera": create_autospec(CameraDriver, instance=True),
            "guide_camera": create_autospec(CameraDriver, instance=True),
            "enclosure": create_autospec(EnclosureDriver, instance=True),
            "guiding": create_autospec(GuidingDriver, instance=True),
        }
        self.indi_session = MagicMock()
        self.indi_session.set_property.return_value = True
        self._previous = {name: getattr(context, name) for name in self.REPLACED_ATTRIBUTES}
        self.context._mount_driver = self.drivers["mount"]
        self.context._focuser_driver = self.drivers["focuser"]
        self.context._filter_wheel_driver = self.drivers["filter_wheel"]
        self.context._camera_driver = self.drivers["camera"]
        self.context._guide_camera_driver = self.drivers["guide_camera"]
        self.context._enclosure_driver = self.drivers["enclosure"]
        self.context._guiding_driver = self.drivers["guiding"]
        self.context.driver = self.indi_session
        # The context builds its INDI diagnostics once and keeps them, tied
        # to the session that existed then. An earlier test (or the app
        # start-up) may have built them over another session, so a device
        # setting would miss the recording session. Rebuild them over it.
        from wayfindinglib.drivers.indi.diagnostics import IndiDiagnostics

        self.context._indi_diagnostics = IndiDiagnostics(session=self.indi_session)
        for mock in self.drivers.values():
            mock.connect = AsyncMock(return_value=True)
            mock.disconnect = AsyncMock(return_value=True)
        for camera in ("camera", "guide_camera"):
            self.drivers[camera].get_last_image = AsyncMock(return_value=None)
        # The guide loop starts only when the mount says it tracks.
        self.drivers["mount"].get_status = AsyncMock(
            return_value=MountStatus(
                ra="0h 0m 0s", dec="0", altitude="45", azimuth="180", tracking_status="Tracking"
            )
        )

        async def run_cycle(  # ruff: ignore[unused-async] -- the driver method is async
            exposure_seconds: float, gain: float | None, commands: Any
        ) -> None:
            """Behave like a real guiding driver: use the cycle's commands.

            Real drivers expose and pulse only through `commands`, which
            call the guarded `hardware_operations` functions. A refusal
            therefore stops this cycle the same way it stops a real one.
            """
            commands.expose(exposure_seconds, gain)
            commands.pulse("north", 100.0)

        self.drivers["guiding"].run_cycle = AsyncMock(side_effect=run_cycle)

    def restore(self) -> None:
        """Put the real drivers back on a context other tests share."""
        for name, value in self._previous.items():
            setattr(self.context, name, value)

    def make_guide_exposure_test_reachable(self, monkeypatch: Any) -> None:
        """Let `run_exposure_test` get past its set-up checks.

        The test first checks that the telescope computer answers and that
        a guide camera with guide optics is configured. A test machine has
        neither, so those checks are answered here. The command guard
        under test comes after them.

        Parameters
        ----------
        monkeypatch : `pytest.MonkeyPatch`
            Undoes the changes after the test.
        """
        from types import SimpleNamespace

        from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

        monkeypatch.setattr(remote_transfer_tasks, "check_remote_connection", lambda context: True)
        monkeypatch.setattr(self.context, "active_guide_camera", lambda: SimpleNamespace(name="Guider"))
        monkeypatch.setattr(self.context, "guider_plate_scale_arcsec_per_px", lambda: 1.0)

    def commands_sent(self) -> list[str]:
        """List the movement and capture commands any driver received.

        Returns
        -------
        commands : `list` [`str`]
            Entries like ``"mount.slew"``, one per call.
        """
        sent = []
        for name, mock in self.drivers.items():
            for call in mock.mock_calls:
                method = call[0].split(".")[0]
                if method in COMMAND_DRIVER_METHODS:
                    sent.append(f"{name}.{method}")
        return sent

    def calls_to(self, method_name: str) -> int:
        """Count the calls to one method on any driver, commands or not.

        Parameters
        ----------
        method_name : `str`
            For example ``"connect"``.

        Returns
        -------
        count : `int`
            Calls to that method over all the drivers.
        """
        return sum(1 for mock in self.drivers.values() for call in mock.mock_calls if call[0] == method_name)
