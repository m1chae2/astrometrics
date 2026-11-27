"""Purpose: Report a failed MCP tool call in the shared error shape.

Description: An MCP tool call that fails must come back flagged as an error
(``isError`` in the MCP result), so the AI client knows the text is a failure
and not data. The registries build the content of that reply here. The
servers' ``call_tool`` handlers turn it into a result with the flag set.

Each error says how it is reported (``report``) and the level it is logged at
(``log_level``). `log_tool_failure` logs at that level, and `error_content`
tells the AI client when the error is a status (something offline or not
reachable) rather than a failure.

The module imports only the MCP package at run time, so the gap server can
use it without loading astrometricslib.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

if TYPE_CHECKING:
    from astrometricslib.exceptions import ErrorInfo

logger = logging.getLogger(__name__)

_REPORT_NOTES = {
    "status": "(This is a status, not a failure: a device, computer or service is offline or not reachable.)",
    "notify": "(The user must act on this.)",
}
"""The line added to a tool error for each ``report`` other than ``error``."""


class ToolErrorContent(list):
    """Content blocks that report a failed tool call.

    It behaves as a plain list of blocks. The server's ``call_tool`` handler
    checks for this type and sets ``isError`` on the MCP result.
    """


def error_content(info: ErrorInfo) -> ToolErrorContent:
    """Build the content of a failed tool call.

    The text is ``code: message``. When the error has details, a JSON copy of
    them follows on the next lines. The request id is in the message of an
    internal error, and in the details of any other error. An error whose
    ``report`` is ``status`` or ``notify`` ends with a line that says so.

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
    if info.report in _REPORT_NOTES:
        text += "\n" + _REPORT_NOTES[info.report]
    return ToolErrorContent([TextContent(type="text", text=text)])


def log_tool_failure(name: str, exc: BaseException, info: ErrorInfo) -> None:
    """Log a failed tool call once, at the level the error asks for.

    An unexpected exception (code ``internal``) is logged as an ``ERROR``
    with its traceback. Any other error is logged at its ``log_level``, with
    the traceback only at ``ERROR`` or above.

    Parameters
    ----------
    name : `str`
        The tool that failed.
    exc : `BaseException`
        The exception the tool raised.
    info : `ErrorInfo`
        The error as reported to the client.
    """
    if info.code == "internal":
        logger.error("Tool %s failed (%s)", name, info.code, exc_info=exc)
        return
    level = info.log_level_number
    logger.log(
        level,
        "Tool %s failed (%s): %s",
        name,
        info.code,
        info.message,
        exc_info=exc if level >= logging.ERROR else None,
    )


def as_call_tool_result(content: list[Any]) -> CallToolResult:
    """Turn a registry's reply into what an MCP ``call_tool`` handler returns.

    Parameters
    ----------
    content : `list` or `ToolErrorContent`
        The registry's reply.

    Returns
    -------
    result : `~mcp.types.CallToolResult`
        The content, flagged as an error if `content` is a
        `ToolErrorContent`.
    """
    return CallToolResult(content=list(content), is_error=isinstance(content, ToolErrorContent))
