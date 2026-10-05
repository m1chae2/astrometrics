"""Tool registry and dynamic reflection for the Core Library MCP Server.

Provides static registration and automatic type-hint parsing for
offline astrometrics tools.
"""

import base64
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.types import ImageContent, TextContent, Tool

from astrometricslib.foundation.errors import ErrorInfo, to_error_info
from astrometricslib.foundation.logging import log_context, new_request_id
from astrometricslib.foundation.paths import is_path_inside, resolve_mounted_path
from astrometricslib.mcp.profile import current_profile, find_withheld_tools, load_manifest, refusal_message
from astrometricslib.mcp.tool_errors import error_content

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Centralized MCP tool registration and dispatching.

    Attributes
    ----------
    tools : `dict`
        Mapping of registered tool name to a dict with keys
        ``"func"`` (the registered callable) and ``"tool_def"`` (the
        `mcp.types.Tool` definition).
    withheld : `dict`
        Tools that `apply_profile` removed from ``tools``, in the same
        form. Empty until a profile is applied.
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        """Start with no registered or withheld tools."""
        self.tools = {}
        self.withheld = {}
        self.withheld_reasons = {}

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

    def apply_profile(self, manifest_path: Path, profile: str | None = None) -> dict[str, str]:
        """Remove the tools a profile may not use.

        A removed tool is no longer listed, and a call to it gets the
        "Unknown tool" error. A tool that is missing from the manifest is
        removed, so a new tool stays hidden until it is reviewed.

        Parameters
        ----------
        manifest_path : `pathlib.Path`
            The server's ``tool_manifest.json``.
        profile : `str`, optional
            The profile name. Defaults to the one chosen by the
            ``ASTROMETRICS_MCP_PROFILE`` environment variable, or
            ``"investigator"``.

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
        """
        # is raised and caught within this same function (the path
        # sandboxing block below); it never propagates to the caller.
        if name not in self.tools:
            return error_content(
                ErrorInfo(
                    code="permission_denied" if name in self.withheld_reasons else "not_found",
                    message=refusal_message(name, self.withheld_reasons.get(name)),
                )
            )

        request_id = new_request_id()
        with log_context(request_id=request_id, method=name):
            return await self._run_tool(name, arguments, request_id)

    async def _run_tool(self, name: str, arguments: dict, request_id: str) -> list[Any]:
        """Validate a call's arguments, check its paths, and run the tool.

        Parameters
        ----------
        name : `str`
            Name of the registered tool to execute.
        arguments : `dict`
            Arguments to pass to the tool's implementation.
        request_id : `str`
            Id of the call. It tags the log lines and the error reply.

        Returns
        -------
        content : `list`
            The tool's result, or a `ToolErrorContent` if it failed.
        """  # ruff: ignore[docstring-missing-exception] -- PermissionError
        # is raised and caught within this same function (the path
        # sandboxing block below); it never propagates to the caller.
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
            return error_content(
                ErrorInfo(
                    code="invalid_argument",
                    message=f"Invalid arguments for tool '{name}'. Validation failed: {e}",
                    request_id=request_id,
                )
            )

        # MCP Path Sandboxing validation. The check refuses the call if it
        # cannot decide: a tool that takes a path must never run on a path
        # the check could not verify.
        path_arguments = {
            key: val
            for key, val in arguments.items()
            if ("path" in key.lower() or key.lower().endswith("_dir")) and isinstance(val, str)
        }
        if path_arguments:
            try:
                from astrometricslib.foundation.config import get_configuration

                config = get_configuration()
                sandbox_roots = [
                    os.path.realpath(str(config.get_library_path())),
                    os.path.realpath(str(config.get_frames_path())),
                    # The pipeline's output, which can be on another disk.
                    os.path.realpath(str(config.get_stacks_path())),
                ]

                for val in path_arguments.values():
                    real_val = os.path.realpath(resolve_mounted_path(val))
                    if not any(is_path_inside(root, real_val) for root in sandbox_roots):
                        raise PermissionError(
                            f"Access denied: path '{val}' is outside the allowed sandbox directories."
                        )
            except PermissionError as pe:
                return error_content(
                    ErrorInfo(
                        code="permission_denied", message=f"Security violation. {pe!s}", request_id=request_id
                    )
                )
            except Exception as exc:
                logger.exception("Sandbox path validation failed for tool %r; refusing the call.", name)
                return error_content(
                    ErrorInfo(
                        code="permission_denied",
                        message=(
                            f"Security violation. The path arguments of '{name}' "
                            f"could not be checked: {exc!s}"
                        ),
                        request_id=request_id,
                    )
                )

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**arguments)
            else:
                result = func(**arguments)

            if isinstance(result, list) and len(result) > 0 and isinstance(result[0], TextContent):
                return result

            if hasattr(result, "png_bytes") and hasattr(result, "description"):
                # A picture goes back as a real image, not as base64 text.
                return [
                    ImageContent(
                        type="image",
                        data=base64.b64encode(result.png_bytes).decode("ascii"),
                        mimeType="image/png",
                    ),
                    TextContent(type="text", text=json.dumps(result.description, indent=2, default=str)),
                ]

            if hasattr(result, "savefig") and callable(result.savefig):
                return _figure_as_image(result)

            serialized = _serialize_result(result)
            result_str = json.dumps(serialized, indent=2, default=str)
            max_bytes = 40000  # Cap output to ~10k tokens to prevent transport and context blowout
            if len(result_str) > max_bytes:
                truncated_note = (
                    f"\n\n... [Output truncated: payload exceeded {max_bytes} bytes. "
                    "Use specific filtering arguments or limit queries to avoid context blowout.]"
                )
                result_str = result_str[:max_bytes] + truncated_note
            return [TextContent(type="text", text=result_str)]
        except Exception as exc:
            info = to_error_info(exc, request_id)
            if info.code in {"invalid_argument", "not_found", "conflict", "permission_denied"}:
                logger.warning("Tool %s failed (%s): %s", name, info.code, info.message)
            else:
                logger.exception("Tool %s failed (%s)", name, info.code)
            return error_content(info)


FIGURE_DOTS_PER_INCH = 100
"""Resolution of a plot sent to a client. A 16 x 9 inch figure becomes a
1600 x 900 picture, large enough to read axis labels and small enough (a few
hundred kilobytes) for the MCP transport."""


def _figure_as_image(figure: Any) -> list[ImageContent | TextContent]:
    """Turn a matplotlib figure into an image reply and free the figure.

    A plot tool returns a figure object. Sent as text it reads
    ``Figure(1600x900)``, which tells a client nothing, so it is drawn to a
    PNG here instead. The figure is closed afterwards so repeated calls do
    not use up memory.

    Parameters
    ----------
    figure : `matplotlib.figure.Figure`
        The figure to draw.

    Returns
    -------
    content : `list` [`ImageContent` or `TextContent`]
        The picture, and a short description of its size and titles.
    """
    import io

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=FIGURE_DOTS_PER_INCH, bbox_inches="tight")
    width_inches, height_inches = figure.get_size_inches()
    titles = [axis.get_title() for axis in figure.get_axes() if axis.get_title()]
    description = {
        "size_pixels": [
            round(width_inches * FIGURE_DOTS_PER_INCH),
            round(height_inches * FIGURE_DOTS_PER_INCH),
        ],
        "panel_titles": titles[:12],
    }
    try:
        import matplotlib.pyplot as plt

        plt.close(figure)
    except ImportError:
        pass
    return [
        ImageContent(
            type="image", data=base64.b64encode(buffer.getvalue()).decode("ascii"), mimeType="image/png"
        ),
        TextContent(type="text", text=json.dumps(description, indent=2)),
    ]


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
        "targets": "target",
        "stars": "star",
        "processing": "processing",
        "processing.diagnostics": "diagnostics",
        "processing.calibration": "calibration",
        "visualization": "visualization",
        "jobs": "jobs",
    }
    register_astrometrics_tools(registry, astrometrics, branch_mapping)


# Runs at import time, so `import astrometricslib.mcp` builds the
# reflected tool set as a side effect. Deliberate -- the MCP server
# always needs that tool set built before it can serve requests, and
# every caller of this module (the `mcp` CLI entry point, `backend`,
# `wayfindinglib.mcp`) wants it to have already run.
register_astrometrics_reflected_tools()
