"""Unit tests for IngestionService.get_ingestion_status errors.

An unknown job raises NotFoundError, and a service with no job table
raises ConfigurationError, instead of a made-up "failed" status.
"""

from collections.abc import Iterable, Mapping
from types import SimpleNamespace
from typing import Any
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
        calibration_library=calibration,
        quality_measurement_service=measurement,
    )
    service._log = MagicMock()

    assert service._reindex_library("job1") is True

    assert measurement.started == ["M 13"]
