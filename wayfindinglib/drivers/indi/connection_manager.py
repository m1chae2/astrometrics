"""Manages the lifecycle and health of the INDI server connection."""

import concurrent.futures
import logging
import socket
import time

logger = logging.getLogger(__name__)

_LOOKUP_THREAD = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="IndiHostLookup")
"""One helper thread for name lookups; slow lookups cannot pile up."""


class ConnectionManager:
    """Handle the INDI server connection lifecycle and responsiveness."""

    def __init__(self, hostname: str, port: int = 7624):  # ruff: ignore[missing-return-type-special-method]
        self.hostname = hostname
        self.port = port
        self.base_connection_cooldown = 5.0  # Seconds between attempts while connected or newly failed
        self.max_connection_cooldown = 30.0  # Longest wait between attempts to a host that stays down
        self.connection_cooldown = self.base_connection_cooldown  # Seconds between connection attempts
        self.consecutive_failures = 0
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
        except OSError:
            # OSError covers a refused connection, a timeout, and an
            # unknown host name.
            self.last_responsive_value = False
            return False

    def is_host_resolvable(self, host: str, port: int, timeout: float = 1.0) -> bool:
        """Check, without waiting long, that the server name can be looked up.

        Looking up a ``.local`` name that nothing answers takes about 5 seconds
        inside the INDI client's own connect call, and that call holds the one
        worker that serves every INDI request, so a switched-off telescope
        stalled unrelated calls (such as the device list) for seconds. The
        lookup is done here in a helper thread instead and given `timeout`
        seconds; a lookup that is still running is left to finish in the
        background and the attempt is skipped.

        Parameters
        ----------
        host : `str`
            Server host name or address.
        port : `int`
            Server port.
        timeout : `float`, optional
            Seconds to wait for the lookup. Defaults to 1.

        Returns
        -------
        resolvable : `bool`
            `True` if the name resolved within `timeout`.
        """
        lookup = _LOOKUP_THREAD.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        try:
            lookup.result(timeout=timeout)
            return True
        except concurrent.futures.TimeoutError, OSError:
            return False

    def record_connection_result(self, connected: bool) -> None:
        """Remember whether a connection attempt worked, and adjust the wait.

        A failed attempt to a host that is switched off can block for about
        5 seconds (looking up a ``.local`` name that does not answer), so
        retrying every 5 seconds kept the connection thread busy nearly all
        the time. Each failure in a row doubles the wait, up to
        `max_connection_cooldown`; a success puts it back to the base value.

        Parameters
        ----------
        connected : `bool`
            Whether the attempt ended with a connected server.
        """
        if connected:
            self.consecutive_failures = 0
            self.connection_cooldown = self.base_connection_cooldown
            return
        self.consecutive_failures += 1
        self.connection_cooldown = min(
            self.max_connection_cooldown,
            self.base_connection_cooldown * 2 ** (self.consecutive_failures - 1),
        )

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
