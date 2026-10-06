"""Unit tests for SyncService error handling.

Checks that a sync call that cannot run raises an error category
instead of returning an error dictionary.
"""

import pytest

from astrometricslib import ConfigurationError
from backend.services.infrastructure.sync_service import SyncService


def test_sync_all_from_remote_without_observatory_raises() -> None:
    """Syncing with no observatory control set up raises ConfigurationError."""
    service = SyncService(observatory_api=None)

    with pytest.raises(ConfigurationError, match="not set up"):
        service.sync_all_from_remote()
