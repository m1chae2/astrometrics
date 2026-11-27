"""Tests for the MCP tool error reply and its log line.

A tool error says when it is a status (something offline) rather than a
failure, and is logged at the level the error asks for.
"""

import logging

import pytest

from astrometricslib.exceptions import ExternalServiceError, NotFoundError, StorageError, to_error_info
from mcp_servers.common.tool_errors import error_content, log_tool_failure


def _text(error: Exception) -> str:
    """Build the reply text of a failed tool call.

    Parameters
    ----------
    error : `Exception`
        The error the tool raised.

    Returns
    -------
    text : `str`
        The one text block of the reply.
    """
    return error_content(to_error_info(error, request_id="r1"))[0].text


def test_a_status_error_says_it_is_a_status() -> None:
    """An offline device is reported as a status, not a failure."""
    text = _text(ExternalServiceError("The observatory computer is not reachable.", report="status"))
    assert text.startswith("external_service: The observatory computer is not reachable.")
    assert "This is a status, not a failure" in text


def test_an_error_has_no_status_line() -> None:
    """An action that failed reads as a failure."""
    assert "status" not in _text(NotFoundError("No target 'M 99'."))


@pytest.mark.parametrize(
    ("error", "level", "has_traceback"),
    [
        (NotFoundError("No target."), logging.INFO, False),
        (ExternalServiceError("Offline.", report="status", log_level="INFO"), logging.INFO, False),
        (StorageError("Disk full."), logging.ERROR, True),
        (KeyError("bug"), logging.ERROR, True),
    ],
)
def test_a_tool_failure_is_logged_at_the_errors_level(
    caplog: pytest.LogCaptureFixture, error: Exception, level: int, has_traceback: bool
) -> None:
    """The line follows ``log_level``; only an ``ERROR`` has a traceback."""
    with caplog.at_level(logging.DEBUG, logger="mcp_servers.common.tool_errors"):
        log_tool_failure("target_get", error, to_error_info(error, request_id="r1"))
    assert [record.levelno for record in caplog.records] == [level]
    assert (caplog.records[0].exc_info is not None) is has_traceback
