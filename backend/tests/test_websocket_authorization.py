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


def test_disallowed_origin_is_rejected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A hostile site's origin must not pass the allowlist check."""
    assert not session_auth.is_origin_allowed("https://evil.example.com", ALLOWED_ORIGINS)


def test_allowed_origin_is_accepted():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The UI's own dev-server origin must pass."""
    assert session_auth.is_origin_allowed("http://localhost:5173", ALLOWED_ORIGINS)


@pytest.mark.parametrize("origin", [None, "null"])
def test_opaque_and_absent_origins_defer_to_token(origin):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """`file://` renderers and non-browser clients rely on the token.

    Neither can be resolved against the allowlist, so the origin check
    passes them through and the token check is what actually gates
    them.
    """
    assert session_auth.is_origin_allowed(origin, ALLOWED_ORIGINS)


def test_valid_token_is_accepted():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The process's own token must validate."""
    assert session_auth.is_token_valid(session_auth.SESSION_TOKEN)


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_missing_or_wrong_token_is_rejected(token):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Absent, empty, and incorrect tokens must all fail."""
    assert not session_auth.is_token_valid(token)


def test_token_is_not_guessable():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The generated token must have real entropy behind it."""
    assert len(session_auth.SESSION_TOKEN) >= 32


def test_environment_token_is_honored(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A launcher-supplied token must win over a generated one."""
    monkeypatch.setenv("ASTROMETRICS_SESSION_TOKEN", "launcher-supplied-token")
    assert session_auth._resolve_session_token() == "launcher-supplied-token"


def test_token_is_generated_when_unset(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
def test_handshake_from_hostile_origin_is_closed(client: TestClient, endpoint):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
def test_handshake_without_token_is_closed(client: TestClient, endpoint):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """An allowed origin still needs the session token."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            endpoint,
            headers={"origin": "http://localhost:5173"},
        ) as connection:
            connection.receive_text()


def test_terminal_handshake_with_origin_and_token_succeeds(client: TestClient):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A legitimate UI client must still be able to connect."""
    with client.websocket_connect(
        f"/ws/terminal?token={session_auth.SESSION_TOKEN}",
        headers={"origin": "http://localhost:5173"},
    ) as connection:
        assert "Connected to Astrometrics Terminal" in connection.receive_text()


def test_session_token_endpoint_serves_the_token(client: TestClient):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The UI needs a way to fetch the token; CORS gates who may read it."""
    response = client.get("/api/session-token")
    assert response.status_code == 200
    assert response.json()["token"] == session_auth.SESSION_TOKEN
