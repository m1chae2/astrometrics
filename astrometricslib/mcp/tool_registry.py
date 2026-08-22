"""Tool registry and dynamic reflection for the Core Library MCP Server.

Provides static registration and automatic type-hint parsing for
offline astrometrics tools.
"""

import inspect
import json
import logging
import os
from typing import Any

from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Centralized MCP tool registration and dispatching.

    Attributes
    ----------
    tools : `dict`
        Mapping of registered tool name to a dict with keys
        ``"func"`` (the registered callable) and ``"tool_def"`` (the
        `mcp.types.Tool` definition).
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the empty dictionary of registered tools."""
        self.tools = {}

    def register(self, name: str, description: str, input_schema: dict[str, Any] | None = None):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Build a decorator that registers a tool under `name`.

        Parameters
        ----------
        name : `str`
            Unique name under which the tool is registered.
        description : `str`
            Human-readable description of the tool, exposed to MCP
            clients.
        input_schema : `dict`, optional
            JSON schema describing the tool's expected arguments. If
            `None` (default), an empty object schema is used.

        Returns
        -------
        decorator : `callable`
            A decorator that registers the wrapped function as the
            tool's implementation and returns it unchanged.
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
        """Return the list of all Tool definitions currently registered.

        Returns
        -------
        tool_definitions : `list` [`mcp.types.Tool`]
            All registered tool definitions.
        """
        return [t["tool_def"] for t in self.tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Execute a registered tool by name with the given arguments.

        Provides type validation and path sandboxing before
        dispatching to the registered callable.

        Parameters
        ----------
        name : `str`
            Name of the registered tool to execute.
        arguments : `dict`
            Arguments to pass to the tool's implementation.

        Returns
        -------
        content : `list` [`mcp.types.TextContent`]
            The tool's result (or an error message) wrapped as MCP
            text content.
        """  # ruff: ignore[docstring-missing-exception] -- PermissionError
        # is raised and caught within this same function (the path
        # sandboxing block below); it never propagates to the caller.
        if name not in self.tools:
            return [TextContent(type="text", text=f"Error: Unknown tool {name}")]

        if not isinstance(arguments, dict):
            arguments = {}

        tool_info = self.tools[name]
        func = tool_info["func"]
        input_schema = tool_info["tool_def"].inputSchema

        try:
            import jsonschema

            jsonschema.validate(instance=arguments, schema=input_schema)
        except ImportError:
            pass
        except Exception as e:
            return [
                TextContent(
                    type="text", text=f"Error: Invalid arguments for tool '{name}'. Validation failed: {e}"
                )
            ]

        # MCP Path Sandboxing validation
        try:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()
            lib_path = os.path.realpath(str(config.get_library_path()))
            frm_path = os.path.realpath(str(config.get_frames_path()))

            for key, val in list(arguments.items()):
                if "path" in key.lower() and isinstance(val, str):
                    real_val = os.path.realpath(val)
                    # Allow validation if it starts with either of
                    # our valid sandbox roots
                    is_under_lib = os.path.commonpath([lib_path, real_val]) == lib_path
                    is_under_frm = os.path.commonpath([frm_path, real_val]) == frm_path
                    if not (is_under_lib or is_under_frm):
                        raise PermissionError(
                            f"Access denied: path '{val}' is outside the allowed sandbox directories."
                        )
        except PermissionError as pe:
            return [TextContent(type="text", text=f"Error: Security violation. {pe!s}")]
        except Exception as exc:
            logger.debug("Sandbox path validation skipped for an unresolvable argument: %s", exc)

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**arguments)
            else:
                result = func(**arguments)

            if isinstance(result, list) and len(result) > 0 and isinstance(result[0], TextContent):
                return result

            serialized = _serialize_result(result)
            return [TextContent(type="text", text=json.dumps(serialized, indent=2, default=str))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error during tool execution: {e!s}")]


def _serialize_result(val: Any) -> Any:
    """Recursively serialize domain models to JSON-safe structures.

    Parameters
    ----------
    val : `Any`
        Value to serialize.

    Returns
    -------
    serialized : `Any`
        JSON-safe primitive or dict structure.
    """
    if hasattr(val, "serialize") and callable(val.serialize):
        return val.serialize()
    if hasattr(val, "model_dump") and callable(val.model_dump):
        return val.model_dump()
    if hasattr(val, "to_dict") and callable(val.to_dict):
        return val.to_dict()
    if isinstance(val, list):
        return [_serialize_result(item) for item in val]
    if isinstance(val, dict):
        return {k: _serialize_result(v) for k, v in val.items()}
    return val


registry = ToolRegistry()


def get_astrometrics():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Fetch the high-level interface API from the library.

    Returns
    -------
    astrometrics : `astrometricslib.Astrometrics` or `None`
        the high-level interface instance, or `None` if the library
        could not be imported.
    """
    try:
        from astrometricslib import Astrometrics

        return Astrometrics()
    except ImportError:
        return None


def register_astrometrics_reflected_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Dynamically register public high-level interface methods as tools."""
    astrometrics = get_astrometrics()
    if not astrometrics:
        return

    from astrometricslib.mcp.reflection import register_astrometrics_tools

    branch_mapping = {
        "": "target",
        "targets": "target",
        "stars": "star",
        "moving_objects": "moving_object",
        "processing": "processing",
        "visualization": "visualization",
    }
    register_astrometrics_tools(registry, astrometrics, branch_mapping)


# Runs at import time: constructing the `Astrometrics` astrometrics inside
# `get_astrometrics()` triggers `disk_interface.verify_and_upgrade_database`,
# so `import astrometricslib.mcp` performs a database migration check
# as a side effect. Deliberate -- the MCP server always needs the
# reflected tool set built before it can serve requests, and every
# caller of this module (the `mcp` CLI entry point, `backend`,
# `wayfindinglib.mcp`) wants that migration to have already run.
register_astrometrics_reflected_tools()
