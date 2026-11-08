"""Unit tests for IngestionService.get_ingestion_status errors.

An unknown job raises NotFoundError, and a service with no job table
raises ConfigurationError, instead of a made-up "failed" status.
"""

from collections.abc import Iterable, Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.exceptions import ConfigurationError, NotFoundError
from backend.services.processing.ingestion_service import IngestionService


def test_an_unknown_job_raises_not_found() -> None:
    """Asking for a job id that does not exist raises NotFoundError."""
    job_service = MagicMock()
    job_service.get_job.return_value = None
    service = IngestionService(job_service=job_service)

    with pytest.raises(NotFoundError, match="no ingestion job"):
        service.get_ingestion_status("missing-job")


def test_no_job_service_raises_configuration_error() -> None:
    """A service without a job table cannot look up jobs."""
    with pytest.raises(ConfigurationError, match="job service"):
        IngestionService().get_ingestion_status("job1")


class _Measurement:
    """A stand-in quality measurement service that records its calls."""

    def __init__(self, fail_announce: bool = False) -> None:
        """Choose whether announcing raises."""
        self.announced: list[list[dict]] = []
        self.started: list[str] = []
        self.fail_announce = fail_announce

    def announce_summaries(self, summaries: Iterable[Mapping[str, Any]]) -> None:
        """Record an announcement, or fail.

        Raises
        ------
        RuntimeError
            When the stand-in was told to fail.
        """
        if self.fail_announce:
            raise RuntimeError("socket gone")
        self.announced.append(list(summaries))

    def start_frame_measurements(self, target_id: str, limit: int | None = None) -> dict:
        """Record a start.

        Returns
        -------
        result : `dict`
            The reply the real service gives.
        """
        self.started.append(target_id)
        return {"started": True, "target_id": target_id}


SUMMARY = {
    "target_id": "M 13",
    "frames_measured": 2,
    "rows_written": 26,
    "observing_night_ids": ["2026-10-13"],
}


def test_an_ingestion_announces_the_syncs_summary_and_starts_no_second_run() -> None:
    """The sync measured the new frames; the ingestion only announces that."""
    measurement = _Measurement()
    service = IngestionService(quality_measurement_service=measurement)
    service._log = MagicMock()

    service._announce_measurements("job1", [SUMMARY])

    assert measurement.announced == [[SUMMARY]]
    assert measurement.started == []


def test_a_failing_announcement_never_fails_the_ingestion() -> None:
    """An error announcing is logged and swallowed."""
    service = IngestionService(quality_measurement_service=_Measurement(fail_announce=True))
    service._log = MagicMock()

    service._announce_measurements("job1", [SUMMARY])


def test_an_ingestion_that_measured_nothing_announces_nothing() -> None:
    """With no summaries (calibration or no new frames), nothing is sent."""
    measurement = _Measurement()
    service = IngestionService(quality_measurement_service=measurement)

    service._announce_measurements("job1", [])

    assert measurement.announced == [] and measurement.started == []


def test_a_library_reindex_starts_a_measurement_for_each_target_that_gained_frames() -> None:
    """Only targets whose frame count rose are measured."""
    measurement = _Measurement()
    target_service = MagicMock()
    target_service.reindex_library.return_value = SimpleNamespace(
        targets=[
            SimpleNamespace(target_id="M 13", frames_before=10, frames_after=14),
            SimpleNamespace(target_id="M 57", frames_before=5, frames_after=5),
        ]
    )
    calibration = MagicMock()
    service = IngestionService(
        target_service=target_service,
        calibration=calibration,
        quality_measurement_service=measurement,
    )
    service._log = MagicMock()

    assert service._reindex_library("job1") is True

    assert measurement.started == ["M 13"]


def _offline_service() -> IngestionService:
    """Build an ingestion service whose observatory computer cannot be reached.

    Returns
    -------
    service : `IngestionService`
        A service whose remote listing raises `ExternalServiceError`.
    """
    from astrometricslib.exceptions import ExternalServiceError

    wayfinder = MagicMock()
    wayfinder.control.remote.list.side_effect = ExternalServiceError(
        "The observatory computer is not reachable."
    )
    return IngestionService(wayfinder=wayfinder)


@pytest.mark.parametrize("folder", ["M 81", "calibration"])
def test_offline_stats_and_file_list_are_a_status_not_an_error(
    folder: str, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreachable observatory computer gives `reachable: False`, no log."""
    import logging

    service = _offline_service()
    with caplog.at_level(logging.DEBUG):
        stats = service.get_remote_stats(folder)
        listing = service.list_remote_files(folder)
    assert stats["reachable"] is False
    assert stats["fileCount"] == 0
    assert listing == {"files": [], "resolvedFolder": folder, "reachable": False}
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_offline_telescope_polls_log_no_error_and_note_the_change_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Polls of an offline telescope log one INFO line, no WARNING or ERROR."""
    import logging

    from astrometricslib.exceptions import ExternalServiceError
    from backend.services.observatory.telescope_service import TelescopeService

    wayfinder = MagicMock()
    wayfinder.control.mount.status.side_effect = ExternalServiceError("offline")
    service = TelescopeService(wayfinder=wayfinder)
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            data = service.get_status()
    assert data["connectionStatus"] == "Disconnected"
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len([record for record in caplog.records if "status is unavailable" in record.getMessage()]) == 1


def test_scan_reports_offline_computer_as_status() -> None:
    """An empty scan from an offline computer gives ``reachable: False``."""
    from unittest.mock import MagicMock

    from backend.services.processing.ingestion_service import IngestionService

    wayfinder = MagicMock()
    wayfinder.control.remote.list.return_value = []
    wayfinder.control.remote.check_connection.return_value = False
    service = IngestionService.__new__(IngestionService)
    service._wayfinder = wayfinder
    assert service.scan_remote_targets_rpc() == {"folders": [], "reachable": False}

    wayfinder.control.remote.list.return_value = ["M_31"]
    assert service.scan_remote_targets_rpc() == {"folders": ["M_31"], "reachable": True}
