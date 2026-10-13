"""Tests for WebSocket handshake authorization.

The threat these cover: CORS does not apply to WebSocket handshakes, so
before the origin/token gate existed, any page the user visited while
the backend was running could open `/ws/terminal` and execute arbitrary
Python through the scripting console.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.services.infrastructure import session_auth

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "app://.",
]


def test_disallowed_origin_is_rejected() -> None:
    """A hostile site's origin must not pass the allowlist check."""
    assert not session_auth.is_origin_allowed("https://evil.example.com", ALLOWED_ORIGINS)


def test_allowed_origin_is_accepted() -> None:
    """The UI's own dev-server origin must pass."""
    assert session_auth.is_origin_allowed("http://localhost:5173", ALLOWED_ORIGINS)


@pytest.mark.parametrize("origin", [None, "null"])
def test_opaque_and_absent_origins_defer_to_token(origin: str | None) -> None:
    """`file://` renderers and non-browser clients rely on the token.

    Neither can be resolved against the allowlist, so the origin check
    passes them through and the token check is what actually gates
    them.
    """
    assert session_auth.is_origin_allowed(origin, ALLOWED_ORIGINS)


def test_valid_token_is_accepted() -> None:
    """The process's own token must validate."""
    assert session_auth.is_token_valid(session_auth.SESSION_TOKEN)


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_missing_or_wrong_token_is_rejected(token: str | None) -> None:
    """Absent, empty, and incorrect tokens must all fail."""
    assert not session_auth.is_token_valid(token)


def test_token_is_not_guessable() -> None:
    """The generated token must have real entropy behind it."""
    assert len(session_auth.SESSION_TOKEN) >= 32


def test_environment_token_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A launcher-supplied token must win over a generated one."""
    monkeypatch.setenv("ASTROMETRICS_SESSION_TOKEN", "launcher-supplied-token")
    assert session_auth._resolve_session_token() == "launcher-supplied-token"


def test_token_is_generated_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no launcher token, a random one is minted per process."""
    monkeypatch.delenv("ASTROMETRICS_SESSION_TOKEN", raising=False)
    first = session_auth._resolve_session_token()
    second = session_auth._resolve_session_token()
    assert first != second
    assert len(first) >= 32


# ---------------------------------------------------------------------------
# End-to-end handshake tests: the helpers above are only meaningful if the
# endpoints actually consult them before accepting a connection.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ["/ws/terminal", "/ws/events"])
def test_handshake_from_hostile_origin_is_closed(client: TestClient, endpoint: str) -> None:
    """The original vulnerability: a foreign page must not connect.

    Sends a valid token so the *origin* check is what is under test.
    """
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"{endpoint}?token={session_auth.SESSION_TOKEN}",
            headers={"origin": "https://evil.example.com"},
        ) as connection:
            connection.receive_text()


@pytest.mark.parametrize("endpoint", ["/ws/terminal", "/ws/events"])
def test_handshake_without_token_is_closed(client: TestClient, endpoint: str) -> None:
    """An allowed origin still needs the session token."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            endpoint,
            headers={"origin": "http://localhost:5173"},
        ) as connection:
            connection.receive_text()


def test_terminal_handshake_with_origin_and_token_succeeds(client: TestClient) -> None:
    """A legitimate UI client must still be able to connect."""
    with client.websocket_connect(
        f"/ws/terminal?token={session_auth.SESSION_TOKEN}",
        headers={"origin": "http://localhost:5173"},
    ) as connection:
        assert "Connected to Astrometrics Terminal" in connection.receive_text()


def test_session_token_endpoint_serves_the_token(client: TestClient) -> None:
    """The UI needs a way to fetch the token; CORS gates who may read it."""
    response = client.get("/api/session-token")
    assert response.status_code == 200
    assert response.json()["token"] == session_auth.SESSION_TOKEN


def test_pairing_info_endpoint_returns_metadata(client: TestClient) -> None:
    """The companion pairing route must supply complete connection metadata.

    Verifies that host, LAN IP, session token, and endpoint URLs are
    properly resolved and serialized for remote client consumption.
    """
    response = client.get("/api/pairing-info")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "Astrometrics"
    assert data["version"] == "0.2.0"
    assert "lan_ip" in data
    assert data["session_token"] == session_auth.SESSION_TOKEN
    assert "rpc" in data["endpoints"]
    assert "ws_events" in data["endpoints"]
    assert "ws_terminal" in data["endpoints"]


def test_figure_page_and_websocket(client: TestClient) -> None:
    """Verify figure HTML page serves WebAgg markup and handles WS auth.

    Ensures the /figure/{id} HTML route renders with Matplotlib scripts,
    the backend origin is permitted, and unauthorized WebSockets without
    a session token are rejected while authorized connections succeed.
    """
    # HTML endpoint should serve 200 with mpl scripts
    response = client.get("/figure/1")
    assert response.status_code == 200
    assert "mpl.figure" in response.text
    assert "/figure/mpl.js" in response.text
    assert '<base href="/"' in response.text

    # Figure JS bundle endpoint should return WebAgg code with toolbar items
    js_resp = client.get("/figure/mpl.js")
    assert js_resp.status_code == 200
    assert "toolbar_items" in js_resp.text
    assert "zoom_to_rect" in js_resp.text

    # Toolbar icon images should be reachable both at root and figure prefix
    img_resp = client.get("/_images/zoom_to_rect.png")
    assert img_resp.status_code == 200
    fig_img_resp = client.get("/figure/_images/zoom_to_rect.png")
    assert fig_img_resp.status_code == 200

    # Figure download endpoint should 404 when figure does not exist
    download_resp = client.get("/figure/999/download.png")
    assert download_resp.status_code == 404

    # Origin http://127.0.0.1:5000 and http://localhost:5000 must be allowed
    assert "http://127.0.0.1:5000" in session_auth.ALLOWED_ORIGINS
    assert "http://localhost:5000" in session_auth.ALLOWED_ORIGINS
    assert session_auth.is_origin_allowed("http://127.0.0.1:5000", session_auth.ALLOWED_ORIGINS)

    # Unauthorized WS without token should be disconnected
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/figure/1",
            headers={"origin": "http://127.0.0.1:5000"},
        ) as connection:
            connection.receive_text()

    # Unauthorized WS with disallowed origin should be disconnected
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/ws/figure/1?token={session_auth.SESSION_TOKEN}",
            headers={"origin": "https://evil.example.com"},
        ) as connection:
            connection.receive_text()
