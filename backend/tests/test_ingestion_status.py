"""Unit tests for IngestionService.get_ingestion_status errors.

An unknown job raises NotFoundError, and a service with no job table
raises ConfigurationError, instead of a made-up "failed" status.
"""

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
