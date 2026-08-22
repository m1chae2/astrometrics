"""Purpose: Central registry for all MCP tools.

This module is shared between the backend (for the LLM agent) and the
external MCP server process. REQ: AGENT-1.1, AGENT-3.1
"""

import inspect
import json
import re
import typing
from typing import Any, Union

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

            # Otherwise, wrap it in TextContent
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error during tool execution: {e!s}")]


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
        if "error" in res:
            return {"status": "error", "message": res["error"].get("message", "Unknown RPC error")}
        return res.get("result", {"status": "error", "message": "Malformed RPC response"})


# --- Dynamic Tool Reflection ---


def parse_docstring_params(doc: str) -> dict[str, str]:
    """Parse Google-style docstring parameters.

    Parameters
    ----------
    doc : `str`
        Docstring text to parse.

    Returns
    -------
    result : `dict`
        Mapping of parameter name to its parsed description.
    """
    if not doc:
        return {}
    param_desc = {}
    lines = doc.splitlines()
    in_params = False
    for line in lines:
        if "Parameters" in line or "Args:" in line:
            in_params = True
            continue
        if in_params:
            line.strip().startswith("###") or (line.strip() == "" and param_desc)
            match = re.match(r"\s*-\s*([a-zA-Z0-9_]+)\s*:\s*(.*)", line)
            if match:
                param_desc[match.group(1)] = match.group(2).strip()
            else:
                match_args = re.match(r"\s*([a-zA-Z0-9_]+)\s*:\s*(.*)", line)
                if match_args:
                    param_desc[match_args.group(1)] = match_args.group(2).strip()
    return param_desc


def get_json_type(py_type) -> str:  # ruff: ignore[missing-type-function-argument]
    """Map a Python PEP-484 type to a JSON Schema type name.

    Parameters
    ----------
    py_type : `type`
        Python type, or typing construct (e.g. `Union`), to map.

    Returns
    -------
    result : `str`
        JSON Schema type name. Defaults to ``"string"`` for
        unrecognized types.
    """
    if py_type is str:
        return "string"
    elif py_type is int:
        return "integer"
    elif py_type is float:
        return "number"
    elif py_type is bool:
        return "boolean"
    elif py_type in (list, list):
        return "array"
    elif py_type in (dict, dict):
        return "object"

    origin = typing.get_origin(py_type)
    if origin is Union:
        args = typing.get_args(py_type)
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return get_json_type(non_none[0])

    return "string"


def generate_tool_schema(func) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Generate a JSON Schema for a function's parameters.

    Derives property types from `func`'s type hints and parameter
    descriptions from its docstring.

    Parameters
    ----------
    func : `Callable`
        Function to introspect.

    Returns
    -------
    result : `dict`
        JSON Schema object with ``"type"``, ``"properties"``, and
        ``"required"`` keys.
    """
    sig = inspect.signature(func)
    try:
        type_hints = typing.get_type_hints(func)
    except Exception:
        type_hints = {}
    doc = inspect.getdoc(func) or ""
    param_descs = parse_docstring_params(doc)

    properties = {}
    required = []

    for name, param in sig.parameters.items():
        if name in ("self", "args", "kwargs"):
            continue

        py_type = type_hints.get(name, str)
        js_type = get_json_type(py_type)

        properties[name] = {"type": js_type, "description": param_descs.get(name, f"Parameter {name}")}

        if param.default == inspect.Parameter.empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def make_reflected_executor(branch_name: str, sub_api_name: str, method_name: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Create an async execution wrapper for a reflected tool.

    Parameters
    ----------
    branch_name : `str`
        Name of the high-level interface branch (e.g. ``"observatory"``).
    sub_api_name : `str`
        Name of the sub-API within the branch.
    method_name : `str`
        Name of the method to invoke via RPC.

    Returns
    -------
    result : `Callable`
        Async function that executes the RPC and returns its data,
        raising `RuntimeError` on failure.
    """

    async def execute_reflected(**kwargs):  # ruff: ignore[missing-type-kwargs, missing-return-type-private-function]
        method_key = f"{branch_name}:{sub_api_name}:{method_name}"
        res = await execute_rpc(method_key, kwargs)
        if res.get("status") == "success":
            return res.get("data")
        raise RuntimeError(res.get("message", f"Execution failed for {method_key}"))

    return execute_reflected


def register_reflected_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Dynamically registers all astrometrics methods as reflected tools."""
    astrometrics = get_astrometrics()
    if not astrometrics:
        return

    mapping = {
        "observatory.slew_to_target": "slew_to_target",
        "observatory.set_filter": "set_filter",
        "observatory.get_telescope_status": "get_telescope_status",
        "observatory.park": "park_telescope",
        "observatory.unpark": "unpark_telescope",
        "observatory.set_tracking": "set_tracking",
        "observatory.focus_move": "focus_move",
        "observatory.get_focuser_position": "get_focuser_position",
        "observatory.connect": "control_connect",
        "astronomy.list": "astronomy_list",
        "system.save": "system_save",
        "targets.list": "list_targets",
    }

    for branch_name in ["observatory", "observation", "astronomy", "analysis", "system", "targets"]:
        branch = getattr(astrometrics, branch_name, None)
        if not branch and branch_name == "targets":

            class DummyTargetsBranch:
                """Fallback target management branch for reflection."""

                def list(self) -> list[Any]:
                    """Retrieve the list of all registered targets.

                    Returns
                    -------
                    targets : `list`
                        All target indices currently registered.
                    """
                    return astrometrics.targets.list()

                def get(self, target_id: str) -> Any:
                    """Retrieve a specific target by its ID.

                    Returns
                    -------
                    target : `Any`
                        The target matching `target_id`.
                    """
                    return astrometrics.targets.get(target_id)

                def create(self, target_id: str) -> Any:
                    """Create a new target index.

                    Returns
                    -------
                    target : `Any`
                        The newly created target index.
                    """
                    return astrometrics.targets.create(target_id)

                def delete(self, target_id: str) -> bool:
                    """Delete a target index.

                    Returns
                    -------
                    deleted : `bool`
                        `True` if the target index was deleted.
                    """
                    return astrometrics.targets.delete(target_id)

                def save(self) -> None:
                    """Save all target indices."""
                    astrometrics.targets.save()

            branch = DummyTargetsBranch()

        if not branch:
            continue

        for method_name in dir(branch):
            if method_name.startswith("_"):
                continue
            method = getattr(branch, method_name)
            if not callable(method):
                continue

            dotted_name = f"{branch_name}.{method_name}"
            raw_tool_name = mapping.get(dotted_name)

            if not raw_tool_name:
                raw_tool_name = f"{branch_name}_{method_name}"

            tool_name = f"backend_{raw_tool_name}"

            if tool_name in registry.tools and dotted_name not in mapping:
                continue

            doc = inspect.getdoc(method) or ""
            description = doc.split("\n\n")[0] if "\n\n" in doc else doc
            if not description:
                description = f"Dynamically reflected tool {tool_name}"
            schema = generate_tool_schema(method)

            # Helper executor that routes to the correct flat method
            async def execute_reflected(b=branch_name, m=method_name, **kwargs):  # ruff: ignore[missing-type-function-argument, missing-type-kwargs, missing-return-type-private-function]
                method_key = f"{b}:{m}"
                res = await execute_rpc(method_key, kwargs)
                if res.get("status") == "success":
                    return res.get("data")
                raise RuntimeError(res.get("message", f"Execution failed for {method_key}"))

            registry.register(tool_name, description, schema)(execute_reflected)


# Import static tools first

# Run dynamic reflection registration
register_reflected_tools()
