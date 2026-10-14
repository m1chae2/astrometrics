"""Tests for HandoffService and cross-device continuity endpoints.

Validates the thread-safe active workspace continuity store, WebSocket
event dispatching, GSConnect beam bridging, and the corresponding REST API
endpoints used by desktop and mobile companion applications.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from astrometricslib import ConfigurationError, ExternalServiceError, NotFoundError
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
    """Verify beam raises ConfigurationError when KDE Connect CLI is absent.

    Ensures that when neither gsconnect-cli nor kdeconnect-cli is installed,
    the error names both programs and carries the deep link in its details.
    """
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    service = HandoffService()
    with pytest.raises(ConfigurationError, match="Neither gsconnect-cli nor kdeconnect-cli") as caught:
        service.beam_to_device(target="M42", mode="Planetarium")

    assert "target=M42" in caught.value.details["deep_link"]


def test_handoff_service_beam_cli_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify beam raises ExternalServiceError when the CLI exits non-zero."""
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/kdeconnect-cli")
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=MagicMock(returncode=1, stderr="no device")))

    with pytest.raises(ExternalServiceError, match="no device"):
        HandoffService().beam_to_device(target="M42")


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


def _call(client: TestClient, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call one RPC method through ``/api/rpc`` and return the reply body.

    Parameters
    ----------
    client : `~fastapi.testclient.TestClient`
        The test client.
    method : `str`
        The RPC method name.
    params : `dict`, optional
        The method's parameters.

    Returns
    -------
    body : `dict`
        The JSON-RPC reply.
    """
    response = client.post(
        "/api/rpc", json={"jsonrpc": "2.0", "method": method, "params": params or {}, "id": method}
    )
    assert response.status_code == 200
    return response.json()


def test_handoff_state_methods_via_rpc(client: TestClient) -> None:
    """Check ``handoff:get_state`` and ``handoff:update_state`` over RPC.

    Ensures the RPC layer serializes and updates the workspace continuity
    state properly.
    """
    initial_state = _call(client, "handoff:get_state")["result"]["data"]
    assert "active_mode" in initial_state

    payload: dict[str, Any] = {
        "active_mode": "Planetarium",
        "selected_target": "Vega",
        "coordinates": {"ra_hours": 18.61, "dec_degrees": 38.78},
        "origin_device": "mobile",
    }
    posted_state = _call(client, "handoff:update_state", payload)["result"]["data"]
    assert posted_state["selected_target"] == "Vega"
    assert posted_state["active_mode"] == "Planetarium"

    assert _call(client, "handoff:get_state")["result"]["data"]["selected_target"] == "Vega"


def test_list_paired_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify paired companion device output parsing from KDE Connect CLI.

    Tests that standard CLI list format is converted into structured objects
    with name, id, and reachability.
    """
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/kdeconnect-cli")
    mock_output = (
        "- Samsung Galaxy Z Flip7: flip7_id_123 (reachable)\n"
        "- Galaxy Tab S9: tab_id_456 (paired and reachable)\n"
    )
    mock_proc = MagicMock(returncode=0, stdout=mock_output, stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_proc)

    service = HandoffService()
    devices = service.list_paired_devices()

    assert len(devices) == 2
    assert devices[0]["name"] == "Samsung Galaxy Z Flip7"
    assert devices[0]["id"] == "flip7_id_123"
    assert devices[0]["reachable"] is True
    assert devices[1]["name"] == "Galaxy Tab S9"


def test_send_device_alert_dispatches_ws_and_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify alert emission over WebSocket and GSConnect notification ping.

    Ensures that calling send_device_alert notifies both active socket
    subscribers and executes the CLI notification command.
    """
    mock_socket_mgr = MagicMock()
    service = HandoffService(socket_manager=mock_socket_mgr)

    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/kdeconnect-cli")
    mock_proc = MagicMock(returncode=0, stdout="", stderr="")
    mock_run = MagicMock(return_value=mock_proc)
    monkeypatch.setattr("subprocess.run", mock_run)

    result = service.send_device_alert(
        title="Guiding Lost",
        message="Guide star SNR dropped below threshold",
        priority="high",
        ring_device=False,
        device_id="flip7_id_123",
    )

    assert result["success"] is True
    assert "websocket" in result["channels"]
    assert "gsconnect_ping" in result["channels"]
    assert "gsconnect_ring" not in result["channels"]

    mock_socket_mgr.broadcast_ui_event_sync.assert_called_once()
    event_name, payload = mock_socket_mgr.broadcast_ui_event_sync.call_args[0]
    assert event_name == "device_alert"
    assert payload["title"] == "Guiding Lost"
    assert payload["device_id"] == "flip7_id_123"

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "--ping-msg" in cmd
    assert "Guiding Lost: Guide star SNR dropped below threshold" in cmd
    assert "--device" in cmd
    assert "flip7_id_123" in cmd


def test_send_device_alert_with_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify emergency device alert triggers audible ring alarm.

    Validates that setting ring_device=True invokes kdeconnect-cli --ring.
    """
    service = HandoffService()
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/kdeconnect-cli")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("subprocess.run", mock_run)

    result = service.send_device_alert(
        title="Meridian Limit Exceeded",
        message="Mount reached mechanical stop",
        priority="critical",
        ring_device=True,
    )

    assert result["success"] is True
    assert "gsconnect_ring" in result["channels"]
    assert mock_run.call_count == 2  # 1 for ping-msg, 1 for ring


def test_share_file_to_device(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify file sharing to a paired device via GSConnect CLI.

    Checks non-existent file rejection and valid file invocation.
    """
    service = HandoffService()

    # Reject missing file
    with pytest.raises(NotFoundError, match="does not exist"):
        service.share_file_to_device("/nonexistent/photo.png")

    # Accept existing file and run CLI
    sample_file = tmp_path / "stacked_m31.png"
    sample_file.write_text("dummy-image-bytes")

    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/kdeconnect-cli")
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("subprocess.run", mock_run)

    result = service.share_file_to_device(str(sample_file), device_id="flip7_id")
    assert result["success"] is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == [
        "/usr/bin/kdeconnect-cli",
        "--share",
        str(sample_file),
        "--device",
        "flip7_id",
    ]


def test_device_and_alert_methods_via_rpc(client: TestClient) -> None:
    """Check ``handoff:list_devices`` and ``handoff:send_alert`` over RPC."""
    assert isinstance(_call(client, "handoff:list_devices")["result"]["data"], list)

    payload = {
        "title": "Sequence Completed",
        "message": "Target M42: 30 exposures stacked successfully",
        "priority": "normal",
        "ring_device": False,
    }
    data = _call(client, "handoff:send_alert", payload)["result"]["data"]
    assert data["success"] is True
    assert data["alert"]["title"] == "Sequence Completed"


def test_share_file_method_reports_a_missing_file_as_not_found(client: TestClient) -> None:
    """Check ``handoff:share_file`` reports a missing file as not_found."""
    reply = _call(client, "handoff:share_file", {"file_path": "/nonexistent/photo.png"})

    error = reply["error"]["data"]
    assert error["code"] == "not_found"
    assert "does not exist" in error["message"]
    assert error["requestId"]
