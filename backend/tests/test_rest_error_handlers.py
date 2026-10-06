"""Unit tests for the REST error handlers in main_backend.

An expected error keeps its code and gets a matching HTTP status. An
unexpected exception is reported as ``internal`` with no internal detail.
"""

import asyncio
import json

from starlette.requests import Request

from astrometricslib import ConflictError
from backend.main_backend import expected_error_handler, global_exception_handler


def _request() -> Request:
    """Build a bare POST request for the handlers.

    Returns
    -------
    request : `~starlette.requests.Request`
        A request with no headers.
    """
    return Request({"type": "http", "method": "POST", "path": "/api/example", "headers": []})


def test_expected_error_keeps_code_and_maps_status() -> None:
    """A ConflictError becomes a 409 reply with code ``conflict``."""
    response = asyncio.run(expected_error_handler(_request(), ConflictError("The mount is parked.")))

    assert response.status_code == 409
    error = json.loads(response.body)["error"]
    assert error["code"] == "conflict"
    assert error["message"] == "The mount is parked."


def test_unexpected_error_hides_detail() -> None:
    """A bare exception becomes a 500 ``internal`` reply without its text."""
    response = asyncio.run(global_exception_handler(_request(), KeyError("secret internal key")))

    assert response.status_code == 500
    error = json.loads(response.body)["error"]
    assert error["code"] == "internal"
    assert "secret" not in error["message"]
    assert error["requestId"] in error["message"]
