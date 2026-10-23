"""Unit tests for IngestionService.get_ingestion_status errors.

An unknown job raises NotFoundError, and a service with no job table
raises ConfigurationError, instead of a made-up "failed" status.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import ConfigurationError, NotFoundError
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

    def __init__(self, fail_start: bool = False) -> None:
        """Choose whether starting the job raises."""
        self.announced: list[str] = []
        self.started: list[str] = []
        self.fail_start = fail_start

    def announce_rows_since(self, target_id: str, since: object) -> None:
        """Record an announcement."""
        self.announced.append(target_id)

    def start_frame_measurements(self, target_id: str, limit: int | None = None) -> dict:
        """Record a start, or fail.

        Returns
        -------
        result : `dict`
            The reply the real service gives.

        Raises
        ------
        RuntimeError
            When the stand-in was told to fail.
        """
        if self.fail_start:
            raise RuntimeError("no thread")
        self.started.append(target_id)
        return {"started": True, "target_id": target_id}


def test_an_ingestion_that_added_frames_announces_and_starts_the_measurement() -> None:
    """After the sync step the service announces rows and starts the job."""
    measurement = _Measurement()
    service = IngestionService(quality_measurement_service=measurement)
    service._log = MagicMock()

    service._measure_new_frames("job1", "M 13", datetime(2026, 10, 22, tzinfo=UTC))

    assert measurement.announced == ["M 13"]
    assert measurement.started == ["M 13"]


def test_a_failing_measurement_never_fails_the_ingestion() -> None:
    """An error starting the job is logged and swallowed."""
    service = IngestionService(quality_measurement_service=_Measurement(fail_start=True))
    service._log = MagicMock()

    service._measure_new_frames("job1", "M 13", datetime(2026, 10, 22, tzinfo=UTC))


def test_a_calibration_ingestion_measures_nothing() -> None:
    """With no target name, nothing is announced or started."""
    measurement = _Measurement()
    service = IngestionService(quality_measurement_service=measurement)

    service._measure_new_frames("job1", None, datetime(2026, 10, 22, tzinfo=UTC))

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
        calibration_library=calibration,
        quality_measurement_service=measurement,
    )
    service._log = MagicMock()

    assert service._reindex_library("job1") is True

    assert measurement.started == ["M 13"]
