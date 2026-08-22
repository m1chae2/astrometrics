"""Service for performing periodic system maintenance tasks.

REQ: SYS-1.0: Periodic maintenance and cleanup.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class MaintenanceService:
    """Handle background maintenance: log pruning, database cleanup."""

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        job_service,  # ruff: ignore[missing-type-function-argument]
        pruning_days: int = 7,
        interval_seconds: int = 3600,
        system_status_service=None,  # ruff: ignore[missing-type-function-argument]
        telescope_service=None,  # ruff: ignore[missing-type-function-argument]
    ):
        self.job_service = job_service
        self.pruning_days = pruning_days
        self.interval_seconds = interval_seconds
        self.system_status_service = system_status_service
        self.telescope_service = telescope_service
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Start the background maintenance thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_maintenance_loop, daemon=True, name="MaintenanceThread"
        )
        self._thread.start()
        logger.info(f"MaintenanceService started with pruning threshold of {self.pruning_days} days.")

    def stop(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Stop the background maintenance thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("MaintenanceService stopped.")

    def _run_maintenance_loop(self):  # ruff: ignore[missing-return-type-private-function]
        """Periodically runs maintenance tasks."""
        # Initial run after a short delay
        time.sleep(10)

        while not self._stop_event.is_set():
            try:
                self.perform_cleanup()
            except Exception as e:
                logger.error(f"Error during maintenance cleanup: {e}")

            # Wait for next interval or stop signal
            # We sleep in small chunks to be responsive to stop event
            for _ in range(self.interval_seconds):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def perform_cleanup(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Execute all cleanup tasks."""
        logger.info("Starting periodic maintenance cleanup...")

        # 1. Prune old jobs and logs
        removed_count = self.job_service.prune_old_jobs(self.pruning_days)
        if removed_count > 0:
            logger.info(f"Pruned {removed_count} jobs older than {self.pruning_days} days.")

        # Add future maintenance tasks here (e.g. temporary file cleanup)

        logger.info("Maintenance cleanup completed.")

    async def check_health(self) -> dict:
        """Aggregate hardware and system health status.

        Combines system resource usage with the connected telescope
        bus (INDI) status into a single response payload.

        Returns
        -------
        result : `dict`
            A dict with ``"status"`` and a ``"data"`` payload
            containing ``"resources"`` and ``"indi"`` status.
        """
        # 1. Resource usage
        resources = {"system_ram_usage_percent": 0.0, "vram_status": "Unknown"}
        if self.system_status_service:
            resources = self.system_status_service.get_resource_usage()

        # 2. INDI status
        indi_status = {"status": "Disconnected", "devices": []}
        if self.telescope_service:
            status = self.telescope_service.get_status()
            if hasattr(status, "model_dump"):
                status_dict = status.model_dump(by_alias=True)
            elif hasattr(status, "dict"):
                status_dict = status.dict(by_alias=True)
            else:
                status_dict = status

            indi_status = {"status": status_dict.get("connectionStatus", "Disconnected"), "devices": []}

        return {"status": "success", "data": {"resources": resources, "indi": indi_status}}
