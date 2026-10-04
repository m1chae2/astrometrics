"""Purpose: Tests for how the RPC layer reports errors and converts results.

Description: These tests cover three behaviors of the RPC layer:

* An unknown method name is reported as "Method not found", but a
  ``KeyError`` raised inside a service is not.
* ``serialize_rpc_result`` turns NumPy values into plain Python values
  instead of strings.
* The MCP RPC proxy can run a method in-process and serialize the result.
"""

import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from backend.mcp import tool_registry
from backend.routers import rpc_router
from backend.services.rpc_protocol import RPCMethodNotFoundError, RPCRequest, serialize_rpc_result


def _body(response: object) -> dict:
    """Decode the JSON body of a FastAPI response.

    Parameters
    ----------
    response : `~fastapi.responses.JSONResponse`
        The response to decode.

    Returns
    -------
    body : `dict`
        The parsed JSON body.
    """
    return json.loads(response.body)


@pytest.fixture
def registered_methods(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Give the router an empty handler table that a test can fill.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's monkeypatch fixture.

    Returns
    -------
    handlers : `dict`
        The handler table the router now uses.
    """
    handlers: dict = {}
    monkeypatch.setattr(rpc_router.rpc_registry, "_handlers", handlers)
    monkeypatch.setattr(rpc_router.rpc_registry, "_resolve_dynamic_reflected_handler", lambda _method: None)
    return handlers


@pytest.mark.anyio
async def test_an_unknown_method_is_reported_as_not_found(registered_methods: dict) -> None:
    """Give a method with no handler error code -32601 and HTTP 404."""
    response = await rpc_router.handle_rpc(RPCRequest(method="nothing:here", id=1))
    assert response.status_code == 404
    assert _body(response)["error"]["code"] == -32601


@pytest.mark.anyio
async def test_execute_raises_the_dedicated_error_for_an_unknown_method(registered_methods: dict) -> None:
    """The registry raises `RPCMethodNotFoundError`, not a bare `KeyError`."""
    with pytest.raises(RPCMethodNotFoundError):
        await rpc_router.rpc_registry.execute("nothing:here", {})


@pytest.mark.anyio
async def test_a_key_error_inside_a_service_is_an_internal_error(registered_methods: dict) -> None:
    """Report a `KeyError` from a handler as an internal error."""

    def handler() -> None:
        """Raise the way a service does when it looks up a missing key.

        Raises
        ------
        KeyError
            Always.
        """
        raise KeyError("target")

    registered_methods["test:boom"] = handler
    response = await rpc_router.handle_rpc(RPCRequest(method="test:boom", id=2))
    assert response.status_code == 500
    assert _body(response)["error"]["code"] == -32603


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (np.int64(7), 7),
        (np.int32(-3), -3),
        (np.float32(1.5), 1.5),
        (np.bool_(True), True),
        (np.array([1, 2, 3]), [1, 2, 3]),
        (np.array([[1.0, 2.0], [3.0, 4.0]]), [[1.0, 2.0], [3.0, 4.0]]),
    ],
)
def test_numpy_values_become_plain_python_values(value: object, expected: object) -> None:
    """NumPy scalars and arrays keep their value and their type family."""
    result = serialize_rpc_result(value)
    assert result == expected
    assert not isinstance(result, str)
    json.dumps(result)


def test_numpy_values_inside_containers_are_converted() -> None:
    """NumPy values nested in dicts, lists, and tuples are converted too."""
    value = {"count": np.int64(4), "values": (np.float64(0.5), np.int8(2)), "ok": np.bool_(False)}
    result = serialize_rpc_result(value)
    assert result == {"count": 4, "values": [0.5, 2], "ok": False}
    assert type(result["count"]) is int
    assert type(result["ok"]) is bool


def test_numpy_nan_becomes_none() -> None:
    """A NumPy NaN is sent as `None`, like a Python NaN."""
    assert serialize_rpc_result(np.float32("nan")) is None
    assert serialize_rpc_result(np.array([1.0, math.nan])) == [1.0, None]


def test_an_unknown_type_is_logged_before_the_str_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """Warn, naming the type, when a value falls back to text."""

    class Opaque:
        """A type with no JSON form."""

        def __str__(self) -> str:
            """Return a fixed text.

            Returns
            -------
            text : `str`
                The text form.
            """
            return "opaque"

    with caplog.at_level("WARNING", logger="backend.services.rpc_protocol"):
        assert serialize_rpc_result(Opaque()) == "opaque"
    assert "Opaque" in caplog.text


@pytest.mark.anyio
async def test_the_mcp_proxy_runs_a_method_in_process(
    monkeypatch: pytest.MonkeyPatch, registered_methods: dict
) -> None:
    """Run `execute_rpc` in-process and serialize the result."""

    def handler() -> dict:
        """Return a result holding a NumPy value.

        Returns
        -------
        result : `dict`
            A dictionary with a NumPy integer.
        """
        return {"n": np.int64(3)}

    registered_methods["test:numbers"] = handler
    monkeypatch.setattr(tool_registry, "get_container", lambda: SimpleNamespace(initialized=True))

    reply = await tool_registry.execute_rpc("test:numbers", {})
    assert reply == {"status": "success", "data": {"n": 3}}
