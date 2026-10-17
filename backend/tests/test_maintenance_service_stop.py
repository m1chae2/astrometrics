"""Purpose: Tests that the maintenance thread stops as soon as it is told to.

Description: Shutdown waits for this thread, so a thread that sleeps through
its pauses holds the whole backend open. These tests stop the service during
the first pause and check that the thread ends quickly and runs no cleanup.
"""

import time
from unittest.mock import MagicMock

from backend.services.infrastructure.maintenance_service import MaintenanceService


def test_stop_during_the_first_pause_ends_the_thread_without_a_cleanup() -> None:
    """A stop before the first cleanup pass ends the thread and skips it."""
    job_service = MagicMock()
    service = MaintenanceService(job_service=job_service)
    service.start()
    thread = service._thread

    started_at = time.monotonic()
    service.stop()

    assert thread is not None
    assert not thread.is_alive()
    assert time.monotonic() - started_at < 1.0
    job_service.prune_old_jobs.assert_not_called()
