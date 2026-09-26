"""Purpose: Unit tests for cumulative multi-session alignment and tracking.

Description: Verifies that AlignmentService and LoggerInterface support
querying all alignment attempts across all recorded sessions when session_id
is "all", enabling long-term empirical mount tracking analytics.
"""

from unittest.mock import MagicMock

import pytest

from backend.services.observatory.alignment_service import AlignmentService


def test_get_session_data_all_retrieves_cumulative_attempts() -> None:
    """Verify get_session_data('all') queries all attempts and polar status."""
    mock_logger = MagicMock()
    mock_logger.get_session_alignment_attempts.return_value = [
        {
            "status": "aligned",
            "delta_ra_arcsec": 0.5,
            "delta_dec_arcsec": -0.8,
            "mount_ra": 310.5,
            "mount_dec": 45.2,
            "pointing_error_arcsec": 0.94,
            "timestamp": 1720000000.0,
            "target_name": "Deneb",
        },
        {
            "status": "aligned",
            "delta_ra_arcsec": 1.2,
            "delta_dec_arcsec": 0.3,
            "mount_ra": 279.2,
            "mount_dec": 38.8,
            "pointing_error_arcsec": 1.24,
            "timestamp": 1720100000.0,
            "target_name": "Vega",
        },
    ]
    mock_logger.get_polar_alignment_logs.return_value = [
        {
            "status": "aligned",
            "total_error_arcsec": 42.0,
            "alt_error_arcsec": 20.0,
            "az_error_arcsec": 36.9,
            "pole_ra": 310.0,
            "pole_dec": 89.9,
            "paa_points": [],
            "timestamp": 1720100000.0,
        }
    ]

    service = AlignmentService(
        indi_interface=None,
        imaging_service=None,
        star_identifier=None,
        logger_interface=mock_logger,
    )

    data = service.get_session_data("all")

    mock_logger.get_session_alignment_attempts.assert_called_once_with("all")
    mock_logger.get_polar_alignment_logs.assert_called_once_with(limit=1)

    assert len(data["alignmentAttempts"]) == 2
    assert data["alignmentAttempts"][0]["targetName"] == "Deneb"
    assert data["alignmentAttempts"][1]["targetName"] == "Vega"
    assert data["polarAlignment"] is not None
    assert data["polarAlignment"]["totalErrorArcsec"] == pytest.approx(42.0)


def test_get_cumulative_tracking_data_delegates_to_all_sessions() -> None:
    """Verify get_cumulative_tracking_data delegates to get_session_data."""
    mock_logger = MagicMock()
    mock_logger.get_session_alignment_attempts.return_value = []
    mock_logger.get_polar_alignment_logs.return_value = []

    service = AlignmentService(
        indi_interface=None,
        imaging_service=None,
        star_identifier=None,
        logger_interface=mock_logger,
    )

    data = service.get_cumulative_tracking_data()

    mock_logger.get_session_alignment_attempts.assert_called_once_with("all")
    assert data["alignmentAttempts"] == []
