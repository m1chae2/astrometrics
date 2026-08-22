"""Purpose: Transport layer for PHD2's event server.

Description: A read-only TCP client that connects to PHD2's line-delimited
JSON event stream and yields parsed events, reconnecting on a dropped
connection with the same cooldown discipline
wayfindinglib.drivers.indi.connection_manager.ConnectionManager already uses
for INDI reconnects. Phase 0 is passive only -- this client never sends
PHD2 commands (dither/calibrate/etc. belong to later telescope-controller
phases).
"""

import json
import logging
import socket
import time
from collections.abc import Callable, Iterator
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PHD2_PORT = 4400

# Matches ConnectionManager.connection_cooldown's INDI reconnect discipline,
# so both hardware integrations back off from a dead endpoint the same way.
_RECONNECT_COOLDOWN_SECONDS = 5.0


class PHD2Client:
    """Read-only TCP client for PHD2's event server.

    Parameters
    ----------
    host : `str`, optional
        Hostname PHD2's event server is listening on (default
        `"localhost"`).
    port : `int`, optional
        Port PHD2's event server is listening on (default `4400`).
    socket_factory : `Callable[[], socket.socket]`, optional
        Factory used to create the underlying socket. Overridable so
        tests can connect to a local test-only server without needing a
        real PHD2 process. Defaults to a plain `AF_INET`/`SOCK_STREAM`
        socket.
    reconnect_cooldown_seconds : `float`, optional
        Minimum time between connection attempts (default
        `_RECONNECT_COOLDOWN_SECONDS`). Overridable so tests can exercise
        reconnect behavior without waiting the production cooldown.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        host: str = "localhost",
        port: int = DEFAULT_PHD2_PORT,
        socket_factory: Callable[[], socket.socket] | None = None,
        reconnect_cooldown_seconds: float = _RECONNECT_COOLDOWN_SECONDS,
    ):
        """Initialize the client without opening a connection yet."""
        self.host = host
        self.port = port
        self._socket_factory = socket_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        self._reconnect_cooldown_seconds = reconnect_cooldown_seconds
        self._last_connection_attempt = 0.0

    def _can_attempt_connect(self) -> bool:
        """Return whether the reconnect cooldown has elapsed.

        Returns
        -------
        can_attempt : `bool`
            `True` if at least `_RECONNECT_COOLDOWN_SECONDS` have passed
            since the last connection attempt, in which case this call
            also records the current attempt's timestamp.
        """
        now = time.time()
        if (now - self._last_connection_attempt) >= self._reconnect_cooldown_seconds:
            self._last_connection_attempt = now
            return True
        return False

    def _connect(self) -> socket.socket:
        """Open a new connection to PHD2's event server.

        Returns
        -------
        connected_socket : `socket.socket`
            The connected socket, ready for line-delimited JSON reads.
        """
        sock = self._socket_factory()
        sock.connect((self.host, self.port))
        return sock

    def events(self, stop_event: Any | None = None) -> Iterator[dict[str, Any]]:
        """Yield parsed JSON event dicts from PHD2's event stream.

        Reconnects on a dropped connection, subject to the reconnect
        cooldown, until `stop_event` is set.

        Parameters
        ----------
        stop_event : `threading.Event`, optional
            When set, the generator stops yielding and returns. If `None`
            (default), runs until an unrecoverable error occurs.

        Yields
        ------
        event : `dict`
            One decoded JSON message per PHD2 event.
        """
        while stop_event is None or not stop_event.is_set():
            if not self._can_attempt_connect():
                time.sleep(0.1)
                continue
            try:
                sock = self._connect()
            except OSError as connect_error:
                logger.warning(f"PHD2 connection to {self.host}:{self.port} failed: {connect_error}")
                continue

            try:
                file_handle = sock.makefile("r")
                for line in file_handle:
                    if stop_event is not None and stop_event.is_set():
                        return
                    stripped_line = line.strip()
                    if not stripped_line:
                        continue
                    try:
                        yield json.loads(stripped_line)
                    except json.JSONDecodeError:
                        logger.warning(f"Skipping malformed PHD2 event line: {stripped_line!r}")
            except OSError as read_error:
                logger.warning(f"PHD2 connection to {self.host}:{self.port} dropped: {read_error}")
            finally:
                sock.close()
