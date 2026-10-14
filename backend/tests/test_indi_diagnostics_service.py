"""Purpose: Integration tests for the raw INDI inspection actions.

Description: `IndiDiagnosticsService` serves the three raw INDI actions
through `control.equipment` (`status(include=["indi_devices"])`,
`status(include=["indi_properties"], device_name=...)` and
`set_device_property`), which use `IndiDiagnostics`
(`wayfindinglib/drivers/indi/diagnostics.py`). These tests confirm the
full chain -- `rpc_router.py`'s three actions -> `IndiDiagnosticsService`
-> `control.equipment` -> `IndiDiagnostics` -- returns data end to end
through the real JSON-RPC endpoint.
"""

from typing import Any

from fastapi.testclient import TestClient


def _unwrap(result: Any) -> Any:
    """Unwrap a `{"status": ..., "data": ...}` RPC envelope if present.

    Returns
    -------
    unwrapped : `Any`
        ``result["data"]`` if `result` is such an envelope, else `result`
        unchanged.
    """
    if isinstance(result, dict) and "data" in result and "status" in result:
        return result["data"]
    return result


def test_telescope_indi_devices_returns_data_through_the_full_chain(client: TestClient) -> None:
    """Verify `telescope:indi_devices` reaches the real simulator session."""
    resp = client.post(
        "/api/rpc", json={"jsonrpc": "2.0", "method": "telescope:indi_devices", "params": {}, "id": "1"}
    )
    assert resp.status_code == 200
    result = _unwrap(resp.json().get("result"))
    assert isinstance(result, list)


def test_telescope_indi_properties_returns_data_through_the_full_chain(client: TestClient) -> None:
    """Verify `telescope:indi_properties` reaches the real session."""
    devices_resp = client.post(
        "/api/rpc", json={"jsonrpc": "2.0", "method": "telescope:indi_devices", "params": {}, "id": "1"}
    )
    devices = _unwrap(devices_resp.json().get("result")) or []

    resp = client.post(
        "/api/rpc",
        json={
            "jsonrpc": "2.0",
            "method": "telescope:indi_properties",
            "params": {"device_name": devices[0] if devices else "Telescope Simulator"},
            "id": "2",
        },
    )
    assert resp.status_code == 200
    assert isinstance(_unwrap(resp.json().get("result")), dict)


def test_telescope_set_indi_property_reaches_the_full_chain(client: TestClient) -> None:
    """Verify `telescope:set_indi_property` reaches the real simulator session.

    Asserts only that the call completes through the whole chain and
    returns a boolean -- an unknown device/property correctly reports
    `False` rather than raising, which is itself proof the request
    reached `IndiDiagnostics.set_property` rather than 404ing earlier.
    """
    resp = client.post(
        "/api/rpc",
        json={
            "jsonrpc": "2.0",
            "method": "telescope:set_indi_property",
            "params": {
                "device_name": "Telescope Simulator",
                "property_name": "CONNECTION",
                "value": "On",
                "element": "CONNECT",
            },
            "id": "3",
        },
    )
    assert resp.status_code == 200
    assert isinstance(_unwrap(resp.json().get("result")), bool)
