"""Purpose: Say what the astrometrics-backend MCP server offers.

Description: These tools reach the running backend over HTTP, through its
``/api/rpc`` route, the same way the app's window does. The server never
loads the backend in its own process. It imports only the two guard
functions from ``backend.services.infrastructure`` that refuse destructive
calls and code. The server's manifest, ``tool_manifest.json`` in this
folder, decides which tools a profile may use.
"""

import base64
import io
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from mcp.types import ImageContent, Resource, TextContent

from astrometricslib.drivers import scratch_directory
from astrometricslib.exceptions import (
    AstrometricsError,
    ConflictError,
    ErrorInfo,
    ExternalServiceError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    error_from_info,
    to_error_info,
)
from astrometricslib.jobs import new_request_id
from backend.services.infrastructure.agent_code_policy import check_agent_code
from backend.services.infrastructure.destructive_guard import destructive_rpc_reason
from mcp_servers.backend.mcp_http import post_to_backend
from mcp_servers.common.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

SERVER_NAME = "astrometrics-backend"
"""The server's name in the client configurations."""

MANIFEST_PATH = Path(__file__).resolve().parent / "tool_manifest.json"
"""The reviewed list of this server's tools."""

NOTIFICATIONS_URI = "astrometrics://notifications"
"""The address of the resource that lists unread job notifications."""

USER_SCRIPT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\.py")
"""A plain script file name: letters, digits, ``_`` and ``-``, ending in
``.py``. It has no folder part and no dots except the extension."""

RECENT_RUN_LIMIT = 20
"""The most runs `terminal_get_recent_runs` returns. It matches the backend's
history size."""

MAXIMUM_PLOT_WIDTH_PIXELS = 1200
"""A figure wider than this is shrunk before it is sent as an image."""

ToolFunction = Callable[..., Awaitable[Any]]
"""An async function that runs one tool."""

TOOL_SPECS: list[tuple[str, str, dict[str, Any], ToolFunction]] = []
"""Every tool this server can offer: name, description, argument schema and
function. `build_registry` registers them all."""


def tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    specs: list[tuple[str, str, dict[str, Any], ToolFunction]] = TOOL_SPECS,
) -> Callable[[ToolFunction], ToolFunction]:
    """Build a decorator that adds a function to a list of tool specs.

    Parameters
    ----------
    name : `str`
        The tool's name.
    description : `str`
        What the tool does, shown to the client.
    input_schema : `dict`
        The JSON schema of the tool's arguments.
    specs : `list`, optional
        The list to add the tool to. Defaults to `TOOL_SPECS`; the job tools
        in `mcp_servers.backend.app_jobs` keep their own list.

    Returns
    -------
    decorator : `Callable`
        Records the function it wraps and returns it unchanged.
    """

    def decorator(func: ToolFunction) -> ToolFunction:
        """Record `func` as the tool's implementation.

        Returns
        -------
        func : `Callable`
            The same function.
        """
        specs.append((name, description, input_schema, func))
        return func

    return decorator


def suggest_remediation(tool_name: str, message: str) -> dict[str, Any]:
    """Suggest how a client can recover from a failed tool call.

    Parameters
    ----------
    tool_name : `str`
        Name of the tool that failed.
    message : `str`
        The error message the client sees.

    Returns
    -------
    remediation : `dict` [`str`, `Any`]
        A ``suggestion`` and a tool to call next. The hint is specific when
        the message names a known problem (a missing target, a disconnected
        device, a filter, bad syntax), and general otherwise.
    """
    text = message.lower()
    if "target" in text and "not found" in text:
        return {
            "suggestion": "Target names are case-sensitive. Verify exact catalog identifier.",
            "recommended_tool": "call_mcp_tool('astrometricslib-core', 'target_query', {})",
        }
    if "disconnected" in text or "not connected" in text or "indi" in text:
        return {
            "suggestion": "Hardware driver is currently offline or disconnected.",
            "recommended_tool": "call_mcp_tool('wayfindinglib-core', 'observatory_equipment_connect', {})",
        }
    if "filter" in text:
        return {
            "suggestion": "Requested filter wheel slot is unknown or unconfigured.",
            "recommended_tool": "call_mcp_tool('wayfindinglib-core', 'observatory_imaging_status', {})",
        }
    if "syntax" in text or "unexpected" in text:
        return {
            "suggestion": "Check input arguments against parameter schema or query inspect_api.",
            "recommended_tool": (
                "call_mcp_tool('astrometrics-backend', 'terminal_inspect_api', {'target': '...'})"
            ),
        }
    return {
        "suggestion": f"Tool '{tool_name}' failed: {message}",
        "inspect_tool": (
            f"call_mcp_tool('astrometrics-backend', 'terminal_inspect_api', {{'target': '{tool_name}'}})"
        ),
    }


def build_registry() -> ToolRegistry:
    """Build the registry with every tool this server can offer.

    Returns
    -------
    registry : `ToolRegistry`
        All tools, before a profile removes any, including the job tools of
        `mcp_servers.backend.app_jobs`. A failed call's error holds a
        recovery hint from `suggest_remediation`.
    """
    # The job tools call `execute_rpc` from this module, so they are loaded
    # here, once this module is complete.
    from mcp_servers.backend.app_jobs import JOB_TOOL_SPECS

    registry = ToolRegistry(remediation=suggest_remediation)
    for name, description, input_schema, func in [*TOOL_SPECS, *JOB_TOOL_SPECS]:
        registry.register(name, description, input_schema)(func)
    return registry


async def execute_rpc(method: str, params: dict[str, Any] | None = None) -> Any:
    """Call one backend RPC method through the ``/api/rpc`` route.

    Parameters
    ----------
    method : `str`
        The RPC method name, such as ``"target:list"``.
    params : `dict`, optional
        The method's parameters. If `None` (default), none are sent.

    Returns
    -------
    data : `Any`
        The method's result, without the router's success wrapper.

    Raises
    ------
    ExternalServiceError
        If the backend cannot be reached, sends an answer that is not a
        JSON-RPC reply, or reports an error without an error record.

    Notes
    -----
    When the backend sends an error record, that error is raised again as
    the same category (see `error_from_info`), such as `NotFoundError`.
    """
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": "mcp-proxy"}
    reply = await post_to_backend("/api/rpc", payload)
    if not isinstance(reply, dict):
        raise ExternalServiceError(f"The backend sent a malformed answer to {method}.")
    error = reply.get("error")
    if error is not None:
        data = error.get("data") if isinstance(error, dict) else None
        if isinstance(data, dict) and "code" in data and "message" in data:
            backend_error = error_from_info(ErrorInfo.model_validate(data))
            raise backend_error
        message = error.get("message") if isinstance(error, dict) else error
        raise ExternalServiceError(f"The backend reported an error for {method}: {message}")
    result = reply.get("result")
    # The router wraps every result as {"status": "success", "data": ...}.
    if isinstance(result, dict) and result.get("status") == "success" and "data" in result:
        return result["data"]
    return result


class NotificationResource:
    """Offer the unread job notifications as an MCP resource."""

    def list_resources(self) -> list[Resource]:
        """List the one resource this server offers.

        Returns
        -------
        resources : `list` [`mcp.types.Resource`]
            The notifications resource.
        """
        return [
            Resource(
                uri=NOTIFICATIONS_URI,
                name="System Notifications",
                description="Real-time notifications about background job completions",
                mime_type="application/json",
            )
        ]

    async def read_resource(self, uri: str) -> str:
        """Read the unread notifications from the backend.

        Parameters
        ----------
        uri : `str`
            The resource address. Only `NOTIFICATIONS_URI` is known.

        Returns
        -------
        content : `str`
            The unread notifications as a JSON list.

        Raises
        ------
        NotFoundError
            If `uri` is not `NOTIFICATIONS_URI`.
        """
        if uri != NOTIFICATIONS_URI:
            raise NotFoundError(f"Unknown resource: {uri}")
        notifications = await execute_rpc("system:notifications", {"unread_only": True})
        return json.dumps(notifications)


# ---------------------------------------------------------------------------
# Backend JSON-RPC & Diagnostics Tools
# ---------------------------------------------------------------------------


@tool(
    "backend_call_rpc",
    (
        "Execute any backend JSON-RPC 2.0 method directly against the running "
        "FastAPI /api/rpc endpoint to isolate backend vs frontend issues."
    ),
    {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "description": (
                    "The exact JSON-RPC method name registered on the backend "
                    "(e.g. 'astronomy:visible', 'target:list', 'images:last')."
                ),
            },
            "params": {
                "type": "object",
                "description": "Optional parameters dictionary to pass to the RPC method.",
            },
        },
        "required": ["method"],
    },
)
async def tool_backend_call_rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a JSON-RPC 2.0 method directly against the backend.

    Parameters
    ----------
    method : `str`
        The exact JSON-RPC method name.
    params : `dict[str, Any]`, optional
        Parameters passed to the RPC method. Defaults to empty dict.

    Returns
    -------
    result : `dict[str, Any]`
        ``status`` (``"success"``), the ``method``, the returned
        ``data``, and the elapsed time in milliseconds.

    Raises
    ------
    PermissionDeniedError
        If the method deletes data or runs code.
    AstrometricsError
        Whatever error the call raised. Its details then also hold
        ``elapsed_ms``, so a slow failure can be told from a fast one.
    """
    refusal = destructive_rpc_reason(method)
    if method.startswith("terminal:"):
        refusal = "Terminal methods run code and are only available through electron_run_python."
    if refusal:
        raise PermissionDeniedError(refusal, details={"method": method})

    start_time = time.perf_counter()
    try:
        data = await execute_rpc(method, params or {})
    except AstrometricsError as exc:
        exc.details.setdefault("elapsed_ms", round((time.perf_counter() - start_time) * 1000, 2))
        raise
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {"status": "success", "method": method, "elapsed_ms": elapsed_ms, "data": data}


@tool(
    "backend_health_check",
    "Check health and connectivity of the running Astrometrics backend server.",
    {"type": "object", "properties": {}},
)
async def tool_backend_health_check() -> dict[str, Any]:
    """Probe the backend API listener and report status.

    Returns
    -------
    result : `dict[str, Any]`
        Whether the backend answered, how long it took, and why it did
        not answer if it failed.
    """
    start_time = time.perf_counter()

    # Probe backend JSON-RPC via execute_rpc with lightweight target:list.
    # A failed probe is the answer of a health check, not its failure.
    details = "Backend responding normally"
    try:
        await execute_rpc("target:list", {})
        is_online = True
    except Exception as exc:  # health probe: any failure means degraded
        info = to_error_info(exc, new_request_id())
        logger.warning("Backend health probe failed (%s): %s", info.code, info.message, exc_info=exc)
        is_online = False
        details = info.message
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "status": "healthy" if is_online else "degraded",
        "backend_online": is_online,
        "latency_ms": elapsed_ms,
        "details": details,
    }


# ---------------------------------------------------------------------------
# AI Agent Desktop Control Tools
# ---------------------------------------------------------------------------


@tool(
    "electron_run_python",
    "Execute Python code against astrometrics and wayfinder public APIs inside the supervised runtime.",
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code snippet or script to execute.",
            },
        },
        "required": ["code"],
    },
)
async def tool_electron_run_python(code: str) -> dict[str, Any]:
    """Execute Python code in the supervised Astrometrics backend environment.

    Parameters
    ----------
    code : `str`
        Python code snippet to execute.

    Returns
    -------
    result : `dict[str, Any]`
        Execution envelope containing status, stdout, stderr, result,
        plots, execution time in milliseconds, and active workspace
        manifest.

    Raises
    ------
    PermissionDeniedError
        If the code does something agents may not do, such as delete
        files.
    """
    refusal = check_agent_code(code)
    if refusal:
        raise PermissionDeniedError(refusal)
    return await execute_rpc("terminal:execute", {"code_str": code, "source": "agent"})


@tool(
    "terminal_get_workspace",
    "Inspect active user variables in the Python workspace (MATLAB-style Workspace viewer).",
    {"type": "object", "properties": {}},
)
async def tool_terminal_get_workspace() -> dict[str, Any]:
    """Return manifest of user variables currently resident in memory.

    Returns
    -------
    result : `dict[str, Any]`
        List of variable descriptors with name, type, shape, and
        byte size.
    """
    variables = await execute_rpc("terminal:get_workspace", {})
    return {"status": "success", "variables": variables}


@tool(
    "terminal_inspect_api",
    "Inspect signatures, arguments, and docstrings of an object or API branch.",
    {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Object or attribute name to introspect "
                    "(e.g. 'astrometrics.targets', 'wayfinder.control')."
                ),
            },
        },
        "required": ["target"],
    },
)
async def tool_terminal_inspect_api(target: str) -> dict[str, Any]:
    """Introspect an Astrometrics or Wayfinder public API component.

    Parameters
    ----------
    target : `str`
        The name of the component or method to inspect.

    Returns
    -------
    result : `dict[str, Any]`
        Method signatures and numpydoc summaries.
    """
    code = f"result = inspect_api({target})"
    data = await execute_rpc("terminal:execute", {"code_str": code, "source": "agent"})
    return data.get("result", data) if isinstance(data, dict) else data


@tool(
    "ui_navigate_mode",
    "Navigate the UI workspace to a specific view (e.g. 'Planetarium', 'Image Processing') and target.",
    {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "Planetarium",
                    "Observatory Manager",
                    "Image Processing",
                    "Observation Manager",
                    "Command Console",
                ],
                "description": "Target workspace mode.",
            },
            "target": {
                "type": "string",
                "description": "Optional target name to select (e.g. 'M31', 'NGC 7000').",
            },
        },
        "required": ["mode"],
    },
)
async def tool_ui_navigate_mode(mode: str, target: str | None = None) -> dict[str, Any]:
    """Switch active UI workspace mode and optionally select a target.

    Parameters
    ----------
    mode : `str`
        Target view mode name.
    target : `str`, optional
        Target object to select in the new view.

    Returns
    -------
    result : `dict[str, Any]`
        Dispatch status.
    """
    await execute_rpc("ui:navigate", {"mode": mode, "target": target})
    return {"status": "success", "mode": mode, "target": target}


@tool(
    "ui_inspect_variable",
    "Open the UI Variable Inspector panel to display a specific workspace variable (MATLAB Variable Editor).",
    {
        "type": "object",
        "properties": {
            "variable_name": {
                "type": "string",
                "description": "Name of the variable in the workspace to inspect.",
            },
        },
        "required": ["variable_name"],
    },
)
async def tool_ui_inspect_variable(variable_name: str) -> dict[str, Any]:
    """Command UI to open Variable Inspector for a workspace variable.

    Parameters
    ----------
    variable_name : `str`
        Variable name in the active Python session.

    Returns
    -------
    result : `dict[str, Any]`
        Dispatch status.
    """
    await execute_rpc("ui:inspect_variable", {"variable_name": variable_name})
    return {"status": "success", "variable_name": variable_name}


@tool(
    "docs_get",
    "Read repository documentation and guides (same topics available in Command Console Doc Viewer).",
    {
        "type": "object",
        "properties": {
            "topic_id": {
                "type": "string",
                "description": "Relative documentation topic ID or file path (e.g. 'Getting_Started.md').",
            },
        },
        "required": ["topic_id"],
    },
)
async def tool_docs_get(topic_id: str) -> dict[str, Any]:
    """Retrieve markdown documentation topic content.

    Parameters
    ----------
    topic_id : `str`
        Documentation path or identifier.

    Returns
    -------
    result : `dict[str, Any]`
        Documentation topic content and title.
    """
    return await execute_rpc("docs:get_topic", {"topic_id": topic_id})


@tool(
    "ui_editor_sync",
    "Read or update the active code buffer in the Command Console Code Editor.",
    {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Optional code to set in editor. If omitted, returns current editor code.",
            },
        },
    },
)
async def tool_ui_editor_sync(code: str | None = None) -> dict[str, Any]:
    """Sync code with the Command Console editor.

    Parameters
    ----------
    code : `str`, optional
        Code to push to editor.

    Returns
    -------
    result : `dict[str, Any]`
        Current or updated editor code status.
    """
    if code is not None:
        return await execute_rpc("ui:editor_set", {"code_content": code})
    return await execute_rpc("ui:editor_get", {})


# ---------------------------------------------------------------------------
# What the person sees in the app: status and the two allowed controls
# ---------------------------------------------------------------------------

APP_STATUS_SECTIONS = (
    "health",
    "connections",
    "system",
    "active_jobs",
    "view",
    "telescope",
    "guiding",
    "indi_devices",
    "indi_properties",
)
"""The parts of the app's state `tool_app_status` can report."""

DEFAULT_STATUS_SECTIONS = tuple(name for name in APP_STATUS_SECTIONS if name != "indi_properties")
"""What `tool_app_status` reports when no section is asked for.
``indi_properties`` is left out because it needs a device name."""

GUIDING_SAMPLES_REPORTED = 20
"""How many of the newest guide samples the guiding section lists."""

GUIDING_SAMPLE_KEYS = ("time", "dra", "ddec", "pulseRa", "pulseDec", "snr", "rmsRa", "rmsDec")
"""The fields of a guide sample worth reporting."""

MAXIMUM_INDI_PROPERTIES = 80
"""Most INDI properties one device answer lists."""

APP_CONTROL_ACTIONS = ("navigate",)
"""What an AI may do in the app. Pausing and resuming pipelines freeze or
thaw Siril and the plate solver, so they are not offered. Notifications are
not offered either: the AI tells the person in its own reply."""


@tool(
    "app_status",
    (
        "Report the app's live state: backend health, the telescope (position, tracking or parked, pier "
        "side, ambient and camera temperature, focuser, filter), guiding, INDI devices and their "
        "properties, system resources, and the jobs that are running. Reads only."
    ),
    {
        "type": "object",
        "properties": {
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": list(APP_STATUS_SECTIONS)},
                "description": (
                    "Which parts to report. Defaults to all of them except indi_properties, which "
                    "needs a device."
                ),
            },
            "device": {
                "type": "string",
                "description": "For indi_properties: the INDI device name, from indi_devices.",
            },
            "property_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For indi_properties: only these property names, such as TELESCOPE_PIER_SIDE.",
            },
        },
    },
)
async def tool_app_status(
    include: list[str] | None = None,
    device: str | None = None,
    property_names: list[str] | None = None,
) -> dict[str, Any]:
    """Report the running app's live state, resources and jobs.

    Parameters
    ----------
    include : `list` [`str`], optional
        Sections to report, from `APP_STATUS_SECTIONS`. All but
        ``indi_properties`` when omitted.
    device : `str`, optional
        The INDI device for ``indi_properties``.
    property_names : `list` [`str`], optional
        Only these INDI properties.

    Returns
    -------
    status : `dict` [`str`, `Any`]
        One key per section asked for, and ``unavailable`` for what the
        app cannot say. A section that could not be read holds
        ``{"error": ErrorInfo}`` instead, so the other sections still
        come back.

    Raises
    ------
    InvalidArgumentError
        If a section is unknown, or ``indi_properties`` is asked for
        without a device.
    """
    sections = list(include) if include else list(DEFAULT_STATUS_SECTIONS)
    unknown = [name for name in sections if name not in APP_STATUS_SECTIONS]
    if unknown:
        raise InvalidArgumentError(
            f"Unknown section(s) {unknown}. Choose from {list(APP_STATUS_SECTIONS)}.",
            details={"unknown": unknown},
        )
    if "indi_properties" in sections and not device:
        raise InvalidArgumentError(
            "The indi_properties section needs a device name; list them with the indi_devices section."
        )

    answer: dict[str, Any] = {}
    unavailable: list[str] = []
    if "health" in sections:
        answer["health"] = await tool_backend_health_check()
    if "connections" in sections or "system" in sections:
        health = await _read_section("system", _system_health())
        if "indi" not in health:  # the section failed; report the error in both
            for section in ("connections", "system"):
                if section in sections:
                    answer[section] = health
        else:
            if "connections" in sections:
                answer["connections"] = {"indi": health.get("indi")}
            if "system" in sections:
                answer["system"] = health.get("resources")
    if "active_jobs" in sections:
        answer["active_jobs"] = await _read_section("active_jobs", _active_jobs())
    if "telescope" in sections:
        answer["telescope"] = await _read_section("telescope", _telescope_status())
    if "guiding" in sections:
        answer["guiding"] = await _read_section("guiding", _guiding_status())
    if "indi_devices" in sections:
        answer["indi_devices"] = await _read_section("indi_devices", _indi_devices())
    if "indi_properties" in sections:
        answer["indi_properties"] = await _read_section(
            "indi_properties", _indi_properties(device, property_names)
        )
    if "view" in sections:
        unavailable.append(
            "view: the backend does not keep track of which view the window shows, so the current view "
            "cannot be reported."
        )
    if unavailable:
        answer["unavailable"] = unavailable
    return answer


async def _read_section(name: str, work: Awaitable[Any]) -> Any:
    """Read one section of `tool_app_status`, keeping a failure as data.

    The sections are independent, so one that fails must not hide the
    others. The failure is logged once, with its traceback.

    Parameters
    ----------
    name : `str`
        The section's name, for the log.
    work : `Awaitable`
        The coroutine that reads the section.

    Returns
    -------
    section : `Any`
        What the coroutine returned, or ``{"error": ErrorInfo}`` if it
        raised.
    """
    try:
        return await work
    except Exception as exc:  # one failed section must not stop the rest
        info = to_error_info(exc, new_request_id())
        logger.warning("app_status section %s failed (%s): %s", name, info.code, info.message, exc_info=exc)
        return {"error": info.model_dump(by_alias=True)}


def _unwrap(response: Any) -> Any:
    """Strip the success and data layers around a backend answer.

    Some services wrap their own answer as ``{"status": "success",
    "data": ...}``, so the real answer can be a level or two down.

    Parameters
    ----------
    response : `Any`
        What `execute_rpc` returned.

    Returns
    -------
    payload : `Any`
        The innermost value.
    """
    value = response
    while isinstance(value, dict) and value.get("status") == "success" and "data" in value:
        value = value["data"]
    return value


async def _system_health() -> dict[str, Any]:
    """Read the backend's health answer, which holds connections and resources.

    Returns
    -------
    health : `dict` [`str`, `Any`]
        The answer, with an ``indi`` connection entry and ``resources``.

    Raises
    ------
    ExternalServiceError
        If the answer has no ``indi`` entry.
    """
    data = _unwrap(await execute_rpc("system:health", {}))
    if not isinstance(data, dict) or "indi" not in data:
        raise ExternalServiceError("The backend's health answer did not include the connections.")
    return data


async def _telescope_status() -> dict[str, Any]:
    """Report the live telescope state the app shows in its header.

    The backend's status already holds the pointing, tracking, pier side,
    park state, tracking rate, temperatures, focuser and filter, as
    `control.mount.status` reads them.

    Returns
    -------
    telescope : `dict` [`str`, `Any`]
        The status without its guiding and alignment history.

    Raises
    ------
    ExternalServiceError
        If the backend sends no telescope status.
    """
    status = _unwrap(await execute_rpc("telescope:status", {}))
    if not isinstance(status, dict):
        raise ExternalServiceError("The backend did not send a telescope status.")
    return {key: value for key, value in status.items() if key not in ("guidingHistory", "alignmentAttempts")}


async def _guiding_status() -> dict[str, Any]:
    """Report the live guiding state and its newest samples.

    Returns
    -------
    guiding : `dict` [`str`, `Any`]
        ``is_guiding``, the running ``stats`` (RMS in arcseconds), and the
        newest `GUIDING_SAMPLES_REPORTED` samples.

    Raises
    ------
    ExternalServiceError
        If the backend sends no guiding status.
    """
    status = _unwrap(await execute_rpc("guiding:status", {}))
    if not isinstance(status, dict):
        raise ExternalServiceError("The backend did not send a guiding status.")
    history = status.get("history") or []
    return {
        **{key: value for key, value in status.items() if key != "history"},
        "samples_total": len(history),
        "recent_samples": [
            {key: sample.get(key) for key in GUIDING_SAMPLE_KEYS if key in sample}
            for sample in history[-GUIDING_SAMPLES_REPORTED:]
        ],
    }


async def _indi_devices() -> Any:
    """List the INDI devices the app can see.

    Returns
    -------
    devices : `list` [`str`]
        The device names.
    """
    return _unwrap(await execute_rpc("telescope:indi_devices", {}))


async def _indi_properties(device: str, property_names: list[str] | None) -> dict[str, Any]:
    """Read the properties of one INDI device. Nothing is changed.

    Parameters
    ----------
    device : `str`
        The device name.
    property_names : `list` [`str`], optional
        Only these properties.

    Returns
    -------
    answer : `dict` [`str`, `Any`]
        ``device``, ``properties`` (each with its label, state, type and
        elements) and how many exist.

    Raises
    ------
    ExternalServiceError
        If the backend sends no property list.
    """
    properties = _unwrap(await execute_rpc("telescope:indi_properties", {"device_name": device}))
    if not isinstance(properties, dict):
        raise ExternalServiceError(f"The backend did not send the properties of {device}.")
    total = len(properties)
    if property_names:
        wanted = set(property_names)
        properties = {name: record for name, record in properties.items() if name in wanted}
    shown = dict(list(properties.items())[:MAXIMUM_INDI_PROPERTIES])
    return {
        "device": device,
        "properties_total": total,
        "properties_shown": len(shown),
        "properties": {
            name: {key: record.get(key) for key in ("label", "state", "type", "perm", "elements")}
            for name, record in shown.items()
        },
    }


async def _active_jobs() -> Any:
    """List the jobs the backend records as running.

    The list comes from the job history read (``jobs:query`` with
    ``active_only``), the same read the app uses.

    Returns
    -------
    jobs : `list` [`dict`]
        Up to 50 jobs whose status is ``started`` or ``running``, newest
        first. A job whose process most likely died has ``looks_stale``
        true and ``is_active`` false.
    """
    answer = _unwrap(await execute_rpc("jobs:query", {"active_only": True, "limit": 50}))
    return (answer.get("jobs") or []) if isinstance(answer, dict) else []


@tool(
    "app_controls",
    "Do what the person can do in the app: switch the view.",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(APP_CONTROL_ACTIONS),
                "description": "navigate: switch to a view (and select a target).",
            },
            "mode": {
                "type": "string",
                "enum": [
                    "Planetarium",
                    "Observatory Manager",
                    "Image Processing",
                    "Observation Manager",
                    "Command Console",
                ],
                "description": "For navigate: the view to show.",
            },
            "target": {"type": "string", "description": "For navigate: a target to select."},
        },
        "required": ["action"],
    },
)
async def tool_app_controls(
    action: str,
    mode: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Switch the app's view.

    Parameters
    ----------
    action : `str`
        ``"navigate"``.
    mode : `str`, optional
        The view, for ``navigate``.
    target : `str`, optional
        A target to select, for ``navigate``.

    Returns
    -------
    result : `dict` [`str`, `Any`]
        What was dispatched.

    Raises
    ------
    InvalidArgumentError
        If the action is not one of `APP_CONTROL_ACTIONS`, or a needed
        argument is missing.
    """
    if action == "navigate":
        if not mode:
            raise InvalidArgumentError("action='navigate' needs mode.")
        return await tool_ui_navigate_mode(mode, target)
    raise InvalidArgumentError(
        f"action must be one of {list(APP_CONTROL_ACTIONS)}. Pausing jobs and notifications are not offered."
    )


def build_script_header(description: str) -> str:
    """Write the comment block that marks a script as written by an AI.

    Parameters
    ----------
    description : `str`
        What the script is for. May be empty. Line breaks become more
        comment lines, so the description cannot add code.

    Returns
    -------
    header : `str`
        Comment lines that end with a blank line.
    """
    lines = [
        "# Added by an AI client through the astrometrics-backend MCP server.",
        f"# Date added: {date.today().isoformat()}",
        "# Not reviewed or run yet. Read it before you press Run.",
    ]
    if description.strip():
        lines += [f"# Description: {line.strip()}" for line in description.strip().splitlines()]
    return "\n".join(lines) + "\n\n"


@tool(
    "terminal_add_script",
    (
        "Add a new Python script to the app's Command Console 'My scripts' list for the person to "
        "review and run. You cannot run it. It will not replace an existing script. The console "
        "preloads only the public objects a notebook would make: astrometrics (Astrometrics), wayfinder "
        "(Wayfinder), np, plt and fits (astropy.io.fits), plus three console helpers: selection "
        "(read-only: what is picked in Image Processing now, with target (the library's Target, or None), "
        "path, kind ('stack' or 'frame'), star (name, library_id, ra_deg and dec_deg, and x_px and y_px, "
        "the column and row in the file at path counted from 1) and set_at; fields are None when nothing "
        "is picked), show(target=..., star=..., path=..., display=...) (opens a target, star or file in "
        "Image Processing or the Astronomy Manager when the run ends) and doc(name) (opens that name's "
        "API reference entry). The app's internal services are not available. Use only public names; "
        "an old name fails with 'moved to' and the new call. Write the script so a useful part can move "
        "into the library: put the analysis in a function with explicit inputs (for example "
        "def measure_night(target, night): ...) that uses only astrometrics and wayfinder calls, and use "
        "selection, show, doc and print only in a few driver lines at the end that call it. "
        "astrometrics.visualization.plot_image(path, overlays=['detections', 'stars']) draws a "
        "frame or stack with its detected and named stars."
    ),
    {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "A plain file name ending in .py, such as 'plot_guiding.py'. No folders.",
            },
            "content": {"type": "string", "description": "The Python source of the script."},
            "description": {
                "type": "string",
                "description": "What the script does. It goes in a comment at the top of the file.",
            },
        },
        "required": ["filename", "content"],
    },
)
async def tool_terminal_add_script(filename: str, content: str, description: str = "") -> dict[str, Any]:
    """Save a new user script for the person to review and run.

    Parameters
    ----------
    filename : `str`
        A plain ``.py`` file name with no folder.
    content : `str`
        The script's Python source.
    description : `str`, optional
        What the script does. It is written into the header comment.

    Returns
    -------
    result : `dict` [`str`, `Any`]
        The saved ``filename``, its ``size_bytes``, ``status`` and a
        ``next_step`` sentence for the person.

    Raises
    ------
    InvalidArgumentError
        If the file name is not a plain ``.py`` name, or the content is
        empty.
    ConflictError
        If a user script with that name exists. The tool never overwrites.

    Notes
    -----
    The tool only saves the file. It never calls a method that runs code,
    so the person must run the script in the Command Console.
    """
    if not USER_SCRIPT_NAME_PATTERN.fullmatch(filename):
        raise InvalidArgumentError(
            f"{filename!r} is not allowed. Use a plain file name made of letters, digits, '_' and '-', "
            "ending in '.py', with no folders."
        )
    if not content.strip():
        raise InvalidArgumentError("The script is empty.")
    existing = await execute_rpc("terminal:list_scripts", {})
    taken = {str(entry.get("filename", "")).lower(): str(entry.get("filename", "")) for entry in existing}
    if filename.lower() in taken:
        raise ConflictError(
            f"A user script named {taken[filename.lower()]!r} already exists. Nothing was changed. "
            "Choose a different file name."
        )
    saved = await execute_rpc(
        "terminal:save_script",
        {"filename": filename, "content": build_script_header(description) + content},
    )
    return {
        "filename": saved["filename"],
        "size_bytes": saved["size_bytes"],
        "status": saved["status"],
        "next_step": (
            f"Tell the person to open Command Console > Scripts > My scripts, open {saved['filename']}, "
            "review it, and press Run. You cannot run it. An open Command Console shows the new "
            "file in its My scripts list right away."
        ),
    }


def _plot_image(plot_path: str) -> tuple[ImageContent | None, str | None]:
    """Read one figure file and make it an image for the client.

    Only a ``.png`` file inside the app's scratch folder is read. A figure
    wider than `MAXIMUM_PLOT_WIDTH_PIXELS` is shrunk to that width.

    Parameters
    ----------
    plot_path : `str`
        The figure's file path, as the backend reported it.

    Returns
    -------
    image : `ImageContent` or `None`
        The picture, or `None` when it cannot be read.
    problem : `str` or `None`
        Why there is no picture, or `None` when there is one.
    """
    from PIL import Image

    path = Path(plot_path)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None, "The figure file is gone."
    if resolved.suffix.lower() != ".png" or not resolved.is_relative_to(Path(scratch_directory()).resolve()):
        return None, "The figure file is not in the app's scratch folder."
    try:
        with Image.open(resolved) as picture:
            picture.load()
            if picture.width > MAXIMUM_PLOT_WIDTH_PIXELS:
                new_height = max(1, round(picture.height * MAXIMUM_PLOT_WIDTH_PIXELS / picture.width))
                picture = picture.resize((MAXIMUM_PLOT_WIDTH_PIXELS, new_height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            picture.save(buffer, format="PNG")
    except (OSError, ValueError) as error:
        return None, f"The figure file could not be read: {error}"
    return (
        ImageContent(
            type="image", data=base64.b64encode(buffer.getvalue()).decode("ascii"), mime_type="image/png"
        ),
        None,
    )


@tool(
    "terminal_get_recent_runs",
    (
        "Read what the person's recent Command Console runs printed and drew, newest first. Each run has its "
        "time, label, code, status, printed text (stdout), error text (stderr) and figure file paths. With "
        "include_plots, each figure is also returned as an image. Read-only: you cannot run code."
    ),
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": RECENT_RUN_LIMIT,
                "description": "How many recent runs to return. Default 1.",
            },
            "include_plots": {
                "type": "boolean",
                "description": "Return each figure as an image, in run order. Default true.",
            },
        },
    },
)
async def tool_terminal_get_recent_runs(
    limit: int = 1, include_plots: bool = True
) -> list[TextContent | ImageContent]:
    """Return the recent Command Console runs, with their figures.

    Parameters
    ----------
    limit : `int`, optional
        How many runs to return, from 1 to `RECENT_RUN_LIMIT`.
    include_plots : `bool`, optional
        Whether to attach each figure as an image.

    Returns
    -------
    content : `list` [`TextContent` or `ImageContent`]
        First a JSON text with the runs (newest first). Each run lists its
        ``plot_paths`` and, with images, ``plot_images`` (how many were
        attached, and why any are missing). Then the images, in run order.

    Raises
    ------
    InvalidArgumentError
        If ``limit`` is not a whole number from 1 to `RECENT_RUN_LIMIT`.

    Notes
    -----
    The backend keeps only the last runs, with printed text cut to a length
    limit. It keeps no workspace variables.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= RECENT_RUN_LIMIT:
        raise InvalidArgumentError(
            f"limit must be a whole number from 1 to {RECENT_RUN_LIMIT}. Got {limit!r}."
        )
    runs = await execute_rpc("terminal:get_recent_runs", {"limit": limit})
    images: list[ImageContent] = []
    for run in runs:
        if not include_plots:
            continue
        attached = 0
        problems: list[str] = []
        for plot_path in run.get("plot_paths", []):
            image, problem = _plot_image(plot_path)
            if image is not None:
                images.append(image)
                attached += 1
            elif problem:
                problems.append(f"{Path(plot_path).name}: {problem}")
        run["plot_images"] = {"attached": attached, "problems": problems}
    body = {"run_count": len(runs), "runs": runs}
    return [TextContent(type="text", text=json.dumps(body, indent=2, default=str)), *images]
