"""Tests for the models the system status service reports with."""

from backend.services.infrastructure.system_status_service import IndiStatus, SystemHealth


def test_indi_status_defaults_to_unknown() -> None:
    """Verify IndiStatus reads UNKNOWN when nothing has set it."""
    assert IndiStatus().status == "UNKNOWN"


def test_system_health_starts_with_an_unknown_indi_status() -> None:
    """Verify a fresh health report carries an UNKNOWN INDI status."""
    assert SystemHealth().indi.status == "UNKNOWN"
