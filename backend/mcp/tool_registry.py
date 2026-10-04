"""Purpose: Central registry for all MCP tools.

This module is shared between the backend (for the LLM agent) and the
external MCP server process. REQ: AGENT-1.1, AGENT-3.1
"""

import inspect
import json
import logging
from pathlib import Path
from typing import Any

from mcp.types import TextContent, Tool

from astrometricslib.mcp.profile import current_profile, find_withheld_tools, load_manifest, refusal_message
from backend.services.infrastructure.agent_code_policy import check_agent_code
from backend.services.infrastructure.destructive_guard import destructive_rpc_reason

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Centralized model context protocol tool registration and dispatching.

    Attributes
    ----------
    tools : `dict`
        Tool name -> a dict with the callable and its `Tool` definition.
    withheld : `dict`
        Tools that `apply_profile` removed from ``tools``. Empty until a
        profile is applied.
    withheld_reasons : `dict`
        Tool name -> why `apply_profile` removed it. Used in the error a
        client gets when it calls the tool.
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.tools = {}
        self.withheld = {}
        self.withheld_reasons = {}

    def apply_profile(self, manifest_path: Path, profile: str | None = None) -> dict[str, str]:
        """Remove the tools a profile may not use.

        This is the same rule the library servers use (see
        `astrometricslib.mcp.profile`). A removed tool is not listed, and
        a call to it gets the "Unknown tool" error. The in-app agent uses
        the full registry, so only the MCP server entry point calls this.

        Parameters
        ----------
        manifest_path : `pathlib.Path`
            The server's ``tool_manifest.json``.
        profile : `str`, optional
            The profile name. Defaults to the one the
            ``ASTROMETRICS_MCP_PROFILE`` environment variable chooses.

        Returns
        -------
        withheld : `dict` [`str`, `str`]
            Tool name -> why it was removed.
        """
        profile = profile or current_profile()
        reasons = find_withheld_tools(list(self.tools), load_manifest(manifest_path), profile)
        for name in reasons:
            self.withheld[name] = self.tools.pop(name)
        self.withheld_reasons.update(reasons)
        logger.info(
            "MCP profile %r: serving %d tools, withholding %d.", profile, len(self.tools), len(reasons)
        )
        return reasons

    def register(self, name: str, description: str, input_schema: dict[str, Any] | None = None):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Build a decorator that registers a tool under `name`.

        Parameters
        ----------
        name : `str`
            Unique tool name used for dispatch.
        description : `str`
            Human-readable description surfaced to the LLM agent.
        input_schema : `dict`, optional
            JSON Schema describing the tool's arguments. If `None`
            (default), an empty object schema is used.

        Returns
        -------
        decorator : `Callable`
            Decorator that stores the wrapped function and its tool
            definition in the registry, then returns the function
            unchanged.
        """
        if input_schema is None:
            input_schema = {"type": "object", "properties": {}}

        def decorator(func):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            self.tools[name] = {
                "func": func,
                "tool_def": Tool(name=name, description=description, inputSchema=input_schema),
            }
            return func

        return decorator

    def get_tool_definitions(self) -> list[Tool]:
        """Return list of all Tool definitions in registry.

        Returns
        -------
        definitions : `list` [`Tool`]
            The `Tool` definition for every function currently
            registered.
        """
        return [t["tool_def"] for t in self.tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Execute a registered tool by name with the given arguments.

        Guards against malformed argument payloads from the LLM.

        Parameters
        ----------
        name : `str`
            Name of the registered tool to execute.
        arguments : `dict`
            Keyword arguments to pass to the tool function.

        Returns
        -------
        result : `list` [`TextContent`]
            The tool's result wrapped as MCP text content, or a
            single `TextContent` describing an error.
        """
        if name not in self.tools:
            return [TextContent(type="text", text=refusal_message(name, self.withheld_reasons.get(name)))]

        # REQ: SEC-1.5: Audit logging of all MCP tool invocations.
        import logging

        audit_logger = logging.getLogger("mcp.audit")
        arg_summary = list(arguments.keys()) if isinstance(arguments, dict) else type(arguments).__name__
        audit_logger.info(f"Tool invoked: {name} | Args: {arg_summary}")

        # Sanitize arguments: the LLM occasionally produces a string
        # instead of a dict.
        if not isinstance(arguments, dict):
            import logging

            logging.getLogger(__name__).warning(
                f"Tool '{name}' received non-dict arguments ({type(arguments).__name__}: {arguments!r}). "
                f"Defaulting to empty dict."
            )
            arguments = {}

        tool_info = self.tools[name]
        func = tool_info["func"]
        input_schema = tool_info["tool_def"].inputSchema

        # REQ: AGENT-3.1 - Strict argument validation
        try:
            import jsonschema

            jsonschema.validate(instance=arguments, schema=input_schema)
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "jsonschema library not found. Skipping strict argument validation."
            )
        except Exception as e:
            return [
                TextContent(
                    type="text", text=f"Error: Invalid arguments for tool '{name}'. Validation failed: {e}"
                )
            ]

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**arguments)
            else:
                result = func(**arguments)

            # If result is already List[TextContent], return it
            if isinstance(result, list) and len(result) > 0 and isinstance(result[0], TextContent):
                return result

            # Enrich error dictionaries with actionable remediation hints
            if isinstance(result, dict) and result.get("status") == "error":
                result = self._enrich_error_remediation(name, result)

            # Token budget safeguard: truncate oversized lists/payloads
            result_str = json.dumps(result, indent=2)
            max_bytes = 40000  # Cap output to ~10k tokens to prevent context blowout
            if len(result_str) > max_bytes:
                truncated_note = (
                    f"\n\n... [Output truncated: payload exceeded {max_bytes} bytes. "
                    "Use specific filtering arguments or limit queries to avoid context blowout.]"
                )
                result_str = result_str[:max_bytes] + truncated_note

            return [TextContent(type="text", text=result_str)]
        except Exception as e:
            remediation = self._get_generic_remediation(name, str(e))
            error_payload = {
                "status": "error",
                "tool": name,
                "error_type": type(e).__name__,
                "message": str(e),
                "remediation": remediation,
            }
            return [TextContent(type="text", text=json.dumps(error_payload, indent=2))]

    def _enrich_error_remediation(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        """Attach actionable recovery hints to tool error envelopes.

        Parameters
        ----------
        tool_name : `str`
            Name of the executing tool.
        result : `dict[str, Any]`
            The raw error result payload.

        Returns
        -------
        enriched : `dict[str, Any]`
            Error dictionary augmented with remediation tips.
        """
        msg = str(result.get("message", "")).lower()
        remediation: dict[str, Any] = {}

        if "target" in msg and "not found" in msg:
            remediation = {
                "suggestion": "Target names are case-sensitive. Verify exact catalog identifier.",
                "recommended_tool": "call_mcp_tool('astrometricslib-core', 'target_list', {})",
            }
        elif "disconnected" in msg or "not connected" in msg or "indi" in msg:
            remediation = {
                "suggestion": "Hardware driver is currently offline or disconnected.",
                "recommended_tool": "call_mcp_tool('wayfindinglib-core', 'observatory_connect', {})",
            }
        elif "filter" in msg:
            remediation = {
                "suggestion": "Requested filter wheel slot is unknown or unconfigured.",
                "recommended_tool": "call_mcp_tool('wayfindinglib-core', 'observatory_get_filter_names', {})",
            }
        elif "syntax" in msg or "unexpected" in msg:
            remediation = {
                "suggestion": "Check input arguments against parameter schema or query inspect_api.",
                "recommended_tool": (
                    "call_mcp_tool('astrometrics-backend', 'terminal_inspect_api', {'target': '...'})"
                ),
            }

        if remediation:
            result["remediation"] = remediation
        return result

    def _get_generic_remediation(self, tool_name: str, err_str: str) -> dict[str, Any]:
        """Return fallback recovery guidance for uncaught exceptions.

        Parameters
        ----------
        tool_name : `str`
            Name of the executing tool.
        err_str : `str`
            The exception string message.

        Returns
        -------
        remediation : `dict[str, Any]`
            Remediation dictionary with suggestion and inspection command.
        """
        return {
            "suggestion": (f"Tool '{tool_name}' encountered an unhandled exception: {err_str}"),
            "inspect_tool": (
                f"call_mcp_tool('astrometrics-backend', 'terminal_inspect_api', {{'target': '{tool_name}'}})"
            ),
        }


registry = ToolRegistry()


def get_container():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Fetch the singleton backend dependency container.

    Returns
    -------
    result : `object` or `None`
        The backend `container` instance, or `None` if
        `backend.container` cannot be imported.
    """
    try:
        from backend.container import container

        return container
    except ImportError:
        return None


def get_astrometrics():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Fetch a new Astrometrics high-level interface API instance.

    Returns
    -------
    result : `Astrometrics` or `None`
        A new `Astrometrics` astrometrics instance, or `None` if
        `astrometricslib` cannot be imported.
    """
    try:
        from astrometricslib import Astrometrics

        return Astrometrics()
    except ImportError:
        return None


async def execute_rpc(method: str, params: dict | None = None) -> dict:
    """Execute an RPC method through the in-process or HTTP backend.

    If the backend container is available, delegates directly to
    `RPCHandlerRegistry` to run the logic in-process. Otherwise,
    sends a JSON-RPC 2.0 POST request to the backend listener.


    Parameters
    ----------
    method : `str`
        Dotted RPC method name to execute.
    params : `dict`, optional
        Parameters to pass to the RPC method. If `None` (default),
        an empty dictionary is used.

    Returns
    -------
    result : `dict`
        On success, includes ``"status"`` and ``"data"``. On
        failure, includes ``"status"`` and ``"message"``.
    """
    if params is None:
        params = {}

    container_inst = get_container()
    if container_inst and container_inst.initialized:
        from backend.routers.rpc_router import rpc_registry
        from backend.services.rpc_protocol import serialize_rpc_result

        try:
            res = await rpc_registry.execute(method, params)
            serialized_result = serialize_rpc_result(res)
            return {"status": "success", "data": serialized_result}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        from backend.mcp.mcp_http import post_to_backend

        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": "mcp-proxy"}
        res = await post_to_backend("/api/rpc", payload)
        if not isinstance(res, dict):
            return {"status": "error", "message": f"Malformed response: {res!r}"}
        if res.get("status") == "error":
            return {"status": "error", "message": str(res.get("error", "Backend communication error"))}
        if "error" in res:
            error_val = res["error"]
            if isinstance(error_val, dict):
                msg = error_val.get("message", "Unknown RPC error")
            else:
                msg = str(error_val)
            return {"status": "error", "message": msg}
        return {"status": "success", "data": res.get("result")}


# ---------------------------------------------------------------------------
# Backend JSON-RPC & Diagnostics Tools
# ---------------------------------------------------------------------------


@registry.register(
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
        Standardized RPC response containing execution status, returned data,
        or error details with elapsed time in milliseconds.
    """
    import time

    refusal = destructive_rpc_reason(method)
    if method.startswith("terminal:"):
        refusal = "Terminal methods run code and are only available through electron_run_python."
    if refusal:
        return {"status": "error", "method": method, "elapsed_ms": 0.0, "data": None, "message": refusal}

    start_time = time.perf_counter()
    res = await execute_rpc(method, params or {})
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "status": res.get("status", "error"),
        "method": method,
        "elapsed_ms": elapsed_ms,
        "data": res.get("data"),
        "message": res.get("message"),
    }


@registry.register(
    "backend_health_check",
    "Check health and connectivity of the running Astrometrics backend server.",
    {"type": "object", "properties": {}},
)
async def tool_backend_health_check() -> dict[str, Any]:
    """Probe the backend API listener and report status.

    Returns
    -------
    result : `dict[str, Any]`
        Dictionary containing connection status, container state, and latency.
    """
    import time

    start_time = time.perf_counter()
    container_inst = get_container()
    container_active = bool(container_inst and container_inst.initialized)

    # Probe backend JSON-RPC via execute_rpc with lightweight target:list
    probe_res = await execute_rpc("target:list", {})
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    is_online = probe_res.get("status") == "success"

    return {
        "status": "healthy" if is_online else "degraded",
        "backend_online": is_online,
        "in_process_container": container_active,
        "latency_ms": elapsed_ms,
        "details": probe_res.get("message") if not is_online else "Backend responding normally",
    }


# ---------------------------------------------------------------------------
# AI Agent Desktop Control Tools
# ---------------------------------------------------------------------------


@registry.register(
    "ui_show_notification",
    "Post a native desktop toast notification to inform or alert the user.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Notification headline."},
            "body": {"type": "string", "description": "Notification details."},
            "urgency": {
                "type": "string",
                "enum": ["low", "normal", "critical"],
                "description": "Urgency level.",
                "default": "normal",
            },
        },
        "required": ["title", "body"],
    },
)
async def tool_ui_show_notification(title: str, body: str, urgency: str = "normal") -> dict[str, Any]:
    """Post native desktop notification via backend WebSocket/event dispatch.

    Parameters
    ----------
    title : `str`
        Notification title or summary.
    body : `str`
        Detailed notification message body.
    urgency : `str`, optional
        Notification urgency level ('low', 'normal', 'critical').

    Returns
    -------
    result : `dict[str, Any]`
        Dictionary containing dispatch status and notification details.
    """
    import logging

    logging.getLogger("mcp.ui").info("AI Notification: %s - %s (urgency=%s)", title, body, urgency)
    await execute_rpc(
        "events:broadcast",
        {
            "event": "notification",
            "data": {"title": title, "body": body, "urgency": urgency},
        },
    )
    return {"status": "success", "posted": True, "title": title}


@registry.register(
    "ui_pause_pipelines",
    "Pause active background processing and stacking pipelines (e.g. Siril).",
    {"type": "object", "properties": {}},
)
async def tool_ui_pause_pipelines() -> dict[str, Any]:  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
    """Freeze compute subprocesses via POSIX SIGSTOP.

    Returns
    -------
    result : `dict[str, Any]`
        Dictionary containing operation status and paused state.
    """
    import shutil
    import subprocess

    pkill_path = shutil.which("pkill") or "/usr/bin/pkill"
    try:
        subprocess.run([pkill_path, "-STOP", "-f", "siril-cli"], check=False)
        subprocess.run([pkill_path, "-STOP", "-f", "solve-field"], check=False)
        return {"status": "success", "paused": True}
    except Exception as err:
        return {"status": "error", "message": str(err)}


@registry.register(
    "ui_resume_pipelines",
    "Resume previously paused background processing and stacking pipelines.",
    {"type": "object", "properties": {}},
)
async def tool_ui_resume_pipelines() -> dict[str, Any]:  # ruff: ignore[unused-async] -- awaited by ToolRegistry.execute
    """Thaw compute subprocesses via POSIX SIGCONT.

    Returns
    -------
    result : `dict[str, Any]`
        Dictionary containing operation status and resumed state.
    """
    import shutil
    import subprocess

    pkill_path = shutil.which("pkill") or "/usr/bin/pkill"
    try:
        subprocess.run([pkill_path, "-CONT", "-f", "siril-cli"], check=False)
        subprocess.run([pkill_path, "-CONT", "-f", "solve-field"], check=False)
        return {"status": "success", "resumed": True}
    except Exception as err:
        return {"status": "error", "message": str(err)}


@registry.register(
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
    """
    refusal = check_agent_code(code)
    if refusal:
        return {"status": "error", "message": refusal}
    res = await execute_rpc("terminal:execute", {"code_str": code, "source": "agent"})
    if res.get("status") == "success" and "data" in res:
        return res["data"]
    return res


@registry.register(
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
    res = await execute_rpc("terminal:get_workspace", {})
    if res.get("status") == "success" and "data" in res:
        return {"status": "success", "variables": res["data"]}
    return res


@registry.register(
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
    res = await execute_rpc("terminal:execute", {"code_str": code, "source": "agent"})
    if res.get("status") == "success" and "data" in res:
        return res["data"].get("result", res["data"])
    return res


@registry.register(
    "ui_navigate_mode",
    "Navigate the UI workspace to a specific view (e.g. 'Planetarium', 'Image Processing') and target.",
    {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "Planetarium",
                    "Observatory",
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


@registry.register(
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


@registry.register(
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
    res = await execute_rpc("docs:get_topic", {"topic_id": topic_id})
    if res.get("status") == "success" and "data" in res:
        return res["data"]
    return res


@registry.register(
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
        res = await execute_rpc("ui:editor_set", {"code_content": code})
        if res.get("status") == "success" and "data" in res:
            return res["data"]
        return res

    res = await execute_rpc("ui:editor_get", {})
    if res.get("status") == "success" and "data" in res:
        return res["data"]
    return res


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

MOUNT_DEVICE_KEYWORDS = (
    "mount",
    "telescope",
    "gti",
    "adventurer",
    "eq",
    "lx200",
    "celestron",
    "ioptron",
    "skywatcher",
)
"""Words that mark an INDI device as the mount, in lower case."""

MOUNT_PROPERTIES = (
    "TELESCOPE_PIER_SIDE",
    "TELESCOPE_TRACK_STATE",
    "TELESCOPE_TRACK_MODE",
    "TELESCOPE_PARK",
    "EQUATORIAL_EOD_COORD",
    "HORIZONTAL_COORD",
    "TELESCOPE_SLEW_RATE",
    "HEMISPHERE",
)
"""The few mount properties worth reporting with the telescope status."""

GUIDING_SAMPLES_REPORTED = 20
"""How many of the newest guide samples the guiding section lists."""

GUIDING_SAMPLE_KEYS = ("time", "dra", "ddec", "pulse_ra", "pulse_dec", "snr", "rms_ra", "rms_dec")
"""The fields of a guide sample worth reporting. The backend repeats each in
camelCase for the web page, which a client does not need."""

MAXIMUM_INDI_PROPERTIES = 80
"""Most INDI properties one device answer lists."""

APP_CONTROL_ACTIONS = ("navigate", "notify")
"""What an AI may do in the app. Pausing and resuming pipelines freeze or
thaw Siril and the plate solver, so they are not offered."""


@registry.register(
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
        One key per section asked for, ``unavailable`` for what the app
        cannot say, or ``{"error": ...}`` for an unknown section.
    """
    sections = list(include) if include else list(DEFAULT_STATUS_SECTIONS)
    unknown = [name for name in sections if name not in APP_STATUS_SECTIONS]
    if unknown:
        return {"error": f"Unknown section(s) {unknown}. Choose from {list(APP_STATUS_SECTIONS)}."}

    answer: dict[str, Any] = {}
    unavailable: list[str] = []
    if "health" in sections:
        answer["health"] = await tool_backend_health_check()
    if "connections" in sections or "system" in sections:
        health = await execute_rpc("system:health", {})
        data = _unwrap(health)
        if not isinstance(data, dict) or "indi" not in data:
            note = health.get("message", "the backend did not answer")
            for section in ("connections", "system"):
                if section in sections:
                    answer[section] = {"error": note}
        else:
            if "connections" in sections:
                answer["connections"] = {"indi": data.get("indi")}
            if "system" in sections:
                answer["system"] = data.get("resources")
    if "active_jobs" in sections:
        answer["active_jobs"] = await _active_jobs()
    if "telescope" in sections:
        answer["telescope"] = await _telescope_status()
    if "guiding" in sections:
        answer["guiding"] = await _guiding_status()
    if "indi_devices" in sections:
        answer["indi_devices"] = await _indi_devices()
    if "indi_properties" in sections:
        answer["indi_properties"] = await _indi_properties(device, property_names)
    if "view" in sections:
        unavailable.append(
            "view: the backend does not keep track of which view the window shows, so the current view "
            "cannot be reported."
        )
    if unavailable:
        answer["unavailable"] = unavailable
    return answer


def _unwrap(response: Any) -> Any:
    """Strip the success and data layers around a backend answer.

    A backend call comes back wrapped by the HTTP proxy, by the RPC router
    and sometimes by the service itself, so the real answer can be two or
    three levels down.

    Parameters
    ----------
    response : `Any`
        What `execute_rpc` returned.

    Returns
    -------
    payload : `Any`
        The innermost value, or the response itself if it was an error.
    """
    value = response
    while isinstance(value, dict) and value.get("status") == "success" and "data" in value:
        value = value["data"]
    if isinstance(value, dict) and value.get("status") == "error":
        return {"error": value.get("message", "the backend reported an error")}
    return value


async def _telescope_status() -> dict[str, Any]:
    """Report the live telescope state the app shows in its header.

    The pointing, tracking, temperatures, focuser and filter come from the
    backend, which holds the hardware connection. Pier side is not in that
    status, so it is read from the mount's INDI properties.

    Returns
    -------
    telescope : `dict` [`str`, `Any`]
        The status without its guiding history, plus ``mount_indi`` with
        the pier side and tracking switches when the mount device is found.
    """
    status = _unwrap(await execute_rpc("telescope:status", {}))
    if not isinstance(status, dict) or "error" in status:
        return status if isinstance(status, dict) else {"error": "the backend did not answer"}
    answer = {
        key: value for key, value in status.items() if key not in ("guidingHistory", "alignmentAttempts")
    }
    devices = _unwrap(await execute_rpc("telescope:indi_devices", {}))
    mount = next(
        (name for name in (devices if isinstance(devices, list) else []) if _looks_like_a_mount(name)), None
    )
    if mount is not None:
        properties = await _indi_properties(mount, list(MOUNT_PROPERTIES))
        if isinstance(properties, dict) and "properties" in properties:
            answer["mount_indi"] = {"device": mount, **_describe_switches(properties["properties"])}
    return answer


def _looks_like_a_mount(device_name: str) -> bool:
    """Say whether an INDI device name looks like a telescope mount.

    Returns
    -------
    is_mount : `bool`
        `True` if the name contains a mount keyword.
    """
    lowered = device_name.lower()
    return any(keyword in lowered for keyword in MOUNT_DEVICE_KEYWORDS)


def _describe_switches(properties: dict[str, Any]) -> dict[str, Any]:
    """Boil mount properties down to the values a person reads.

    Parameters
    ----------
    properties : `dict` [`str`, `Any`]
        Property name to its INDI record.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        ``pier_side``, ``tracking``, ``parked`` and ``track_mode`` where
        the mount reports them, and the raw elements of the rest.
    """

    def on(name: str) -> list[str]:
        """List the switch elements that are on in one property.

        Returns
        -------
        names : `list` [`str`]
            The element names set to ``On``.
        """
        record = properties.get(name) or {}
        return [element for element, value in (record.get("elements") or {}).items() if value == "On"]

    summary: dict[str, Any] = {}
    if "TELESCOPE_PIER_SIDE" in properties:
        side = on("TELESCOPE_PIER_SIDE")
        summary["pier_side"] = side[0].removeprefix("PIER_") if side else None
    if "TELESCOPE_TRACK_STATE" in properties:
        summary["tracking"] = "TRACK_ON" in on("TELESCOPE_TRACK_STATE")
    if "TELESCOPE_PARK" in properties:
        summary["parked"] = "PARK" in on("TELESCOPE_PARK")
    if "TELESCOPE_TRACK_MODE" in properties:
        mode = on("TELESCOPE_TRACK_MODE")
        summary["track_mode"] = mode[0].removeprefix("TRACK_") if mode else None
    for name in ("EQUATORIAL_EOD_COORD", "HORIZONTAL_COORD"):
        if name in properties:
            summary[name.lower()] = (properties[name] or {}).get("elements")
    return summary


async def _guiding_status() -> dict[str, Any]:
    """Report the live guiding state and its newest samples.

    Returns
    -------
    guiding : `dict` [`str`, `Any`]
        ``is_guiding``, the running ``stats`` (RMS in arcseconds), and the
        newest `GUIDING_SAMPLES_REPORTED` samples.
    """
    status = _unwrap(await execute_rpc("guiding:status", {}))
    if not isinstance(status, dict) or "error" in status:
        return status if isinstance(status, dict) else {"error": "the backend did not answer"}
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
    devices : `list` [`str`] or `dict`
        The device names, or an error.
    """
    return _unwrap(await execute_rpc("telescope:indi_devices", {}))


async def _indi_properties(device: str | None, property_names: list[str] | None) -> dict[str, Any]:
    """Read the properties of one INDI device. Nothing is changed.

    Parameters
    ----------
    device : `str`, optional
        The device name.
    property_names : `list` [`str`], optional
        Only these properties.

    Returns
    -------
    answer : `dict` [`str`, `Any`]
        ``device``, ``properties`` (each with its label, state, type and
        elements) and how many exist, or ``{"error": ...}``.
    """
    if not device:
        return {"error": "Give a device name; list them with the indi_devices section."}
    properties = _unwrap(await execute_rpc("telescope:indi_properties", {"device_name": device}))
    if not isinstance(properties, dict) or "error" in properties:
        return properties if isinstance(properties, dict) else {"error": "the backend did not answer"}
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


async def _active_jobs() -> dict[str, Any]:
    """List the jobs recorded as running, from the job history.

    Returns
    -------
    jobs : `dict` [`str`, `Any`]
        The result of the job history query for active jobs, or an error.
    """
    import asyncio

    astrometrics = get_astrometrics()
    if astrometrics is None:
        return {"error": "astrometricslib is not available."}
    return await asyncio.to_thread(astrometrics.jobs.query, active_only=True, limit=20)


@registry.register(
    "app_controls",
    "Do what the person can do in the app: switch the view, or show a notification.",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(APP_CONTROL_ACTIONS),
                "description": "navigate: switch to a view (and select a target). notify: show a toast.",
            },
            "mode": {
                "type": "string",
                "enum": [
                    "Planetarium",
                    "Observatory",
                    "Image Processing",
                    "Observation Manager",
                    "Command Console",
                ],
                "description": "For navigate: the view to show.",
            },
            "target": {"type": "string", "description": "For navigate: a target to select."},
            "title": {"type": "string", "description": "For notify: the headline."},
            "body": {"type": "string", "description": "For notify: the details."},
            "urgency": {"type": "string", "enum": ["low", "normal", "critical"], "default": "normal"},
        },
        "required": ["action"],
    },
)
async def tool_app_controls(
    action: str,
    mode: str | None = None,
    target: str | None = None,
    title: str | None = None,
    body: str | None = None,
    urgency: str = "normal",
) -> dict[str, Any]:
    """Switch the app's view or show a notification.

    Parameters
    ----------
    action : `str`
        ``"navigate"`` or ``"notify"``.
    mode : `str`, optional
        The view, for ``navigate``.
    target : `str`, optional
        A target to select, for ``navigate``.
    title, body : `str`, optional
        The notification text, for ``notify``.
    urgency : `str`, optional
        The notification urgency.

    Returns
    -------
    result : `dict` [`str`, `Any`]
        What was dispatched, or ``{"error": ...}`` when the action is not
        one of `APP_CONTROL_ACTIONS` or a needed argument is missing.
    """
    if action == "navigate":
        if not mode:
            return {"error": "action='navigate' needs mode."}
        return await tool_ui_navigate_mode(mode, target)
    if action == "notify":
        if not title or not body:
            return {"error": "action='notify' needs title and body."}
        return await tool_ui_show_notification(title, body, urgency)
    return {"error": f"action must be one of {list(APP_CONTROL_ACTIONS)}. Pausing jobs is not offered."}
