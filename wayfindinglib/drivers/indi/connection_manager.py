"""Manages the lifecycle and health of the INDI server connection."""

import logging
import socket
import time

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Handle the INDI server connection lifecycle and responsiveness."""

    def __init__(self, hostname: str, port: int = 7624):  # ruff: ignore[missing-return-type-special-method]
        self.hostname = hostname
        self.port = port
        self.connection_cooldown = 5.0  # Seconds between connection attempts
        self.last_connection_attempt = 0
        self.last_responsive_check = 0
        self.last_responsive_value = False

    def is_server_responsive(self) -> bool:
        """Check if the INDI server is reachable and responsive.

        Sends a handshake to verify. Caches result for 2 seconds to
        prevent blocking frequent status polls.

        Returns
        -------
        responsive : `bool`
            `True` if the server responded to the handshake.
        """
        now = time.time()
        if (now - self.last_responsive_check) < 5.0:
            return self.last_responsive_value

        self.last_responsive_check = now
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((self.hostname, self.port))
            # Send a basic command to elicit a response.
            sock.sendall(b"<getProperties version='1.7'/>")
            data = sock.recv(1024)
            sock.close()
            self.last_responsive_value = len(data) > 0
            return self.last_responsive_value
        except Exception:
            self.last_responsive_value = False
            return False

    def can_attempt_reconnect(self) -> bool:
        """Determine if enough time has passed to attempt a reconnection.

        Returns
        -------
        can_reconnect : `bool`
            `True` if the cooldown has elapsed (also updates the
            last-attempt timestamp as a side effect).
        """
        now = time.time()
        if (now - self.last_connection_attempt) >= self.connection_cooldown:
            self.last_connection_attempt = now
            return True
        return False
