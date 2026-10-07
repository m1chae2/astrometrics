"""Purpose: Report a failed MCP tool call in the shared error shape.

Description: An MCP tool call that fails must come back flagged as an error
(``isError`` in the MCP result), so the AI client knows the text is a failure
and not data. The registries build the content of that reply here. The
servers' ``call_tool`` handlers turn it into a result with the flag set.

The module imports only the MCP package at run time, so the gap server can
use it without loading astrometricslib.
"""

import json
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

if TYPE_CHECKING:
    from astrometricslib import ErrorInfo


class ToolErrorContent(list):
    """Content blocks that report a failed tool call.

    It behaves as a plain list of blocks. The server's ``call_tool`` handler
    checks for this type and sets ``isError`` on the MCP result.
    """


def error_content(info: ErrorInfo) -> ToolErrorContent:
    """Build the content of a failed tool call.

    The text is ``code: message``. When the error has details, a JSON copy of
    them follows on the next lines. The request id is in the message of an
    internal error, and in the details of any other error.

    Parameters
    ----------
    info : `ErrorInfo`
        The error to report.

    Returns
    -------
    content : `ToolErrorContent`
        One text block describing the error.
    """
    text = f"{info.code}: {info.message}"
    details = dict(info.details)
    if info.request_id and info.code != "internal":
        details.setdefault("requestId", info.request_id)
    if details:
        text += "\n" + json.dumps(details, indent=2, default=str)
    if info.retryable:
        text += "\n(Trying again may work.)"
    return ToolErrorContent([TextContent(type="text", text=text)])


def as_call_tool_result(content: Any) -> Any:
    """Turn a registry's reply into what an MCP ``call_tool`` handler returns.

    Parameters
    ----------
    content : `list` or `ToolErrorContent`
        The registry's reply.

    Returns
    -------
    result : `~mcp.types.CallToolResult` or `list`
        A result flagged as an error if `content` is a `ToolErrorContent`.
        Otherwise the list, unchanged.
    """
    if isinstance(content, ToolErrorContent):
        return CallToolResult(content=list(content), isError=True)
    return content
