"""Purpose: Central registry for all MCP tools.

This module is shared between the backend (for the LLM agent) and the
external MCP server process. REQ: AGENT-1.1, AGENT-3.1
"""

import inspect
import json
from typing import Any

from mcp.types import TextContent, Tool


class ToolRegistry:
    """Centralized model context protocol tool registration and dispatching."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.tools = {}

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
            return [TextContent(type="text", text=f"Error: Unknown tool {name}")]

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
        from backend.routers.rpc_router import rpc_registry, serialize_rpc_result

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
    res = await execute_rpc("terminal:execute", {"code_str": code})
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
    res = await execute_rpc("terminal:execute", {"code_str": code})
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
