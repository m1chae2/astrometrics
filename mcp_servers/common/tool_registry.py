"""Purpose: The one tool registry every Python MCP server uses.

Description: A registry holds a server's tools: each tool's name, its
description, the JSON schema of its arguments and the function that runs it.
When a client calls a tool, the registry:

1. refuses a tool the profile withholds, or one it does not know;
2. checks the arguments against the tool's schema;
3. for a server that reads files, refuses a path outside the library folders
   (the "sandbox");
4. runs the tool and turns its result into MCP content: a picture becomes an
   image, a model or record becomes JSON, and a long answer is cut short;
5. turns a failure into the shared error reply (see `tool_errors`).

The library servers (astrometricslib-core, wayfindinglib-core) and the backend
server all build one of these. Only the parts that differ are options: the
path sandbox, and a hint that tells the client how to recover from an error.
"""

import base64
import inspect
import io
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.types import ImageContent, TextContent, Tool

from astrometricslib import (
    ErrorInfo,
    get_configuration,
    is_path_inside,
    log_context,
    new_request_id,
    resolve_mounted_path,
    to_error_info,
)
from mcp_servers.common.profile import current_profile, find_withheld_tools, load_manifest, refusal_message
from mcp_servers.common.tool_errors import ToolErrorContent, error_content

logger = logging.getLogger(__name__)

audit_logger = logging.getLogger("mcp.audit")
"""Logs one line for every tool call, with the argument names only."""

MAXIMUM_REPLY_BYTES = 40000
"""Longest text answer sent to a client, about 10,000 tokens. A longer answer
is cut short with a note, so it cannot overflow the transport or the
client's context."""

FIGURE_DOTS_PER_INCH = 100
"""Resolution of a plot sent to a client. A 16 x 9 inch figure becomes a
1600 x 900 picture, large enough to read axis labels and small enough (a few
hundred kilobytes) for the MCP transport."""

EXPECTED_ERROR_CODES = frozenset({"invalid_argument", "not_found", "conflict", "permission_denied"})
"""Error codes caused by the call itself. They are logged as warnings, without
a traceback; any other failure is logged with its traceback."""

Remediation = Callable[[str, str], dict[str, Any]]
"""A function that takes a tool name and an error message and returns a hint
on how to recover."""


class ToolRegistry:
    """Hold a server's tools and run them for a client.

    Parameters
    ----------
    sandbox_paths : `bool`, optional
        If `True`, a call whose path arguments (names containing ``path`` or
        ending in ``_dir``) point outside the library, frames or stacks
        folder is refused. The library servers turn this on.
    remediation : `Callable` [[`str`, `str`], `dict`], optional
        Builds a recovery hint from a tool name and an error message. When
        given, a failed call's error details hold the tool name and the hint.

    Attributes
    ----------
    tools : `dict`
        Tool name -> a dict with ``"func"`` (the callable) and
        ``"tool_def"`` (its `mcp.types.Tool` definition).
    withheld : `dict`
        Tools that `apply_profile` removed from ``tools``, in the same form.
        Empty until a profile is applied.
    withheld_reasons : `dict` [`str`, `str`]
        Tool name -> why `apply_profile` removed it. A client that calls the
        tool gets this reason in the error.
    """

    def __init__(self, *, sandbox_paths: bool = False, remediation: Remediation | None = None) -> None:
        """Start with no registered or withheld tools."""
        self.sandbox_paths = sandbox_paths
        self.remediation = remediation
        self.tools: dict[str, dict[str, Any]] = {}
        self.withheld: dict[str, dict[str, Any]] = {}
        self.withheld_reasons: dict[str, str] = {}

    def register(
        self, name: str, description: str, input_schema: dict[str, Any] | None = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Build a decorator that registers a tool under `name`.

        Parameters
        ----------
        name : `str`
            Unique name under which the tool is registered.
        description : `str`
            What the tool does, shown to MCP clients.
        input_schema : `dict`, optional
            JSON schema of the tool's arguments. If `None` (default), the
            tool takes no arguments.

        Returns
        -------
        decorator : `Callable`
            A decorator that registers the function it wraps and returns it
            unchanged.
        """
        schema = input_schema if input_schema is not None else {"type": "object", "properties": {}}

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            """Store `func` as the tool's implementation.

            Returns
            -------
            func : `Callable`
                The same function.
            """
            self.tools[name] = {
                "func": func,
                "tool_def": Tool(name=name, description=description, inputSchema=schema),
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
        """List the definitions of the tools the registry serves.

        Returns
        -------
        tool_definitions : `list` [`mcp.types.Tool`]
            One definition per registered tool.
        """
        return [entry["tool_def"] for entry in self.tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> list[Any]:
        """Run a registered tool by name with the client's arguments.

        Parameters
        ----------
        name : `str`
            Name of the registered tool to run.
        arguments : `dict`
            Arguments for the tool. Anything that is not a dict counts as no
            arguments.

        Returns
        -------
        content : `list`
            The tool's result as MCP content, or a `ToolErrorContent` that
            describes why the call failed.
        """
        if name not in self.tools:
            return error_content(
                ErrorInfo(
                    code="permission_denied" if name in self.withheld_reasons else "not_found",
                    message=refusal_message(name, self.withheld_reasons.get(name)),
                )
            )
        argument_names = list(arguments) if isinstance(arguments, dict) else type(arguments).__name__
        audit_logger.info("Tool invoked: %s | Args: %s", name, argument_names)
        if not isinstance(arguments, dict):
            logger.warning("Tool %r got arguments that are not a dict; using none.", name)
            arguments = {}

        request_id = new_request_id()
        with log_context(request_id=request_id, method=name):
            refusal = self._check_arguments(name, arguments, request_id)
            if refusal is not None:
                return refusal
            return await self._run_tool(name, arguments, request_id)

    def _check_arguments(
        self, name: str, arguments: dict[str, Any], request_id: str
    ) -> ToolErrorContent | None:
        """Check a call's arguments against the schema and the path sandbox.

        Parameters
        ----------
        name : `str`
            The tool being called.
        arguments : `dict`
            The call's arguments.
        request_id : `str`
            Id of the call, for the error reply.

        Returns
        -------
        refusal : `ToolErrorContent` or `None`
            The error to send back, or `None` if the call may run.
        """
        try:
            import jsonschema
        except ImportError:
            # Checking the arguments is optional; it needs the jsonschema
            # package, which is not always installed.
            logger.warning("jsonschema is not installed, so tool arguments are not checked.")
        else:
            try:
                jsonschema.validate(instance=arguments, schema=self.tools[name]["tool_def"].inputSchema)
            except (jsonschema.ValidationError, jsonschema.SchemaError) as error:
                return error_content(
                    ErrorInfo(
                        code="invalid_argument",
                        message=f"Invalid arguments for tool '{name}'. Validation failed: {error}",
                        request_id=request_id,
                    )
                )
        if self.sandbox_paths:
            return _check_path_sandbox(name, arguments, request_id)
        return None

    async def _run_tool(self, name: str, arguments: dict[str, Any], request_id: str) -> list[Any]:
        """Run the tool and turn its result, or its failure, into MCP content.

        Parameters
        ----------
        name : `str`
            Name of the registered tool to run.
        arguments : `dict`
            Arguments to pass to the tool.
        request_id : `str`
            Id of the call. It tags the log lines and the error reply.

        Returns
        -------
        content : `list`
            The tool's result, or a `ToolErrorContent` if it failed.
        """
        func = self.tools[name]["func"]
        try:
            result = await func(**arguments) if inspect.iscoroutinefunction(func) else func(**arguments)
            return result_content(result)
        except Exception as exc:  # every failure becomes an error reply, never a crash
            info = to_error_info(exc, request_id)
            if info.code in EXPECTED_ERROR_CODES:
                logger.warning("Tool %s failed (%s): %s", name, info.code, info.message)
            else:
                logger.exception("Tool %s failed (%s)", name, info.code)
            if self.remediation is not None:
                info.details["tool"] = name
                info.details["remediation"] = self.remediation(name, info.message)
            return error_content(info)


def _check_path_sandbox(name: str, arguments: dict[str, Any], request_id: str) -> ToolErrorContent | None:
    """Refuse a call whose path arguments point outside the library folders.

    The check refuses the call if it cannot decide: a tool that takes a path
    must never run on a path the check could not verify.

    Parameters
    ----------
    name : `str`
        The tool being called.
    arguments : `dict`
        The call's arguments. Those whose name contains ``path`` or ends in
        ``_dir`` are checked.
    request_id : `str`
        Id of the call, for the error reply.

    Returns
    -------
    refusal : `ToolErrorContent` or `None`
        The error to send back, or `None` if every path is allowed.
    """
    paths = [
        value
        for key, value in arguments.items()
        if ("path" in key.lower() or key.lower().endswith("_dir")) and isinstance(value, str)
    ]
    if not paths:
        return None
    try:
        config = get_configuration()
        sandbox_roots = [
            os.path.realpath(str(config.get_library_path())),
            os.path.realpath(str(config.get_frames_path())),
            # The pipeline's output, which can be on another disk.
            os.path.realpath(str(config.get_stacks_path())),
        ]
        outside = [
            value
            for value in paths
            if not any(
                is_path_inside(root, os.path.realpath(resolve_mounted_path(value))) for root in sandbox_roots
            )
        ]
    except Exception as exc:  # the check must fail closed, whatever went wrong
        logger.exception("Sandbox path validation failed for tool %r; refusing the call.", name)
        return error_content(
            ErrorInfo(
                code="permission_denied",
                message=f"Security violation. The path arguments of '{name}' could not be checked: {exc!s}",
                request_id=request_id,
            )
        )
    if outside:
        return error_content(
            ErrorInfo(
                code="permission_denied",
                message=(
                    f"Security violation. Access denied: path '{outside[0]}' is outside the allowed "
                    "sandbox directories."
                ),
                request_id=request_id,
            )
        )
    return None


def result_content(result: Any) -> list[Any]:
    """Turn a tool's result into MCP content.

    Parameters
    ----------
    result : `Any`
        What the tool returned.

    Returns
    -------
    content : `list`
        Content a tool already built is passed on. A picture (an object with
        ``png_bytes`` and ``description``) and a matplotlib figure become an
        image and a short text. Anything else becomes indented JSON text, cut
        at `MAXIMUM_REPLY_BYTES`.
    """
    if isinstance(result, list) and result and isinstance(result[0], (TextContent, ImageContent)):
        return result
    if hasattr(result, "png_bytes") and hasattr(result, "description"):
        # A picture goes back as a real image, not as base64 text.
        return [
            ImageContent(
                type="image", data=base64.b64encode(result.png_bytes).decode("ascii"), mimeType="image/png"
            ),
            TextContent(type="text", text=json.dumps(_picture_description(result), indent=2, default=str)),
        ]
    if callable(getattr(result, "savefig", None)):
        return _figure_as_image(result)
    text = json.dumps(_serialize_result(result), indent=2, default=str)
    if len(text) > MAXIMUM_REPLY_BYTES:
        text = text[:MAXIMUM_REPLY_BYTES] + (
            f"\n\n... [Output truncated: payload exceeded {MAXIMUM_REPLY_BYTES} bytes. "
            "Use specific filtering arguments or limit queries to avoid context blowout.]"
        )
    return [TextContent(type="text", text=text)]


def _picture_description(picture: Any) -> dict[str, Any]:
    """Describe a returned picture in the text that goes beside it.

    Parameters
    ----------
    picture : `ViewableImage`
        The picture.

    Returns
    -------
    description : `dict` [`str`, `Any`]
        Its description, with the automatic stretch it was drawn with when
        it has one.
    """
    stretch = getattr(picture, "stretch_parameters", None)
    if stretch is None:
        return picture.description
    return {**picture.description, "stretch_parameters": stretch.model_dump()}


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


def _serialize_result(value: Any) -> Any:
    """Turn models and records into plain JSON-ready values, recursively.

    Parameters
    ----------
    value : `Any`
        Value to convert.

    Returns
    -------
    serialized : `Any`
        The value as plain dicts, lists and scalars.
    """
    if callable(getattr(value, "serialize", None)):
        return value.serialize()
    if callable(getattr(value, "model_dump", None)):
        return value.model_dump()
    if callable(getattr(value, "to_dict", None)):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize_result(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_result(item) for key, item in value.items()}
    return value
