"""Purpose: Unit tests for ObservatoryService.check_safety (M9).

Description: Verifies check_safety() reads connection state and
humidity from `ObservatoryControl.get_telescope_status()` (via
`hardware_operations.get_telescope_status`) rather than a raw
IndiInterface -- the M9 backend cleanup that dropped the deprecated
`indi_interface` constructor param and `.indi` property outright.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.services.observatory.observatory_service import ObservatoryService


def test_check_safety_reports_safe_when_connected_and_dry():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a connected mount with low humidity reports safe."""
    observatory = MagicMock()
    observatory.active_enclosure.return_value = None
    service = ObservatoryService(observatory_api=observatory)

    with patch(
        "wayfindinglib.tasks.control_tasks.hardware_operations.get_telescope_status",
        return_value={"connectionStatus": "Connected", "humidity": "40.0%"},
    ):
        status = service.check_safety()

    assert status["safe"] is True
    assert status["connected"] is True
    assert status["humidity"] == pytest.approx(40.0)
    assert status["reason"] == ""


def test_check_safety_reports_unsafe_when_disconnected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a disconnected mount reports unsafe with the right reason."""
    observatory = MagicMock()
    observatory.active_enclosure.return_value = None
    service = ObservatoryService(observatory_api=observatory)

    with patch(
        "wayfindinglib.tasks.control_tasks.hardware_operations.get_telescope_status",
        return_value={"connectionStatus": "Disconnected", "humidity": "40.0%"},
    ):
        status = service.check_safety()

    assert status["safe"] is False
    assert status["connected"] is False
    assert status["reason"] == "Disconnected"


def test_check_safety_reports_unsafe_on_high_humidity():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a connected mount with high humidity still reports unsafe."""
    observatory = MagicMock()
    observatory.active_enclosure.return_value = None
    service = ObservatoryService(observatory_api=observatory)

    with patch(
        "wayfindinglib.tasks.control_tasks.hardware_operations.get_telescope_status",
        return_value={"connectionStatus": "Connected", "humidity": "95.0%"},
    ):
        status = service.check_safety()

    assert status["safe"] is False
    assert status["connected"] is True
    assert status["humidity"] == pytest.approx(95.0)
    assert status["reason"] == "High Humidity"


def test_check_safety_reports_enclosure_type_name():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the enclosure field reflects the active enclosure's type."""
    from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType

    enclosure = Enclosure(
        id="enc-1", enclosure_type=EnclosureType.ROLL_OFF_ROOF, park_azimuth_deg=180.0, park_altitude_deg=0.0
    )
    observatory = MagicMock()
    observatory.active_enclosure.return_value = enclosure
    service = ObservatoryService(observatory_api=observatory)

    with patch(
        "wayfindinglib.tasks.control_tasks.hardware_operations.get_telescope_status",
        return_value={"connectionStatus": "Connected", "humidity": "10.0%"},
    ):
        status = service.check_safety()

    assert status["enclosure"] == "ROLL_OFF_ROOF"


def test_check_safety_reports_none_enclosure_when_unconfigured():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no configured enclosure reports "NONE", not a crash."""
    observatory = MagicMock()
    observatory.active_enclosure.return_value = None
    service = ObservatoryService(observatory_api=observatory)

    with patch(
        "wayfindinglib.tasks.control_tasks.hardware_operations.get_telescope_status",
        return_value={"connectionStatus": "Connected", "humidity": "10.0%"},
    ):
        status = service.check_safety()

    assert status["enclosure"] == "NONE"
