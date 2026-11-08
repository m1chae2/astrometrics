"""Purpose: Tests that the health check works inside the server's event loop.

Description: The telescope status read can start its own event loop, which
fails when it runs on the server's loop. These tests use a fake telescope
service that does exactly that and check that the health check still works.
"""

import asyncio
from unittest.mock import MagicMock

from backend.services.infrastructure.maintenance_service import MaintenanceService


class _LoopStartingTelescopeService:
    """Fake telescope service whose status read calls `asyncio.run`."""

    def get_status(self) -> dict:
        """Read the status by starting a new event loop.

        Returns
        -------
        status : `dict`
            A status with a ``"connectionStatus"`` entry.
        """

        async def _read() -> str:
            """Wait briefly, then give a connection state.

            Returns
            -------
            state : `str`
                The connection state.
            """
            await asyncio.sleep(0)
            return "Connected"

        return {"connectionStatus": asyncio.run(_read())}


def test_check_health_runs_the_status_read_off_the_event_loop() -> None:
    """The health check returns the INDI section instead of raising."""
    service = MaintenanceService(
        job_service=MagicMock(),
        telescope_service=_LoopStartingTelescopeService(),  # type: ignore[arg-type]
    )

    result = asyncio.run(service.check_health())

    assert result["status"] == "success"
    assert result["data"]["indi"] == {"status": "Connected", "devices": []}
