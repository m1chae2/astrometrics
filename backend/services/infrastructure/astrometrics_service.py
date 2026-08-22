"""Centralized state coordinator and telemetry service.

Aggregates active status updates from all services into a single,
unified source of truth and broadcasts state changes automatically to
connected UI clients.

REQ: SYS-1.0, BKD-5.3
"""

import asyncio
import logging
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class AstrometricsService:
    """Aggregate live status from other services into one system state.

    Combines telescope, camera, and processing updates from all
    services into a single thread-safe system state, and broadcasts
    state changes to the UI via SocketManager.
    """

    def __init__(self, socket_manager):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize the service with an injected socket manager.

        Parameters
        ----------
        socket_manager : `SocketManager`
            The active backend socket manager instance used to
            broadcast state updates to connected UI clients.
        """
        self.socket_manager = socket_manager
        self._lock = Lock()
        self._loop = None
        try:
            self._loop = asyncio.get_event_loop()
        except Exception as exc:
            logger.debug("No event loop available at construction time: %s", exc)

        # Unified System State
        self._state: dict[str, Any] = {
            "telescope": {
                "ra": "00 00 00",
                "dec": "+00 00 00",
                "altitude": "0.0",
                "azimuth": "0.0",
                "trackingStatus": "Idle",
                "connectionStatus": "Disconnected",
                "temperature": "0.0",
                "humidity": "0.0",
                "filter": "None",
                "focuserPosition": 0,
            },
            "processing": [],
            "health": {
                "resources": {"system_ram_usage_percent": 0.0, "vram_status": "Unknown"},
                "indi": {"status": "Disconnected"},
            },
        }

    def get_state(self) -> dict[str, Any]:
        """Retrieve a copy of the active system state.

        Returns
        -------
        state : `dict`
            A shallow copy of the current system state dictionary.
        """
        with self._lock:
            # Shallow copy is sufficient here; nested dicts are always
            # replaced wholesale, never mutated in place.
            return dict(self._state)

    def update_telescope_state(self, updates: dict[str, Any]) -> None:
        """Update the telescope state and broadcast the change.

        Parameters
        ----------
        updates : `dict`
            One or more telescope parameters to merge into the
            existing telescope state.
        """
        with self._lock:
            self._state["telescope"].update(updates)
            state_to_send = dict(self._state)

        self._broadcast_event("system_state_update", state_to_send)

    def update_processing_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Update the active background processing jobs and broadcast.

        Parameters
        ----------
        jobs : `list` of `dict`
            Active processing job metadata dicts, replacing the
            previously tracked list.
        """
        with self._lock:
            self._state["processing"] = jobs
            state_to_send = dict(self._state)

        self._broadcast_event("system_state_update", state_to_send)

    def update_health_metrics(self, category: str, updates: dict[str, Any]) -> None:
        """Update system health status metrics and broadcast the state update.

        ### Parameters
        - category: Either 'resources' or 'indi'.
        - updates: Dict of health status metrics.
        """
        with self._lock:
            if category in self._state["health"]:
                self._state["health"][category].update(updates)
            state_to_send = dict(self._state)

        self._broadcast_event("system_state_update", state_to_send)

    def _broadcast_event(self, action: str, payload: dict) -> None:
        """Broadcast the action and payload to all WebSocket clients."""
        if not self.socket_manager:
            return

        if not self._loop:
            try:
                self._loop = asyncio.get_running_loop()
            except Exception as exc:
                logger.debug("No running event loop available: %s", exc)

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.socket_manager.dispatch_ui_event(action, payload), self._loop
            )
