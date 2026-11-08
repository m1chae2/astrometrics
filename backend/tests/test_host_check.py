"""Purpose: Tests for the Host header check and the CORS reply headers.

Description: A request that names a host the backend does not serve (DNS
rebinding) is refused. Local names pass. In network mode private addresses
pass too. The RPC reply no longer carries a wildcard CORS header.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main_backend
from backend.services.infrastructure.host_check import is_host_allowed


@pytest.fixture
def client() -> TestClient:
    """Give a test client that does not run the lifespan.

    Returns
    -------
    client : `~fastapi.testclient.TestClient`
        A client for the backend app.
    """
    return TestClient(main_backend.app)


def test_a_foreign_host_is_refused(client: TestClient) -> None:
    """A request with an unexpected Host header gets status 400."""
    response = client.get("/", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_a_foreign_host_cannot_make_an_rpc_call(client: TestClient) -> None:
    """The RPC route refuses a rebinding host."""
    response = client.post(
        "/api/rpc", json={"method": "system:health", "params": {}}, headers={"Host": "evil.example:5000"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost", "localhost:5000", "127.0.0.1:5000", "[::1]:5000"])
def test_local_hosts_pass(client: TestClient, host: str) -> None:
    """The loopback names pass with and without a port."""
    assert client.get("/", headers={"Host": host}).status_code == 200


def test_the_rpc_reply_has_no_wildcard_cors_header(client: TestClient) -> None:
    """An allowed origin is echoed back; a stranger gets no header."""
    allowed = client.post(
        "/api/rpc",
        json={"method": "system:frontend_log", "params": {}},
        headers={"Origin": "http://localhost:5173"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    stranger = client.post(
        "/api/rpc",
        json={"method": "system:frontend_log", "params": {}},
        headers={"Origin": "http://evil.example"},
    )
    assert stranger.headers.get("access-control-allow-origin") != "*"
    assert "access-control-allow-origin" not in stranger.headers


def test_private_addresses_pass_only_in_network_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 192.168.x.x host is refused on loopback and allowed with --lan."""
    monkeypatch.setenv("ASTROMETRICS_BIND_HOST", "127.0.0.1")
    assert not is_host_allowed("192.168.1.20:5000")
    # The value that `run_backend.sh --lan` sets.
    monkeypatch.setenv("ASTROMETRICS_BIND_HOST", "0.0.0.0")  # ruff: ignore[hardcoded-bind-all-interfaces]
    assert is_host_allowed("192.168.1.20:5000")
    assert is_host_allowed("10.0.0.7")
    assert is_host_allowed("stellarmate.local:5000")
    assert not is_host_allowed("8.8.8.8:5000")
    assert not is_host_allowed("evil.example")
    assert not is_host_allowed(None)
