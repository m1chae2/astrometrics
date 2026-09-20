"""Tests for HandoffService and cross-device continuity endpoints.

Validates the thread-safe active workspace continuity store, WebSocket
event dispatching, GSConnect beam bridging, and the corresponding REST API
endpoints used by desktop and mobile companion applications.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.services.infrastructure.handoff_service import HandoffService


def test_handoff_service_defaults() -> None:
    """Verify default initialization values in HandoffService.

    Ensures initial mode is set to 'Image Viewer' and coordinate/telemetry
    structures are initialized cleanly.
    """
    service = HandoffService()
    state = service.get_state()
    assert state["active_mode"] == "Image Viewer"
    assert state["selected_target"] is None
    assert state["origin_device"] == "desktop"
    assert "coordinates" in state
    assert "telemetry" in state


def test_handoff_service_update_and_broadcast() -> None:
    """Verify state mutation and WebSocket broadcast trigger.

    Checks that updates to active mode, target designation, and coordinates
    are reflected in the state snapshot and emitted to connected clients.
    """
    mock_socket_mgr = MagicMock()
    service = HandoffService(socket_manager=mock_socket_mgr)

    updated = service.update_state(
        active_mode="Planetarium",
        selected_target="M31",
        coordinates={"ra_hours": 0.712, "dec_degrees": 41.27},
        origin_device="mobile",
    )

    assert updated["active_mode"] == "Planetarium"
    assert updated["selected_target"] == "M31"
    assert updated["coordinates"]["ra_hours"] == pytest.approx(0.712)
    assert updated["origin_device"] == "mobile"

    mock_socket_mgr.broadcast_ui_event_sync.assert_called_once_with("handoff", updated)


def test_handoff_service_beam_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify beam returns friendly status when KDE Connect CLI is absent.

    Ensures that when neither gsconnect-cli nor kdeconnect-cli is installed,
    a graceful error structure is returned without raising exceptions.
    """
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    service = HandoffService()
    result = service.beam_to_device(target="M42", mode="Planetarium")

    assert result["success"] is False
    assert "Neither gsconnect-cli nor kdeconnect-cli" in result["message"]
    assert "target=M42" in result["deep_link"]


def test_handoff_service_beam_executes_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify beam runs CLI with deep link URL when CLI is available.

    Simulates an installed kdeconnect-cli and verifies subprocess invocation.
    """
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/kdeconnect-cli")
    mock_proc = MagicMock(returncode=0, stdout="", stderr="")
    mock_run = MagicMock(return_value=mock_proc)
    monkeypatch.setattr("subprocess.run", mock_run)

    service = HandoffService()
    result = service.beam_to_device(target="NGC 7000", mode="Planetarium")

    assert result["success"] is True
    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    assert args[0] == [
        "/usr/bin/kdeconnect-cli",
        "--open-url",
        "astrometrics://handoff?mode=Planetarium&target=NGC 7000",
    ]


def test_handoff_endpoints_via_testclient(client: TestClient) -> None:
    """Verify GET and POST /api/handoff/state endpoints via FastAPI client.

    Ensures the HTTP delivery layer serializes and updates the workspace
    continuity state properly.
    """
    # Fetch initial state
    res = client.get("/api/handoff/state")
    assert res.status_code == 200
    initial_state = res.json()
    assert "active_mode" in initial_state

    # Update state via POST
    payload: dict[str, Any] = {
        "active_mode": "Planetarium",
        "selected_target": "Vega",
        "coordinates": {"ra_hours": 18.61, "dec_degrees": 38.78},
        "origin_device": "mobile",
    }
    res_post = client.post("/api/handoff/state", json=payload)
    assert res_post.status_code == 200
    posted_state = res_post.json()
    assert posted_state["selected_target"] == "Vega"
    assert posted_state["active_mode"] == "Planetarium"

    # Verify updated state is returned by GET
    res_get = client.get("/api/handoff/state")
    assert res_get.status_code == 200
    assert res_get.json()["selected_target"] == "Vega"
