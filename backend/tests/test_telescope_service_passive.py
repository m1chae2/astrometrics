"""Tests for TelescopeService tracking state detection and target inference."""

from unittest.mock import MagicMock

import pytest


def test_infer_target_at_coordinates() -> None:
    """Match the mount position to the nearest target within 1 degree."""
    from backend.services.observatory.telescope_service import TelescopeService

    target_service_mock = MagicMock()
    targets = target_service_mock.astrometrics.targets
    targets.query.side_effect = [
        {"targets": [{"id": "M 27", "common_name": None, "separation_deg": 0.3}]},
        {"targets": []},
    ]

    service = TelescopeService(target_service=target_service_mock)

    # Coordinates near M 27 (within 20 arcmin)
    assert service._infer_target_at_coordinates("20h 01m 00s", "+22° 46′ 00″") == "M 27"
    asked = targets.query.call_args_list[0].kwargs
    assert asked["radius_deg"] == pytest.approx(1.0)
    assert asked["sort"] == "separation"
    assert asked["ra"] == pytest.approx(300.25)
    assert asked["dec"] == pytest.approx(22.7667, abs=1e-3)

    # Coordinates far from every target (e.g. Vega)
    assert service._infer_target_at_coordinates("18h 36m 56s", "+38° 47′ 01″") is None


def test_infer_target_at_coordinates_ignores_missing_coordinates() -> None:
    """Return no match without a query when the mount has no position."""
    from backend.services.observatory.telescope_service import TelescopeService

    target_service_mock = MagicMock()
    service = TelescopeService(target_service=target_service_mock)

    assert service._infer_target_at_coordinates(None, None) is None
    assert service._infer_target_at_coordinates("Unknown", "+22° 46′ 00″") is None
    target_service_mock.astrometrics.targets.query.assert_not_called()


def test_get_status_polls_external_guiding() -> None:
    """Verify get_status polls external guiding telemetry."""
    from backend.services.observatory.telescope_service import TelescopeService

    guiding_service_mock = MagicMock()
    guiding_service_mock.get_status.return_value = {"history": [{"time": 100, "dra": 0.1, "ddec": -0.1}]}

    wayfinder_mock = MagicMock()
    wayfinder_mock.control.mount.status.return_value = {
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
