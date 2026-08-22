"""WebSocket connection registry and broadcast helper for the UI."""

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class SocketManager:
    """Manage active websocket connections and broadcast events."""

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        self.active_connections: list[WebSocket] = []
        self._loop = None
        try:
            self._loop = asyncio.get_event_loop()
        except Exception as exc:
            logger.debug("No event loop available at construction time: %s", exc)

    async def connect(self, websocket: WebSocket):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Accept `websocket` and register it as an active connection."""
        await websocket.accept()
        # Capture the running loop if not already set
        if not self._loop:
            self._loop = asyncio.get_running_loop()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Remove `websocket` from the active connection list."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected. Active connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Broadcast a JSON message to all connected clients."""
        payload = json.dumps(message)
        dead_connections = []

        for connection in self.active_connections:
            try:
                await connection.send_text(payload)
            except Exception as e:
                logger.warning(f"Failed to send to client: {e}")
                dead_connections.append(connection)

        # Cleanup dead connections
        for dead in dead_connections:
            self.disconnect(dead)

    async def dispatch_ui_event(self, action: str, payload: dict | None = None):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Send a standardized UI event to all connected clients."""
        if payload is None:
            payload = {}
        event = {"type": "UI_EVENT", "action": action, "payload": payload}
        await self.broadcast(event)

    def broadcast_log(self, message: str, level: str = "INFO"):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Broadcast a log message to all connected clients.

        Safe to call from any thread.
        """
        event = {
            "type": "UI_EVENT",
            "action": "log",
            "payload": {
                "message": message,
                "level": level,
                "timestamp": logging.Formatter().formatTime(
                    logging.LogRecord("", 0, "", 0, "", None, None), "%H:%M:%S"
                ),
            },
        }

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(event), self._loop)


class SocketLoggingHandler(logging.Handler):
    """Custom logging handler that broadcasts logs via SocketManager."""

    def __init__(self, socket_manager: SocketManager):  # ruff: ignore[missing-return-type-special-method]
        super().__init__()
        self.socket_manager = socket_manager

    def emit(self, record):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Format `record` and broadcast it to connected clients."""
        try:
            msg = self.format(record)
            self.socket_manager.broadcast_log(msg, record.levelname)
        except Exception:
            self.handleError(record)
