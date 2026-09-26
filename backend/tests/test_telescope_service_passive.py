"""Tests for TelescopeService tracking state detection and target inference."""

from unittest.mock import MagicMock

import pytest


def test_infer_target_at_coordinates() -> None:
    """Verify target match when coordinates are close."""
    from backend.services.observatory.telescope_service import TelescopeService

    target_mock = MagicMock()
    target_mock.id = "M 27"
    target_mock.name = "M 27"
    target_mock.ra = "19h 59m 36s"
    target_mock.dec = "+22° 43′ 16″"

    target_service_mock = MagicMock()
    target_service_mock.get_targets.return_value = [target_mock]

    service = TelescopeService(target_service=target_service_mock)

    # Coordinates near M 27 (within 20 arcmin)
    matched = service._infer_target_at_coordinates("20h 01m 00s", "+22° 46′ 00″")
    assert matched == "M 27"

    # Coordinates far from M 27 (e.g. Vega)
    matched_far = service._infer_target_at_coordinates("18h 36m 56s", "+38° 47′ 01″")
    assert matched_far is None


def test_get_status_polls_external_guiding() -> None:
    """Verify get_status polls external guiding telemetry."""
    from backend.services.observatory.telescope_service import TelescopeService

    guiding_service_mock = MagicMock()
    guiding_service_mock.get_status.return_value = {"history": [{"time": 100, "dra": 0.1, "ddec": -0.1}]}

    wayfinder_mock = MagicMock()
    wayfinder_mock.control.get_telescope_status.return_value = {
        "ra": "20h 00m 00s",
        "dec": "+22° 00m 00s",
        "trackingStatus": "Tracking",
        "connectionStatus": "Connected",
    }

    service = TelescopeService(guiding_service=guiding_service_mock, wayfinder=wayfinder_mock)
    status = service.get_status()

    guiding_service_mock.poll_external_telemetry.assert_called_once()
    assert len(status["guidingHistory"]) == 1
    assert status["guidingHistory"][0]["dra"] == pytest.approx(0.1)
