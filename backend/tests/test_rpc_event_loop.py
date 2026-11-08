"""Purpose: Tests that large RPC replies are encoded off the event loop.

Description: The RPC router runs the methods that scan a large library in a
small heavy lane, so they cannot slow light calls. It builds a success
reply in a worker thread. These tests check that the bytes match a fixed
expected JSON, that errors keep their shape, and that a slow encoding or
a busy heavy call does not delay a small call that runs at the same time.
"""

import asyncio
import json
import threading
import time

import numpy as np
import pytest
from fastapi.responses import JSONResponse

from astrometricslib.exceptions import NotFoundError
from backend.routers import rpc_router
from backend.services import rpc_protocol
from backend.services.infrastructure import heavy_lane
from backend.services.rpc_protocol import RPCRequest


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
    return handlers


def _large_result() -> list[dict]:
    """Build the large reply used to check the encoding.

    Returns
    -------
    rows : `list` [`dict`]
        20000 small rows.
    """
    return [{"row": i, "values": list(range(5))} for i in range(20000)]


def _large_expected_body() -> bytes:
    """Write out the expected bytes of the large reply by hand.

    Returns
    -------
    body : `bytes`
        The compact JSON-RPC success envelope for `_large_result`.
    """
    rows = ",".join(f'{{"row":{i},"values":[0,1,2,3,4]}}' for i in range(20000))
    return f'{{"jsonrpc":"2.0","result":{{"status":"success","data":[{rows}]}},"id":7}}'.encode()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            {"a": 1, "b": [1.5, float("nan")], "c": "café"},
            b'{"jsonrpc":"2.0","result":{"status":"success","data":{"a":1,"b":[1.5,null],"c":"caf\xc3\xa9"}},"id":7}',
        ),
        (
            {"n": np.int64(3), "f": np.float64(2.5), "arr": np.arange(4)},
            b'{"jsonrpc":"2.0","result":{"status":"success","data":{"n":3,"f":2.5,"arr":[0,1,2,3]}},"id":7}',
        ),
        (_large_result(), _large_expected_body()),
        (None, b'{"jsonrpc":"2.0","result":{"status":"success","data":null},"id":7}'),
    ],
    ids=["nan-and-unicode", "numpy", "large", "none"],
)
def test_success_body_matches_fixed_json(result: object, expected: bytes) -> None:
    """The threadpool-encoded body equals a fixed compact JSON envelope."""
    assert rpc_protocol.encode_rpc_success_body(result, 7) == expected


def test_handler_reply_has_headers_and_json_type(registered_methods: dict) -> None:
    """A success reply keeps the JSON content type and sets no CORS header."""
    registered_methods["x:y"] = lambda: {"v": np.float32(1.5)}
    response = asyncio.run(rpc_router.handle_rpc(RPCRequest(method="x:y", params={}, id=3)))
    assert response.status_code == 200
    assert response.media_type == "application/json"
    assert "access-control-allow-origin" not in response.headers
    assert json.loads(response.body) == {
        "jsonrpc": "2.0",
        "result": {"status": "success", "data": {"v": 1.5}},
        "id": 3,
    }


def test_error_reply_is_unchanged(registered_methods: dict) -> None:
    """A handler error still gives the usual JSON-RPC error envelope."""

    def fail() -> None:
        """Raise a not-found error.

        Raises
        ------
        NotFoundError
            Always.
        """
        raise NotFoundError("nope")

    registered_methods["x:fail"] = fail
    response = asyncio.run(rpc_router.handle_rpc(RPCRequest(method="x:fail", params={}, id=4)))
    assert isinstance(response, JSONResponse)
    body = json.loads(response.body)
    assert body["id"] == 4
    assert body["error"]["message"] == "nope"


def test_slow_encoding_does_not_block_small_call(
    registered_methods: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small RPC finishes while a large reply is still being encoded."""
    real_serialize = rpc_protocol.serialize_rpc_result

    def slow_serialize(obj: object) -> object:
        """Serialize, but take 0.5 s when given the large marker.

        Parameters
        ----------
        obj : `object`
            The value to convert.

        Returns
        -------
        converted : `object`
            The converted value.
        """
        if obj == "big":
            time.sleep(0.5)
        return real_serialize(obj)

    monkeypatch.setattr(rpc_protocol, "serialize_rpc_result", slow_serialize)
    registered_methods["x:big"] = lambda: "big"
    registered_methods["x:small"] = lambda: "small"

    async def scenario() -> float:
        """Start the big call, then time a small one.

        Returns
        -------
        elapsed : `float`
            Seconds the small call took.
        """
        big = asyncio.create_task(rpc_router.handle_rpc(RPCRequest(method="x:big", params={}, id=1)))
        await asyncio.sleep(0.1)
        start = time.perf_counter()
        await rpc_router.handle_rpc(RPCRequest(method="x:small", params={}, id=2))
        elapsed = time.perf_counter() - start
        await big
        return elapsed

    assert asyncio.run(scenario()) < 0.25


def test_heavy_methods_do_not_delay_a_light_call(
    registered_methods: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many busy heavy calls leave a light call fast: few run at once."""
    monkeypatch.setattr(rpc_router, "HEAVY_METHODS", frozenset({"x:heavy"}))
    running = {"now": 0, "most": 0}
    guard = threading.Lock()

    def heavy() -> int:
        """Burn processor time for 0.2 s and note how many run together.

        Returns
        -------
        count : `int`
            Always 1.
        """
        with guard:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        end = time.perf_counter() + 0.2
        while time.perf_counter() < end:
            sum(i * i for i in range(200))
        with guard:
            running["now"] -= 1
        return 1

    registered_methods["x:heavy"] = heavy
    registered_methods["x:light"] = lambda: "light"

    async def scenario() -> float:
        """Start twelve heavy calls, then time a light one.

        Returns
        -------
        elapsed : `float`
            Seconds the light call took.
        """
        heavy_calls = [
            asyncio.create_task(rpc_router.handle_rpc(RPCRequest(method="x:heavy", params={}, id=i)))
            for i in range(12)
        ]
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        await rpc_router.handle_rpc(RPCRequest(method="x:light", params={}, id=99))
        elapsed = time.perf_counter() - start
        await asyncio.gather(*heavy_calls)
        return elapsed

    # The lane's job is checked exactly by `running["most"]`. The time
    # limit catches the real regression, a light call queued in the lane
    # behind twelve 0.2 s calls (about 1.2 s), and leaves room for a slow
    # continuous-integration machine (0.18 s seen there).
    assert asyncio.run(scenario()) < 0.5
    assert running["most"] <= heavy_lane.HEAVY_LANE_SLOTS


def test_declared_heavy_methods_exist() -> None:
    """Every method named as heavy is a declared RPC method."""
    assert rpc_router.HEAVY_METHODS <= rpc_router._DECLARED_METHODS
