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

# Longest a connect or a read may wait before the stop flag is checked again.
# PHD2 sends nothing while it is idle, so without a limit a read on a quiet
# connection would never return and a stop would never be noticed.
_SOCKET_TIMEOUT_SECONDS = 1.0

# Bytes asked for in one socket read.
_READ_CHUNK_BYTES = 4096


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
    socket_timeout_seconds : `float`, optional
        Longest a connect or a read waits before the stop flag is checked
        again (default `_SOCKET_TIMEOUT_SECONDS`).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = DEFAULT_PHD2_PORT,
        socket_factory: Callable[[], socket.socket] | None = None,
        reconnect_cooldown_seconds: float = _RECONNECT_COOLDOWN_SECONDS,
        socket_timeout_seconds: float = _SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the client without opening a connection yet."""
        self.host = host
        self.port = port
        self._socket_factory = socket_factory or (lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        self._reconnect_cooldown_seconds = reconnect_cooldown_seconds
        self._socket_timeout_seconds = socket_timeout_seconds
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
            The connected socket, ready for line-delimited JSON reads. Its
            connect and reads give up after `socket_timeout_seconds`.

        Raises
        ------
        OSError
            If the connection cannot be made, including a connect that
            takes longer than `socket_timeout_seconds`.
        """
        sock = self._socket_factory()
        sock.settimeout(self._socket_timeout_seconds)
        try:
            sock.connect((self.host, self.port))
        except OSError:
            sock.close()
            raise
        return sock

    def events(self, stop_event: Any | None = None) -> Iterator[dict[str, Any]]:
        """Yield parsed JSON event dicts from PHD2's event stream.

        Reconnects on a dropped connection, subject to the reconnect
        cooldown, until `stop_event` is set. A set stop is noticed within
        about `socket_timeout_seconds`, even on a connection that sends
        nothing.

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

        def stopped() -> bool:
            """Report whether the caller has asked the stream to stop.

            Returns
            -------
            stopped : `bool`
                `True` once `stop_event` is set.
            """
            return stop_event is not None and stop_event.is_set()

        while not stopped():
            if not self._can_attempt_connect():
                time.sleep(0.1)
                continue
            try:
                sock = self._connect()
            except OSError as connect_error:
                logger.warning("PHD2 connection to %s:%s failed: %s", self.host, self.port, connect_error)
                continue

            try:
                pending = b""
                while not stopped():
                    try:
                        chunk = sock.recv(_READ_CHUNK_BYTES)
                    except TimeoutError:
                        # Nothing arrived in time; check the stop flag again.
                        continue
                    if not chunk:
                        # PHD2 closed the connection; reconnect later.
                        break
                    pending += chunk
                    while b"\n" in pending:
                        raw_line, pending = pending.split(b"\n", 1)
                        stripped_line = raw_line.decode("utf-8", errors="replace").strip()
                        if not stripped_line:
                            continue
                        try:
                            event = json.loads(stripped_line)
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed PHD2 event line: %r", stripped_line)
                            continue
                        yield event
                        if stopped():
                            return
            except OSError as read_error:
                logger.warning("PHD2 connection to %s:%s dropped: %s", self.host, self.port, read_error)
            finally:
                sock.close()
