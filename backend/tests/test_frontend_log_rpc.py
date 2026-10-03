"""Purpose: Unit tests for the ``system:frontend_log`` RPC method.

Description: The browser app forwards uncaught errors to the backend through
``system:frontend_log``. These tests check the method is registered and that
it writes the message, at the level asked for, to the backend log.
"""

import logging

import pytest

from backend.routers.rpc_router import _log_frontend_message, rpc_registry


def test_the_method_is_registered() -> None:
    """The registry knows ``system:frontend_log``."""
    assert "system:frontend_log" in rpc_registry._handlers


@pytest.mark.parametrize(
    ("level", "expected"),
    [("info", logging.INFO), ("warn", logging.WARNING), ("error", logging.ERROR), ("odd", logging.ERROR)],
)
def test_the_message_is_logged_at_the_requested_level(
    level: str, expected: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Each level maps to a logging level; an unknown one is an error."""
    with caplog.at_level(logging.DEBUG, logger="frontend"):
        _log_frontend_message(level, "something happened")
    assert [record.levelno for record in caplog.records] == [expected]
    assert "something happened" in caplog.records[0].getMessage()


def test_the_stacks_are_added_to_the_message(caplog: pytest.LogCaptureFixture) -> None:
    """The JavaScript and React stacks follow the message."""
    with caplog.at_level(logging.DEBUG, logger="frontend"):
        _log_frontend_message("error", "boom", stack="at foo (a.js:1)", componentStack="in Widget")
    text = caplog.records[0].getMessage()
    assert text.splitlines() == ["boom", "at foo (a.js:1)", "in Widget"]
